import os
import re
import json
import asyncio
import sys
import traceback
import datetime
import uuid
import shutil
import getpass
import functools
import string

import discord
from discord import option
from discord.ext import commands
from dotenv import load_dotenv, dotenv_values
from typing import Optional, Callable, Awaitable
from copy import deepcopy

load_dotenv()

VALID_SPAWN_ID_CHARS = string.ascii_letters + string.digits

# Only allow these Discord user IDs to interact with the bot.
# You can list them in the ALLOWED_USER_IDS env var (comma-separated).
allowed_ids_env = os.getenv("ALLOWED_USER_IDS", "")
LOG_LEVEL = int(os.getenv("LOG_LEVEL", 0))
ALLOWED_USER_IDS = {int(u) for u in allowed_ids_env.split(",") if u.strip()}

DISCORD_CHARACTER_LIMIT = 1950

BOT_WORKING_DIR = os.getcwd()
ALLOWED_PROVIDERS = list(map(str.strip, os.getenv("ALLOWED_PROVIDERS", "openai").split(',')))
DEFAULT_WORKING_DIR = os.path.expanduser(os.getenv("DEFAULT_WORKING_DIR", os.getcwd()))
assert ':' not in DEFAULT_WORKING_DIR
DEFAULT_PROVIDER = os.getenv("DEFAULT_PROVIDER", "openai").strip()
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "codex-mini-latest").strip()
assert DEFAULT_PROVIDER in ALLOWED_PROVIDERS

NO_DOCKER = bool(int(os.getenv("NO_DOCKER", 0)))
CODEX_DOCKER_IMAGE_NAME = os.getenv("CODEX_DOCKER_IMAGE_NAME")
assert NO_DOCKER or CODEX_DOCKER_IMAGE_NAME is not None

CODEX_ENV_FILE = os.getenv("CODEX_ENV_FILE")
assert CODEX_ENV_FILE is None or os.path.exists(CODEX_ENV_FILE)

ALLOW_LEAK_ENV = bool(os.getenv("ALLOW_LEAK_ENV", False))

# performance settings
MAX_CPU_USAGE = float(os.getenv("MAX_CPU_USAGE", 1.0))
MAX_RAM_USAGE_GB = float(os.getenv("MAX_RAM_USAGE_GB", 4.0))

with open("session_json_template.json") as f:
    session_json_template = json.load(f)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="", intents=intents)


# Mapping of spawn IDs to channel, user, and active processes
spawns: dict[str, dict] = {}
try:
    with open("spawns.json") as f:
        spawns = json.load(f)
except json.JSONDecodeError:
    spawns = {}
except FileNotFoundError:
    pass

# Used by the kill command to communicate to the process reader that the process was killed and
# that the process reader should not save the aborted session updates to the session file (i.e. revert)
newly_killed_procs = []

instructions = "You are Codex, a highly autonomous AI coding agent that lives in the terminal. You help users by completing tasks they assign you, e.g. writing, testing or debugging code or doing research for them."

# Set up directory structure
DOT_CODEX_DIR = os.path.expanduser("~/.codex")
CODEX_MASTER_AGENTS_MD_PATH = os.path.join(DOT_CODEX_DIR, "CODEX_MASTER_AGENTS.md")
if not os.path.exists(CODEX_MASTER_AGENTS_MD_PATH):
    CODEX_MASTER_AGENTS_MD_PATH = None
CODEX_MASTER_DIR = os.path.join(DOT_CODEX_DIR, "codex-master")
os.makedirs(CODEX_MASTER_DIR, exist_ok=True)

CLEAN_CODEX_MASTER_DIR = os.getenv("CLEAN_CODEX_MASTER_DIR", 1)


def log(*args, **kwargs):
    if LOG_LEVEL:
        print(*args, **kwargs, file=sys.stderr)


def log_command_usage(func):
    @functools.wraps(func)
    async def wrapper(ctx: discord.ApplicationContext, *args, **kwargs):
        log(f"Command '{func.__name__}' invoked by '{ctx.author.name}' (user {ctx.author.id})...")
        return await func(ctx, *args, **kwargs)
    
    return wrapper


