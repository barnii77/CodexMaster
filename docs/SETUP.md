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

- **Node.js** >= 22
    - Alternative to docker if you want to run Codex CLI without sandbox (not recommended **at all!!**)

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
cp codex.example.env codex.env
```

- Edit **.env** to configure the Discord bot, whitelist, Docker settings, and CodexMaster options. See [.example.env](../.example.env).
- Edit **codex.env** to supply your OpenAI, Anthropic, and any other provider API keys. See [codex.example.env](../codex.example.env).

Ensure that the `CODEX_ENV_FILE` variable in **.env** (default `codex.env`) matches the name of your Codex CLI env file.

## 4. Build the Codex CLI Docker image

CodexMaster spawns Codex CLI in a container by default. Build the image from the project root:

```bash
docker build -t codex-cli-headless .
```

To use the slimmer base image (warning: takes >10 min to build):

```bash
docker build -f Dockerfile.slim -t codex-cli-headless .
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

---

*For more information on commands and usage, see [COMMANDS.md](COMMANDS.md).* 
