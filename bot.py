import os
import re
import json
import asyncio
import sys
import traceback
import datetime
import getpass
import functools
import string
import glob
import aiohttp
import aiofiles

import discord
from discord import option
from discord.ext import commands
from dotenv import load_dotenv, dotenv_values
from typing import Optional, Callable, Awaitable
from copy import deepcopy
from werkzeug.utils import secure_filename
from werkzeug.security import safe_join

load_dotenv()

VALID_SPAWN_ID_CHARS = string.ascii_letters + string.digits + "-_"
VALID_SPAWN_ID_CHARS_REGEX_ENUM = string.ascii_letters + string.digits + "\\-_"

# Only allow these Discord user IDs to interact with the bot.
# You can list them in the ALLOWED_USER_IDS env var (comma-separated).
allowed_ids_env = os.getenv("ALLOWED_USER_IDS", "")
LOG_LEVEL = int(os.getenv("LOG_LEVEL", 0))
ALLOWED_USER_IDS = {int(u) for u in allowed_ids_env.split(",") if u.strip()}

DISCORD_CHARACTER_LIMIT = 1950

ALLOWED_PROVIDERS = list(map(str.strip, os.getenv("ALLOWED_PROVIDERS", "openai").split(',')))
DEFAULT_WORKING_DIR = os.path.expanduser(os.getenv("DEFAULT_WORKING_DIR", os.getcwd()))
assert ':' not in DEFAULT_WORKING_DIR
DEFAULT_PROVIDER = os.getenv("DEFAULT_PROVIDER", "openai").strip()
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "codex-mini-latest").strip()
DEFAULT_AGENT_VERBOSITY = os.getenv("DEFAULT_AGENT_VERBOSITY", "answers").strip().lower()
assert DEFAULT_PROVIDER in ALLOWED_PROVIDERS
assert DEFAULT_AGENT_VERBOSITY in ("answers", "verbose")

ALLOW_DOCKER_EXECUTION = int(int(os.getenv("ALLOW_DOCKER_EXECUTION", 1)))
ALLOW_HOST_EXECUTION = int(int(os.getenv("ALLOW_HOST_EXECUTION", 0)))
DEFAULT_EXECUTION_MODE = os.getenv("DEFAULT_EXECUTION_MODE", "docker").strip()
assert ALLOW_DOCKER_EXECUTION or ALLOW_HOST_EXECUTION
assert DEFAULT_EXECUTION_MODE in ("docker", "host")
if DEFAULT_EXECUTION_MODE == "docker":
    assert ALLOW_DOCKER_EXECUTION
else:
    assert ALLOW_HOST_EXECUTION

CODEX_DOCKER_IMAGE_NAME = os.getenv("CODEX_DOCKER_IMAGE_NAME")
assert (not ALLOW_DOCKER_EXECUTION) or CODEX_DOCKER_IMAGE_NAME is not None

CODEX_ENV_FILE = (os.getenv("CODEX_ENV_FILE") or "").strip() or None
assert CODEX_ENV_FILE is None or os.path.exists(CODEX_ENV_FILE)

ALLOW_LEAK_ENV = int(os.getenv("ALLOW_LEAK_ENV", False))

# performance settings
MAX_CPU_USAGE = float(os.getenv("MAX_CPU_USAGE", 1.0))
MAX_RAM_USAGE_GB = float(os.getenv("MAX_RAM_USAGE_GB", 4.0))

DISCORD_RESPONSE_NO_REFERENCE_USER_COMMAND = int(os.getenv("DISCORD_RESPONSE_NO_REFERENCE_USER_COMMAND", False))
DISCORD_LONG_RESPONSE_BULK_AS_CODEBLOCK = int(os.getenv("DISCORD_LONG_RESPONSE_BULK_AS_CODEBLOCK", False))
DISCORD_LONG_RESPONSE_ADD_NUM_LINES_LEFT = int(os.getenv("DISCORD_LONG_RESPONSE_ADD_NUM_LINES_LEFT", False))

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="", intents=intents)


# Mapping of spawn IDs to channel, user, and active processes
spawns: dict[str, dict] = {}


