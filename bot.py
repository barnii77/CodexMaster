import os
import subprocess
import threading
import re
import json
import asyncio
import sys
import traceback

import discord
from discord import option, AutocompleteContext
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="", intents=intents)

# Mapping of spawn IDs to channel, user, and active processes
spawns: dict[str, dict] = {}

# Set up directory structure
SESSIONS_DIR = "~/.codex/sessions"
if os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)


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
    greeting: str
):
    """Responds with a greeting message chosen via autocomplete."""
    await ctx.respond(greeting)


@bot.slash_command(name="spawn", description="Reserve a spawn ID for Codex prompts")
@option("spawn_id", description="The ID under which you will reference the Codex Instance")
async def spawn(
    ctx: discord.ApplicationContext,
    spawn_id: str
):
    """Registers a unique spawn ID that can be used for future prompts."""
    if spawn_id in spawns:
        await ctx.respond(
            f"❌ Spawn ID '{spawn_id}' is already in use.", ephemeral=True
        )
        return
    spawns[spawn_id] = {"spawn_id": spawn_id, "channel": ctx.channel, "user": ctx.author, "processes": []}
    await ctx.respond(
        f"✅ Spawn ID '{spawn_id}' registered. Mention me with 'to {spawn_id}: <message>' to send prompts."
    )


@bot.slash_command(name="kill", description="Kill active processes for a spawn ID")
@option("spawn_id", description="The spawn ID to kill processes for")
async def kill(
    ctx: discord.ApplicationContext,
    spawn_id: str
):
    """Kills all active processes associated with the given spawn ID."""
    if spawn_id not in spawns:
        await ctx.respond(f"❌ Unknown spawn ID '{spawn_id}'.", ephemeral=True)
        return
    procs = spawns[spawn_id]["processes"]
    if not procs:
        await ctx.respond(f"ℹ️ No active processes for spawn ID '{spawn_id}'.", ephemeral=True)
        return
    count = 0
    for proc in procs:
        try:
            proc.kill()
            count += 1
        except Exception:
            pass
    spawns[spawn_id]["processes"] = []
    await ctx.respond(f"✅ Killed {count} process(es) for spawn ID '{spawn_id}'.", ephemeral=True)


def format_response(msg: str) -> str:
    return msg


def format_thought(msg: str) -> str:
    # TODO add thinking bubbles or something
    return msg


def format_command(msg: str) -> str:
    return f'`{msg}`'


def format_command_output(msg: str) -> str:
    return f'```\n{msg}\n```'


def send_codex_notification(worker_entry: dict, notification: bytes):
    msg = notification.decode('utf-8')
    # TODO remove
    send_notification(worker_entry, msg)
    return
    messages = [msg]
    try:
        content = json.loads(msg)
        if content.get("role", "assistant") != "assistant":
            raise RuntimeError("Unexpected message from role '" + content['role'] + "'")
        if "type" not in content:
            raise RuntimeError("Missing attribute 'type'")
        content_ty = content["type"]
        if content_ty == "message":
            assert 'content' in content
            messages = [format_response(c['text']) for c in content['content']]
        elif content_ty == "reasoning":
            assert 'summary' in content
            messages = [format_thought(c['text']) for c in content['summary']]
        elif content_ty == "local_shell_call":
            assert 'action' in content and 'command' in content['action']
            messages = [format_command(' '.join(content['action']['command']))]
        elif content_ty == "local_shell_call_output":
            out = content["output"].encode('utf-8').decode('unicode_escape')
            try:
                out_inner = json.loads(out)['output']
            except Exception:
                messages = [out]
            else:
                messages = [format_command_output(out_inner)]
    except json.JSONDecodeError:
        print(traceback.format_exc(), file=sys.stderr)

    for m in messages:
        send_notification(worker_entry, m)


def send_notification(worker_entry: dict, notification: str, critical: bool = False):
    channel, user_id, spawn_id = worker_entry["channel"], worker_entry["user"].id, worker_entry["spawn_id"]
    ping = f"<@{user_id}> " if critical else ""
    coro = channel.send(f"{ping}from {spawn_id}:\n{notification}")
    asyncio.run_coroutine_threadsafe(coro, bot.loop)


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
        "--update-session-file=false",
    ]
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1
    )
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )
    assert proc.stdout is not None
    entry["processes"].append(proc)

    async def reader():
        async for line in proc.stdout:
            send_codex_notification(entry, line.strip())
        send_notification(entry, f"WORKER '{spawn_id}' FINISHED!", critical=True)

    asyncio.run_coroutine_threadsafe(reader(), bot.loop)


if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    api_key = os.getenv("MODEL_API_KEY")
    if not token:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
        exit(1)
    bot.run(token)
