# Appliance network resilience

N.O.O.B. keeps one recovery interface independent of NetworkManager so a
NetworkManager package, plugin, or profile failure cannot remove every path to
the uConsole. The reference split is deliberately narrow:

- `usb0` is owned by `systemd-networkd`;
- `wlan0` remains owned by NetworkManager;
- `usb0` accepts only DHCPv4, IPv6 router advertisements, and IPv6 link-local
  addressing;
- no static IPv4 address, gateway, DNS server, NTP server, search domain, or
  hostname is invented by N.O.O.B.; and
- Avahi advertises the existing `_noob-kvm._tcp` SSH hint on whichever usable
  interface is present.

This is a recovery path, not a second control plane. The gateway remains bound
to loopback, pairing still pins an independently verified SSH host key, and the
operator still uses an authenticated SSH forward.

## Why the USB route is preferred

The reference appliance can have `usb0` and `wlan0` on the same IPv4 subnet.
Routes learned on `usb0` use metric **100**, below the observed NetworkManager
Wi-Fi metric of 600. This makes replies to a USB-arriving SSH session leave on
USB instead of crossing over to Wi-Fi. Removing the cable removes the USB
route, so Wi-Fi resumes naturally. The installer does not create a default
route itself; it only assigns the metric to routes supplied by DHCP or IPv6 RA.

DHCP and RA DNS data are ignored on `usb0`, preventing a temporary recovery
cable from replacing the appliance's normal resolver configuration.

## Install

Keep an accepted local session or a separately proven management path open
during the first transition. Confirm that the interface is really `usb0`, then
run from the repository:

```sh
ip -brief link show usb0
sudo ./scripts/install_uconsole_network_resilience.sh --interface usb0
```

The installer refuses any other interface name, missing dependencies,
symbolic-link destinations, and pre-existing destination files it does not
own. It installs:

- `/etc/systemd/network/80-noob-usb-recovery.network`;
- `/etc/NetworkManager/conf.d/80-noob-usb-recovery.conf`; and
- `/opt/noob-network-resilience/docs/appliance-network-resilience.md`.

It enables `systemd-networkd`, reloads NetworkManager configuration without
restarting NetworkManager, and reconfigures only `usb0`. A brief address renewal
is possible during the first ownership transfer. If this installer is what
first enables networkd on the appliance, it also disables
`systemd-networkd-wait-online.service`; an optional recovery cable must never
delay `network-online.target`. A host that already used networkd retains its
existing wait-online policy.

## Proof

Do not treat an `active` service alone as acceptance. With both interfaces
connected, verify ownership, addressing, route selection, discovery, and SSH:

```sh
systemctl is-active systemd-networkd.service NetworkManager.service
systemctl is-enabled systemd-networkd-wait-online.service
networkctl --no-pager status usb0
nmcli -f DEVICE,TYPE,STATE,CONNECTION device status
ip -brief address show usb0
ip -4 route show dev usb0
ip -6 route show dev usb0
ip route get <operator-ip> from <usb0-ip>
avahi-browse --resolve --terminate _noob-kvm._tcp
ss -H -ltn
```

Require the `usb0` network file to resolve to
`/etc/systemd/network/80-noob-usb-recovery.network`, require NetworkManager to
report `usb0` as unmanaged while `wlan0` retains its saved connection, and
require the route to the USB-side operator to select `usb0`. The SSH discovery
port must still match a real non-loopback listener and its TXT surface must
remain secret-free.

On a reference appliance where this installer first introduced networkd,
require `systemd-networkd-wait-online.service` to be disabled. The USB network
file's `RequiredForOnline=no` remains a second guard; neither an absent adapter
nor a disconnected cable may delay boot.

Then test failure behavior:

1. Keep a harmless SSH session open over `usb0` and prove it remains usable
   while NetworkManager is stopped.
2. Start NetworkManager again and prove the same session still replies through
   `usb0` while Wi-Fi reconnects.
3. Unplug USB and prove its learned routes disappear and Wi-Fi remains usable.
4. Reconnect USB and prove DHCP or IPv6 link-local plus mDNS returns without a
   manual `ip address add` command.
5. Reboot once with USB attached and once with it detached. Neither boot may
   wait for the recovery cable.

IPv6 link-local addresses must retain an interface scope such as `%en0` when
entered manually. Prefer the resolved `.local` hostname or discovery result so
the client can preserve that scope correctly.

## Roll back

Rollback changes interface ownership and can end the current SSH connection.
Perform it from the uConsole desktop or through a separately proven path:

```sh
sudo rm -f \
  /etc/systemd/network/80-noob-usb-recovery.network \
  /etc/NetworkManager/conf.d/80-noob-usb-recovery.conf
sudo networkctl reload
sudo systemctl disable --now systemd-networkd.service
sudo systemctl reload NetworkManager.service
```

If the host already used `systemd-networkd` for another independently managed
interface, do **not** disable that service. Remove only the two N.O.O.B. files,
reload both managers, and verify the intended owner for every interface before
closing the recovery session.