def normalize_persisted_spawn(spawn_id: str, entry: dict) -> dict:
    if not isinstance(entry, dict):
        raise ValueError("spawn entry is not an object")

    required_keys = {
        "spawn_id",
        "codex_session_id",
        "provider",
        "model",
        "working_dir",
        "execution_mode",
        "verbosity",
        "leak_env",
        "channel",
        "user",
        "processes",
    }
    missing = sorted(required_keys - set(entry))
    if missing:
        raise ValueError(f"missing keys: {', '.join(missing)}")

    if entry["spawn_id"] != spawn_id:
        raise ValueError("spawn_id key mismatch")
    if not isinstance(entry["provider"], str):
        raise ValueError("provider must be a string")
    if entry["provider"].strip() not in ALLOWED_PROVIDERS:
        raise ValueError(f"provider '{entry['provider']}' not allowed")
    if not isinstance(entry["model"], str):
        raise ValueError("model must be a string")
    if not isinstance(entry["working_dir"], str):
        raise ValueError("working_dir must be a string")
    if not isinstance(entry["execution_mode"], str) or entry["execution_mode"] not in ("docker", "host"):
        raise ValueError("execution_mode must be 'docker' or 'host'")
    if not isinstance(entry["verbosity"], str):
        raise ValueError("verbosity must be a string")

    normalized_verbosity = str(entry["verbosity"]).strip().lower()
    if normalized_verbosity not in ("answers", "verbose"):
        raise ValueError("verbosity must be 'answers' or 'verbose'")

    codex_session_id = entry["codex_session_id"]
    if codex_session_id is not None and not isinstance(codex_session_id, str):
        raise ValueError("codex_session_id must be null or string")
    if not isinstance(entry["processes"], list):
        raise ValueError("processes must be a list")

    # Persisted files should not contain live process handles; ensure we start empty.
    entry["processes"] = []
    entry["verbosity"] = normalized_verbosity
    entry["provider"] = entry["provider"].strip()
    entry["model"] = entry["model"].strip()
    entry["leak_env"] = bool(entry["leak_env"])
    return entry


try:
    with open("spawns.json") as f:
        loaded_spawns = json.load(f)
        if not isinstance(loaded_spawns, dict):
            raise ValueError("spawns.json root must be an object")
        invalid_spawn_ids = []
        for spawn_id, entry in loaded_spawns.items():
            try:
                spawns[spawn_id] = normalize_persisted_spawn(spawn_id, entry)
            except Exception as e:
                invalid_spawn_ids.append((spawn_id, str(e)))
        for spawn_id, reason in invalid_spawn_ids:
            if LOG_LEVEL:
                print(
                    f"Dropping incompatible agent '{spawn_id}' from spawns.json: {reason}",
                    file=sys.stderr,
                )
except json.JSONDecodeError:
    spawns = {}
except ValueError:
    spawns = {}
except FileNotFoundError:
    pass

# Used by the kill command to communicate to the process reader that the process was killed and
# that the process reader should not save the aborted session updates to the session file (i.e. revert)
newly_killed_procs = []

instructions = "You are Codex, a highly autonomous AI coding agent that lives in the terminal. You help users by completing tasks they assign you, e.g. writing, testing or debugging code or doing research for them."

DOT_CODEX_DIR = os.path.expanduser("~/.codex")
os.makedirs(DOT_CODEX_DIR, exist_ok=True)


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


def find_codex_session_file_path(session_id: Optional[str]) -> Optional[str]:
    if not session_id:
        return None
    pattern = os.path.join(DOT_CODEX_DIR, "sessions", "**", f"*{session_id}*.jsonl")
    matches = glob.glob(pattern, recursive=True)
    if not matches:
        return None
    matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return matches[0]


def read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def restore_codex_session_file(session_id: Optional[str], previous_content: Optional[str]) -> bool:
    path = find_codex_session_file_path(session_id)
    if previous_content is None:
        if path and os.path.exists(path):
            os.remove(path)
            return True
        return False
    if path is None:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(previous_content)
    return True


