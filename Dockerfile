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
    # --------- Build & language tooling ------------- \
    build-essential \
    git \
    python3 \
    python3-pip \
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

# Copy the dist folder for codex-headless
COPY ./third_party/codex-headless/dist/ /CodexMaster/third_party/codex-headless/dist/
RUN chmod +x /CodexMaster/third_party/codex-headless/dist/bin/codex-linux-sandbox-x64
RUN chmod +x /CodexMaster/third_party/codex-headless/dist/bin/codex-linux-sandbox-arm64
RUN chmod +x /CodexMaster/third_party/codex-headless/dist/cli.mjs

# Copy the entrypoint script
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Inside docker, Codex CLI can't find landlock or similar sandboxing mechanisms (but it's already sandboxed)
ENV CODEX_UNSAFE_ALLOW_NO_SANDBOX=1

# Entrypoint handles switching to non-root user, setting up firewall rules, etc.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
