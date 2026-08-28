#!/bin/sh
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

if [ "$(id -u)" -ne 0 ]; then
    echo "run as root" >&2
    exit 2
fi

DESKTOP_USER="${1:-kali}"
SOURCE_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
case "$DESKTOP_USER" in
    *[!a-zA-Z0-9_.-]*|'')
        echo "invalid desktop user" >&2
        exit 3
        ;;
esac
PASSWD_ENTRY="$(/usr/bin/getent passwd "$DESKTOP_USER" || true)"
USER_HOME="$(printf '%s\n' "$PASSWD_ENTRY" | /usr/bin/cut -d: -f6)"
USER_UID="$(/usr/bin/id -u "$DESKTOP_USER" 2>/dev/null || true)"
NOOB_UID="$(/usr/bin/id -u noob 2>/dev/null || true)"
NOOB_GID="$(/usr/bin/id -g noob 2>/dev/null || true)"
USER_RUNTIME_DIR="/run/user/$USER_UID"
USER_SESSION_BUS="$USER_RUNTIME_DIR/bus"

if [ -z "$USER_HOME" ] || [ -z "$USER_UID" ] || [ ! -d "$USER_HOME" ]; then
    echo "desktop user not found" >&2
    exit 4
fi
if [ -z "$NOOB_UID" ] || [ -z "$NOOB_GID" ]; then
    echo "noob service account not found; install the gateway first" >&2
    exit 4
fi
if [ ! -S "$USER_SESSION_BUS" ]; then
    echo "desktop session bus not found; log into XFCE before installing the shortcut" >&2
    exit 4
fi

for command in /usr/bin/python3 /usr/bin/xdotool /usr/bin/wmctrl /usr/bin/xprop /usr/bin/xwininfo /usr/bin/sudo /usr/bin/xfconf-query; do
    if [ ! -x "$command" ]; then
        echo "missing dependency: $command" >&2
        exit 5
    fi
done

if ! /usr/bin/python3 -c 'import tkinter; from PIL import Image, ImageTk' >/dev/null 2>&1; then
    echo "install python3-tk and python3-pil.imagetk before continuing" >&2
    exit 6
fi

/usr/bin/install -d -m 0755 /opt/noob/appliance
/usr/bin/install -m 0755 "$SOURCE_ROOT/appliance/noob_local_console.py" /opt/noob/appliance/noob_local_console.py
/usr/bin/install -m 0755 "$SOURCE_ROOT/appliance/noob-local-console-toggle" /opt/noob/appliance/noob-local-console-toggle
/usr/bin/install -m 0755 "$SOURCE_ROOT/scripts/noob_pairing_code.py" /opt/noob/appliance/noob_pairing_code.py
/usr/bin/install -d -m 0755 /usr/local/bin
/usr/bin/install -m 0755 "$SOURCE_ROOT/scripts/noob_pairing_code.py" /usr/local/bin/noob-pairing-code

sudoers_tmp="$(/usr/bin/mktemp)"
token_tmp=''
trap '/usr/bin/rm -f "$sudoers_tmp" ${token_tmp:+"$token_tmp"}' EXIT INT TERM

# Provision a dedicated least-privilege credential without replacing an
# existing one.  The full operator credential is never exposed to the desktop
# session, and token bytes are never printed.
if [ -L /etc/noob ]; then
    echo "/etc/noob must not be a symbolic link" >&2
    exit 7
fi
/usr/bin/install -d -o root -g noob -m 0750 /etc/noob
LOCAL_TOKEN_FILE=/etc/noob/local-console.key
if [ -e "$LOCAL_TOKEN_FILE" ] || [ -L "$LOCAL_TOKEN_FILE" ]; then
    if [ ! -f "$LOCAL_TOKEN_FILE" ] || [ -L "$LOCAL_TOKEN_FILE" ]; then
        echo "local console token must be a regular file" >&2
        exit 7
    fi
    if [ "$(/usr/bin/stat -c %u "$LOCAL_TOKEN_FILE")" != "$NOOB_UID" ] || \
       [ "$(/usr/bin/stat -c %a "$LOCAL_TOKEN_FILE")" != 600 ]; then
        echo "local console token must be owned by noob with mode 0600" >&2
        exit 7
    fi
    /usr/bin/sudo -n -u "#$NOOB_UID" /usr/bin/python3 -c \
        'from pathlib import Path; t=Path("/etc/noob/local-console.key").read_text(encoding="ascii").strip(); assert 32 <= len(t) <= 256 and not any(c.isspace() for c in t)' \
        >/dev/null 2>&1 || {
            echo "existing local console token is invalid" >&2
            exit 7
        }
