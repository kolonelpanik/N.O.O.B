# Appliance-local console

The uConsole can be both the gateway and a direct crash-cart screen. The local
console is a small always-on-top viewer that talks only to the gateway's
loopback API. It does not claim a network/Electron control lease and therefore
does not interrupt an operator or agent that is watching from another machine.

## Control model

- **Viewer open, controls disarmed:** the target video is visible on the
  uConsole; its keyboard and trackball still operate the uConsole desktop.
- **Local target control armed:** the gateway exclusively grabs the configured
  keyboard and trackball and forwards them through the same UART/Pico HID path
  as every other controller.
- **Remote control active:** the local viewer remains live and read-only. Its
  Arm action stays disabled until the remote lease ends.
- **Return to desktop:** the viewer confirms local disarm before minimizing.
- **Emergency return:** `Ctrl+Alt+Esc` is consumed by the gateway, releases HID,
  disarms local control, and returns the physical inputs to the uConsole. The
  chord is never sent to the target.

The local viewer and desktop Electron operator can display the same in-memory
capture simultaneously. Observation does not grant control; one input owner is
still enforced by the gateway lease.

Both interfaces also share one **Capture Output** selector. It lists only
profiles validated in the appliance configuration and changes the gateway's
single V4L2 stream; neither UI opens the capture device. Profile changes remain
available for recovery from a degraded video choice, but are blocked whenever
local or remote HID owns control. Target HDMI timing cannot be queried through
the reference adapter, so the operator selects output manually. The UI never
equates a “4K input” product label with 3840 × 2160 capture output.

## Install on the uConsole

From the checked-out repository:

```sh
sudo ./scripts/install_uconsole_ui.sh kali
```

The installer:

1. verifies `python3`, Tk, Pillow/ImageTk, `xdotool`, and XFCE tooling;
2. installs the local console under `/opt/noob/appliance`;
3. creates (or validates and preserves) a distinct mode-`0600`, `noob`-owned
   local-console credential under `/etc/noob/local-console.key`;
4. installs a narrow sudo rule that permits only reading that scoped credential
   as the `noob` service account;
5. adds a normal application-menu launcher; and
6. assigns **Super+N** only when that shortcut is currently unused.

The installer deliberately does not rewrite the gateway configuration or
restart a service that may currently own target input. Confirm the gateway is
already running a build with scoped local-console authentication, then add the
credential path to the existing `[auth]` table in `/etc/noob/noob.toml`:

```toml
[auth]
token_file = "/etc/noob/auth.key"
local_token_file = "/etc/noob/local-console.key"
```

Disarm every controller before the maintenance action, then restart and verify
the gateway explicitly:

```sh
sudo systemctl restart noob-gateway.service
sudo systemctl status noob-gateway.service --no-pager
curl --fail --silent http://127.0.0.1:8765/readyz
```

Do not restart while a key or mouse button is held. If readiness does not
return, inspect the service journal and follow [Recovery](recovery.md) before
arming local input.

The local-console token is cryptographically separate from the full operator
credential. The gateway accepts it only for status, current-frame/stream, and
built-in control arm/disarm endpoints, plus the validated capture-mode catalog
and global capture-mode switch. It cannot claim remote control, send arbitrary
HID commands, invoke emergency release, or select dimensions outside the
gateway's validated allowlist. It is read directly into the local viewer
process and is never placed in an argument, environment variable, URL, browser
storage, clipboard, or log. The client rejects non-loopback gateway origins
before constructing an authenticated request and refuses HTTP redirects or
proxies.

## Daily use

1. Press **Super+N** (or choose **N.O.O.B Local Console** in the application
   menu).
2. Confirm `VIDEO · LIVE` and `HID · READY`.
3. Select **ARM TARGET CONTROL**. The uConsole keyboard, trackball, dedicated
   left/right buttons, and middle ball-click now act on the target.
4. Press **Ctrl+Alt+Esc** when you need the uConsole desktop again.
5. Select **RETURN TO DESKTOP** or press **Super+N** to close the viewer after
   a confirmed disarm. Press **Super+N** again to reopen it.

To change capture resolution, first disarm every input controller, choose a
validated profile under **CAPTURE OUTPUT**, and wait for the requested and
negotiated values to settle. If they differ, the negotiated value is the
authoritative USB output. A failed selection rolls back server-side; choose the
safe 720p profile if the current mode remains degraded.

The window starts pinned above the XFCE desktop, supports a full-screen mode,
and never records frames. Closing the viewer confirms local disarm before it
exits; if release cannot be proven, the viewer stays visible and points the
operator to the emergency chord.