def notify_on_internal_error(func):
    @functools.wraps(func)
    async def wrapper(message: discord.Message):
        try:
            return await func(message)
        except Exception:
            await message.channel.send("Internal error", reference=message)
            raise

    return wrapper


def clean_master_dir():
    if NO_DOCKER or not CLEAN_CODEX_MASTER_DIR:
        return
    live_session_ids = []
    for spawn in spawns.values():
        live_session_ids.append(spawn['session_id'])
    for agent_dir in os.listdir(CODEX_MASTER_DIR):
        agent_dir = os.path.join(CODEX_MASTER_DIR, agent_dir)
        if not any(sess_id in agent_dir for sess_id in live_session_ids):
            log(f"Removing dead agent session dir {agent_dir}")
            shutil.rmtree(agent_dir)


def save_spawns():
    log("Saving spawns")
    spawns_to_save = {}
    for spawn_id, spawn in spawns.items():
        # temporarily remove the stuff that cannot be saved (and also not deepcopy'd)
        procs, chan, user = spawn['processes'], spawn['channel'], spawn['user']
        spawn['processes'] = []
        spawn['channel'] = None
        spawn['user'] = None

        spawns_to_save[spawn_id] = deepcopy(spawn)

        # restore spawn attributes
        spawn['processes'] = procs
        spawn['channel'] = chan
        spawn['user'] = user

    with open("spawns.json", "w") as f:
        json.dump(spawns_to_save, f)

    clean_master_dir()


@bot.event
async def on_ready():
    """Called when the bot is ready and connected to Discord."""
    log(f"Logged in as {bot.user} (ID: {bot.user.id})")
    log("------")


# Global pre-check: only allow listed users to run slash commands
@bot.check
async def globally_allow_only_listed_users(ctx: commands.Context) -> bool:
    if ctx.author.id not in ALLOWED_USER_IDS:
        await ctx.respond("⛔ You are not authorized to use this bot.")
        return False
    return True


@bot.before_invoke
async def auto_defer(interaction: discord.Interaction):
    # this allows longer response delays and displays "thinking..."
    await interaction.response.defer()


@bot.slash_command(name="hello", description="Responds with 'Hello, world!'")
@option(
    "greeting",
    description="Greet me!",
    choices=["Hello, world!", "Hi there!", "Greetings!", "Howdy!"]
)
@log_command_usage
async def hello(
    ctx: discord.ApplicationContext,
    greeting: str
):
    """Responds with a greeting message chosen via autocomplete."""
    await ctx.respond(greeting)


def get_random_id() -> str:
    return str(uuid.uuid4())


def get_session_file_path(session_id: str) -> str:
    if NO_DOCKER:
        return os.path.expanduser(f'~/.codex/sessions/rollout-date-elided-{session_id}.json')
    return os.path.join(os.path.join(CODEX_MASTER_DIR, session_id), f'sessions/rollout-date-elided-{session_id}.json')


def repair_session_items(items: list[dict]) -> list[dict]:
    """Remove messages that will be ignored or are invalid (e.g. local_shell_call's without corresponding outputs)."""
    # Remove messages of unexpected type or with empty content
    items = list(filter(
        lambda item: (item.get('type') in ('message', 'reasoning', 'local_shell_call', 'local_shell_call_output')
                      and (item.get('type'), item.get('summary')) != ('reasoning', [])),
        items,
    ))

    # Record where what call_id's occur
    call_id_occurences = {}
    for item in items:
        call_id = item.get('call_id')
        if call_id is None:
            continue
        call_id_occurences.setdefault(call_id, []).append(item['type'])

    # Identify illegal combinations of shell calls and outputs
    illegal_call_ids = set()
    for call_id, occurences in call_id_occurences.items():
        # Only this specific pattern and order of shell call and output is allowed
        if occurences != ["local_shell_call", "local_shell_call_output"]:
            illegal_call_ids.add(call_id)

    # Filter shell calls without corresponding outputs and vice versa
    items = list(filter(lambda item: 'call_id' not in item or item['call_id'] not in illegal_call_ids, items))

    return items


