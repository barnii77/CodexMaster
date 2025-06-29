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

## 8. Login to Gemini CLI (Optional)

This section requires manually logging in to Gemini CLI by creating a new image from our `codex-cli-headless` container and logging in via oauth.
Below are detailed instructions. They require a browser and 3 terminals.

1. In terminal 3: `docker run --name codex-master-with-gemini-cli-login -e CODEX_HOME=$HOME -e CODEX_USER=$USER --cap-add=NET_ADMIN -it codex-cli-headless bash`
2. In terminal 1 and 2 each: `docker exec -it codex-master-with-gemini-cli-login bash`
3. In terminal 1: `/app/third_party/gemini-cli/bundle/gemini.js --debug`. Select the default colorscheme and press enter.
4. You will be prompted to select an auth method. Choose `Login with Google`.
5. An oauth link will be printed above a UI element with a spinner. It should be a long link. Open it in your browser.
6. The browser will be redirected to localhost, and will say `ERR_CONNECTION_REFUSED`.
7. Open devtools and go to the network tab.
8. Reload the page so the request is sent again.
9. A request will show up in the network tab. Right click on it, select `Copy` and choose `Copy as cURL (bash)`.
10. In terminal 2: paste in this command you just copied and run it.
11. In terminal 1: you should now be authenticated and be prompted to ask gemini something. Type `/quit` and exit Gemini CLI.
12. In terminal 1 and 2 each: `exit`.
13. In terminal 1: `docker stop codex-master-with-gemini-cli-login`
14. In terminal 1 (now outside the container): `docker commit --author="me <me@example.com>" --message="set up gemini cli with google login" codex-master-with-gemini-cli-login codex-cli-headless:latest`

You are now logged in to Gemini CLI with your Google account.

---

*For more information on commands and usage, see [COMMANDS.md](COMMANDS.md).* 
