# CodexMaster

... 'cause my 16yo ass can't afford $200/month😐

## What is this?

Simply put, it's a discord frontend for Claude Code, Codex CLI and Gemini CLI. It does a bit more than that though, because you can spawn and manage multiple agents at once, so it's actually more like the cloud based Codex in ChatGPT, I think.

Note that I have a custom version of all of these command line tools. You can find them in `third_party/`. This is because none of them quite supported all the features I need for this bot out of the box. The closest was probably claude code, which provides a headless mode that actually supports all the tool's features. Still, I had to make a custom modded version of it to remove an artificial limitation (for "safety reasons") that is irrelevant when you run inside of docker. More on that below.

You can use it with Claude Code by adding an `ANTHROPIC_API_KEY` to your `codex.env` file and specifying `claude` as the backend (and an appropriate anthropic model) when creating an agent.
Note that Claude Code is run with `--dangerously-skip-permissions` as root (inside the container, which, using rootless docker, is just your normal user). Since this is not possible in normal
Claude Code, this project uses a modded version of `v1.0.31`. More on this at [third_party/claude-code-unrestricted/README.md](third_party/claude-code-unrestricted/README.md).

## How to use

Roughly, you have to

1. Create a discord bot. This is supposed to be self hosted and access restricted (you have to whitelist users in `.env`). As you may be able from the code, this system is not built to be scaled.
2. Populate `.env` file with bot token, whitelist, any API keys (openai, anthropic, ...), and more. See [.example.env](.example.env).
3. Run directly with `python bot.py`, create a systemd service or a docker container, whatever. Recommended option for deployment on linux is a systemd service that runs as a non-root user.

By default, the bot will spawn Codex CLI in a docker container. You can disable this and have it be spawned directly as a normal process by setting `NO_DOCKER=1` in your `.env` file.

### Requirements

- python (required) >= 3.9
- node.js (required) >= 22
- docker (recommended) >= 21: If you want to run Codex in a docker container to minimize potential damage. I recommend rootless docker.
- linux (recommended): CodexMaster was developed on linux and is only tested there. It is meant to be hosted as a discord bot, after all.

### More details

Setup: See [SETUP.md](docs/SETUP.md). This document also describes the steps to use Gemini CLI with `Login with Google`.

Commands: See [COMMANDS.md](docs/COMMANDS.md).  
Fun fact, those were written by CodexMaster. You can take a look at the logs from that session [here](docs/codex_master_writes_its_own_docs.log).

## License
[MIT License](LICENSE)
