#!/bin/bash
set -euo pipefail

echo "Running entrypoint script (as root)..."

: "${CODEX_USER:=codex}"
: "${CODEX_HOME:=/home/$CODEX_USER}"

###############################################################################
# Block access to private networks
###############################################################################
echo "Applying IPv4 iptables rules..."

# clean the OUTPUT chain
iptables  -F OUTPUT
ip6tables -F OUTPUT

# Block private IPv4 ranges
iptables -A OUTPUT -d 192.168.0.0/16 -j DROP
iptables -A OUTPUT -d 10.0.0.0/8 -j DROP
iptables -A OUTPUT -d 172.16.0.0/12 -j DROP
iptables -A OUTPUT -d 169.254.0.0/16 -j DROP

# Block other special ranges
iptables -A OUTPUT -d 224.0.0.0/4 -j DROP
iptables -A OUTPUT -d 240.0.0.0/4 -j DROP

echo "Applying IPv6 ip6tables rules..."

# Allow IPv6 loopback
ip6tables -A OUTPUT -o lo -j ACCEPT

# Block IPv6 private ranges
ip6tables -A OUTPUT -d fe80::/10 -j DROP
ip6tables -A OUTPUT -d fc00::/7 -j DROP
ip6tables -A OUTPUT -d ::1/128 ! -o lo -j DROP

echo "Allowing through DNS..."
for ip in $(awk '/^nameserver/{print $2}' /etc/resolv.conf); do
    iptables -I OUTPUT -p udp -d "$ip" --dport 53 -j ACCEPT
    iptables -I OUTPUT -p tcp -d "$ip" --dport 53 -j ACCEPT
done

echo "Network restrictions applied successfully"

###############################################################################
# Rename root to $CODEX_USER and move home to $CODEX_HOME
###############################################################################
if [ "$(getent passwd 0 | cut -d: -f1)" != "$CODEX_USER" ]; then
    # Replace first root line in /etc/passwd with alias + original
    if ! getent passwd 0 | grep -q "^${CODEX_USER}:" ; then
        echo "Adding login alias '${CODEX_USER}' for uid 0"

        root_line=$(grep -m1 '^root:' /etc/passwd)
        sed -i "0,/^root:.*/{
            s|^root:.*|${CODEX_USER}:x:0:0::${CODEX_HOME}:/bin/bash\n${root_line}|
        }" /etc/passwd
    fi

    # Replace first root line in /etc/group  with alias + original
    if ! getent group 0 | grep -q "^${CODEX_USER}:" ; then
        echo "Adding group alias '${CODEX_USER}' for gid 0"

        root_grp=$(grep -m1 '^root:' /etc/group)
        sed -i "0,/^root:.*/{
            s|^root:.*|${CODEX_USER}:x:0:\n${root_grp}|
        }" /etc/group
    fi

    # Ensure the target home directory exists
    mkdir -p "$CODEX_HOME"
    chown 0:0 "$CODEX_HOME"

    # Keep the environment in sync for the rest of the script
    export USER="$CODEX_USER" HOME="$CODEX_HOME"
fi

# Unset ENV vars only created for this script
unset CODEX_USER CODEX_HOME

###############################################################################
# Drop every capability but keep uid/gid = 0.
# This effectively turning root into a normal user.
# Then, run either the supplied command or sleep infinity.
###############################################################################
exec setpriv \
     --reuid 0 --regid 0 \
     --clear-groups \
     --bounding-set -all \
     --ambient-caps -all \
     --inh-caps -all \
     -- /bin/bash -c '
           /usr/local/bin/launch_services.sh
           echo "[==== DONE ====]"  # sentinel printed AFTER capabilities drop
           if [ "$#" -gt 0 ]; then
               exec "$@"
           else
               exec sleep infinity
           fi
        ' _ "$@"
