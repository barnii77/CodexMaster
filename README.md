# CodexMaster

... 'cause my 16yo ass can't afford $200/month😐

# What is this?

Simply put, it's a discord frontend for Codex CLI (or rather, [my fork](https://github.com/barnii77/codex-headless) of it). It does a bit more than that though, because you can spawn and manage multiple agents at once, so it's actually more like the cloud based Codex in ChatGPT, I think.

# How to use

1. Create a discord bot. This is supposed to be self hosted and access restricted (you have to whitelist users in `.env`)
2. Populate `.env` file with bot token, whitelist, any API keys (openai, anthropic, ...), and more. See [.example.env](.example.env).
3. Run directly with `python bot.py`, create a systemd service or a docker container, whatever. Recommended option for deployment on linux is a systemd service that runs as a non-root user.

By default, the bot will spawn Codex CLI in a docker container. You can disable this and have it be spawned directly as a normal process by setting `NO_DOCKER=1` in your `.env` file.

## Requirements

- python (required) >= 3.9
- node.js (required) >= 20
- docker (recommended): If you want to run Codex in a docker container to minimize potential damage.

# License
[MIT License](LICENSE)
