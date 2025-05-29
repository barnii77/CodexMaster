import os
import re
import json
import asyncio
import sys
import traceback
import datetime
import uuid

import discord
from discord import option
from discord.ext import commands
from dotenv import load_dotenv
from typing import Optional
from copy import deepcopy

load_dotenv()

# Only allow these Discord user IDs to interact with the bot.
# You can list them in the ALLOWED_USER_IDS env var (comma-separated).
allowed_ids_env = os.getenv("ALLOWED_USER_IDS", "")
LOG_LEVEL = int(os.getenv("LOG_LEVEL", 0))
ALLOWED_USER_IDS = {int(u) for u in allowed_ids_env.split(",") if u.strip()}

BOT_WORKING_DIR = os.getcwd()
ALLOWED_PROVIDERS = list(map(str.strip, os.getenv("ALLOWED_PROVIDERS", "openai").split(',')))
DEFAULT_WORKING_DIR = os.path.expanduser(os.getenv("DEFAULT_WORKING_DIR", os.getcwd()))
assert ':' not in DEFAULT_WORKING_DIR
DEFAULT_PROVIDER = os.getenv("DEFAULT_PROVIDER", "openai").strip()
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "codex-mini-latest").strip()
assert DEFAULT_PROVIDER in ALLOWED_PROVIDERS

NO_DOCKER = int(os.getenv("NO_DOCKER", 0))
CODEX_DOCKER_IMAGE_NAME = os.getenv("CODEX_DOCKER_IMAGE_NAME")
assert NO_DOCKER or CODEX_DOCKER_IMAGE_NAME is not None

all_api_keys = {}
for k, v in os.environ.items():
    if k.endswith('_API_KEY'):
        all_api_keys[k] = v

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

# Used by the kill command to communicate to the process reader that the process was killed and
# that the process reader should not save the aborted session updates to the session file (i.e. revert)
newly_killed_procs = []

instructions = "You are Codex, a highly autonomous AI coding agent that lives in the terminal. You help users by completing tasks they assign you, e.g. writing, testing or debugging code or doing research for them."

# Set up directory structure
SESSIONS_DIR = os.path.expanduser("~/.codex/sessions")
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)


def save_spawns():
    with open("spawns.json", "w") as f:
        spawns_to_save = {}
        for k, spawn in spawns.items():
            spawn = deepcopy(spawn)
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


def write_session_file(session_id: str, items: list[dict]):
    path = get_session_file_path(session_id)
    # remove messages that will be ignored
    filtered_items = []
    for item in items:
        if (item.get('type') not in ('message', 'reasoning', 'local_shell_call', 'local_shell_call_output') or 
            item.get('type') == 'reasoning' and not item.get('summary', [])):
            continue
        filtered_items.append(item)
    items = filtered_items

    content = deepcopy(session_json_template)
    content['session']['id'] = session_id
    timestamp = datetime.datetime.now().isoformat('T', 'milliseconds') + 'Z'
    content['session']['timestamp'] = timestamp
    content['session']['instructions'] = instructions
    content['items'] = items
    with open(path, 'w') as f:
        json.dump(content, f)


def init_session_file(session_id: str):
    write_session_file(session_id, [])


async def run_proc_and_wait(*args):
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,  # leaves stdin open (required by codex cli even in quiet mode when running in docker for whatever reason)
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    await proc.communicate()
    if proc.returncode != 0:
        print(f"[ERROR IN PREVIOUS PROCESS: EXIT CODE {proc.returncode}]", file=sys.stderr)
    return proc.returncode


async def commit_agent_docker_image(spawn_id: str):
    """Writes the changes that were performed within a docker container to the image of name {CODEX_DOCKER_IMAGE_NAME}:{spawn_id}"""
    docker_args = [
        "docker",
        "commit",
        f"{CODEX_DOCKER_IMAGE_NAME}-agent-container-{spawn_id}",
        f"{CODEX_DOCKER_IMAGE_NAME}:{spawn_id}",
    ]
    await run_proc_and_wait(*docker_args)
    docker_args = [
        "docker",
        "rm",
        f"{CODEX_DOCKER_IMAGE_NAME}-agent-container-{spawn_id}",
    ]
    await run_proc_and_wait(*docker_args)


