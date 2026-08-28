# ESP32-CAM hardware acceptance ladder

No item below is satisfied by the source implementation alone. Preserve a
full, hashed flash backup before the first firmware write.

## Identity and boot

- Record chip revision, flash manufacturer, non-destructive eFuse summary, and
  complete flash backup hash.
- Prove the detected flash is exactly 4 MiB.
- Prove the OTA table contains two 1700 KiB slots and the built image retains
  15-20% slot headroom.
- Remove IO0-GND after flashing and prove 20 cold boots with TX/RX disconnected
  and validated 5 V/GND power only.

## Camera

- Record actual sensor PID/name. Require PID `0x26` before saying OV2640.
- Record actual PSRAM initialization and size.
- Decode 100 consecutive VGA JPEG snapshots and confirm dimensions/markers.
- Move the scene and prove frame sequences and hashes change.
- Run MJPEG through the uConsole gateway for 30 minutes, then a 24-hour soak.
- Perform 100 sensor off/on cycles with no stale frame represented as live, no
  monotonic heap loss, and no deinit/reinit failure.
- Verify only one camera upstream stream exists while two operator views fan out
  from the gateway.

## Provisioning and network

- Reject wrong WPA2 key, PoP, and API token without leaking credential data.
- Prove the provisioning AP stops after success.
- Prove mDNS discovery and manual IP both lead to the same pinned device ID.
- Remove/restart the AP and prove bounded reconnect without credential erase or
  reset loop.
- Packet-capture a soak and prove there are no public DNS, HTTP, NTP, telemetry,
  OTA, or cloud connections.

## microSD

- Physically verify 1-bit pin wiring and required pull-ups.
- Store/read/hash/delete a snapshot and a 30-second, 5-FPS, 150-frame clip.
- Fill to each retention threshold and prove only oldest completed objects are
  deleted.
- Test absent, read-only, full, unsupported, and removed-during-write cards.
- Interrupt power during snapshot and clip writes; prove partial objects are not
  listed and no format is attempted on reboot.
- Prove an SD failure cannot kill live streaming or management status.

## Power and watchdog

- Validate the actual regulated 5 V source under simultaneous Wi-Fi, camera,
  and SD writes; a USB-TTL adapter's 5 V pin is not accepted without measured
  transient and reverse-power behavior.
- Inject capture, network, and SD stalls. Prove bounded subsystem recovery or a
  documented watchdog reset reason without a reset storm.
- Record minimum internal heap, PSRAM free space, camera reinits, SD errors,
  disconnects, and reset reason across the 24-hour soak.

## Gateway/operator proof

- Keep target HDMI readiness independent from environmental-camera readiness.
- From both the Electron and uConsole views, prove enable/disable, fresh view,
  zoom/fullscreen, local screenshot, camera-stored snapshot, clip start/stop,
  storage browsing, and free-space truth.
- Prove a camera-off or SD-full condition never routes keyboard/mouse input to
  the wrong target or makes target-video proof green.
