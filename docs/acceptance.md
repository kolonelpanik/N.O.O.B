# Acceptance procedure

Acceptance separates transport evidence from visible target behavior.

## Video

- Resolve the MacroSilicon capture through `/dev/v4l/by-id`.
- Require the `video-index0` node to advertise V4L2 capture.
- Capture one JPEG and visually identify current target content.
- Run a bounded sequential-frame test and record frame count, negotiated mode,
  elapsed time, and gaps.
- Confirm the image changes when the target display changes.
- List capture-output profiles and prove the UI exposes only entries explicitly
  validated for this appliance. Do not infer 4K output from a 4K-input label.
- With all input disarmed, switch each candidate profile. Record its requested
  and negotiated dimensions/fps, video generation, JPEG dimensions, frame age,
  restart counter, USB errors, CPU, and memory.
- Keep the local viewer and Electron stream open during a switch. Prove there
  remains exactly one V4L2 capture owner and that both clients reconnect to the
  same new generation.
- Attempt a stale-generation switch and a switch while local/remote input owns
  control; require deterministic rejection without disturbing video or HID.
- Force one unsupported/mismatched selection in a controlled test and verify
  stale frames disappear, the old process exits before replacement starts, and
  the last validated mode rolls back with fresh-frame proof.
- For every higher-resolution profile, run a 30–60 minute closed-case soak on
  the actual hub topology before treating enumeration as reliable operation.

## UART and Pico

- Observe the Pico `ready` event without transmitting a control command.
- Establish a fresh gateway-owned session.
- Require an ACK for session, ping, release-all, and each benign input test.
- Reject malformed JSON, invalid UTF-8, overlong lines, unknown fields, unknown
  keys, out-of-range mouse movement, and stale sessions.
- Replay one identical `(sid, seq)` request and prove it does not repeat the HID
  action.

## Target-visible HID

Use a new blank TextEdit document. Do not test destructive shortcuts.

- Type a unique benign nonce through the API and read it back through macOS
  Accessibility and the HDMI frame.
- Send a safe modifier combination and verify the visible result.
- Move the pointer by known relative deltas, click harmless blank content, and
  verify focus/pointer state through the captured frame.

An ACK proves only that the Pico accepted and executed a USB HID operation. The
visible frame or target Accessibility readback is the separate acceptance proof.

## Built-in uConsole controls

- Resolve the exact keyboard and pointer links under `/dev/input/by-id` and
  confirm both resolve to `root:input` event devices. Do not configure numeric
  `eventN` paths.
- Confirm `noob-gateway.service` has the `input` supplementary group.
- Before arm, prove the uConsole keyboard and trackball still control only the
  local appliance and `local_input.armed` is false.
- Authenticated-arm local input. Confirm both devices report an exclusive grab
  and one shared controller lease is used after the first physical event.
- Type a benign nonce with the built-in keyboard. Confirm exact target-visible
  characters, Shift/modifier behavior, Backspace, Enter, and arrow keys through
  the HDMI frame. Holding a key must rely on target-side repeat, not duplicate
  gateway down commands; releasing it must produce one key-up.
- Move the built-in trackball and verify proportional target pointer movement.
- Verify the dedicated left button emits target left down/up, the dedicated
  right button emits target right down/up, and the ball's `BTN_MIDDLE` emits
  target middle down/up.
- Drag harmless content with each applicable button and verify movement occurs
  between one down and one up. Kernel repeats and duplicate edges must not emit
  additional downs or strand any button held.
- Press `Ctrl+Alt+Esc`. Confirm it is not observed on the target, local input
  disarms, both evdev grabs release, and subsequent uConsole input stays local.
- While an HTTP/Electron controller lease is active, local input must not
  preempt it. A conflicting local event must fail closed and disarm.

## Failure injection

- Hold Shift, end the controller lease, and verify release.
- Hold a mouse button, disconnect the WebSocket, and verify release.
- Kill the gateway while a key is held and verify the Pico watchdog releases it.
- Unplug/replug the FT232H and require a new session before further input.
- Restart and reboot the uConsole, then verify both degraded startup and automatic
  recovery when the devices return.
- While a uConsole key is held, unplug either local evdev device (or stop the
  service) and verify `release_all`, disarm, and no automatic re-arm after the
  device reconnects.

## Network and logging

- `ss -lntp` must show the HTTP listener on loopback only.
- Direct LAN access must fail; SSH-forwarded access must succeed.
- If appliance discovery is enabled, `_noob-kvm._tcp` must resolve to the same
  non-loopback SSH port shown by `ss -H -ltn`. Reject startup when that listener
  is absent, loopback-only, or moved to another port.
- Confirm the `_noob-kvm._tcp` TXT record contains only the reviewed `api`,
  `product`, `version`, and `capabilities` keys. Discovery must contain no
  bearer, key, password, cookie, host-key fingerprint, gateway URL, camera
  address, or saved-device identity.
- Stop `noob-discovery.service`; require scan results to expire while manual
  private-address pairing remains usable. Confirm `_noobcam._tcp`, target
  video/HID, the local console, and existing SSH sessions are unaffected.
- Missing/incorrect tokens, oversized bodies, invalid content types, and absent
  controller leases must fail closed.
- Logs may contain operation names, byte counts, latency, and result codes, but
  never typed content, frames, credentials, cookies, or authorization headers.

## Optional environmental camera

- With the camera unconfigured, confirm target `/readyz`, video, UART, HID, and
  local input behave exactly as before.
- Configure only the camera's private IP literal and distinct mode-0600 bearer
  token file. Reject hostnames, URLs, loopback, link-local, public, multicast,
  and unknown configuration keys.
- Prove camera unreachable, disabled, initialized, fresh-frame, viewer,
  storage, and target-video states remain independent.
- Attempt a state mutation with a stale generation and require HTTP 409 without
  changing the sensor. Repeat after a camera reboot identity changes.
- With two gateway viewers, verify one ESP32 MJPEG upstream is fanned out and a
  third viewer above the configured bound is rejected.
- Return redirects, wrong content types, malformed JSON, duplicate JSON keys,
  malformed JPEGs, oversized frames, oversized metadata, and timeouts from a
  controlled test upstream; every case must fail closed without following a
  new location.
- List storage with the minimum/maximum page bounds. Reject duplicate query
  keys, invalid cursors, path traversal, noncanonical media IDs, and out-of-range
  clip frame indexes before an upstream request.
- Explicitly create one snapshot and one short clip. Prove clip capture is a
  bounded asynchronous job, media is retrievable only by opaque ID, and no
  delete/format/retention mutation route exists.
- Stop one active clip explicitly. Require `cancelling` then `cancelled`, no
  published media ID, removal of partial frames, idempotent repeated stop, and
  deterministic rejection after a job is already complete or failed.
- Disable the camera and prove the live stream closes, stale frames are not
  served, storage metadata remains independently reported when supported, and
  target `/readyz` remains unchanged.
- Confirm `power_control` remains false and operator copy describes logical
  sensor/stream state rather than electrical 5 V removal.