def write_session_file(session_id: str, items: list[dict]):
    path = get_session_file_path(session_id)
    items = repair_session_items(items)
    content = deepcopy(session_json_template)
    content['session']['id'] = session_id
    timestamp = datetime.datetime.now().isoformat('T', 'milliseconds') + 'Z'
    content['session']['timestamp'] = timestamp
    content['session']['instructions'] = instructions
    content['items'] = items
    with open(path, 'w') as f:
        json.dump(content, f)


def init_agent_codex_dir(session_id: str):
    if NO_DOCKER:
        return

    # Create ~/.codex/codex-master/{session_id} subdir that looks like a fresh ~/.codex dir.
    # Roughly corresponds to these commands:
    #  mkdir -p ~/.codex/codex-master/{session_id}/sessions
    #  cp ~/.codex/CODEX_MASTER_AGENTS.md ~/.codex/codex-master/{session_id}/AGENTS.md
    agent_dir = os.path.join(CODEX_MASTER_DIR, session_id)
    sessions_dir = os.path.join(agent_dir, "sessions")
    os.makedirs(sessions_dir, exist_ok=True)
    if CODEX_MASTER_AGENTS_MD_PATH is not None:
        shutil.copy2(CODEX_MASTER_AGENTS_MD_PATH, os.path.join(agent_dir, "AGENTS.md"))


def init_session_file(session_id: str):
    init_agent_codex_dir(session_id)
    write_session_file(session_id, [])


async def default_run_proc_and_wait_completion_waiter(proc: asyncio.subprocess.Process):
    await proc.communicate()


async def run_proc_and_wait(
    *args,
    proc_completion_waiter: Callable[[asyncio.subprocess.Process], Awaitable[None]] = default_run_proc_and_wait_completion_waiter,
    silent_errors: bool = False,
):
    log(f"Running {' '.join(args)}")
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,  # leaves stdin open (required by codex cli even in quiet mode when running in docker for whatever reason)
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    await proc_completion_waiter(proc)
    if proc.returncode not in (0, None):
        try:
            raise RuntimeError(f"[ERROR IN SPAWNED PROCESS: EXIT CODE {proc.returncode}]: command failed")
        except RuntimeError as e:
            if silent_errors:
                # Only log the message, not the traceback
                log(str(e))
            else:
                log(traceback.format_exc())
    return proc if proc.returncode is None else None


def get_docker_container_name(spawn_id: str):
    return f"{CODEX_DOCKER_IMAGE_NAME}-agent-container-{spawn_id}"


def env_var_dict_to_setters(env: dict[str, str]) -> list[str]:
    env_var_kvs = [f"{k}={v}" for k, v in env.items()]
    env_var_setters = []
    for ev in env_var_kvs:
        env_var_setters.append("-e")
        env_var_setters.append(ev)
    return env_var_setters


def get_auto_gen_env_vars() -> dict[str, str]:
    return {
        "CODEX_HOME": os.path.expanduser('~'),
        "CODEX_USER": getpass.getuser(),
    }


async def create_agent_docker_container(spawn_id: str, session_id: str, working_dir: str, leak_env: bool = False):
    log(
        f"Creating agent container for {spawn_id} and mounting to {working_dir}. Session ID is {session_id}."
        + (" WARNING: env leak enabled." if leak_env else "")
    )
    auto_gen_env_vars = get_auto_gen_env_vars()
    env_var_setters = env_var_dict_to_setters(auto_gen_env_vars)

    if leak_env:
        filtered_environ = {k: v for k, v in os.environ.items() if k not in auto_gen_env_vars}
        env_var_setters.extend(env_var_dict_to_setters(filtered_environ))
    if CODEX_ENV_FILE is not None:
        env_var_setters.extend(["--env-file", CODEX_ENV_FILE])

    agent_dir = os.path.join(CODEX_MASTER_DIR, session_id)
    dot_codex_dir_in_docker = os.path.join(auto_gen_env_vars["CODEX_HOME"], ".codex")

    docker_args = [
        "docker",
        "create",
        "--name", get_docker_container_name(spawn_id),
        # "--userns=keep-id",  # extra safety (makes root in container != host root)
        "--cap-add=NET_ADMIN",  # required for setting up firewall rules
        "--cpus", str(MAX_CPU_USAGE),
        "--memory", f"{MAX_RAM_USAGE_GB}g",
        "-v", f"{working_dir}:{working_dir}",
        "-v", f"{agent_dir}:{dot_codex_dir_in_docker}",
        "-w", working_dir,
        *env_var_setters,
        CODEX_DOCKER_IMAGE_NAME,
    ]
    await run_proc_and_wait(*docker_args)
    log("DONE: docker create")


