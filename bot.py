import os
import subprocess
import threading
import re
import asyncio

import discord
from discord import option, Option, AutocompleteContext
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix=None, intents=intents)

# Mapping of spawn IDs to channel, user, and active processes
spawns: dict[str, dict] = {}


@bot.event
async def on_ready():
    """Called when the bot is ready and connected to Discord."""
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")


@bot.slash_command(name="hello", description="Responds with 'Hello, world!'")
@option(
    "greeting",
    description="Greet me!",
    choices=["Hello, world!", "Hi there!", "Greetings!", "Howdy!"]
)
async def hello(
    ctx: discord.ApplicationContext,
    greeting: Option(str, "Greeting text", autocomplete=True) = "Hello, world!"
):
    """Responds with a greeting message chosen via autocomplete."""
    await ctx.respond(greeting)


@bot.slash_command(name="spawn", description="Reserve a spawn ID for Codex prompts")
@option("spawn_id", description="The ID under which you will reference the Codex Instance")
async def spawn(
    ctx: discord.ApplicationContext,
    spawn_id: Option(str, "Unique spawn session ID")
):
    """Registers a unique spawn ID that can be used for future prompts."""
    if spawn_id in spawns:
        await ctx.respond(
            f"❌ Spawn ID '{spawn_id}' is already in use.", ephemeral=True
        )
        return
    spawns[spawn_id] = {"channel": ctx.channel, "user": ctx.author, "processes": []}
    await ctx.respond(
        f"✅ Spawn ID '{spawn_id}' registered. Mention me with 'to {spawn_id}: <message>' to send prompts."
    )


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Parse bot mention pattern: <@bot_id> to spawn_id: prompt
    pattern = rf"^<@!?{bot.user.id}>\s+to\s+(\w+):\s*(.+)\s*"
    match = re.match(pattern, message.content)
    if not match:
        return

    spawn_id, prompt = match.groups()
    if spawn_id not in spawns:
        await message.channel.send(
            f"❌ Unknown spawn ID '{spawn_id}'.", reference=message
        )
        return

    entry = spawns[spawn_id]
    args = [
        "node",
        "third_party/codex-headless/dist/cli.mjs",
        "-q",
        prompt,
    ]
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1
    )
    entry["processes"].append(proc)

    def reader():
        for line in proc.stdout:
            coro = entry["channel"].send(f"<@{entry['user'].id}> {line.strip()}")
            asyncio.run_coroutine_threadsafe(coro, bot.loop)

    threading.Thread(target=reader, daemon=True).start()

    await bot.process_commands(message)


if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    api_key = os.getenv("MODEL_API_KEY")
    if not token:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
        exit(1)
    bot.run(token)
