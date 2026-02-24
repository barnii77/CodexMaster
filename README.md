# CodexMaster

... 'cause my 16yo ass can't afford $200/month😐

## What is this?

Simply put, it's a Discord frontend for Codex CLI. It does a bit more than that though, because you can spawn and manage multiple agents at once, so it's actually more like the cloud-based Codex in ChatGPT, I think.

### Why would you even need this?

Well, I don't know about you, but I don't always have immediate access to a terminal where I can run all of these tools in a properly isolated environment.

### Some more info

This version uses the standard `codex` CLI directly (no `codex-headless` fork and no Claude/Gemini integrations). If you run agents in Docker, the image installs `@openai/codex` and mounts your host `~/.codex` into the container so Codex auth/config/session data are shared.

## How to use

Roughly, you have to

1. Create a discord bot. This is supposed to be self hosted and access restricted (you have to whitelist users in `.env`). As you may be able from the code, this system is not built to be scaled.
2. Populate `.env` file with bot token, whitelist, any API keys (openai, anthropic, ...), and more. See [.example.env](.example.env).
3. Run directly with `python bot.py`, create a systemd service or a docker container, whatever. Recommended option for deployment on linux is a systemd service that runs as a non-root user.

Execution mode is now chosen per agent at `/spawn` time (`docker` or `host`). Use `ALLOW_DOCKER_EXECUTION`, `ALLOW_HOST_EXECUTION`, and `DEFAULT_EXECUTION_MODE` in `.env` to control which modes are available and which one is the default.

Agent notification detail is also configurable per spawn via `verbosity` (`answers` vs `verbose`). Set `DEFAULT_AGENT_VERBOSITY` in `.env` to choose the default.

`codex.env` is optional. It is only a convenience file for passing API keys / Codex env flags into agent runs (especially Docker mode or host mode with `leak_env=false`).

### Requirements

- python (required) >= 3.9
- node.js (optional, host mode only) >= 22
- rootless docker (recommended) >= 21: If you want to run Codex in a docker container to minimize potential damage. I recommend rootless docker.
- linux (recommended): CodexMaster was developed on linux and is only tested there. It is meant to be hosted as a discord bot, after all.

### More details

Setup: See [SETUP.md](docs/SETUP.md).

Commands: See [COMMANDS.md](docs/COMMANDS.md).

## License
[MIT License](LICENSE)