async def start_agent_docker_container_proc_completion_waiter(proc: asyncio.subprocess.Process):
    """Special logic that awaits the completion of the start command. Instead of waiting forever, we wait until it prints '[==== DONE ====]'."""
    async for line in proc.stdout:
        log("New line from entrypoint.sh:", line.decode('utf-8', errors="replace"))
        if b"[==== DONE ====]" in line:
            return
    raise RuntimeError("`docker start -a spawn_id` exited unexpectedly")


async def start_agent_docker_container(spawn_id: str):
    log(f"Starting docker container for {spawn_id}")
    docker_args = [
        "docker",
        "start",
        # This would, per se, wait forever, but we have a special completion waiter that
        # waits for a sentinel echo.
        "-a",
        get_docker_container_name(spawn_id),
    ]
    await run_proc_and_wait(
        *docker_args,
        proc_completion_waiter=start_agent_docker_container_proc_completion_waiter
    )
    log(f"DONE: docker start")


async def stop_agent_docker_container(spawn_id: str, silent_errors: bool = False):
    log(f"Force-stopping docker container for {spawn_id}")
    docker_args = [
        "docker",
        "stop",
        "-t", "5",
        get_docker_container_name(spawn_id),
    ]
    await run_proc_and_wait(*docker_args, silent_errors=True)
    log(f"DONE: docker stop")


async def delete_agent_docker_container(spawn_id: str):
    log(f"Removing docker container for {spawn_id}")
    docker_args = [
        "docker",
        "rm",
        get_docker_container_name(spawn_id),
    ]
    await run_proc_and_wait(*docker_args)
    log("DONE: docker rm")


async def launch_agent(
    spawn_id: str,
    prompt: str,
    session_id: str,
    provider: str,
    model: str,
    working_dir: str,
    leak_env: bool = False,
):
    assert not leak_env or ALLOW_LEAK_ENV

    if NO_DOCKER:
        log(f"Launching agent {spawn_id} WITHOUT docker...")
        optional_docker_prefix = []
        cli_script_abspath = os.path.join(BOT_WORKING_DIR, "third_party/codex-headless/dist/cli.mjs")
        if leak_env:
            proc_env = None
        elif CODEX_ENV_FILE is not None:
            proc_env = dotenv_values(CODEX_ENV_FILE)
        else:
            # Empty env dict might be interpreted as "inherit all"
            proc_env = {"__ENV_LEAK_DISABLED": 1}
    else:
        log(f"Launching agent {spawn_id} in docker container...")
        proc_env = None  # docker itself gets all host env vars

        # Start docker container first (non-blocking)
        await start_agent_docker_container(spawn_id)

        optional_docker_prefix = [
            "docker",
            "exec",
            "-i",  # leaves stdin open (required by codex cli even in quiet mode for whatever reason)
            "-u", getpass.getuser(),
            get_docker_container_name(spawn_id),
        ]
        cli_script_abspath = "/CodexMaster/third_party/codex-headless/dist/cli.mjs"
        working_dir = None  # launch docker itself in current working dir

    log("Launching codex...")
    args = optional_docker_prefix + [
        "node",
        cli_script_abspath,
        "-q", prompt,
        "--session-id", session_id,
        # "--update-session-file", "false",
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
        env=proc_env,
    )
    return proc


