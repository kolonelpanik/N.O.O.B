# Architecture

N.O.O.B (NEVER OUT OF BOUNDS) is a hardware KVM bridge. The target does not
need network access, an agent, a driver package, or an installed application.
It sees an ordinary USB keyboard and mouse plus its own HDMI display path.

```text
Operator or agent
      |
      | SSH tunnel + authenticated HTTP/WebSocket
      v
uConsole gateway (Kali Linux)
      |                         |
      | V4L2                    | 115200 8N1 UART
      v                         v
USB HDMI capture          Adafruit FT232H
      ^                         |
      | HDMI                    | D0/D1/GND only
      |                         v
Target computer <--------- Pico WH USB HID

uConsole keyboard + trackball
      |
      | explicit authenticated arm + exclusive evdev grab
      v
local-input adapter -------^ (same ControlLease and UART command path)
```

## Trust boundaries

1. **Network boundary.** The gateway binds only to loopback. Operators reach it
   through authenticated SSH forwarding and still present an application token.
2. **Command boundary.** The network API accepts a fixed JSON schema. It has no
   filesystem, process, shell, clipboard, or arbitrary USB-report endpoint.
3. **Serial boundary.** The gateway creates the UART session and sequence
   numbers. The Pico accepts a bounded protocol and acknowledges each command.
4. **USB boundary.** The Pico, not the gateway process, emits native USB HID.
   Its independent watchdog releases held input if the serial controller dies.
5. **Observation boundary.** Frames are held only in memory and are not recorded
   by default. A device node or UART ACK is never treated as proof that the
   target visibly accepted an action.
6. **Local-control boundary.** Only the two configured stable uConsole evdev
   identities are opened. They are not grabbed until an authenticated arm
   request. While armed, keyboard and trackball share the same exclusive lease
   as HTTP/Electron control; they cannot preempt another controller.
7. **Capture-mode boundary.** One gateway process owns V4L2. Operator clients
   select only configured MJPEG profile IDs; they cannot supply dimensions,
   frame rates, pixel formats, device paths, or capture command arguments.

## Proof ladder

The system reports the following states independently:

- SSH route and host identity
- stable capture and serial device identity
- fresh, decodable frames
- Pico `ready` and UART session
- command ACK/NACK
- target-visible keyboard and mouse effect
- service persistence after restart/reboot

The `/healthz` endpoint proves only that the HTTP process is alive. The
`/readyz` endpoint additionally requires a fresh video frame and an established
Pico UART session. When local input is enabled, it also requires both configured
evdev identities to be open. Neither endpoint alone proves a target-side action.

## Capture output profiles

The gateway exposes a small allowlist through `GET /api/v1/video/modes` and
changes the global output through `POST /api/v1/video/mode` with an expected
video generation. The local overlay and Electron are consumers of that same
fan-out; neither opens the capture device.

A switch is accepted only while input is unowned and local evdev is disarmed.
The gateway serializes the transition, makes stale frames unavailable, reaps
the old capture process, requests the chosen profile, reads back the negotiated
V4L2 format and frame interval, and dimension-checks a fresh JPEG. Only then is
the new generation ready. Failure triggers a bounded rollback to the previous
validated profile; rollback failure is reported as degraded rather than hidden
behind the old frame.

`requested` and `negotiated` status remain distinct because UVC devices may
coerce unsupported timing. Profile selection controls capture output—not the
target's HDMI setting. The reference adapter cannot query HDMI DV timing or
EDID, so automatic source matching is not claimed. A “4K” marketing label on a
capture adapter does not establish 3840 × 2160 USB output.

## Built-in uConsole controls

Local input defaults disabled. With `[local_input].enabled = true`, an operator
must still authenticate and explicitly arm it:

```http
POST /api/v1/local-input/arm
Authorization: Bearer <token>
Content-Type: application/json

{}
```

`POST /api/v1/local-input/disarm` uses the same authentication and empty JSON
body. `GET /api/v1/status` reports device readiness, armed/grab state, disarm
reason, and bounded error counters; it never reports typed keys or text.

Arming grabs the configured keyboard and pointer together with `EVIOCGRAB`.
This prevents the same movement or keypress from acting on both the uConsole
desktop and target. Local events are mapped to the canonical `key`,
`mouse_move`, and `mouse_button` envelopes, validated, and then delivered by
the existing gateway lease and UART ACK path. Unsupported evdev keys and all
kernel repeat events are ignored. A target key stays down from its exact
physical down transition until its exact up transition, allowing the target OS
to implement normal repeat without duplicate HID downs.

The reserved `Ctrl+Alt+Esc` emergency chord is buffered and never forwarded.
It disarms both devices, drops the exclusive grabs, and requests `release_all`
through the Pico. A normal modifier chord remains usable: pending emergency
keys are forwarded in order as soon as a non-emergency key makes the intent
unambiguous.

Trackball relative X/Y events are accumulated until `SYN_REPORT`, split into
the protocol's signed 8-bit movement bounds, and submitted in order. Physical
mouse buttons retain their stock identities: `BTN_LEFT` maps to target left,
`BTN_RIGHT` to target right, and the ball's `BTN_MIDDLE` to target middle.
Exact down/up transitions preserve native click, hold, and drag behavior.
Duplicate down/up edges and kernel repeats are suppressed. Device loss, failed
dispatch, serial uncertainty, explicit disarm, API emergency release, or
process shutdown sends the existing fail-closed release path; the Pico's
independent held-input timeout remains the outer safety bound.

## Input API envelopes

Authenticated controllers send input to `POST /api/v1/input` with their
`X-NOOB-Lease` header. The explicit canonical envelope remains supported:

```json
{"op":"type","text":"ls -la\n","interval_ms":0}
```

For Phase-4 agent compatibility, the HTTP boundary also accepts these bounded
forms and normalizes them before the UART protocol sees them:

```json
{"action":"type","text":"ls -la\n"}
{"action":"combo","keys":["GUI","SPACE"]}
```

An omitted type interval defaults to `0` ms, an omitted combo hold defaults to
`50` ms, and the generic `GUI` alias maps deterministically to `LEFT_GUI`.
Explicit `LEFT_GUI` and `RIGHT_GUI` keys retain their side. The adapter rejects
mixed `action`/`op` envelopes, unknown fields, duplicate normalized keys,
oversized text, and every value outside the canonical command bounds. Responses
retain the same `{ "ok": true, "result": ... }` ACK envelope.