async def create_agent_docker_image(spawn_id: str):
    """Create a new docker image called {CODEX_DOCKER_IMAGE_NAME}:{spawn_id} by creating (not running) a temporary container and the committing it"""
    docker_args = [
        "docker",
        "create",
        "--name", f"{CODEX_DOCKER_IMAGE_NAME}-agent-container-{spawn_id}",
        CODEX_DOCKER_IMAGE_NAME,
    ]
    await run_proc_and_wait(*docker_args)
    await commit_agent_docker_image(spawn_id)


async def del_agent_docker_image(spawn_id: str):
    """Delete a docker image called {CODEX_DOCKER_IMAGE_NAME}:{spawn_id}"""
    docker_args = [
        "docker",
        "rmi",
        f"{CODEX_DOCKER_IMAGE_NAME}:{spawn_id}",
    ]
    await run_proc_and_wait(*docker_args)


async def launch_agent(
    spawn_id: str,
    prompt: str,
    session_id: str,
    provider: str,
    model: str,
    working_dir: str,
):
    if NO_DOCKER:
        optional_docker_prefix = []
        cli_script_abspath = os.path.join(BOT_WORKING_DIR, "third_party/codex-headless/dist/cli.mjs")
    else:
        api_key_env_var_kvs = [f"{k}={v}" for k, v in all_api_keys.items()]
        env_var_setters = []
        for ev in api_key_env_var_kvs:
            env_var_setters.append("-e")
            env_var_setters.append(ev)
        optional_docker_prefix = [
            "docker",
            "run",
            "--name", f"{CODEX_DOCKER_IMAGE_NAME}-agent-container-{spawn_id}",
            "-i",  # leaves stdin open (required by codex cli even in quiet mode for whatever reason)
            "-v", f"{working_dir}:/root",
            "-v", f"{SESSIONS_DIR}:/root/.codex/sessions",
            *env_var_setters,
            f"{CODEX_DOCKER_IMAGE_NAME}:{spawn_id}",
        ]
        cli_script_abspath = "/CodexMaster/third_party/codex-headless/dist/cli.mjs"

    args = optional_docker_prefix + [
        "node",
        cli_script_abspath,
        "-q", prompt,
        "--session-id", session_id,
        "--full-auto",
        "--provider", provider,
        "-m", model,
    ]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,  # leaves stdin open (required by codex cli even in quiet mode when running in docker for whatever reason)
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=working_dir,
    )
    return proc


@bot.slash_command(name="set_instructions", description="Sets the instructions that will be passed to ALL Codex Agents spawned from now on")
@option("new_instructions", description="The new instructions")
async def set_instructions(ctx: discord.ApplicationContext, new_instructions: str):
    """Sets the instructions on which ALL Agents operate."""
    global instructions
    instructions = new_instructions
    await ctx.respond("✅ Instructions updated")


@bot.slash_command(name="spawn", description="Reserve a spawn ID for Codex prompts")
@option("spawn_id", description="The ID under which you will reference the Codex Instance")
@option("provider", description="The Provider to use")
@option("model", description="The model to use")
@option("working_dir", description="The working directory for the agent to work in")
async def spawn(ctx: discord.ApplicationContext, spawn_id: str, provider: str = DEFAULT_PROVIDER, model: str = DEFAULT_MODEL, working_dir: str = DEFAULT_WORKING_DIR):
    """Registers a unique spawn ID that can be used for future prompts."""
    if spawn_id in spawns:
        await ctx.respond(
            f"❌ Spawn ID '{spawn_id}' is already in use.", ephemeral=True
        )
        return
    provider = provider.strip()
    if provider not in ALLOWED_PROVIDERS:
        await ctx.respond(
            f"❌ Provider '{provider}' is not allowed.", ephemeral=True
        )
        return
    working_dir = os.path.expanduser(working_dir)
    if ':' in working_dir:
        await ctx.respond(
                f"❌ Working Dir '{working_dir}' must not contain ':'", ephemeral=True
        )
        return
    if not os.path.exists(working_dir) or not os.path.isdir(working_dir):
        await ctx.respond(
            f"❌ Working Dir '{working_dir}' does not exist.", ephemeral=True
        )
        return
    session_id = get_random_id()
    init_session_file(session_id)
    if not NO_DOCKER:
        await create_agent_docker_image(spawn_id)
    spawns[spawn_id] = {
        "spawn_id": spawn_id,
        "session_id": session_id,
        "provider": provider,
        "model": model.strip(),
        "working_dir": working_dir,
        "channel": ctx.channel,
        "user": ctx.author,
        "processes": []
    }
    save_spawns()
    await ctx.respond(
        f"✅ Spawn ID '{spawn_id}' registered. Mention me with 'to {spawn_id}: <message>' to send prompts."
    )


