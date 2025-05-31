#!/bin/bash
set -euo pipefail

echo "Running entrypoint script..."

ONCE_MARK=/var/opt/codex/.once_completed   # any path in the container’s rw layer

# define defaults for CODEX_USER and CODEX_HOME env vars
: "${CODEX_USER:=codex}"
: "${CODEX_HOME:=/home/$CODEX_USER}"

if [ "$(id -u)" = 0 ] && [ ! -f "$ONCE_MARK" ]; then
    echo "[once] running one-time initialization as root"

    # only run once on first container launch, never again
    echo "Creating non-root user $CODEX_USER..."
    useradd -m -d $CODEX_HOME -u 1001 $CODEX_USER  || {
        echo "ERROR: Failed to create user $CODEX_USER" >&2
        exit 1
    }

    # remember that we have already done it
    mkdir -p "$(dirname "$ONCE_MARK")"
    touch  "$ONCE_MARK"

    echo "[once] initialization finished"
fi

# Check if running as root (needed for iptables)
if [ "$EUID" -eq 0 ]; then
    # Block access to private networks
    echo "Applying IPv4 iptables rules..."
    
    # Block private IPv4 ranges
    iptables -A OUTPUT -d 192.168.0.0/16 -j DROP 2>/dev/null || echo "Warning: Could not apply 192.168.0.0/16 rule"
    iptables -A OUTPUT -d 10.0.0.0/8 -j DROP 2>/dev/null || echo "Warning: Could not apply 10.0.0.0/8 rule"  
    iptables -A OUTPUT -d 172.16.0.0/12 -j DROP 2>/dev/null || echo "Warning: Could not apply 172.16.0.0/12 rule"
    iptables -A OUTPUT -d 169.254.0.0/16 -j DROP 2>/dev/null || echo "Warning: Could not apply 169.254.0.0/16 rule"
    
    # Block other special ranges
    iptables -A OUTPUT -d 224.0.0.0/4 -j DROP 2>/dev/null || echo "Warning: Could not apply multicast rule"
    iptables -A OUTPUT -d 240.0.0.0/4 -j DROP 2>/dev/null || echo "Warning: Could not apply reserved rule"
    
    echo "Applying IPv6 ip6tables rules..."
    
    # Allow IPv6 loopback
    ip6tables -I OUTPUT -o lo -j ACCEPT 2>/dev/null || echo "Warning: Could not apply IPv6 loopback rule"
    
    # Block IPv6 private ranges
    ip6tables -A OUTPUT -d fe80::/10 -j DROP 2>/dev/null || echo "Warning: Could not apply IPv6 link-local rule"
    ip6tables -A OUTPUT -d fc00::/7 -j DROP 2>/dev/null || echo "Warning: Could not apply IPv6 unique local rule"
    ip6tables -A OUTPUT -d ::1/128 ! -o lo -j DROP 2>/dev/null || echo "Warning: Could not apply IPv6 localhost rule"

    echo "Allowing through DNS..."
    for ip in $(awk '/^nameserver/{print $2}' /etc/resolv.conf); do
        iptables -I OUTPUT -p udp -d "$ip" --dport 53 -j ACCEPT
        iptables -I OUTPUT -p tcp -d "$ip" --dport 53 -j ACCEPT
    done
    
    echo "Network restrictions applied successfully"
    
    # Switch to non-root user
    echo "Switching to non-root user $CODEX_USER..."
    exec gosu $CODEX_USER "$0" "$@"
    echo "ERROR: gosu failed, still root!" >&2
    exit 1
else
    echo "Running as user $CODEX_USER"
fi

echo "[==== DONE ====]"

# If arguments were passed, run them
if [ $# -gt 0 ]; then
    echo "Executing: $@"
    exec "$@"
else
    echo "No command specified, keeping container alive..."
    exec sleep infinity
    # or alternatively: exec tail -f /dev/null
fi
