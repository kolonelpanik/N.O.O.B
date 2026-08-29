#!/bin/sh
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
LC_ALL=C
export LC_ALL

usage() {
    echo "Usage: sudo $0 --interface usb0" >&2
}

INTERFACE=''
INTERFACE_SET=false
while [ "$#" -gt 0 ]; do
    case "$1" in
        --interface)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            [ "$INTERFACE_SET" = false ] || {
                echo "--interface may be supplied only once" >&2
                exit 2
            }
            INTERFACE=$2
            INTERFACE_SET=true
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
if [ "$INTERFACE" != usb0 ]; then
    echo "the reference recovery lane is exactly usb0" >&2
    exit 3
fi
if [ ! -d "/sys/class/net/$INTERFACE" ]; then
    echo "network interface does not exist: $INTERFACE" >&2
    exit 4
fi

SOURCE_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
NETWORK_SOURCE="$SOURCE_ROOT/packaging/noob-usb-recovery.network.in"
NM_SOURCE="$SOURCE_ROOT/packaging/noob-usb-recovery.networkmanager.conf.in"
DOC_SOURCE="$SOURCE_ROOT/docs/appliance-network-resilience.md"
NETWORK_DEST=/etc/systemd/network/80-noob-usb-recovery.network
NM_DEST=/etc/NetworkManager/conf.d/80-noob-usb-recovery.conf
DOC_DEST=/opt/noob-network-resilience/docs/appliance-network-resilience.md
MANAGED_MARKER='# Managed by N.O.O.B. USB recovery installer. Do not edit in place.'

for command in \
    /usr/bin/grep /usr/bin/install /usr/bin/mktemp \
    /usr/bin/mv /usr/bin/networkctl /usr/bin/rm /usr/bin/sed \
    /usr/bin/systemctl /usr/sbin/ip; do
    if [ ! -x "$command" ]; then
        echo "missing dependency: $command" >&2
        exit 5
    fi
done
if [ ! -x /usr/lib/systemd/systemd-networkd ]; then
    echo "missing dependency: /usr/lib/systemd/systemd-networkd" >&2
    exit 5
fi
for source in "$NETWORK_SOURCE" "$NM_SOURCE" "$DOC_SOURCE"; do
    if [ ! -f "$source" ] || [ -L "$source" ]; then
        echo "network resilience packaging source is missing or unsafe" >&2
        exit 6
    fi
done

for directory in /etc/systemd/network /etc/NetworkManager/conf.d; do
    if [ -L "$directory" ]; then
        echo "refusing symbolic-link directory: $directory" >&2
        exit 7
    fi
    /usr/bin/install -d -o root -g root -m 0755 "$directory"
done
for destination in "$NETWORK_DEST" "$NM_DEST"; do
    if [ -L "$destination" ]; then
        echo "refusing symbolic-link destination: $destination" >&2
        exit 7
    fi
    if [ -e "$destination" ]; then
        if [ ! -f "$destination" ] || \
           ! /usr/bin/grep -Fqx "$MANAGED_MARKER" "$destination"; then
            echo "refusing to replace an unmanaged destination: $destination" >&2
            exit 7
        fi
    fi
done

network_tmp="$(/usr/bin/mktemp /etc/systemd/network/.noob-usb-recovery.XXXXXX)"
nm_tmp="$(/usr/bin/mktemp /etc/NetworkManager/conf.d/.noob-usb-recovery.XXXXXX)"
trap '/usr/bin/rm -f "$network_tmp" "$nm_tmp"' EXIT INT TERM
/usr/bin/sed "s/__NOOB_USB_INTERFACE__/${INTERFACE}/g" \
    "$NETWORK_SOURCE" >"$network_tmp"
/usr/bin/sed "s/__NOOB_USB_INTERFACE__/${INTERFACE}/g" \
    "$NM_SOURCE" >"$nm_tmp"
/usr/bin/chown root:root "$network_tmp" "$nm_tmp"
/usr/bin/chmod 0644 "$network_tmp" "$nm_tmp"
/usr/bin/mv "$network_tmp" "$NETWORK_DEST"
network_tmp=''
/usr/bin/mv "$nm_tmp" "$NM_DEST"
nm_tmp=''
trap - EXIT INT TERM

/usr/bin/install -d -o root -g root -m 0755 \
    /opt/noob-network-resilience/docs
/usr/bin/install -o root -g root -m 0644 "$DOC_SOURCE" "$DOC_DEST"

# Transfer ownership before networkd starts so both managers never configure
# usb0 concurrently. NetworkManager is reloaded, never restarted, so wlan0
# stays on its active profile. An inactive NetworkManager needs no handoff; its
# next start will read the same persistent managed=0 rule.
if /usr/bin/systemctl --quiet is-active NetworkManager.service; then
    if [ ! -x /usr/bin/nmcli ]; then
        echo "NetworkManager is active but nmcli is unavailable" >&2
        exit 8
    fi
    /usr/bin/nmcli general reload conf
    nm_managed="$(/usr/bin/nmcli -g GENERAL.NM-MANAGED device show "$INTERFACE")"
    if [ "$nm_managed" != no ]; then
        echo "NetworkManager did not release $INTERFACE after configuration reload" >&2
        exit 8
    fi
fi

# systemd-networkd sees only usb0 because this package installs one exact
# match. RequiredForOnline=no prevents an absent recovery cable delaying boot.
networkd_was_enabled=false
if /usr/bin/systemctl --quiet is-enabled systemd-networkd.service; then
    networkd_was_enabled=true
fi
/usr/bin/systemctl enable --now systemd-networkd.service
if [ "$networkd_was_enabled" = false ]; then
    # Enabling networkd follows the unit's Also= relationship and otherwise
    # enables its wait-online helper too. This installer introduces only an
    # optional recovery link, so that helper would wait on an absent cable and
    # delay network-online.target. Preserve it only on hosts where networkd was
    # already an intentional network owner before this installation.
    /usr/bin/systemctl disable systemd-networkd-wait-online.service
fi
/usr/bin/networkctl reload
/usr/bin/networkctl reconfigure "$INTERFACE"

if ! /usr/bin/systemctl --quiet is-active systemd-networkd.service; then
    echo "systemd-networkd did not remain active" >&2
    exit 9
fi

echo "N.O.O.B. USB recovery enabled on $INTERFACE."
echo "Addressing comes only from DHCP, IPv6 RA, and IPv6 link-local; no static address was installed."
echo "usb0 routes use metric 100 while carrier is present and do not supply DNS."
/usr/sbin/ip -brief address show dev "$INTERFACE"
/usr/bin/networkctl --no-pager status "$INTERFACE"