@bot.slash_command(name="set_provider", description="Change the provider for an already existing Agent")
@option("spawn_id", description="The ID of the Agent")
@option("provider", description="The Provider to use")
async def set_provider(ctx: discord.ApplicationContext, spawn_id: str, provider: str = DEFAULT_PROVIDER):
    if spawn_id not in spawns:
        await ctx.respond(f"❌ Unknown spawn ID '{spawn_id}'.", ephemeral=True)
        return
    provider = provider.strip()
    if provider not in ALLOWED_PROVIDERS:
        await ctx.respond(f"❌ Invalid provider '{provider}'.", ephemeral=True)
        return
    entry = spawns[spawn_id]
    entry["provider"] = provider
    save_spawns()
    await ctx.respond(f"✅ Provider for '{spawn_id}' set to '{provider}'.", ephemeral=True)


@bot.slash_command(name="set_model", description="Change the model for an already existing Agent")
@option("spawn_id", description="The ID of the Agent")
@option("model", description="The model to use")
async def set_model(ctx: discord.ApplicationContext, spawn_id: str, model: str = DEFAULT_MODEL):
    if spawn_id not in spawns:
        await ctx.respond(f"❌ Unknown spawn ID '{spawn_id}'.", ephemeral=True)
        return
    entry = spawns[spawn_id]
    entry["model"] = model
    save_spawns()
    await ctx.respond(f"✅ Model for '{spawn_id}' set to '{model}'.", ephemeral=True)


async def kill_impl(ctx: discord.ApplicationContext, spawn_id: str, delete: bool = False, revert_chat_state: bool = True):
    """Kills all active processes associated with the given spawn ID."""
    if spawn_id not in spawns:
        await ctx.respond(f"❌ Unknown spawn ID '{spawn_id}'.", ephemeral=True)
        return
    procs = spawns[spawn_id]["processes"]
    if not procs:
        append_msg = "."
        if delete:
            append_msg = f" (permanently deleted agent '{spawn_id}')."
            if not NO_DOCKER:
                await del_agent_docker_image(spawn_id)
            del spawns[spawn_id]
        save_spawns()
        await ctx.respond(f"ℹ️ No active processes for spawn ID '{spawn_id}'" + append_msg, ephemeral=True)
        return
    count = 0
    for item in procs:
        try:
            proc = item["proc"]
            proc.kill()
            await proc.wait()
            if revert_chat_state:
                # Signals to the reader of the proc that the proc was killed
                newly_killed_procs.append(proc)
            count += 1
        except Exception:
            pass
    spawns[spawn_id]["processes"] = []
    if delete:
        if not NO_DOCKER:
            await del_agent_docker_image(spawn_id)
        del spawns[spawn_id]
    save_spawns()
    await ctx.respond(f"✅ Killed {count} process(es) for spawn ID '{spawn_id}'.", ephemeral=True)


@bot.slash_command(name="kill", description="Kill active processes for a spawn ID")
@option("spawn_id", description="The spawn ID to kill processes for")
@option("delete", description="Delete it fully?", type=bool)
@option("revert_chat_state", description="Revert the chat state to before you sent your last message?", type=bool)
async def kill(ctx: discord.ApplicationContext, spawn_id: str, delete: bool = False, revert_chat_state: bool = True):
    return await kill_impl(ctx, spawn_id, delete, revert_chat_state)


