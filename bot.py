import os
import subprocess
import threading
import re
import json
import asyncio
import sys
import traceback
import datetime
import uuid

import discord
from discord import option, AutocompleteContext
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# Only allow these Discord user IDs to interact with the bot.
# You can list them in the ALLOWED_USER_IDS env var (comma-separated).
allowed_ids_env = os.getenv("ALLOWED_USER_IDS", "")
DEBUG = int(os.getenv("DEBUG", 0))
ALLOWED_USER_IDS = {int(u) for u in allowed_ids_env.split(",") if u.strip()}

with open("session_json_template.json") as f:
    session_json_template = json.load(f)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="", intents=intents)


# Global pre-check: only allow listed users to run slash commands
@bot.check
async def globally_allow_only_listed_users(ctx: commands.Context) -> bool:
    if ctx.author.id not in ALLOWED_USER_IDS:
        await ctx.respond("⛔ You are not authorized to use this bot.", ephemeral=True)
        return False
    return True


# Mapping of spawn IDs to channel, user, and active processes
spawns: dict[str, dict] = {}
try:
    with open("spawns.json") as f:
        spawns = json.load(f)
except FileNotFoundError:
    pass

instructions = "You are Codex, a highly autonomous AI coding agent that lives in the terminal. You help users by comlpeting tasks they assign you, e.g. writing, testing or debugging code or doing research for them."

# Set up directory structure
SESSIONS_DIR = os.path.expanduser("~/.codex/sessions")
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)


def save_spawns():
    with open("spawns.json", "w") as f:
        spawns_to_save = {}
        for k, spawn in spawns.items():
            spawn = spawn.copy()
            spawn['processes'].clear()
            spawn['channel'] = None
            spawn['user'] = None
            spawns_to_save[k] = spawn
        json.dump(spawns_to_save, f)


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


def get_random_id() -> str:
    return str(uuid.uuid4())


def get_session_file_path(session_id: str) -> str:
    return os.path.expanduser(f'~/.codex/sessions/rollout-date-elided-{session_id}.json')


def init_session_file(session_id: str):
    path = get_session_file_path(session_id)
    if os.path.exists(path):
        return
    content = session_json_template.copy()
    content['session']['id'] = session_id
    timestamp = datetime.datetime.now().isoformat('T', 'milliseconds') + 'Z'
    content['session']['timestamp'] = timestamp
    content['session']['instructions'] = instructions
    with open(path, 'w') as f:
        json.dump(content, f)


@bot.slash_command(name="set_instructions", description="Sets the instructions that will be passed to all Codex Agents spawned from now on")
@option("new_instructions", description="The new instructions")
async def set_instructions(ctx: discord.ApplicationContext, new_instructions: str):
    """Sets the instructions on which Agents operate."""
    global instructions
    instructions = new_instructions


@bot.slash_command(name="spawn", description="Reserve a spawn ID for Codex prompts")
@option("spawn_id", description="The ID under which you will reference the Codex Instance")
async def spawn(ctx: discord.ApplicationContext, spawn_id: str):
    """Registers a unique spawn ID that can be used for future prompts."""
    if spawn_id in spawns:
        await ctx.respond(
            f"❌ Spawn ID '{spawn_id}' is already in use.", ephemeral=True
        )
        return
    session_id = get_random_id()
    init_session_file(session_id)
    spawns[spawn_id] = {
        "spawn_id": spawn_id,
        "session_id": session_id,
        "channel": ctx.channel,
        "user": ctx.author,
        "processes": []
    }
    save_spawns()
    await ctx.respond(
        f"✅ Spawn ID '{spawn_id}' registered. Mention me with 'to {spawn_id}: <message>' to send prompts."
    )


@bot.slash_command(name="kill", description="Kill active processes for a spawn ID")
@option("spawn_id", description="The spawn ID to kill processes for")
@option("delete", description="Delete it fully?", type=bool)
async def kill(ctx: discord.ApplicationContext, spawn_id: str, delete: bool):
    """Kills all active processes associated with the given spawn ID."""
    if spawn_id not in spawns:
        await ctx.respond(f"❌ Unknown spawn ID '{spawn_id}'.", ephemeral=True)
        return
    procs = spawns[spawn_id]["processes"]
    if not procs:
        await ctx.respond(f"ℹ️ No active processes for spawn ID '{spawn_id}'.", ephemeral=True)
        if delete:
            del spawns[spawn_id]
        save_spawns()
        return
    count = 0
    for item in procs:
        try:
            item["proc"].kill()
            count += 1
        except Exception:
            pass
    spawns[spawn_id]["processes"] = []
    if delete:
        del spawns[spawn_id]
    save_spawns()
    await ctx.respond(f"✅ Killed {count} process(es) for spawn ID '{spawn_id}'.", ephemeral=True)