@bot.slash_command(name="set_instructions", description="Sets the instructions that will be passed to ALL Codex Agents spawned from now on")
@option("new_instructions", description="The new instructions")
@log_command_usage
async def set_instructions(ctx: discord.ApplicationContext, new_instructions: str):
    """Sets the instructions on which ALL Agents operate."""
    global instructions
    instructions = new_instructions
    await ctx.respond("✅ Instructions updated")


@bot.slash_command(name="spawn", description="Reserve a spawn ID for Codex prompts")
@option("spawn_id", description="The ID under which you will reference the Codex Instance")
@option("working_dir", description="The working directory for the agent to work in")
@option("provider", description="The Provider to use")
@option("model", description="The model to use")
@option("leak_env", description="If set, leaks the env vars from the host system into the Codex container")
@log_command_usage
async def spawn(ctx: discord.ApplicationContext, spawn_id: str, working_dir: str, provider: str = DEFAULT_PROVIDER, model: str = DEFAULT_MODEL, leak_env: bool = False):
    """Registers a unique spawn ID that can be used for future prompts."""
    if spawn_id in spawns:
        await ctx.respond(
            f"❌ Spawn ID **{spawn_id}** is already in use."
        )
        return
    if len(spawn_id) > 64:
        await ctx.respond(
            f"❌ Spawn ID **{spawn_id}** too long - must be at most 64 characters, but is {len(spawn_id)}."
        )
        return
    if not all(c in VALID_SPAWN_ID_CHARS for c in spawn_id):
        await ctx.respond(
            f"❌ Spawn ID **{spawn_id}** contains invalid characters - only '{VALID_SPAWN_ID_CHARS}' allowed."
        )
        return
    provider = provider.strip()
    if provider not in ALLOWED_PROVIDERS:
        await ctx.respond(
            f"❌ Provider '{provider}' is not allowed."
        )
        return
    working_dir = os.path.expanduser(working_dir)
    if ':' in working_dir:
        await ctx.respond(
                f"❌ Working Dir '{working_dir}' must not contain ':'"
        )
        return
    if not os.path.exists(working_dir) or not os.path.isdir(working_dir):
        await ctx.respond(
            f"❌ Working Dir '{working_dir}' does not exist."
        )
        return
    if not ALLOW_LEAK_ENV and leak_env:
        await ctx.respond(
            f"❌ leak_env=True has been configured as disallowed."
        )
        return
    session_id = get_random_id()
    init_session_file(session_id)
    if not NO_DOCKER:
        await create_agent_docker_container(spawn_id, session_id, working_dir, leak_env)
    spawns[spawn_id] = {
        "spawn_id": spawn_id,
        "session_id": session_id,
        "provider": provider,
        "model": model.strip(),
        "working_dir": working_dir,
        "leak_env": leak_env,
        "channel": ctx.channel,
        "user": ctx.author,
        "processes": []
    }
    save_spawns()
    await ctx.respond(
        f"✅ Spawn ID **{spawn_id}** registered. Mention me with 'to {spawn_id}: <message>' to send prompts."
    )


@bot.slash_command(name="set_provider", description="Change the provider for an already existing Agent")
@option("spawn_id", description="The ID of the Agent")
@option("provider", description="The Provider to use")
@log_command_usage
async def set_provider(ctx: discord.ApplicationContext, spawn_id: str, provider: str = DEFAULT_PROVIDER):
    if spawn_id not in spawns:
        await ctx.respond(f"❌ Unknown spawn ID **{spawn_id}**.")
        return
    provider = provider.strip()
    if provider not in ALLOWED_PROVIDERS:
        await ctx.respond(f"❌ Invalid provider '{provider}'.")
        return
    entry = spawns[spawn_id]
    entry["provider"] = provider
    save_spawns()
    await ctx.respond(f"✅ Provider for **{spawn_id}** set to '{provider}'.")


