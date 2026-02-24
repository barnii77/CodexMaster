# Setup Guide

This document describes how to install, configure, and run CodexMaster, a self-hosted Discord frontend for Codex CLI.

## Prerequisites

### Required

- **Python** >= 3.9

### Recommended

- **Docker** >= 21 (recommended for sandboxed Codex CLI)
- **Linux** (Debian/Ubuntu)
    - Has only been tested on these operating systems (should in theory work on others as well though)

### Optional

- **Codex CLI on host** (only if you enable host execution mode)
    - The bot assumes `codex` is already installed on the host when using `execution_mode=host`
- **Node.js** >= 22 (only if you install Codex via npm on the host)

## 1. Clone the repository

```bash
git clone https://git.barnii77.dev/barnii77/CodexMaster
# or git clone https://github.com/barnii77/CodexMaster
cd CodexMaster
```

## 2. (Optional) Create a Python virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The Python dependencies are listed in [requirements.txt](../requirements.txt):

- py-cord
- python-dotenv
- asyncio

## 3. Populate environment files

Copy and edit the example env files:

```bash
cp .example.env .env
```

- Edit **.env** to configure the Discord bot, whitelist, Docker settings, and CodexMaster options. See [.example.env](../.example.env).
- Configure execution modes in **.env**:
  - `ALLOW_DOCKER_EXECUTION=1` enables Docker-backed agents
  - `ALLOW_HOST_EXECUTION=1` enables host-backed agents
  - `DEFAULT_EXECUTION_MODE=docker|host` selects the default `/spawn` mode
- Configure default Discord notification detail in **.env**:
  - `DEFAULT_AGENT_VERBOSITY=answers` shows intermediate/final answers plus token usage
  - `DEFAULT_AGENT_VERBOSITY=verbose` also shows thoughts and tool calls

`codex.env` is now optional.

If you want an explicit env file for API keys and Codex runtime flags (recommended for Docker mode and for host mode with `leak_env=false`):

```bash
cp codex.example.env codex.env
```

Then edit **codex.env** (OpenAI key, provider keys, Codex flags) and ensure `CODEX_ENV_FILE` in **.env** points to it.

If you do not want to use `codex.env`, set `CODEX_ENV_FILE=` (blank) in **.env** and provide needed variables via the bot process environment (or use `leak_env=true` when spawning agents, if allowed).

## 4. Build the Codex CLI Docker image

If Docker execution is enabled, build the Codex CLI image from the project root:

```bash
docker build -t codexmaster-codex .
```

To use the slimmer base image (warning: takes >10 min to build):

```bash
docker build -f Dockerfile.slim -t codexmaster-codex .
```

## 5. Install Docker (Debian/Ubuntu)

### a) Rootful Docker

```bash
sudo apt update
sudo apt install -y docker.io
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
# Log out and back in for group changes to apply
```

### b) Rootless Docker (recommended)

```bash
sudo apt update
sudo apt install -y uidmap dbus-user-session
# Ensure you have Docker installed (see above or follow official docs)
dockerd-rootless-setuptool.sh install
systemctl --user enable --now docker
# You may need to logout/login to activate the user service
```

## 6. Systemd service for CodexMaster

Create a non-root system user (if not already) or use your existing account. Below is an example unit file—replace `<USER>` and `<PATH_TO_REPO>` accordingly:

```ini
[Unit]
Description=CodexMaster Discord Bot
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=<USER>
WorkingDirectory=<PATH_TO_REPO>
ExecStart=<PATH_TO_REPO>/.venv/bin/python <PATH_TO_REPO>/bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Save this as `/etc/systemd/system/codexmaster.service`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now codexmaster.service
```

You can check logs with:

```bash
journalctl -u codexmaster -f
```

Note that if you only have rootful docker, you must set `User=<USER>` under `[Service]` in the systemd config to `root`, which you probably don't want.

## 7. Running manually

If you prefer not to use systemd, activate your environment and run:

```bash
source .venv/bin/activate
python bot.py
```

## 8. Notes

This codebase is now Codex-only (no Claude Code / Gemini CLI integrations).

---

*For more information on commands and usage, see [COMMANDS.md](COMMANDS.md).* 