def get_host_proc_env(leak_env: bool) -> Optional[dict[str, str]]:
    if leak_env:
        return None

    passthrough_keys = [
        "PATH",
        "HOME",
        "USER",
        "SHELL",
        "TERM",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
    ]
    env = {k: v for k in passthrough_keys if (v := os.environ.get(k)) is not None}
    if CODEX_ENV_FILE is not None:
        for k, v in dotenv_values(CODEX_ENV_FILE).items():
            if v is not None:
                env[k] = v
    return env


def get_agent_uploads_dir(working_dir: str, spawn_id: str) -> str:
    return os.path.join(working_dir, ".codexmaster_uploads", spawn_id)


def build_codex_prompt(user_prompt: str) -> str:
    return f"{instructions}\n\nUser request:\n{user_prompt}"


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


def is_docker_execution_mode(mode: str) -> bool:
    return mode == "docker"


def is_host_execution_mode(mode: str) -> bool:
    return mode == "host"


def normalize_agent_verbosity(verbosity: Optional[str]) -> str:
    if verbosity is None:
        return DEFAULT_AGENT_VERBOSITY
    verbosity = str(verbosity).strip().lower()
    if verbosity not in ("answers", "verbose"):
        return DEFAULT_AGENT_VERBOSITY
    return verbosity


def is_verbose_agent_verbosity(verbosity: str) -> bool:
    return normalize_agent_verbosity(verbosity) == "verbose"


