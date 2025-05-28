# Use official Node.js LTS base image (with Debian)
FROM node:22-bookworm

# Set the working directory to /root (acts like ~)
WORKDIR /root

# Install essential developer tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3 python3-pip \
    git curl wget unzip zip \
    build-essential \
    vim nano \
    ca-certificates \
    openssh-client \
    gnupg \
    lsb-release \
    software-properties-common \
    locales \
    && rm -rf /var/lib/apt/lists/*

# Set UTF-8 as default locale (good for logs, etc.)
RUN sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && \
    locale-gen && \
    update-locale LANG=en_US.UTF-8
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

# Copy the CLI file for codex-headless
COPY ./third_party/codex-headless/dist/cli.mjs /CodexMaster/third_party/codex-headless/dist/cli.mjs
