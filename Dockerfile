# Use official Node.js LTS base image
FROM node:22-bookworm

# Install all requirements, some developer tools, and nuke apt
RUN apt-get update && apt-get install -y --no-install-recommends \
    # -------------- Base / OS metadata -------------- \
    ca-certificates \
    locales \
    lsb-release \
    software-properties-common \
    # -------------- System utilities ---------------- \
    procps \
    iproute2 \
    iptables \
    ipset \
    dnsutils \
    # ------------ Networking clients ---------------- \
    curl \
    wget \
    openssh-client \
    xdg-utils \
    # --------- Build & language tooling ------------- \
    build-essential \
    git \
    python3 \
    python3-pip \
    python3-venv \
    aggregate \
    # -------------- Editors & shells ---------------- \
    vim \
    nano \
    zsh \
    fzf \
    # -------- Search & data processing tools -------- \
    ripgrep \
    jq \
    less \
    man-db \
    # ------------ Privilege & CI helpers ------------ \
    util-linux \
    gnupg \
    gh \
    # ---------------- Compression ------------------- \
    unzip \
    zip \
    iputils-ping

# ------------- Optionally Nuke APT -------------- \
# rm -rf /var/lib/apt/lists/*

# Set UTF-8 as default locale (good for logs, etc.)
RUN sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && \
    locale-gen && \
    update-locale LANG=en_US.UTF-8
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# No need to install claude code using NPM because we provide our own modded version
RUN npm install -g @anthropic-ai/claude-code@1.0.31

# Create and activate venv and install dependencies
RUN mkdir -p /app
RUN python3 -m venv /app/.venv
RUN bash -c "source /app/.venv/bin/activate && pip install pydantic_extra_types==2.10.3 pydantic==2.10.6 mcp==1.3.0 tzdata==2025.1 openai==1.66.2 typer==0.15.2"

# Copy and build the gemini-cli part
COPY ./third_party/gemini-cli /app/third_party/gemini-cli
RUN chmod +x /app/third_party/gemini-cli/bundle.sh
RUN bash -lc "cd /app/third_party/gemini-cli && ./bundle.sh && cd -"

# Copy the dist folder for codex-headless
COPY ./third_party/codex-headless/dist/ /app/third_party/codex-headless/dist/
RUN chmod +x /app/third_party/codex-headless/dist/bin/codex-linux-sandbox-x64
RUN chmod +x /app/third_party/codex-headless/dist/bin/codex-linux-sandbox-arm64
RUN chmod +x /app/third_party/codex-headless/dist/cli.mjs
COPY ./third_party/claude-code-unrestricted/claude /usr/local/bin/claude
RUN chmod +x /usr/local/bin/claude

# Copy custom tools for Codex (python scripts invoked through terminal).
# No extension so it can be called like `web_search -q "query"`
COPY ./tools/web_search.py /app/tools/web_search.py
RUN chmod +x /app/tools/web_search.py

# Copy the entrypoint script
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Copy script to launch background services (e.g. MCP servers)
COPY launch_services.sh /usr/local/bin/launch_services.sh
RUN chmod +x /usr/local/bin/launch_services.sh

# Entrypoint handles switching to non-root user, setting up firewall rules, etc.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