else
    token_tmp="$(/usr/bin/mktemp /etc/noob/.local-console.key.XXXXXX)"
    /usr/bin/python3 -c 'import secrets; print(secrets.token_urlsafe(48))' >"$token_tmp"
    /usr/bin/chown "$NOOB_UID:$NOOB_GID" "$token_tmp"
    /usr/bin/chmod 0600 "$token_tmp"
    /usr/bin/mv "$token_tmp" "$LOCAL_TOKEN_FILE"
    token_tmp=''
fi

/usr/bin/sed "s/__NOOB_DESKTOP_USER__/${DESKTOP_USER}/g" \
    "$SOURCE_ROOT/packaging/noob-local-console.sudoers" >"$sudoers_tmp"
/usr/bin/chmod 0440 "$sudoers_tmp"
/usr/sbin/visudo -cf "$sudoers_tmp" >/dev/null
/usr/bin/install -m 0440 "$sudoers_tmp" /etc/sudoers.d/noob-local-console

# Install launch metadata only into a root-owned system directory.  Never
# follow or replace a path below the desktop user's writable home as root.
/usr/bin/install -d -m 0755 /usr/local/share/applications
/usr/bin/install -m 0644 \
    "$SOURCE_ROOT/packaging/noob-local-console.desktop" \
    /usr/local/share/applications/noob-local-console.desktop
/usr/bin/install -d -m 0755 /usr/local/share/icons/hicolor/scalable/apps
/usr/bin/install -m 0644 \
    "$SOURCE_ROOT/design/noob-icon.svg" \
    /usr/local/share/icons/hicolor/scalable/apps/noob.svg

# Put a real launcher on the operator's XFCE desktop.  Every user-home
# mutation runs as that user, so root neither creates writable-home content nor
# follows a user-controlled destination path.  XFCE can still ask for launch
# confirmation on its first use when the GVfs trust attribute is unavailable.
DESKTOP_DIR="$USER_HOME/Desktop"
/usr/bin/sudo -n -u "#$USER_UID" /usr/bin/install -d -m 0755 "$DESKTOP_DIR"
/usr/bin/sudo -n -u "#$USER_UID" /usr/bin/install -m 0755 \
    /usr/local/share/applications/noob-local-console.desktop \
    "$DESKTOP_DIR/N.O.O.B Local Console.desktop"
if [ -x /usr/bin/gio ]; then
    /usr/bin/sudo -n -u "#$USER_UID" /usr/bin/env \
        XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=$USER_SESSION_BUS" \
        /usr/bin/gio set \
        "$DESKTOP_DIR/N.O.O.B Local Console.desktop" \
        metadata::trusted true >/dev/null 2>&1 || true
fi
if [ -x /usr/bin/gtk-update-icon-cache ]; then
    /usr/bin/gtk-update-icon-cache -q -f /usr/local/share/icons/hicolor \
        >/dev/null 2>&1 || true
fi

shortcut='/commands/custom/<Super>n'
run_xfconf() {
    /usr/bin/sudo -u "#$USER_UID" /usr/bin/env \
        XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=$USER_SESSION_BUS" \
        DISPLAY=:0 \
        XAUTHORITY="$USER_HOME/.Xauthority" \
        /usr/bin/xfconf-query "$@"
}
current="$(run_xfconf -c xfce4-keyboard-shortcuts -p "$shortcut" 2>/dev/null || true)"
if [ -z "$current" ]; then
    run_xfconf -c xfce4-keyboard-shortcuts -p "$shortcut" --create \
        -t string -s '/opt/noob/appliance/noob-local-console-toggle'
elif [ "$current" != '/opt/noob/appliance/noob-local-console-toggle' ]; then
    echo "Super+N is already assigned; launcher installed without replacing it" >&2
fi

echo "N.O.O.B local console, pairing-code helper, desktop icon, and scoped credential installed. Restart the gateway after enabling auth.local_token_file, then launch from the desktop, menu, or Super+N."