async def create_agent_docker_container(spawn_id: str, working_dir: str, leak_env: bool = False):
    log(
        f"Creating agent container for {spawn_id} and mounting to {working_dir}."
        + (" WARNING: env leak enabled." if leak_env else "")
    )
    auto_gen_env_vars = get_auto_gen_env_vars()
    env_var_setters = env_var_dict_to_setters(auto_gen_env_vars)

    if leak_env:
        filtered_environ = {k: v for k, v in os.environ.items() if k not in auto_gen_env_vars}
        env_var_setters.extend(env_var_dict_to_setters(filtered_environ))
    if CODEX_ENV_FILE is not None:
        env_var_setters.extend(["--env-file", CODEX_ENV_FILE])

    dot_codex_dir_in_docker = os.path.join(auto_gen_env_vars["CODEX_HOME"], ".codex")
    mounts = [
        "-v", f"{working_dir}:{working_dir}",
        "-v", f"{DOT_CODEX_DIR}:{dot_codex_dir_in_docker}",
    ]

    docker_args = [
        "docker",
        "create",
        "--name", get_docker_container_name(spawn_id),
        "--cap-add=NET_ADMIN",  # required for setting up firewall rules
        "--cpus", str(MAX_CPU_USAGE),
        "--memory", f"{MAX_RAM_USAGE_GB}g",
    ] + mounts + [
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
    codex_session_id: Optional[str],
    provider: str,
    model: str,
    working_dir: str,
    leak_env: bool = False,
    execution_mode: str = DEFAULT_EXECUTION_MODE,
):
    assert not leak_env or ALLOW_LEAK_ENV
    codex_working_dir = working_dir

    if is_host_execution_mode(execution_mode):
        log(f"Launching agent {spawn_id} on host...")
        optional_docker_prefix = []
        proc_env = get_host_proc_env(leak_env)
        subprocess_cwd = working_dir
    elif is_docker_execution_mode(execution_mode):
        log(f"Launching agent {spawn_id} in docker container...")
        proc_env = None  # docker itself gets all host env vars

        # Start docker container first (non-blocking)
        await start_agent_docker_container(spawn_id)

        optional_docker_prefix = [
            "docker",
            "exec",
            "-i",  # leaves stdin open (required by codex cli even in quiet mode for whatever reason)
            # "-u", getpass.getuser(),
            get_docker_container_name(spawn_id),
        ]
        subprocess_cwd = None  # launch docker itself in current working dir
    else:
        raise RuntimeError(f"Unknown execution mode '{execution_mode}'")

    codex_options = [
        "--json",
        "--skip-git-repo-check",
        "--full-auto",
    ]
    if provider == "oss":
        codex_options.append("--oss")
    else:
        codex_options.extend(["-c", f'model_provider="{provider}"'])
    if model != "default":
        codex_options.extend(["-m", model])

    if codex_session_id:
        args = optional_docker_prefix + [
            "codex",
            "exec",
            "resume",
            *codex_options,
            codex_session_id,
            prompt,
        ]
    else:
        args = optional_docker_prefix + [
            "codex",
            "exec",
            *codex_options,
            "-C", codex_working_dir,
            prompt,
        ]

    log(f"launch_agent: running async command `{' '.join(args)}`")
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,  # leaves stdin open (required by codex cli even in quiet mode when running in docker for whatever reason)
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=subprocess_cwd,
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
@option("execution_mode", choices=["docker", "host"], description="Run Codex in Docker or directly on the host")
@option("verbosity", choices=["answers", "verbose"], description="Only answers+token usage, or include tool calls and thoughts")
@option("leak_env", description="If set to true, leaks host environment variables into the Codex runtime")
@option("allow_create_working_dir", description="If set to true, it will create the working dir if it does not exist")
@log_command_usage
async def spawn(
    ctx: discord.ApplicationContext,
    spawn_id: str,
    working_dir: str,
    provider: str = DEFAULT_PROVIDER,
    model: str = "default",
    execution_mode: str = DEFAULT_EXECUTION_MODE,
    verbosity: str = DEFAULT_AGENT_VERBOSITY,
    leak_env: bool = False,
    allow_create_working_dir: bool = True,
):
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
        if allow_create_working_dir:
            os.makedirs(working_dir)
            await ctx.respond(
                f"ℹ️ Creating '{working_dir}'."
            )
        else:
            await ctx.respond(
                f"❌ Working Dir '{working_dir}' does not exist."
            )
            return
    if not ALLOW_LEAK_ENV and leak_env:
        await ctx.respond(
            f"❌ leak_env=True has been configured as disallowed."
        )
        return
    execution_mode = execution_mode.strip().lower()
    if execution_mode not in ("docker", "host"):
        await ctx.respond(f"❌ Unknown execution mode '{execution_mode}'.")
        return
    if is_docker_execution_mode(execution_mode) and not ALLOW_DOCKER_EXECUTION:
        await ctx.respond("❌ Docker execution has been disabled by configuration.")
        return
    if is_host_execution_mode(execution_mode) and not ALLOW_HOST_EXECUTION:
        await ctx.respond("❌ Host execution has been disabled by configuration.")
        return
    verbosity = normalize_agent_verbosity(verbosity)

    if is_docker_execution_mode(execution_mode):
        await create_agent_docker_container(spawn_id, working_dir, leak_env)
    spawns[spawn_id] = {
        "spawn_id": spawn_id,
        "codex_session_id": None,
        "provider": provider,
        "model": model.strip(),
        "working_dir": working_dir,
        "execution_mode": execution_mode,
        "verbosity": verbosity,
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
    entry = spawns[spawn_id]
    procs = entry["processes"]
    execution_mode = entry["execution_mode"]
    use_docker = is_docker_execution_mode(execution_mode)
    if not procs:
        append_msg = "."
        if delete:
            append_msg = f" (permanently deleted agent **{spawn_id}**)."
            if use_docker:
                # Stop container (without printing a full-blown traceback if not running)
                await stop_agent_docker_container(spawn_id, silent_errors=True)
                await delete_agent_docker_container(spawn_id)
            del spawns[spawn_id]
        save_spawns()
        await ctx.respond(f"ℹ️  No active processes for spawn ID **{spawn_id}**" + append_msg)
        return

    if use_docker:
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

    entry["processes"] = []
    if delete:
        if use_docker:
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
        await ctx.respond("ℹ️  No spawn workers registered.")
        return
    now = datetime.datetime.now()
    lines: list[str] = []
    for sid, entry in spawns.items():
        procs = entry["processes"]
        execution_mode = entry["execution_mode"]
        verbosity = entry["verbosity"]
        if procs:
            lines.append(f"**{sid}** ({execution_mode}, {verbosity}):")
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
            lines.append(f"**{sid}** ({execution_mode}, {verbosity}): no active processes")
    await ctx.respond("\n".join(lines))


def format_response(msg: str) -> str:
    return msg


def format_thought(msg: str) -> str:
    indicator = "💭💭💭💭💭"
    return f"{indicator}\n{msg}\n{indicator}"


def format_command(msg: str) -> str:
    return f'`{msg}`'


def format_code_block(msg: str) -> str:
    return f'```\n{msg}\n```'


def format_command_output(msg: str) -> str:
    return format_code_block(msg)


def extract_codex_session_id_from_event(event: dict) -> Optional[str]:
    event_type = event.get("type")
    if event_type == "thread.started":
        thread_id = event.get("thread_id")
        return thread_id if isinstance(thread_id, str) else None
    return None


def format_token_usage_summary(usage: dict) -> str:
    input_tokens = usage.get("input_tokens")
    cached_input_tokens = usage.get("cached_input_tokens")
    output_tokens = usage.get("output_tokens")
    reasoning_output_tokens = usage.get("reasoning_output_tokens")
    total_tokens = usage.get("total_tokens")

    parts = []
    if isinstance(input_tokens, int):
        parts.append(f"in={input_tokens}")
    if isinstance(cached_input_tokens, int):
        parts.append(f"cached_in={cached_input_tokens}")
    if isinstance(output_tokens, int):
        parts.append(f"out={output_tokens}")
    if isinstance(reasoning_output_tokens, int):
        parts.append(f"reasoning_out={reasoning_output_tokens}")
    if isinstance(total_tokens, int):
        parts.append(f"total={total_tokens}")

    if not parts:
        return format_code_block(json.dumps(usage, indent=2))
    return "Tokens: " + ", ".join(parts)


def format_codex_item_tool(item: dict) -> str:
    item_type = str(item.get("type", "tool"))
    action = item.get("action") if isinstance(item.get("action"), dict) else {}
    action_type = str(action.get("type", "other"))

    if item_type == "web_search":
        if action_type == "search":
            query = action.get("query") or item.get("query") or ""
            return format_command(f'web_search "{query}"' if query else "web_search")
        if action_type == "open_page":
            url = action.get("url") or item.get("query") or ""
            return format_command(f"web_open {url}" if url else "web_open")
        query = item.get("query")
        if isinstance(query, str) and query:
            return format_command(f"web_search {query}")
        return format_command("web_search")

    if item_type in ("local_shell_call", "shell"):
        cmd = item.get("command")
        if isinstance(cmd, list):
            return format_command(" ".join(map(str, cmd)))
        if isinstance(cmd, str):
            return format_command(cmd)

    # Generic fallback for newer/unknown tool item types
    minimal = {
        "type": item.get("type"),
        "id": item.get("id"),
        "action": item.get("action"),
        "query": item.get("query"),
    }
    return format_code_block(json.dumps(minimal, indent=2))


def send_codex_notification(worker_entry: dict, event: dict, verbosity: str, reference=None):
    action = ""
    messages: list[str] = []
    verbosity = normalize_agent_verbosity(verbosity)
    verbose = is_verbose_agent_verbosity(verbosity)

    try:
        event_type = event.get("type")
        if event_type == "error":
            msg = event.get("message")
            if isinstance(msg, str) and msg:
                messages = [format_code_block(msg)]
                action = " error"
            else:
                return
        elif event_type == "turn.failed":
            err = event.get("error") or {}
            msg = err.get("message") if isinstance(err, dict) else None
            if isinstance(msg, str) and msg:
                messages = [format_code_block(msg)]
                action = " error"
            else:
                return
        elif event_type == "turn.completed":
            usage = event.get("usage")
            if isinstance(usage, dict):
                messages = [format_token_usage_summary(usage)]
                action = " used tokens"
            else:
                return
        elif event_type in ("item.started", "item.completed"):
            item = event.get("item")
            if not isinstance(item, dict):
                return
            item_type = item.get("type")
            item_completed = event_type == "item.completed"
            if item_type == "agent_message":
                if not item_completed:
                    return
                msg = item.get("text")
                if isinstance(msg, str) and msg:
                    messages = [format_response(msg)]
                    action = " responded"
                else:
                    return
            elif item_type == "reasoning":
                if not (verbose and item_completed):
                    return
                msg = item.get("text")
                if isinstance(msg, str) and msg:
                    messages = [format_thought(msg)]
                    action = " thought"
                else:
                    return
            else:
                if not verbose:
                    return
                messages = [format_codex_item_tool(item)]
                action = " tool"
                if not item_completed:
                    action = " started tool"
        elif event_type in ("thread.started", "turn.started", "turn.cancelled"):
            return
        else:
            return
    except Exception:
        log(traceback.format_exc())
        return

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

    is_first_iter = True
    while msg:
        msg_piece = close_unterminated_code_blocks(msg[:DISCORD_CHARACTER_LIMIT]) + (
            f"\n\n... ({len(msg[DISCORD_CHARACTER_LIMIT:].splitlines())} lines left)"
            if DISCORD_LONG_RESPONSE_ADD_NUM_LINES_LEFT and len(msg) >= DISCORD_CHARACTER_LIMIT
            else ""
        )
        msg = msg[DISCORD_CHARACTER_LIMIT:]
        if DISCORD_LONG_RESPONSE_BULK_AS_CODEBLOCK and not is_first_iter:
            # Put the bulk of long messages into code blocks
            msg_piece = f"```\n{msg_piece}\n```"

        coro = channel.send(msg_piece, reference=reference)
        asyncio.run_coroutine_threadsafe(coro, bot.loop)
        is_first_iter = False


async def save_message_attachments(message, save_directory):
    """Save all attachments from a Discord message"""

    # Create directory if it doesn't exist
    os.makedirs(save_directory, exist_ok=True)

    if not message.attachments:
        print("No attachments found")
        return []

    saved_files = []

    async with aiohttp.ClientSession() as session:
        for attachment in message.attachments:
            try:
                # Secure the filename
                safe_filename = secure_filename(attachment.filename)
                if not safe_filename:
                    safe_filename = f"attachment_{attachment.id}"

                # Use safe_join to prevent directory traversal
                filepath = safe_join(save_directory, safe_filename)
                if filepath is None:
                    print(f"Unsafe path detected for {attachment.filename}, skipping")
                    continue

                # Handle duplicates
                counter = 1
                original_filepath = filepath
                while os.path.exists(filepath):
                    name, ext = os.path.splitext(safe_filename)
                    duplicate_filename = f"{name}_{counter}{ext}"
                    filepath = safe_join(save_directory, duplicate_filename)
                    if filepath is None:
                        break
                    counter += 1

                if filepath is None:
                    print(f"Could not create safe path for {attachment.filename}")
                    continue

                # Download the file
                async with session.get(attachment.url) as response:
                    if response.status == 200:
                        async with aiofiles.open(filepath, 'wb') as f:
                            async for chunk in response.content.iter_chunked(8192):
                                await f.write(chunk)

                        saved_files.append(filepath)
                        print(f"Saved: {os.path.basename(filepath)} ({attachment.size} bytes)")
                    else:
                        print(f"Failed to download {attachment.filename}: HTTP {response.status}")

            except Exception as e:
                print(f"Error saving {attachment.filename}: {e}")

    return saved_files


@bot.event
@notify_on_internal_error
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Drop all messages from users not on the allow-list
    if message.author.id not in ALLOWED_USER_IDS:
        return

    # Trim content
    message.content = message.content.strip()

    # Parse bot mention pattern: <@bot_id> to spawn_id: prompt
    pattern = rf"^<@!?{bot.user.id}>\s+to\s+([{VALID_SPAWN_ID_CHARS_REGEX_ENUM}]+?):\s*(.+)\s*"
    match = re.match(pattern, message.content)
    if not match:
        pattern_without_spawn_id_constraints = rf"^<@!?{bot.user.id}>\s+to\s+(.+?):\s*(.+)\s*"
        if re.match(pattern_without_spawn_id_constraints, message.content):
            await message.channel.send(
                f"ℹ️ In case you meant to DM an agent, your syntax is incorrect. This is the pattern by which messages are matched: `{pattern}`", reference=message
            )
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

    codex_session_id = entry["codex_session_id"]
    provider = entry["provider"]
    model = entry["model"]
    working_dir = entry["working_dir"]
    leak_env = entry["leak_env"]
    execution_mode = entry["execution_mode"]
    verbosity = entry["verbosity"]
    if provider not in ALLOWED_PROVIDERS:
        await message.channel.send(
            f"❌ This agent uses provider '{provider}', which is not allowed by current bot config. Recreate the agent or update `ALLOWED_PROVIDERS`.",
            reference=message
        )
        return
    if is_docker_execution_mode(execution_mode) and not ALLOW_DOCKER_EXECUTION:
        await message.channel.send("❌ This agent is configured for Docker, but Docker execution is disabled.", reference=message)
        return
    if is_host_execution_mode(execution_mode) and not ALLOW_HOST_EXECUTION:
        await message.channel.send("❌ This agent is configured for host execution, but host execution is disabled.", reference=message)
        return

    prev_session_file_content = None
    if codex_session_id:
        prev_session_file_path = find_codex_session_file_path(codex_session_id)
        if prev_session_file_path and os.path.exists(prev_session_file_path):
            prev_session_file_content = read_text_file(prev_session_file_path)

    # Upload attachments and append `Uploaded attachments:\n- attachment1_path\n- attachment2_path\n...` to prompt
    if message.attachments:
        attachments_dir = get_agent_uploads_dir(working_dir, spawn_id)
        log(f"Saving attachments to {attachments_dir}...")
        saved_files = await save_message_attachments(message, attachments_dir)
        log(f"Done saving attachments!")
        prompt += "\n\nUploaded attachments:"
        for filepath in saved_files:
            prompt += f"\n- {filepath}"

    # Start the agent
    prompt = build_codex_prompt(prompt)
    proc = await launch_agent(spawn_id, prompt, codex_session_id, provider, model, working_dir, leak_env, execution_mode)
    await message.channel.send(f"✅ AGENT **{spawn_id}** DEPLOYED...", reference=message)

    assert proc.stdout is not None
    entry["processes"].append({"proc": proc, "start_time": datetime.datetime.now()})

    # This allows configuring the bot so the responses will not reference the original user message. This way,
    # the user will not be spammed with 'new message' notifications (and won't and up with 10s of unread messages).
    if DISCORD_RESPONSE_NO_REFERENCE_USER_COMMAND:
        reference = None
    else:
        reference = message

    async def reader():
        nonlocal codex_session_id
        log(f"Spawning reader routine for agent {spawn_id}")
        async for line in proc.stdout:
            line = line.strip().decode('utf-8', errors='replace')
            if not line:
                continue
            try:
                line_json = json.loads(line)
            except json.JSONDecodeError:
                log("[ERROR] New line from process:", line)
                log(traceback.format_exc())
                continue

            log("New line from process:", line)
            maybe_session_id = extract_codex_session_id_from_event(line_json)
            if maybe_session_id and maybe_session_id != codex_session_id:
                codex_session_id = maybe_session_id
                entry["codex_session_id"] = maybe_session_id
                save_spawns()

            send_codex_notification(entry, line_json, verbosity=verbosity, reference=reference)

        # Send termination notification
        send_notification(entry, f"AGENT **{spawn_id}** COMPLETED HIS MISSION!", critical=True, reference=reference)
        log(f"Retiring reader routine for agent {spawn_id}")

        # Stop container
        if is_docker_execution_mode(execution_mode):
            await stop_agent_docker_container(spawn_id)

        await proc.wait()
        if proc in newly_killed_procs:
            log(f"Reverting session file for Codex session ID {codex_session_id}")
            newly_killed_procs.remove(proc)
            restored = restore_codex_session_file(codex_session_id, prev_session_file_content)
            if not restored:
                log(f"Could not restore session file for {codex_session_id}")
            if prev_session_file_content is None:
                # First run was reverted; drop the stored session id so the next prompt starts fresh.
                entry["codex_session_id"] = None
                save_spawns()

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
