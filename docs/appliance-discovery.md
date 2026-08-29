# Appliance discovery and SSH pairing

N.O.O.B. advertises one bounded local mDNS service so the Mac operator and MCP
clients can find the uConsole without a port scan:

```text
_noob-kvm._tcp.local
```

The SRV port is the uConsole's **actual SSH pairing port**. The gateway remains
bound to loopback and is never advertised as a direct LAN HTTP service. After
pairing, the client pins the independently verified SSH host key and forwards
the loopback gateway through SSH.

Discovery is only an untrusted routing hint. Independent SSH host-key
verification remains mandatory before pairing. The advertisement intentionally
contains no bearer, password, key, cookie, host-key fingerprint, saved-device
identifier, camera credential, or gateway URL. Its fixed TXT surface is:

```text
api=1
product=N.O.O.B.
version=0.2.0
capabilities=target-video,target-hid,ssh-forward
```

The environmental ESP32-CAM remains a different device and advertises
`_noobcam._tcp.local`. A `_noobcam` result must never be interpreted as a
uConsole SSH endpoint, and the uConsole never republishes the camera's bearer
or private upstream address.

For an appliance recovery path that remains available when NetworkManager is
unhealthy, install the exact `usb0` ownership split in
[Appliance network resilience](appliance-network-resilience.md). Discovery
continues to use the same bounded service and independent host-key proof on
that interface; it does not expose the loopback gateway.

## Install on the uConsole

This is a packaging procedure; the repository does not automatically mutate a
live appliance. On Kali/Debian, first install the reviewed OS dependencies:

```sh
sudo apt-get install avahi-daemon avahi-utils
```

Confirm SSH is listening on the intended LAN-reachable port, then install with
that port explicitly. Port 22 is an example, not an inferred default:

```sh
sudo ss -H -ltn
sudo ./scripts/install_uconsole_discovery.sh --ssh-port 22
```

The installer refuses missing dependencies, duplicate or invalid ports,
symbolic-link destinations, a missing `noob` service account, and any port that
does not have a non-loopback TCP listener. It installs:

- `/etc/default/noob-discovery` — isolated root-owned public port configuration;
- `/etc/systemd/system/noob-discovery.service` — the hardened publisher;
- `/opt/noob-discovery/appliance/noob_discovery_preflight.py` — the listener check;
- `/opt/noob-discovery/docs/appliance-discovery.md` — the on-device procedure.

The same preflight runs before every service start. If SSH later moves to a
different port or becomes loopback-only, systemd stops publishing the stale
endpoint and retries without weakening SSH or the gateway. Avahi may publish
both IPv4 and IPv6 results. Clients prefer RFC1918 IPv4, then IPv6 ULA, then the
resolved local hostname. A bare `fe80::/10` link-local address is rejected
because it has no interface scope; a scoped link-local address remains a
last-resort manual or discovery result after the hostname fallback.

## Validate without trusting discovery

On the uConsole:

```sh
systemctl --no-pager --full status noob-discovery.service
journalctl -u noob-discovery.service --since -10min --no-pager
avahi-browse --resolve --terminate _noob-kvm._tcp
```

Verify that the resolved port matches `ss -H -ltn`, and that the TXT keys are
exactly the non-secret fixed surface above. The advertiser process should run
as `noob`; `noob-gateway.service` should still listen only on loopback.

On macOS, browse and resolve the service separately:

```sh
dns-sd -B _noob-kvm._tcp local.
dns-sd -L "N.O.O.B. on <uconsole-hostname>" _noob-kvm._tcp local.
```

Stop each `dns-sd` command with Ctrl+C. Select the discovery result in the
operator, request its SSH host key, and compare the complete `SHA256:` value
against a separately trusted appliance record before pinning. TXT data is not
identity proof. If multicast DNS is filtered or unavailable, enter the private
uConsole address, local hostname, or a link-local address with its required
interface scope and SSH port manually; that fallback is a permanent supported
path. For example, an IPv6 link-local address must retain a scope such as
`%en0`; do not copy a bare `fe80::` value from discovery output.

## Roll back

The first rollback is reversible and leaves all installed files for review:

```sh
sudo systemctl disable --now noob-discovery.service
```

This removes only the mDNS hint. SSH, the loopback gateway, local console,
target video/HID, and `_noobcam._tcp` are unaffected. Re-enable the same
reviewed configuration with:

```sh
sudo systemctl enable --now noob-discovery.service
```

To change the advertised SSH port, first change and prove the real SSH
listener while retaining an accepted management session, then rerun the
installer with the new explicit port. Do not advertise a replacement until
independent SSH acceptance succeeds.
