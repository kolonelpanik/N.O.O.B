# Appliance-local console

The uConsole can be both the gateway and a direct crash-cart screen. The local
console is a small always-on-top viewer for target HDMI and the optional
environment camera. It talks only to the gateway's loopback API. It does not
claim a network/Electron control lease and therefore does not interrupt an
operator or agent that is watching from another machine.

## Control model

- **Viewer open, controls disarmed:** the target video is visible on the
  uConsole; its keyboard and trackball still operate the uConsole desktop.
- **Local target control armed:** the gateway exclusively grabs the configured
  keyboard and trackball and forwards them through the same UART/Pico HID path
  as every other controller.
- **Remote control active:** the local viewer remains live and read-only. Its
  Arm action stays disabled until the remote lease ends.
- **Return to desktop:** the viewer confirms local disarm before minimizing.
- **Environment view:** switching away from target HDMI first performs and
  confirms an authenticated local disarm, even if the last status sample said
  the controls were already disarmed. Target-control arming remains unavailable
  while the environment camera is selected.
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

## Environment camera and microSD

Choose **ENVIRONMENT CAMERA** to use the ESP32-CAM view. The camera badge keeps
configuration, reachability, logical stream state, and fresh-frame readiness
separate from target HDMI. **TURN CAMERA VIEW ON/OFF** changes the camera's
logical sensor/stream state only. The FTDI/power adapter remains energized; the
interface does not claim physical USB power control.

When the camera reports usable microSD storage, the appliance shows capacity,
free space, object count, and a bounded list of recent snapshots and clips.
**SNAPSHOT TO SD** requests one JPEG. **10S CLIP TO SD** starts a bounded,
two-frame-per-second camera job without freezing the viewer; the button becomes
**STOP CLIP** while the job is queued or running. Stop requests pass through
`cancelling` and converge on `cancelled`, removing the unpublished partial.
A completed clip is a catalogued frame collection, not an MP4. The local console does not
expose delete, format, or arbitrary-path operations. A completed job plus the
returned media metadata proves that the camera-storage service accepted and
catalogued the object; inspect the listed item before treating it as usable
evidence.

The **SCREENSHOT** button is different: it saves the currently displayed fresh
JPEG locally under `~/Pictures/N.O.O.B Screenshots`. The directory is mode
`0700`; each new, non-overwriting file is mode `0600`. Screenshots happen only
on this explicit operator action. **FIT**, **100%**, and **200%** control view
scale; drag the image to pan when the selected scale exceeds the viewport.

The viewer holds an owner-private advisory lock under the desktop user's
runtime directory. A duplicate launch fails before creating another Tk event
loop, polling client, or local-control lifecycle; the existing Super+N toggle
can then raise the already-running window.

## Install on the uConsole

From the checked-out repository:

```sh
sudo ./scripts/install_uconsole_ui.sh kali
```

The installer:

1. verifies `python3`, Tk, Pillow/ImageTk, `xdotool`, and XFCE tooling;
2. installs the local console and the root-owned `noob-pairing-code` identity
   helper under `/opt/noob/appliance`, with a terminal command at
   `/usr/local/bin/noob-pairing-code`;
3. creates (or validates and preserves) a distinct mode-`0600`, `noob`-owned
   local-console credential under `/etc/noob/local-console.key`;
4. installs a narrow sudo rule that permits only reading that scoped credential
   as the `noob` service account;
5. installs the branded icon, a normal application-menu launcher, and an
   executable **N.O.O.B Local Console** shortcut on the desktop as the desktop
   user (never as root); and
6. assigns **Super+N** only when that shortcut is currently unused.

The header always shows the public `PAIRING · 0000-0000` comparison code for
the current Ed25519 SSH host identity. Use that trusted, physically displayed
value during first connection. Run `noob-pairing-code` in a uConsole terminal
when the full advanced `SHA256:` fingerprint is also required for an audit.

XFCE may show its normal “untrusted application launcher” confirmation the
first time the desktop icon is opened when the filesystem does not support the
GVfs trust attribute. The installer attempts that metadata update as the
desktop user but does not weaken permissions if it is unavailable.

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
credential. The gateway accepts it only for status, target and environment
frames, built-in control arm/disarm, validated capture-mode selection, logical
environment-camera state, and bounded microSD list/snapshot/clip operations. It
cannot claim remote control, send arbitrary HID commands, invoke emergency
release, delete or format storage, download arbitrary paths, or select
dimensions outside the gateway's validated allowlist. It is read directly into
the local viewer process and is never placed in an argument, environment
variable, URL, browser storage, clipboard, or log. The client rejects
non-loopback gateway origins before constructing an authenticated request and
refuses HTTP redirects or proxies.

