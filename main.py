import discord
import os
import pprint
import sqlite3
import asyncio

from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from langchain.chat_models import ChatOpenAI
from langchain.prompts.chat import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    SystemMessagePromptTemplate,
    AIMessagePromptTemplate,
)
from discord import (DMChannel, TextChannel)
from conversation import Conversation
from repository import Repository
from message import Message
from utils import Utils

def clean_up_response(discord_name, original_response):
    if original_response.startswith(discord_name + ":"):
        original_response = original_response[len(discord_name + ":"):]
    elif original_response.startswith("AI:"):
        original_response = original_response[len("AI:"):]
    return original_response.strip()

async def run_chain(channel, chain, discord_context, conversation_context, long_term_memory):
    discord_name = 'EhrlichGPT'
    response = await chain.arun(
        name="Bryan Ehrlich",
        discord_name=discord_name,
        qualities="Kind, Witty, Funny, Willing to help, Acerbic, Serious when context calls for it",
        discord_context=discord_context,
        conversation_context=conversation_context,
        long_term_memory=long_term_memory,
    )
    response = clean_up_response(discord_name, response)

    if response == 'PASS':
        print('Bot declined to respond')
    else:
        await channel.send(response)



async def delayed_typing_indicator(channel):
    await asyncio.sleep(2)
    async with channel.typing():
        await asyncio.sleep(float('inf'))

discord_bot_key = os.environ['DISCORD_BOT_TOKEN']
intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

conversations = {}
chat = ChatOpenAI(temperature=0.9, max_tokens=500)

os.makedirs("conversations", exist_ok=True)

@client.event
async def on_ready():
    print(f'We have logged in as {client.user}')
    for db_file in os.listdir("conversations"):
        if db_file == ".gitignore":
            continue
        channel_db = os.path.splitext(db_file)[0]
        channel_id = int(channel_db.split('.')[0])
        db_path = Repository.get_db_path(channel_id)
        Repository.create_db_if_not_exists(db_path)
        conversations[channel_id] = Repository.load_conversation(channel_id, db_path)

@client.event
async def on_message(message):
    pprint.pprint(message)
    channel_id = message.channel.id
    db_path = Repository.get_db_path(channel_id)
    Repository.create_db_if_not_exists(db_path)

    if channel_id not in conversations:
        conversations[channel_id] = Conversation(channel_id, [], '', '')

    current_conversation = conversations[channel_id]
    if message.author == client.user:
        # Add our own AI message to conversation
        current_conversation.add_message(Message("ai", message.content))
        Repository.save_message(db_path, "ai", message.content)
        async with current_conversation.lock:
            await Repository.summarize_conversation(current_conversation)
        return
    else:
        # TODO: These messages are getting deleted if we're mid-summarizer - do we need a message list lock?
        formatted_sender = message.author.name + "#" + message.author.discriminator
        current_conversation.add_message(Message(formatted_sender, message.content))
        Repository.save_message(db_path, formatted_sender, message.content)

        if isinstance(message.channel, DMChannel):
            context = "Direct Message"
        elif isinstance(message.channel, TextChannel):
            context = "Group Room with " + str(len(message.channel.members)) + " members"
        else:
            context = "Unknown"

        if not current_conversation.lock.locked():
            async with current_conversation.lock:
                while True:
                    chat_prompt_template = ChatPromptTemplate.from_messages(conversations[channel_id].get_conversation_prompts())
                    chain = LLMChain(llm=chat, prompt=chat_prompt_template)
                    typing_indicator_task = asyncio.create_task(delayed_typing_indicator(message.channel))
                    chain_run_task = asyncio.create_task(run_chain(message.channel, chain, context, current_conversation.get_active_memory(), None))
                    await asyncio.wait([typing_indicator_task, chain_run_task], return_when=asyncio.FIRST_COMPLETED)
                    typing_indicator_task.cancel()
                    if not current_conversation.sync_busy_history():
                        break


client.run(discord_bot_key)