@bot.slash_command(name="set_model", description="Change the model for an already existing Agent")
@option("spawn_id", description="The ID of the Agent")
@option("model", description="The model to use")
@log_command_usage
async def set_model(ctx: discord.ApplicationContext, spawn_id: str, model: str = DEFAULT_MODEL):
    if spawn_id not in spawns:
        await ctx.respond(f"❌ Unknown spawn ID **{spawn_id}**.")
        return
    entry = spawns[spawn_id]
    entry["model"] = model
    save_spawns()
    await ctx.respond(f"✅ Model for **{spawn_id}** set to '{model}'.")


async def kill_impl(ctx: discord.ApplicationContext, spawn_id: str, delete: bool = False, revert_chat_state: bool = True):
    """Kills all active processes associated with the given spawn ID."""
    if spawn_id not in spawns:
        await ctx.respond(f"❌ Unknown spawn ID **{spawn_id}**.")
        return
    procs = spawns[spawn_id]["processes"]
    if not procs:
        append_msg = "."
        if delete:
            append_msg = f" (permanently deleted agent **{spawn_id}**)."
            if not NO_DOCKER:
                # Stop container (without printing a full-blown traceback if not running)
                await stop_agent_docker_container(spawn_id, silent_errors=True)
                await delete_agent_docker_container(spawn_id)
            del spawns[spawn_id]
        save_spawns()
        await ctx.respond(f"ℹ️ No active processes for spawn ID **{spawn_id}**" + append_msg)
        return

    if not NO_DOCKER:
        await stop_agent_docker_container(spawn_id)

    count = 0
    for item in procs:
        proc = item["proc"]
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await proc.wait()
        except Exception:
            pass

        await ctx.respond(f"✅ Killed a process for agent **{spawn_id}**.")
        if revert_chat_state:
            # Signals to the reader of the proc that the proc was killed
            newly_killed_procs.append(proc)
        count += 1

    spawns[spawn_id]["processes"] = []
    if delete:
        if not NO_DOCKER:
            await delete_agent_docker_container(spawn_id)
        del spawns[spawn_id]
    save_spawns()
    await ctx.respond(f"✅ Killed {count} process(es) for spawn ID **{spawn_id}**.")


@bot.slash_command(name="kill", description="Kill active processes for a spawn ID")
@option("spawn_id", description="The spawn ID to kill processes for")
@option("delete", description="Delete it fully?", type=bool)
@option("revert_chat_state", description="Revert the chat state to before you sent your last message?", type=bool)
@log_command_usage
async def kill(ctx: discord.ApplicationContext, spawn_id: str, delete: bool = False, revert_chat_state: bool = True):
    return await kill_impl(ctx, spawn_id, delete, revert_chat_state)


@bot.slash_command(name="delete_all_agents", description="Delete spawns.json")
@option("confirmation", description="Type CONFIRM to confirm the action")
@log_command_usage
async def del_spawns_file(ctx: discord.ApplicationContext, confirmation: str):
    if confirmation != "CONFIRM":
        await ctx.respond("❌ You must confirm this action by typing CONFIRM in the confirmation field")
        return
    spawn_ids = set(spawns)
    for spawn_id in spawn_ids:
        await kill_impl(ctx, spawn_id, delete=True)
    if os.path.exists("spawns.json"):
        os.remove("spawns.json")
    await ctx.respond("✅ Killed and deleted all agents and docker containers - EVERYTHING!")


@bot.slash_command(name="list", description="List active workers and their processes with PID and runtime")
@log_command_usage
async def list_spawns(ctx: discord.ApplicationContext):
    """Lists all spawn IDs and their active processes, showing PID and runtime."""
    if not spawns:
        await ctx.respond("ℹ️ No spawn workers registered.")
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
    await ctx.respond("\n".join(lines))


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
        log(traceback.format_exc())

    for m in messages:
        send_notification(worker_entry, m, reference=reference, action=action)


