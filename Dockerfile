# Use official Node.js LTS base image (with Debian)
FROM node:22-bookworm

# Set the working directory to /root (acts like ~)
WORKDIR /root/project

# Install essential developer tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3 python3-pip git curl ca-certificates

# Ensure a UTF-8 locale
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# Copy the CLI file for codex-headless
COPY ./third_party/codex-headless/dist/cli.mjs /CodexMaster/third_party/codex-headless/dist/cli.mjs

# Keep the container alive forever (until it is stopped)
CMD ["tail", "-f", "/dev/null"]
