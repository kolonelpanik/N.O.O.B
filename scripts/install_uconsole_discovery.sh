#!/bin/sh
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

usage() {
    echo "Usage: sudo $0 --ssh-port PORT" >&2
}

SSH_PORT=''
SSH_PORT_SET=false
while [ "$#" -gt 0 ]; do
    case "$1" in
        --ssh-port)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            [ "$SSH_PORT_SET" = false ] || {
                echo "--ssh-port may be supplied only once" >&2
                exit 2
            }
            SSH_PORT=$2
            SSH_PORT_SET=true
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "run as root" >&2
    exit 2
fi
case "$SSH_PORT" in
    ''|*[!0-9]*|0|??????*)
        echo "--ssh-port must be an integer from 1 through 65535" >&2
        exit 3
        ;;
esac
if [ "$SSH_PORT" -gt 65535 ]; then
    echo "--ssh-port must be an integer from 1 through 65535" >&2
    exit 3
fi

SOURCE_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PREFLIGHT_SOURCE="$SOURCE_ROOT/appliance/noob_discovery_preflight.py"
UNIT_SOURCE="$SOURCE_ROOT/packaging/noob-discovery.service"
DOC_SOURCE="$SOURCE_ROOT/docs/appliance-discovery.md"

for command in /usr/bin/install /usr/bin/python3 /usr/bin/ss /usr/bin/systemctl /usr/bin/avahi-publish-service; do
    if [ ! -x "$command" ]; then
        echo "missing dependency: $command" >&2
        echo "On Kali/Debian, install avahi-daemon and avahi-utils before retrying." >&2
        exit 4
    fi
done
if [ ! -f "$PREFLIGHT_SOURCE" ] || [ -L "$PREFLIGHT_SOURCE" ] || \
   [ ! -f "$UNIT_SOURCE" ] || [ -L "$UNIT_SOURCE" ] || \
   [ ! -f "$DOC_SOURCE" ] || [ -L "$DOC_SOURCE" ]; then
    echo "discovery packaging source is missing or unsafe" >&2
    exit 5
fi
if ! /usr/bin/id noob >/dev/null 2>&1; then
    echo "noob service account not found; install the gateway first" >&2
    exit 6
fi

# Refuse to advertise a guessed or loopback-only endpoint.  The same preflight
# runs on every systemd start, so a later SSH configuration change fails
# closed instead of leaving a stale advertisement online.
NOOB_DISCOVERY_SSH_PORT="$SSH_PORT" \
    /usr/bin/python3 "$PREFLIGHT_SOURCE"

if [ ! -d /etc/default ] || [ -L /etc/default ]; then
    echo "/etc/default must be a real directory" >&2
    exit 7
fi
for destination in /etc/default/noob-discovery /etc/systemd/system/noob-discovery.service; do
    if [ -L "$destination" ]; then
        echo "refusing symbolic-link destination: $destination" >&2
        exit 7
    fi
done
/usr/bin/install -d -o root -g root -m 0755 \
    /opt/noob-discovery/appliance /opt/noob-discovery/docs
/usr/bin/install -m 0755 "$PREFLIGHT_SOURCE" \
    /opt/noob-discovery/appliance/noob_discovery_preflight.py
/usr/bin/install -m 0644 "$DOC_SOURCE" \
    /opt/noob-discovery/docs/appliance-discovery.md

config_tmp="$(/usr/bin/mktemp /etc/default/.noob-discovery.XXXXXX)"
trap '/usr/bin/rm -f "$config_tmp"' EXIT INT TERM
{
    echo "# Public, non-secret N.O.O.B. discovery configuration."
    printf 'NOOB_DISCOVERY_SSH_PORT=%s\n' "$SSH_PORT"
} >"$config_tmp"
/usr/bin/chown root:root "$config_tmp"
/usr/bin/chmod 0644 "$config_tmp"
/usr/bin/mv "$config_tmp" /etc/default/noob-discovery
config_tmp=''
trap - EXIT INT TERM

/usr/bin/install -m 0644 "$UNIT_SOURCE" \
    /etc/systemd/system/noob-discovery.service
/usr/bin/systemctl daemon-reload
/usr/bin/systemctl enable --now avahi-daemon.service
/usr/bin/systemctl enable --now noob-discovery.service
if ! /usr/bin/systemctl --quiet is-active noob-discovery.service; then
    echo "discovery publisher did not remain active; inspect its systemd journal" >&2
    exit 8
fi

echo "N.O.O.B. discovery enabled on _noob-kvm._tcp at SSH port $SSH_PORT."
echo "The advertisement is an untrusted hint; pairing still requires independent SSH host-key verification."
