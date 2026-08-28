# Environmental camera gateway

The environmental camera is an optional, independent proof lane. It never
replaces the target HDMI capture and never participates in `/readyz`.

## Fixed upstream boundary

The gateway accepts only a configured RFC1918 IPv4 or unique-local IPv6
literal plus a port and the full, separately verified `cam_...` device ID. The
ESP32 bearer token is read from a separate mode-0600 file. Scheme and every
upstream path are protocol-owned constants; an API caller cannot provide a
URL, hostname, redirect, credential, filesystem path, or camera object name.

```toml
[environment_camera]
enabled = true
host = "192.168.50.84"
expected_device_id = "cam_0123456789abcdef"
port = 80
token_file = "/etc/noob/environment-camera.key"
```

An enabled lane refuses to start without an ID matching exactly
`cam_[0-9a-f]{16}`. Authenticated status is accepted only when its full
`device_id` equals that pin. mDNS and the unauthenticated well-known document
remain discovery hints; neither can replace this root-owned identity pin.

The camera token must be distinct from both gateway operator credentials. The
gateway checks both the configured paths and loaded token values; credential
reuse leaves only the optional camera lane inert and reports
`camera_credential_reused` without changing target readiness.
Outbound requests ignore proxy environment variables, request identity
encoding, reject redirects, and cap connection time, response time, metadata,
JPEGs, media size, viewers, page size, clip duration, and clip frame rate.

The gateway host remains loopback-only. `environment_camera.host` is the
deterministic manual-address surface for the camera upstream; it does not
change the N.O.O.B. appliance listener. mDNS discovery belongs in the client or
appliance pairing layer and must resolve to an explicitly trusted device before
changing this root-owned configuration.

## Public state

`GET /api/v1/status` adds `environment_camera` without changing the existing
`serial`, `video`, `local_input`, or `control` objects. A dedicated
`GET /api/v1/environment-camera/status` returns the same isolated state:

```json
{
  "configured": true,
  "reachable": true,
  "device_id": "cam_0123456789abcdef",
  "expected_device_id": "cam_0123456789abcdef",
  "observed_device_id": "cam_0123456789abcdef",
  "identity_verified": true,
  "stream_enabled": true,
  "sensor_enabled": true,
  "sensor_initialized": true,
  "power_control": false,
  "frame_ready": true,
  "generation": 9,
  "sequence": 321,
  "width": 640,
  "height": 480,
  "last_frame_age_ms": 82,
  "viewers": 1,
  "provisioned": true,
  "provisioning_active": false,
  "wifi": {
    "state": "connected",
    "ipv4": "192.168.50.84",
    "rssi_dbm": -48
  },
  "network_verified": true,
  "configured_pinmap": "ai_thinker_candidate",
  "pinmap_verified": true,
  "sensor": {
    "detected": true,
    "name": "OV2640",
    "pid": 38,
    "ov2640_verified": true
  },
  "psram": {
    "initialized": true,
    "size_bytes": 4194304
  },
  "hardware_verified": true,
  "reported_frame": {
    "sequence": 321,
    "width": 640,
    "height": 480,
    "pixel_format": "jpeg",
    "last_frame_age_ms": 82,
    "fresh": true,
    "v1_fresh_verified": true
  },
  "storage": {},
  "last_error": null
}
```

`power_control` is false for the reference USB-TTL power arrangement. Turning
the camera off is a logical sensor/stream operation; it does not remove 5 V.

The public generation remains monotonic across upstream state changes and
camera reboot identities. Mutations require the exact currently observed
generation so two operator interfaces cannot silently overwrite one another.

`reachable` proves an authenticated API response from the pinned ID. It does
not collapse the other proof fields: `network_verified`, `hardware_verified`,
and `reported_frame.v1_fresh_verified` retain the station/provisioning,
AI-Thinker candidate pin map, OV2640 PID/name, PSRAM, and exact 640 x 480 JPEG
evidence independently. A false proof remains visible for diagnosis, while a
contradictory non-v1 status claim or non-VGA JPEG is rejected.

## Bounded routes

All routes require gateway bearer authentication. The appliance-local token is
accepted only by the explicitly enumerated camera routes below; it still cannot
claim remote HID control or call emergency release.

| Method | Route | Contract |
| --- | --- | --- |
| `GET` | `/api/v1/environment-camera/status` | Isolated cached proof state |
| `POST` | `/api/v1/environment-camera/state` | Exact `{enabled, expected_generation}` |
| `GET` | `/api/v1/environment-camera/frame.jpg` | Fresh bounded JPEG |
| `GET` | `/api/v1/environment-camera/stream.mjpeg` | Gateway fan-out from one ESP32 MJPEG connection |
| `GET` | `/api/v1/environment-camera/storage?limit=1..50&cursor=...` | Storage state plus bounded media page |
| `GET` | `/api/v1/environment-camera/storage/{media_id}` | Metadata for one opaque ID |
| `GET` | `/api/v1/environment-camera/storage/{media_id}/content` | Snapshot JPEG only |
| `GET` | `/api/v1/environment-camera/storage/{media_id}/frames/{0..149}.jpg` | One bounded clip frame |
| `POST` | `/api/v1/environment-camera/snapshot` | Exact `{expected_generation}`; stores one explicit snapshot |
| `POST` | `/api/v1/environment-camera/clip` | Exact `{duration_seconds, fps, expected_generation}`; 1–30 seconds, 1–5 fps, maximum 150 frames |
| `GET` | `/api/v1/environment-camera/jobs/{job_id}` | Bounded asynchronous clip progress |
| `POST` | `/api/v1/environment-camera/jobs/{job_id}/stop` | Exact `{}`; idempotently converges an active clip to `cancelled` |

Media IDs are exactly `m_` plus 32 lowercase hexadecimal characters. Job IDs
are exactly `j_` plus 32 lowercase hexadecimal characters. Neither is a path.
The gateway exposes no delete, overwrite, retention mutation, format, arbitrary
download URL, or arbitrary storage root in v1.

Clip creation returns HTTP 202 with an opaque job ID. Operators poll the fixed
job route, then refresh storage after completion. The gateway never holds a
mutation request open for the entire capture interval and never retries an
ambiguous storage mutation.

The desktop operator's **Screenshot** action is intentionally outside this
storage API: it downloads the current authenticated gateway frame to the local
workstation. Only the separately labeled **Store camera snapshot** action calls
the microSD snapshot mutation. Agent clients retrieve an individual completed
clip frame only through the fixed opaque-ID route above, with the index bounded
to `0..149` and checked against the clip manifest before JPEG retrieval.

An explicit stop maps to the camera's authenticated job cancellation. The
camera checks cancellation between bounded frame waits, removes the unpublished
partial clip, and reports `cancelling` until the existing job endpoint reaches
`cancelled`. It does not delete already-published media. Completed or failed
jobs reject stop, while repeated stop of a cancelling/cancelled job is
idempotent. Every clip still auto-stops within 30 seconds if no stop arrives.

## Privacy and evidence

The target HDMI lane remains memory-only and is not automatically recorded.
Environmental media is written only after an explicit snapshot or bounded clip
request and remains on camera-owned storage. Stored imagery is sensitive and
must be represented as such in the operator interfaces.

Camera reachability, logical enabled state, fresh gateway frame, storage state,
and completed media are separate proofs. None proves target HDMI readiness,
UART health, HID acceptance, or electrical camera power removal.
