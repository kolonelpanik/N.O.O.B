# N.O.O.B. environmental camera firmware

This directory is an isolated, source-only native ESP-IDF implementation for a
classic ESP32-CAM environmental feed. It is deliberately separate from the
uConsole gateway, Electron operator, target HDMI capture, and Pico HID firmware.

It has **not** been flashed to hardware by this change. The photographed module
is treated as an AI-Thinker pin-map candidate, not as a verified AI-Thinker
board. OV2640, working PSRAM, the candidate pins, the microSD slot, and the
available 5 V power remain hardware acceptance gates.

## Pinned baseline

- ESP-IDF `6.0.2`, target `esp32`.
- `espressif/esp32-camera == 2.1.7`.
- `espressif/mdns == 1.11.3`.
- `espressif/network_provisioning == 1.2.4`.
- `espressif/cjson == 1.7.19~2`.
- 4 MiB flash with two 1700 KiB OTA application slots.
- Bootloader application rollback; a pending image is confirmed only after the
  camera diagnostic gate and authenticated management startup both pass.
- VGA `640x480` JPEG, JPEG quality 12, two PSRAM frame buffers.
- 1-bit SDMMC, no automatic formatting.

The dependency manifest pins exact component versions. The first successful
official build should review and commit the generated `dependencies.lock` so
transitive resolution is also reproducible.

## Truth-first boot behavior

The firmware does not make a product claim from a board name or silkscreen. It:

1. proves the detected flash is exactly 4 MiB;
2. probes PSRAM and refuses VGA product mode without it;
3. attempts the documented AI-Thinker candidate pin map;
4. reads the sensor PID and requires `OV2640_PID` (`0x26`);
5. requires a bounded, marker-valid JPEG before setting `pinmap_verified=true`;
6. exposes failures independently through authenticated status if Wi-Fi can run.

The capture task is the only code that calls `esp_camera_fb_get()` and
`esp_camera_fb_return()`. HTTP and SD workers use copies of the newest validated
JPEG, so slow clients cannot retain the driver's frame buffers.

Disabling the camera stops the stream and clip entry points, deinitializes the
driver, and drives the verified candidate PWDN GPIO high. The MCU, Wi-Fi,
health/status API, and microSD browser remain online. This is sensor/capture
standby; it is not removal of 5 V power from the complete board.

## Network and authentication

First boot uses Espressif's local SoftAP network-provisioning component with:

- WPA2 SoftAP key;
- Security 1 X25519 + AES-CTR session protection;
- a unique per-device proof of possession;
- no credential values in logs.

Once provisioned, the camera joins the local 2.4 GHz N.O.O.B. network and
advertises `_noobcam._tcp`. The nonsecret well-known document supports both
mDNS discovery and manual-IP pairing. All `/api/v1/*` endpoints require the
per-device bearer token in the `Authorization` header. Tokens in query strings
are explicitly rejected. The intended client is the uConsole gateway, which
owns one upstream MJPEG connection and fans it out to human and agent clients.

There is no cloud service, public NTP pool, telemetry uploader, external OTA
URL, arbitrary URL fetch, shell, raw filesystem path, or unauthenticated media
endpoint in this source lane.

## Storage model

The reference board's built-in SD slot is mounted in 1-bit SDMMC mode. This uses
CLK/GPIO14, CMD/GPIO15, and D0/GPIO2 while avoiding GPIO4/flash-LED and the risky
GPIO12 VDD_SDIO strap. Board pull-ups must still be verified physically.

Completed objects live under `/NOOB/media/<opaque-id>/`. A new object is first
written under `.partial-<opaque-id>`, flushed and closed, given a bounded
manifest, and committed by directory rename. Incomplete directories are never
listed and are removed during the next bounded scan. Mount failure never causes
formatting.

A v1 clip is a transparent JPEG sequence plus manifest, not H.264 or MP4:

- 1-30 seconds;
- 1-5 frames per second;
- no more than 150 frames;
- one active clip worker;
- explicit stop/cancel plus automatic maximum-duration stop.

Retention deletes the oldest completed opaque objects only, using a persistent
ordinal. It never deletes the active `.partial` clip through the normal
retention path.

## Build-only quick start

1. Install ESP-IDF 6.0.2 with the Espressif Installation Manager or an official
   release checkout and activate its environment.
2. Copy no credential file into this repository. Use the bounded release
   configurator with an absolute owner-only material path outside the clone:

   ```sh
   python3 scripts/configure_release.py \
     --material-file "/absolute/owner-only/path/noob-cam.json" \
     --device-label noob-cam \
     --generate-if-missing
   ```

   It reconstructs the ignored mode-`0600` `sdkconfig` from the tracked
   `sdkconfig.defaults`, then inserts only the three per-device values in
   memory. Existing menuconfig drift is deliberately not inherited by a
   governed release.
3. Keep the generated `sdkconfig` local and ignored. The tracked defaults pin
   classic ESP32, DIO/40 MHz flash I/O, a 4 MiB image, `partitions.csv`, PSRAM
   support, bootloader rollback, and enabled interrupt/task watchdogs. The
   build refuses a local configuration that does not retain that complete
   contract.
4. Run:

   ```sh
   ./scripts/build_source.sh
   ```

The script builds and sizes the firmware but never invokes a flash command. It
rejects any ESP-IDF version other than 6.0.2 and rejects an application binary
above the source lane's 1445 KiB release-headroom ceiling. See
[`CONFIGURATION.md`](CONFIGURATION.md) for protected-material handling and
pairing-file generation.

## Runtime API

[`protocol/contract.json`](protocol/contract.json) is the machine-readable
bounded contract. [`PROTOCOL.md`](PROTOCOL.md) explains behavior and error
semantics. The camera uses opaque `m_...` media and `j_...` job identifiers;
callers never provide filenames or paths.

## Important non-claims

Source review or a successful host test does not prove:

- the physical carrier's pins;
- an OV2640 sensor;
- PSRAM existence or stability;
- SD pull-ups, card format, hot removal, or write endurance;
- 5 V supply quality during simultaneous Wi-Fi/camera/SD activity;
- sustained frame rate;
- repeated sensor PWDN/deinit/reinit stability;
- binary size before an actual IDF build;
- 24-hour soak, power-cut recovery, or power-only cold boots.

Those gates are enumerated in [`ACCEPTANCE.md`](ACCEPTANCE.md).