@bot.slash_command(name="list", description="List active workers and their processes with PID and runtime")
async def list_spawns(ctx: discord.ApplicationContext):
    """Lists all spawn IDs and their active processes, showing PID and runtime."""
    if not spawns:
        await ctx.respond("ℹ️ No spawn workers registered.", ephemeral=True)
        return
    now = datetime.datetime.now()
    lines: list[str] = []
    for sid, entry in spawns.items():
        procs = entry["processes"]
        if procs:
            lines.append(f"**{sid}**:")
            for item in procs:
                p = item["proc"]
                delta = now - item["start_time"]
                secs = int(delta.total_seconds())
                h, rem = divmod(secs, 3600)
                m, s = divmod(rem, 60)
                if h:
                    elapsed = f"{h}h{m}m{s}s"
                elif m:
                    elapsed = f"{m}m{s}s"
                else:
                    elapsed = f"{s}s"
                lines.append(f" • PID {p.pid} – running for {elapsed}")
        else:
            lines.append(f"**{sid}**: no active processes")
    await ctx.respond("\n".join(lines), ephemeral=True)


def format_response(msg: str) -> str:
    return msg


def format_thought(msg: str) -> str:
    # TODO add thinking bubbles or something
    return msg


def format_command(msg: str) -> str:
    return f'`{msg}`'


def format_command_output(msg: str) -> str:
    return f'```\n{msg}\n```'


def rfind_nth(haystack: str, needle: str, nth: int) -> int:
    idx = -1
    n = 0
    while n < nth:
        idx = haystack[:idx].rfind(needle)
        if idx == -1:
            return -1
        n += 1
    return idx


def send_codex_notification(worker_entry: dict, notification: bytes, reference=None):
    msg = notification.decode('utf-8')
    action = ''
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
            action = ' responded'
        elif content_ty == "reasoning":
            assert 'summary' in content
            messages = [format_thought(c['text']) for c in content['summary']]
            action = ' thought'
        elif content_ty == "local_shell_call":
            assert 'action' in content and 'command' in content['action']
            messages = [format_command(' '.join(content['action']['command']))]
            action = ' executed'
        elif content_ty == "local_shell_call_output":
            try:
                out = content["output"].encode('utf-8').decode('unicode_escape')
                # You can't properly load `out`. It should be json, but it is not escaped, so we have to
                # hackily extract the content of the 'output' field.
                start = out.find(':') + 2
                end = rfind_nth(out, ',', 2) - 1
                out_inner = out[start:end]
            except Exception:
                messages = [out]
            else:
                messages = [format_command_output(out_inner)]
            action = ' got result'
    except Exception:
        print(traceback.format_exc(), file=sys.stderr)

    for m in messages:
        send_notification(worker_entry, m, reference=reference, action=action)


def send_notification(worker_entry: dict, notification: str, critical: bool = False, reference=None, action=''):
    channel, user_id, spawn_id = worker_entry["channel"], worker_entry["user"].id, worker_entry["spawn_id"]
    ping = f"<@{user_id}> " if critical else ""
    coro = channel.send(f"{ping}**{spawn_id}**{action}:\n{notification}", reference=reference)
    asyncio.run_coroutine_threadsafe(coro, bot.loop)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Drop all messages from users not on the allow-list
    if message.author.id not in ALLOWED_USER_IDS:
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
    entry['user'] = message.author
    entry['channel'] = message.channel

    session_id = entry['session_id']
    args = [
        "node",
        "third_party/codex-headless/dist/cli.mjs",
        "-q",
        prompt,
        "--session-id",
        session_id,
    ]
    sess_fp = get_session_file_path(session_id)
    if not os.path.exists(sess_fp):
        init_session_file(session_id)
    with open(sess_fp) as f:
        sess = json.load(f)

    # the first n_msg_in_chat + 1 (the user's new message that triggered this function) must be ignored
    num_lines_to_ignore = len(sess['items']) + 1

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    entry["processes"].append({"proc": proc, "start_time": datetime.datetime.now()})

    async def reader():
        if DEBUG:
            print("Spawning process", file=sys.stderr)
        n_ignored = 0

        async for line in proc.stdout:
            if n_ignored < num_lines_to_ignore:
                if DEBUG:
                    print("[IGNORED] New line from process:", line, file=sys.stderr)
                n_ignored += 1
                continue

            line = line.strip()
            if DEBUG:
                print("New line from process:", line, file=sys.stderr)

            send_codex_notification(entry, line, reference=message)

        send_notification(entry, f"WORKER '{spawn_id}' FINISHED!", critical=True, reference=message)
        if DEBUG:
            print("Retiring process", file=sys.stderr)
        await proc.wait()

    asyncio.run_coroutine_threadsafe(reader(), bot.loop)


if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    api_key = os.getenv("MODEL_API_KEY")
    if not token:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
        exit(1)
    bot.run(token)