## Daily use

1. Press **Super+N**, double-click **N.O.O.B Local Console** on the desktop, or
   choose it from the application menu.
2. Confirm `VIDEO · LIVE` and `HID · READY`.
3. Select **ARM TARGET CONTROL**. The uConsole keyboard, trackball, dedicated
   left/right buttons, and middle ball-click now act on the target.
4. Press **Ctrl+Alt+Esc** when you need the uConsole desktop again.
5. Select **DESKTOP** or press **Super+N** to hide the viewer after a confirmed
   disarm. Press **Super+N** again to reopen it.

To change capture resolution, first disarm every input controller, choose a
validated profile under **CAPTURE OUTPUT**, and wait for the requested and
negotiated values to settle. If they differ, the negotiated value is the
authoritative USB output. A failed selection rolls back server-side; choose the
safe 720p profile if the current mode remains degraded.

The window starts pinned above the XFCE desktop. **FULL SCREEN** performs a
borderless re-map, sets the fullscreen/above hints, and explicitly requests the
physical screen geometry instead of accepting XFCE's panel-reduced work area.
The console checks its mapped origin and client size after entry and retries the
geometry if XFCE applies a late work-area constraint. The bottom rail remains
inside the full-screen client with always-visible **EXIT FULLSCREEN** and
**DESKTOP** controls. `Escape` exits full screen without changing target-input
ownership; **DESKTOP** confirms disarm, restores normal window geometry, and
hides the console. Closing the viewer also confirms local disarm before it
exits; if release cannot be proven, the viewer stays visible and points the
operator to the emergency chord.

### Live XFCE fullscreen acceptance

Automated tests prove the borderless remap order, exact `WIDTHxHEIGHT+0+0`
fallback, geometry verification, and restoration of the prior window geometry.
After installation, complete the window-manager acceptance on the real
uConsole:

1. Record the normal window size and position, then select **FULL SCREEN**.
2. Confirm no title bar, border, or XFCE panel is visible and the image area
   reaches all four physical edges.
3. Confirm **EXIT FULLSCREEN** and **DESKTOP** remain visible and clickable.
4. Select **EXIT FULLSCREEN** and confirm the prior decorated size and position
   return.
5. Enter full screen again, arm control, then select **DESKTOP**. Confirm target
   input is released, the normal geometry is restored, and the console hides.
6. Repeat once with `Escape`; verify it changes only window presentation. Use
   `Ctrl+Alt+Esc` separately to prove the HID emergency-release path.

While full screen is active, capture the X11 proof without changing state:

```sh
NOOB_XID="$(xdotool search --onlyvisible --class NoobLocalConsole | tail -n 1)"
test -n "$NOOB_XID"
xdotool getdisplaygeometry
xprop -id "$NOOB_XID" _NET_WM_STATE _NET_WM_WINDOW_TYPE
xwininfo -id "$NOOB_XID" | sed -n '/Absolute upper-left X:/p;/Absolute upper-left Y:/p;/Width:/p;/Height:/p;/Override Redirect State:/p'
wmctrl -lGx | grep -i 'NoobLocalConsole' || true
```

Acceptance requires `Map State: IsViewable`, an absolute client origin of
`0,0`, and client width/height equal to `xdotool getdisplaygeometry`. Before the
fallback, XFCE may expose `_NET_WM_STATE_FULLSCREEN`; after the borderless
override-redirect fallback is mapped, the window manager may remove or stop
reporting that hint because the client is deliberately outside normal WM
placement. In that fallback state, `Override Redirect State: yes` plus the
viewable physical-screen geometry is authoritative. An override-redirect
window may likewise be absent from `wmctrl -lGx`; if it is listed, its X/Y/W/H
must also match `0 0` and the display dimensions.

After **EXIT FULLSCREEN**, run the same `xprop` and `xwininfo` commands again.
`_NET_WM_STATE_FULLSCREEN` must be absent, override redirect must be `no`, and
the previously recorded decorated geometry must be restored. The local console
also issues fixed-argument `wmctrl` add/remove hints at runtime before applying
its Tk override/geometry fallback; no shell, window title, or external input is
interpolated into those commands.

Do not mark full-screen acceptance complete from unit tests alone: an actual
XFCE compositor/session is the authoritative proof for panel suppression,
physical-edge coverage, and visible recovery controls.