def close_unterminated_code_blocks(s: str) -> str:
    """
    Ensures any unclosed inline `…` or block ```…``` code fences get closed
    in the correct order (inline first, then block).
    """
    fence3 = "```"
    # Count triple fences
    n3 = s.count(fence3)

    # Temporarily strip them to count only inline backticks
    tmp = s.replace(fence3, "")
    n1 = tmp.count("`")

    # Close inline code first, if needed
    if n1 % 2 == 1:
        s += "`"

    # Then close triple‐fence blocks
    if n3 % 2 == 1:
        # ensure it's on its own line
        if not s.endswith("\n"):
            s += "\n"
        s += fence3

    return s


def send_notification(worker_entry: dict, notification: str, critical: bool = False, reference=None, action=''):
    channel, user_id, spawn_id = worker_entry["channel"], worker_entry["user"].id, worker_entry["spawn_id"]
    ping = f"<@{user_id}> " if critical else ""

    msg = f"{ping}**{spawn_id}**{action}:\n{notification}"
    msg = close_unterminated_code_blocks(msg[:DISCORD_CHARACTER_LIMIT]) + (
        f"\n\n... ({len(msg[DISCORD_CHARACTER_LIMIT:].splitlines())} lines left)"
        if len(msg) >= DISCORD_CHARACTER_LIMIT
        else ""
    )

    coro = channel.send(msg, reference=reference)
    asyncio.run_coroutine_threadsafe(coro, bot.loop)


def get_history_item_content(item: dict):
    return item.get('content') or item.get('summary') or item.get('output') or item['action']['command']


@bot.event
@notify_on_internal_error
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
            f"❌ Unknown spawn ID **{spawn_id}**.", reference=message
        )
        return

    log(f"Received valid and authorized request to send a message to agent {spawn_id}...")
    entry = spawns[spawn_id]
    entry['user'] = message.author
    entry['channel'] = message.channel

    session_id = entry['session_id']
    provider = entry.get('provider', DEFAULT_PROVIDER).strip()
    if provider not in ALLOWED_PROVIDERS:
        provider = DEFAULT_PROVIDER
    model = entry.get("model", DEFAULT_MODEL).strip()
    working_dir = entry.get("working_dir", DEFAULT_WORKING_DIR)
    leak_env = entry.get("leak_env", False)
    proc = await launch_agent(spawn_id, prompt, session_id, provider, model, working_dir, leak_env)
    
    await message.channel.send(f"✅ AGENT **{spawn_id}** DEPLOYED...", reference=message)

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
        log(f"Spawning reader routine for agent {spawn_id}")

        notifications: list[dict] = []
        async for line in proc.stdout:
            line = line.strip().decode('utf-8')
            try:
                line_json = json.loads(line)
            except json.JSONDecodeError:
                log("[ERROR] New line from process:", line)
                log(traceback.format_exc())
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
                    log("[IGNORED] New line from process:", line)
                    break
            if ignore_this:
                continue

            log("New line from process:", line)

            send_codex_notification(entry, line, reference=message)

        # Send termination notification
        send_notification(entry, f"AGENT **{spawn_id}** COMPLETED HIS MISSION!", critical=True, reference=message)
        log(f"Retiring reader routine for agent {spawn_id}")

        # Stop container
        if not NO_DOCKER:
            await stop_agent_docker_container(spawn_id)

        await proc.wait()

        # Overwrite the auto-updated sessions file manually to remove messages the cli ignores.
        # Example: empty reasoning summary messages.
        # However, revert content if we were killed.
        if proc in newly_killed_procs:
            log(f"Reverting session file for ID {session_id}")
            newly_killed_procs.remove(proc)
            write_session_file(session_id, prev_sess_items_og)
        else:
            log(f"Updating session file for ID {session_id}")
            write_session_file(session_id, notifications)
        
        # Remove this process from entry["processes"]
        entry_procs = entry["processes"]
        for i in range(len(entry_procs)):
            p = entry_procs[i]
            if p['proc'] == proc:
                entry_procs.pop(i)
                break

    asyncio.run_coroutine_threadsafe(reader(), bot.loop)


if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        log("Error: DISCORD_BOT_TOKEN environment variable not set.")
        sys.exit(1)
    bot.run(token)