@bot.slash_command(name="delete_all_agents", description="Delete spawns.json")
@option("confirmation", description="Type CONFIRM to confirm the action")
async def del_spawns_file(ctx: discord.ApplicationContext, confirmation: str):
    if confirmation != "CONFIRM":
        await ctx.respond("❌ You must confirm this action by typing CONFIRM in the confirmation field")
        return
    spawn_ids = set(spawns)
    for spawn_id in spawn_ids:
        await kill_impl(ctx, spawn_id, True)
    if os.path.exists("spawns.json"):
        os.remove("spawns.json")
    await ctx.respond("✅ Killed and deleted all agents and docker containers - EVERYTHING!")


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
    indicator = "💭💭💭💭💭"
    return f"{indicator}\n{msg}\n{indicator}"


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


def send_codex_notification(worker_entry: dict, msg: str, reference=None):
    action = ''
    messages = [msg]
    try:
        content = json.loads(msg)
        if content.get("role", "unknown") == "user":
            # raise RuntimeError("Unexpected message from role '" + content['role'] + "'")
            return
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
            assert 'output' in content
            out = content['output']
            try:
                out = out.encode('utf-8').decode('unicode_escape')
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


def get_history_item_content(item: dict):
    return item.get('content') or item.get('summary') or item.get('output') or item['action']['command']


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
    provider = entry.get('provider', DEFAULT_PROVIDER).strip()
    if provider not in ALLOWED_PROVIDERS:
        provider = DEFAULT_PROVIDER
    model = entry.get("model", DEFAULT_MODEL).strip()
    working_dir = entry.get("working_dir", DEFAULT_WORKING_DIR)
    proc = await launch_agent(spawn_id, prompt, session_id, provider, model, working_dir)

    sess_fp = get_session_file_path(session_id)
    if not os.path.exists(sess_fp):
        init_session_file(session_id)
    with open(sess_fp) as f:
        sess = json.load(f)
    prev_sess_items_og = deepcopy(sess['items'])
    prev_sess_items = deepcopy(prev_sess_items_og)

    assert proc.stdout is not None
    entry["processes"].append({"proc": proc, "start_time": datetime.datetime.now()})

    async def reader():
        if LOG_LEVEL:
            print("Spawning process", file=sys.stderr)

        notifications: list[dict] = []
        async for line in proc.stdout:
            line = line.strip().decode('utf-8')
            try:
                line_json = json.loads(line)
            except json.JSONDecodeError:
                if LOG_LEVEL:
                    print("[ERROR] New line from process:", line, file=sys.stderr)
                print(traceback.format_exc(), file=sys.stderr)
                continue
            notifications.append(line_json)

            # Ignore messages that were part of the session file (that will be re-printed) by
            # matchig their content against what is printed (not all will be printed for some reason).
            # This assumes that all messages that will be printed (up until the new user message) are
            # part of the session file as well.
            ignore_this = False
            while prev_sess_items:
                prev_item = prev_sess_items.pop(0)
                prev_content = get_history_item_content(prev_item)
                content = get_history_item_content(line_json)
                if content == prev_content:
                    ignore_this = True
                    if LOG_LEVEL:
                        print("[IGNORED] New line from process:", line, file=sys.stderr)
                    break
            if ignore_this:
                continue

            if LOG_LEVEL:
                print("New line from process:", line, file=sys.stderr)

            send_codex_notification(entry, line, reference=message)

        # Send termination notification
        send_notification(entry, f"AGENT **{spawn_id}** COMPLETED HIS MISSION!", critical=True, reference=message)
        if LOG_LEVEL:
            print("Retiring process", file=sys.stderr)
        await proc.wait()

        # Overwrite the auto-updated sessions file manually to remove messages the cli ignores.
        # Example: empty reasoning summary messages.
        # However, revert content if we were killed
        if proc in newly_killed_procs:
            newly_killed_procs.remove(proc)
            write_session_file(session_id, prev_sess_items_og)
        else:
            write_session_file(session_id, notifications)

        # Save container state to image
        if not NO_DOCKER:
            await commit_agent_docker_image(spawn_id)

    asyncio.run_coroutine_threadsafe(reader(), bot.loop)


if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
        exit(1)
    bot.run(token)

