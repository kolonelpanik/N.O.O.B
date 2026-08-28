# Environmental camera protocol v1

The camera is a private upstream device. A laptop UI, uConsole UI, ChatGPT app,
or Claude integration should call the N.O.O.B. gateway instead of connecting to
this API directly.

## Discovery and authentication

`GET /.well-known/noob-camera` is the only unauthenticated resource. It contains
the nonsecret device ID, role, API version/base, authentication scheme, and
capabilities. The same device advertises `_noobcam._tcp` with nonsecret TXT
fields. Neither mechanism proves trust; the gateway must authenticate and pin
the full device ID.

Every `/api/v1/*` request uses:

```http
Authorization: Bearer <unique per-device gateway token>
```

Tokens in a URI/query are rejected. Tokens, Wi-Fi credentials, frames, and
filenames are excluded from normal logs.

Errors use one stable envelope:

```json
{"error":{"code":"generation_conflict","message":"..."}}
```

Messages remain bounded and never contain a local path or underlying secret.

## Camera state and freshness

`GET /api/v1/status` reports the configured candidate pin map separately from
`pinmap_verified`, the detected sensor PID/name, PSRAM result, logical enable
state, generation, frame sequence, frame age, and freshness.

Sensor evidence is deliberately model-specific and model-neutral at the same
time. The detected `name`/`pid` pair must be exactly `OV2640`/`0x26` or
`OV3660`/`0x3660`, and `supported_sensor_verified` is true only for one of those
allowlisted pairs. The retained `ov2640_verified` field is true only for the
OV2640 pair and is always false for OV3660. Unknown sensors remain observable
in status but cannot set `pinmap_verified`, serve frames, or pass acceptance.

State mutation is generation checked:

```http
PUT /api/v1/camera/state
Content-Type: application/json

{"enabled":false,"expected_generation":3}
```

Unknown JSON fields are rejected. A stale generation returns `409
generation_conflict`. Disabling while a clip is active returns `409
recording_active`. When disabled, snapshot, stream, and new storage captures
return `409 camera_disabled`; old completed media and status remain available.

`GET /api/v1/camera/snapshot.jpg` returns a fresh JPEG, never base64. Response
headers include the boot ID and frame sequence. `GET
/api/v1/camera/stream.mjpg` permits one upstream gateway stream; a second stream
receives `429 stream_busy`. The stream runs as an asynchronous HTTP worker so
health/status, camera-off, and recording-stop requests remain reachable while
the gateway is consuming MJPEG.

## Media

Store the current fresh frame:

```http
POST /api/v1/storage/snapshots
Content-Type: application/json

{"expected_generation":3}
```

Start one bounded clip:

```http
POST /api/v1/storage/clips
Content-Type: application/json

{"duration_ms":10000,"fps":5,"expected_generation":3}
```

The clip request returns `202` with a `j_...` identifier. Poll
`GET /api/v1/jobs/{job_id}`. Stop the active clip with `DELETE` on the same job
URI; it first reports `cancelling`, removes the unpublished partial directory,
then converges to `cancelled`. Completed/failed jobs cannot be replayed or
cancelled.

List media with `GET /api/v1/media?limit=20&cursor=<opaque-media-id>`. The
cursor is opaque and page size is 1-50. Metadata is available at
`GET /api/v1/media/{media_id}`. Snapshot bytes use `/content`; clip frames use
`/frames/{index}.jpg`. Indices are bounded to 0-149 and checked against the
manifest. `DELETE /api/v1/media/{media_id}` deletes only that completed opaque
object.

No endpoint accepts a path, filename, root directory, arbitrary URL, recording
destination, or unbounded duration.
