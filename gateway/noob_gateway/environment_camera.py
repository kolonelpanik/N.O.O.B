"""Optional, fixed-upstream ESP32 environmental camera gateway.

This module deliberately owns every upstream path.  Public callers select only
bounded operations and opaque object IDs; they never provide a URL, hostname,
filesystem path, redirect target, or camera credential.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import re
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

import aiohttp

from .auth import load_token
from .config import EnvironmentCameraConfig
from .video import JPEGStreamParser, jpeg_dimensions

MEDIA_ID_RE = re.compile(r"^m_[0-9a-f]{32}$")
JOB_ID_RE = re.compile(r"^j_[0-9a-f]{32}$")
DEVICE_ID_RE = re.compile(r"^cam_[0-9a-f]{16}$")
BOOT_ID_RE = re.compile(r"^b_[0-9a-f]{16}$")
CURSOR_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

V1_FRAME_WIDTH = 640
V1_FRAME_HEIGHT = 480
V1_MAX_JPEG_BYTES = 256 * 1024
V1_FRESH_FRAME_MAX_AGE_MS = 2000
V1_CONFIGURED_PINMAP = "ai_thinker_candidate"
V1_SENSOR_NAME = "OV2640"
V1_SENSOR_PID = 0x26

_STATUS_PATH = "/api/v1/status"
_STATE_PATH = "/api/v1/camera/state"
_SNAPSHOT_PATH = "/api/v1/camera/snapshot.jpg"
_STREAM_PATH = "/api/v1/camera/stream.mjpg"
_STORAGE_PATH = "/api/v1/storage"
_MEDIA_PATH = "/api/v1/media"
_SNAPSHOT_STORAGE_PATH = "/api/v1/storage/snapshots"
_CLIP_STORAGE_PATH = "/api/v1/storage/clips"


class EnvironmentCameraError(RuntimeError):
    def __init__(self, code: str, *, status: int = 503) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class EnvironmentCameraNotConfigured(EnvironmentCameraError):
    def __init__(self) -> None:
        super().__init__("camera_not_configured", status=409)


class EnvironmentCameraGenerationConflict(EnvironmentCameraError):
    def __init__(self) -> None:
        super().__init__("camera_generation_conflict", status=409)


class EnvironmentCameraViewerLimit(EnvironmentCameraError):
    def __init__(self) -> None:
        super().__init__("camera_viewer_limit", status=503)


@dataclass(frozen=True, slots=True)
class EnvironmentFrame:
    data: bytes
    sequence: int
    captured_at: float
    generation: int
    width: int
    height: int


def valid_media_id(value: str) -> bool:
    return bool(MEDIA_ID_RE.fullmatch(value))


def valid_job_id(value: str) -> bool:
    return bool(JOB_ID_RE.fullmatch(value))


def valid_cursor(value: str) -> bool:
    return bool(CURSOR_RE.fullmatch(value))


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EnvironmentCameraError("camera_bad_response", status=502)
    if not minimum <= value <= maximum:
        raise EnvironmentCameraError("camera_bad_response", status=502)
    return value


def _nullable_bounded_int(
    value: Any, name: str, minimum: int, maximum: int
) -> int | None:
    if value is None:
        return None
    return _bounded_int(value, name, minimum, maximum)


def _bounded_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise EnvironmentCameraError("camera_bad_response", status=502)
    return value


def _bounded_text(value: Any, name: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise EnvironmentCameraError("camera_bad_response", status=502)
    return value


def _nullable_bounded_text(value: Any, name: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, name, maximum)


def _nullable_ipv4(value: Any) -> str | None:
    if value is None:
        return None
    text = _bounded_text(value, "ipv4", 15)
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        raise EnvironmentCameraError("camera_bad_response", status=502) from None
    if address.version != 4:
        raise EnvironmentCameraError("camera_bad_response", status=502)
    return str(address)


def _optional_error_code(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not ERROR_CODE_RE.fullmatch(value):
        return "camera_reported_error"
    return value


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EnvironmentCameraError("camera_bad_response", status=502)
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode_json(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-standard JSON number")
            ),
        )
    except (UnicodeError, ValueError):
        raise EnvironmentCameraError("camera_bad_response", status=502) from None
    return _object(value)


def _status_evidence(
    payload: dict[str, Any], camera: dict[str, Any], *, expected_host: str | None
) -> dict[str, Any]:
    """Validate and retain the camera firmware v1 hardware proof surface.

    False evidence remains observable so an authenticated operator can diagnose
    and retry a corrected sensor. Contradictory or non-v1 claims fail closed.
    """

    provisioning = _object(payload.get("provisioning"))
    provisioned = _bounded_bool(provisioning.get("provisioned"), "provisioned")
    provisioning_active = _bounded_bool(provisioning.get("active"), "active")

    wifi = _object(payload.get("wifi"))
    wifi_state = _bounded_text(wifi.get("state"), "wifi.state", 32)
    wifi_ipv4 = _nullable_ipv4(wifi.get("ipv4"))
    wifi_rssi_dbm = _nullable_bounded_int(
        wifi.get("rssi_dbm"), "wifi.rssi_dbm", -127, 0
    )

    configured_pinmap = _bounded_text(
        camera.get("configured_pinmap"), "configured_pinmap", 64
    )
    if configured_pinmap != V1_CONFIGURED_PINMAP:
        raise EnvironmentCameraError("camera_v1_contract_mismatch", status=502)
    pinmap_verified = _bounded_bool(
        camera.get("pinmap_verified"), "pinmap_verified"
    )

    sensor = _object(camera.get("sensor"))
    sensor_detected = _bounded_bool(sensor.get("detected"), "sensor.detected")
    sensor_name = _nullable_bounded_text(sensor.get("name"), "sensor.name", 64)
    sensor_pid = _nullable_bounded_int(sensor.get("pid"), "sensor.pid", 0, 0xFFFF)
    ov2640_verified = _bounded_bool(
        sensor.get("ov2640_verified"), "sensor.ov2640_verified"
    )
    if sensor_detected != (sensor_name is not None and sensor_pid is not None):
        raise EnvironmentCameraError("camera_v1_contract_mismatch", status=502)
    sensor_verified = bool(
        sensor_detected
        and sensor_name == V1_SENSOR_NAME
        and sensor_pid == V1_SENSOR_PID
        and ov2640_verified
    )
    if ov2640_verified and not sensor_verified:
        raise EnvironmentCameraError("camera_v1_contract_mismatch", status=502)

    psram = _object(camera.get("psram"))
    psram_initialized = _bounded_bool(psram.get("initialized"), "psram.initialized")
    psram_size_bytes = _bounded_int(
        psram.get("size_bytes"), "psram.size_bytes", 0, 1 << 30
    )
    if psram_initialized != (psram_size_bytes > 0):
        raise EnvironmentCameraError("camera_v1_contract_mismatch", status=502)
    psram_verified = psram_initialized and psram_size_bytes > 0

    width = _nullable_bounded_int(
        camera.get("width"), "width", V1_FRAME_WIDTH, V1_FRAME_WIDTH
    )
    height = _nullable_bounded_int(
        camera.get("height"), "height", V1_FRAME_HEIGHT, V1_FRAME_HEIGHT
    )
    pixel_format = _nullable_bounded_text(
        camera.get("pixel_format"), "pixel_format", 16
    )
    if (width is None, height is None, pixel_format is None).count(True) not in {0, 3}:
        raise EnvironmentCameraError("camera_v1_contract_mismatch", status=502)
    if pixel_format is not None and pixel_format != "jpeg":
        raise EnvironmentCameraError("camera_v1_contract_mismatch", status=502)

    frame_sequence = _bounded_int(
        camera.get("frame_sequence"), "frame_sequence", 0, 1 << 62
    )
    last_frame_age_ms = _nullable_bounded_int(
        camera.get("last_frame_age_ms"),
        "last_frame_age_ms",
        0,
        1 << 62,
    )
    fresh = _bounded_bool(camera.get("fresh"), "fresh")
    enabled = _bounded_bool(camera.get("enabled"), "enabled")
    initialized = _bounded_bool(camera.get("initialized"), "initialized")

    exact_frame_claim = bool(
        enabled
        and initialized
        and width == V1_FRAME_WIDTH
        and height == V1_FRAME_HEIGHT
        and pixel_format == "jpeg"
        and last_frame_age_ms is not None
        and last_frame_age_ms <= V1_FRESH_FRAME_MAX_AGE_MS
    )
    if fresh and not exact_frame_claim:
        raise EnvironmentCameraError("camera_v1_contract_mismatch", status=502)
    if not enabled and (initialized or fresh or width is not None):
        raise EnvironmentCameraError("camera_v1_contract_mismatch", status=502)

    configured_address = None
    if expected_host is not None:
        try:
            configured_address = ipaddress.ip_address(expected_host)
        except ValueError:
            # Strict configuration normally makes this unreachable; direct
            # construction in tests must still fail closed rather than guess.
            raise EnvironmentCameraError("camera_bad_response", status=502) from None
    address_matches = bool(
        configured_address is None
        or configured_address.version == 6
        or wifi_ipv4 == str(configured_address)
    )
    network_verified = bool(
        provisioned
        and not provisioning_active
        and wifi_state == "connected"
        and address_matches
    )
    hardware_verified = bool(pinmap_verified and sensor_verified and psram_verified)
    if fresh and not hardware_verified:
        raise EnvironmentCameraError("camera_v1_contract_mismatch", status=502)

    return {
        "provisioned": provisioned,
        "provisioning_active": provisioning_active,
        "wifi": {
            "state": wifi_state,
            "ipv4": wifi_ipv4,
            "rssi_dbm": wifi_rssi_dbm,
        },
        "network_verified": network_verified,
        "configured_pinmap": configured_pinmap,
        "pinmap_verified": pinmap_verified,
        "sensor": {
            "detected": sensor_detected,
            "name": sensor_name,
            "pid": sensor_pid,
            "ov2640_verified": ov2640_verified,
        },
        "psram": {
            "initialized": psram_initialized,
            "size_bytes": psram_size_bytes,
        },
        "hardware_verified": hardware_verified,
        "reported_frame": {
            "sequence": frame_sequence,
            "width": width,
            "height": height,
            "pixel_format": pixel_format,
            "last_frame_age_ms": last_frame_age_ms,
            "fresh": fresh,
            "v1_fresh_verified": bool(
                fresh
                and exact_frame_claim
                and hardware_verified
                and network_verified
            ),
        },
    }


def _storage_view(raw: Any) -> dict[str, Any]:
    value = _object(raw)
    state = value.get("state")
    if state not in {
        "unconfigured",
        "mounting",
        "mounted",
        "absent",
        "read_only",
        "full",
        "error",
    }:
        raise EnvironmentCameraError("camera_bad_response", status=502)
    limits = _object(value.get("limits"))
    return {
        "state": state,
        "mounted": _bounded_bool(value.get("mounted"), "mounted"),
        "writable": _bounded_bool(value.get("writable"), "writable"),
        "total_bytes": _nullable_bounded_int(
            value.get("total_bytes"), "total_bytes", 0, 1 << 50
        ),
        "free_bytes": _nullable_bounded_int(
            value.get("free_bytes"), "free_bytes", 0, 1 << 50
        ),
        "reserve_bytes": _bounded_int(
            value.get("reserve_bytes"), "reserve_bytes", 0, 1 << 40
        ),
        "media_count": _bounded_int(
            value.get("media_count"), "media_count", 0, 10_000_000
        ),
        "active_job_id": _optional_job_id(value.get("active_job_id")),
        "limits": {
            "max_media_items": _bounded_int(
                limits.get("max_media_items"), "max_media_items", 0, 10_000_000
            ),
            "max_total_bytes": _bounded_int(
                limits.get("max_total_bytes"), "max_total_bytes", 0, 1 << 50
            ),
            "max_clip_duration_ms": _bounded_int(
                limits.get("max_clip_duration_ms"),
                "max_clip_duration_ms",
                1000,
                30_000,
            ),
            "max_clip_fps": _bounded_int(
                limits.get("max_clip_fps"), "max_clip_fps", 1, 5
            ),
            "max_clip_frames": _bounded_int(
                limits.get("max_clip_frames"), "max_clip_frames", 1, 150
            ),
        },
        "last_error": _optional_error_code(value.get("last_error")),
    }


def _optional_job_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not valid_job_id(value):
        raise EnvironmentCameraError("camera_bad_response", status=502)
    return value


def _optional_media_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not valid_media_id(value):
        raise EnvironmentCameraError("camera_bad_response", status=502)
    return value


def _media_item(raw: Any, *, max_media_bytes: int) -> dict[str, Any]:
    value = _object(raw)
    media_id = value.get("id")
    if not isinstance(media_id, str) or not valid_media_id(media_id):
        raise EnvironmentCameraError("camera_bad_response", status=502)
    kind = value.get("kind")
    if kind not in {"snapshot", "clip"} or value.get("state") != "complete":
        raise EnvironmentCameraError("camera_bad_response", status=502)
    created_at = value.get("created_at")
    if created_at is not None:
        created_at = _bounded_text(created_at, "created_at", 64)
    content_type = value.get("content_type")
    expected_content_type = (
        "image/jpeg" if kind == "snapshot" else "application/vnd.noob.clip+json"
    )
    if content_type != expected_content_type:
        raise EnvironmentCameraError("camera_bad_response", status=502)
    fps = _nullable_bounded_int(value.get("fps"), "fps", 1, 5)
    frame_count = _bounded_int(value.get("frame_count"), "frame_count", 1, 150)
    duration_ms = _bounded_int(value.get("duration_ms"), "duration_ms", 0, 30_000)
    if kind == "snapshot" and (frame_count != 1 or fps is not None):
        raise EnvironmentCameraError("camera_bad_response", status=502)
    if kind == "clip" and (fps is None or duration_ms < 1000):
        raise EnvironmentCameraError("camera_bad_response", status=502)
    return {
        "id": media_id,
        "kind": kind,
        "state": "complete",
        "created_at": created_at,
        "created_uptime_ms": _bounded_int(
            value.get("created_uptime_ms"), "created_uptime_ms", 0, 1 << 62
        ),
        "size_bytes": _bounded_int(
            value.get("size_bytes"), "size_bytes", 1, max_media_bytes
        ),
        "width": _bounded_int(
            value.get("width"), "width", V1_FRAME_WIDTH, V1_FRAME_WIDTH
        ),
        "height": _bounded_int(
            value.get("height"), "height", V1_FRAME_HEIGHT, V1_FRAME_HEIGHT
        ),
        "frame_count": frame_count,
        "fps": fps,
        "duration_ms": duration_ms,
        "content_type": content_type,
    }


def _job_view(raw: Any) -> dict[str, Any]:
    value = _object(raw)
    job_id = value.get("job_id")
    if not isinstance(job_id, str) or not valid_job_id(job_id):
        raise EnvironmentCameraError("camera_bad_response", status=502)
    if value.get("kind") != "clip":
        raise EnvironmentCameraError("camera_bad_response", status=502)
    state = value.get("state")
    if state not in {
        "queued",
        "running",
        "cancelling",
        "complete",
        "failed",
        "cancelled",
    }:
        raise EnvironmentCameraError("camera_bad_response", status=502)
    frames_written = _bounded_int(value.get("frames_written"), "frames_written", 0, 150)
    frames_target = _bounded_int(value.get("frames_target"), "frames_target", 1, 150)
    if frames_written > frames_target:
        raise EnvironmentCameraError("camera_bad_response", status=502)
    return {
        "job_id": job_id,
        "kind": "clip",
        "state": state,
        "created_uptime_ms": _bounded_int(
            value.get("created_uptime_ms"), "created_uptime_ms", 0, 1 << 62
        ),
        "frames_written": frames_written,
        "frames_target": frames_target,
        "media_id": _optional_media_id(value.get("media_id")),
        "error_code": _optional_error_code(value.get("error_code")),
    }


class EnvironmentCamera:
    """Own one optional ESP32 upstream and fan out its MJPEG frames."""

    def __init__(
        self,
        config: EnvironmentCameraConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        upstream_token: str | None = None,
        forbidden_tokens: Collection[str] = (),
    ) -> None:
        self.config = config
        self._clock = clock
        self._upstream_token = upstream_token
        self._forbidden_tokens = tuple(forbidden_tokens)
        self._session: aiohttp.ClientSession | None = None
        self._status_task: asyncio.Task[None] | None = None
        self._stream_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._activity = asyncio.Event()
        self._condition = asyncio.Condition()
        self._mutation_lock = asyncio.Lock()
        self._viewer_lock = asyncio.Lock()
        self._viewer_count = 0
        self._latest: EnvironmentFrame | None = None
        self._sequence = 0
        self._connection_epoch = 0
        self._public_generation = 0
        self._remote_generation = 0
        self._boot_id: str | None = None
        self._device_id: str | None = None
        self._observed_device_id: str | None = None
        self._identity_verified = False
        self._reachable = False
        self._remote_enabled = False
        self._remote_initialized = False
        self._remote_width: int | None = None
        self._remote_height: int | None = None
        self._evidence = self._empty_evidence()
        self._storage = self._empty_storage()
        self._last_error: str | None = None

    @staticmethod
    def _empty_evidence() -> dict[str, Any]:
        return {
            "provisioned": False,
            "provisioning_active": False,
            "wifi": {"state": None, "ipv4": None, "rssi_dbm": None},
            "network_verified": False,
            "configured_pinmap": None,
            "pinmap_verified": False,
            "sensor": {
                "detected": False,
                "name": None,
                "pid": None,
                "ov2640_verified": False,
            },
            "psram": {"initialized": False, "size_bytes": 0},
            "hardware_verified": False,
            "reported_frame": {
                "sequence": None,
                "width": None,
                "height": None,
                "pixel_format": None,
                "last_frame_age_ms": None,
                "fresh": False,
                "v1_fresh_verified": False,
            },
        }

    @staticmethod
    def _empty_storage() -> dict[str, Any]:
        return {
            "state": "unconfigured",
            "mounted": False,
            "writable": False,
            "total_bytes": None,
            "free_bytes": None,
            "reserve_bytes": 0,
            "media_count": 0,
            "active_job_id": None,
            "limits": {
                "max_media_items": 0,
                "max_total_bytes": 0,
                "max_clip_duration_ms": 30_000,
                "max_clip_fps": 5,
                "max_clip_frames": 150,
            },
            "last_error": None,
        }

    @property
    def configured(self) -> bool:
        return self.config.enabled

    @property
    def ready(self) -> bool:
        frame = self._latest
        return bool(
            self.config.enabled
            and self._reachable
            and self._identity_verified
            and self._evidence["hardware_verified"]
            and self._evidence["network_verified"]
            and self._remote_enabled
            and self._remote_initialized
            and frame is not None
            and frame.generation == self._public_generation
            and self._clock() - frame.captured_at
            <= min(
                self.config.stale_seconds,
                V1_FRESH_FRAME_MAX_AGE_MS / 1000,
            )
        )

    @property
    def status(self) -> dict[str, Any]:
        frame_age_ms = None
        if self._latest is not None:
            frame_age_ms = max(
                0, int((self._clock() - self._latest.captured_at) * 1000)
            )
        return {
            "configured": self.config.enabled,
            "reachable": self._reachable,
            "device_id": self._device_id,
            "expected_device_id": self.config.expected_device_id,
            "observed_device_id": self._observed_device_id,
            "identity_verified": self._identity_verified,
            "stream_enabled": self._remote_enabled,
            "sensor_enabled": self._remote_enabled,
            "sensor_initialized": self._remote_initialized,
            "power_control": False,
            "frame_ready": self.ready,
            "generation": self._public_generation,
            "sequence": self._latest.sequence if self._latest is not None else None,
            "width": self._latest.width
            if self._latest is not None
            else self._remote_width,
            "height": (
                self._latest.height if self._latest is not None else self._remote_height
            ),
            "last_frame_age_ms": frame_age_ms,
            "viewers": self._viewer_count,
            "provisioned": self._evidence["provisioned"],
            "provisioning_active": self._evidence["provisioning_active"],
            "wifi": dict(self._evidence["wifi"]),
            "network_verified": self._evidence["network_verified"],
            "configured_pinmap": self._evidence["configured_pinmap"],
            "pinmap_verified": self._evidence["pinmap_verified"],
            "sensor": dict(self._evidence["sensor"]),
            "psram": dict(self._evidence["psram"]),
            "hardware_verified": self._evidence["hardware_verified"],
            "reported_frame": dict(self._evidence["reported_frame"]),
            "storage": dict(self._storage),
            "last_error": self._last_error,
        }

    def _require_configured(self) -> None:
        if not self.config.enabled:
            raise EnvironmentCameraNotConfigured()

    def _base_url(self) -> str:
        self._require_configured()
        assert self.config.host is not None
        host = f"[{self.config.host}]" if ":" in self.config.host else self.config.host
        return f"http://{host}:{self.config.port}"

    def _headers(self, accept: str) -> dict[str, str]:
        if self._upstream_token is None:
            raise EnvironmentCameraError("camera_auth_unavailable", status=503)
        return {
            "Authorization": f"Bearer {self._upstream_token}",
            "Accept": accept,
            "Accept-Encoding": "identity",
            "Cache-Control": "no-store",
        }

    async def start(self) -> None:
        if not self.config.enabled or self._session is not None:
            return
        if (
            self.config.expected_device_id is None
            or DEVICE_ID_RE.fullmatch(self.config.expected_device_id) is None
        ):
            self._last_error = "camera_identity_unconfigured"
            return
        if self._upstream_token is None:
            assert self.config.token_file is not None
            try:
                self._upstream_token = load_token(self.config.token_file)
            except (OSError, ValueError):
                # The optional camera must not prevent target HDMI/HID startup.
                # Bounded status reports the isolated credential failure.
                self._last_error = "camera_auth_unavailable"
                return
        assert self._upstream_token is not None
        if any(
            hmac.compare_digest(self._upstream_token, token)
            for token in self._forbidden_tokens
        ):
            # Credential reuse would collapse the upstream and operator trust
            # boundaries. Keep the target gateway alive, but leave this
            # optional lane inert until it receives a dedicated secret.
            self._last_error = "camera_credential_reused"
            return
        connector = aiohttp.TCPConnector(
            limit=self.config.max_clients + 4,
            ttl_dns_cache=0,
            use_dns_cache=False,
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            cookie_jar=aiohttp.DummyCookieJar(),
            trust_env=False,
            auto_decompress=False,
        )
        self._stop.clear()
        self._status_task = asyncio.create_task(
            self._status_loop(), name="noob-environment-camera-status"
        )
        self._stream_task = asyncio.create_task(
            self._stream_loop(), name="noob-environment-camera-stream"
        )

    async def stop(self) -> None:
        self._stop.set()
        self._activity.set()
        tasks = [task for task in (self._status_task, self._stream_task) if task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._status_task = None
        self._stream_task = None
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._reachable = False
        self._latest = None

    async def _wait_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _status_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.refresh_status()
            except EnvironmentCameraError as exc:
                self._reachable = False
                self._last_error = exc.code
            except Exception:  # noqa: BLE001 -- isolate this optional subsystem
                self._reachable = False
                self._last_error = "camera_internal_error"
            await self._wait_or_stop(self.config.status_interval_seconds)

    async def _stream_loop(self) -> None:
        while not self._stop.is_set():
            if (
                not self._reachable
                or not self._remote_enabled
                or self._viewer_count == 0
                or not self._identity_verified
                or not self._evidence["hardware_verified"]
                or not self._evidence["network_verified"]
            ):
                self._activity.clear()
                try:
                    await asyncio.wait_for(self._activity.wait(), timeout=1.0)
                except TimeoutError:
                    pass
                continue
            try:
                await self._consume_stream()
            except EnvironmentCameraError as exc:
                self._last_error = exc.code
            except Exception:  # noqa: BLE001 -- isolate this optional subsystem
                self._last_error = "camera_internal_error"
            await self._wait_or_stop(self.config.reconnect_ms / 1000)

    async def _read_limited(
        self, response: aiohttp.ClientResponse, maximum: int
    ) -> bytes:
        if response.content_length is not None and response.content_length > maximum:
            response.close()
            raise EnvironmentCameraError("camera_response_too_large", status=502)
        data = bytearray()
        async for chunk in response.content.iter_chunked(min(64 * 1024, maximum + 1)):
            data.extend(chunk)
            if len(data) > maximum:
                response.close()
                raise EnvironmentCameraError("camera_response_too_large", status=502)
        return bytes(data)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        expected_status: Collection[int] = (200,),
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], int]:
        self._require_configured()
        if self._session is None:
            raise EnvironmentCameraError("camera_not_started", status=503)
        timeout = aiohttp.ClientTimeout(
            total=self.config.request_timeout_seconds,
            connect=self.config.connect_timeout_seconds,
        )
        try:
            async with self._session.request(
                method,
                self._base_url() + path,
                headers=self._headers("application/json"),
                json=body,
                params=params,
                allow_redirects=False,
                timeout=timeout,
            ) as response:
                if 300 <= response.status < 400:
                    raise EnvironmentCameraError("camera_redirect_rejected", status=502)
                raw = await self._read_limited(response, self.config.max_metadata_bytes)
                payload = _decode_json(raw)
                if response.status not in expected_status:
                    error = _object(payload.get("error", {}))
                    code = _optional_error_code(error.get("code"))
                    if response.status == 409 and code == "generation_conflict":
                        raise EnvironmentCameraGenerationConflict()
                    if response.status == 409 and code == "camera_disabled":
                        raise EnvironmentCameraError("camera_disabled", status=409)
                    if response.status == 401:
                        raise EnvironmentCameraError("camera_auth_failed", status=503)
                    if response.status == 404:
                        raise EnvironmentCameraError(
                            "camera_object_not_found", status=404
                        )
                    if response.status == 429:
                        raise EnvironmentCameraError("camera_busy", status=429)
                    raise EnvironmentCameraError(
                        code or "camera_upstream_error",
                        status=502 if response.status >= 500 else response.status,
                    )
                if response.content_type != "application/json":
                    raise EnvironmentCameraError("camera_bad_response", status=502)
                return payload, response.status
        except TimeoutError:
            raise EnvironmentCameraError("camera_timeout", status=504) from None
        except aiohttp.ClientError:
            raise EnvironmentCameraError("camera_unavailable", status=503) from None

    def _update_remote_generation(self, boot_id: str, generation: int) -> bool:
        if self._boot_id == boot_id and generation < self._remote_generation:
            # A status request which began before a completed mutation may
            # arrive afterward. Generations are monotonic within one boot, so
            # never roll authoritative state backward.
            return False
        changed = self._boot_id != boot_id or generation != self._remote_generation
        if self._boot_id is None:
            self._public_generation = generation
        elif boot_id != self._boot_id or generation != self._remote_generation:
            self._public_generation += 1
            self._latest = None
        if changed:
            self._connection_epoch += 1
        self._boot_id = boot_id
        self._remote_generation = generation
        return True

    async def _invalidate_authenticated_status(
        self, observed_device_id: str | None, error_code: str
    ) -> None:
        async with self._mutation_lock:
            self._observed_device_id = observed_device_id
            self._device_id = None
            self._identity_verified = False
            self._reachable = False
            self._remote_enabled = False
            self._remote_initialized = False
            self._remote_width = None
            self._remote_height = None
            self._latest = None
            self._evidence = self._empty_evidence()
            self._last_error = error_code
            self._connection_epoch += 1
            self._activity.set()

    async def refresh_status(self) -> dict[str, Any]:
        try:
            payload, _status = await self._request_json("GET", _STATUS_PATH)
        except EnvironmentCameraError:
            self._reachable = False
            self._activity.set()
            raise
        raw_device_id = payload.get("device_id")
        observed_device_id = (
            raw_device_id
            if isinstance(raw_device_id, str)
            and DEVICE_ID_RE.fullmatch(raw_device_id)
            else None
        )
        try:
            if payload.get("api") != 1:
                raise EnvironmentCameraError("camera_api_mismatch", status=502)
            if observed_device_id is None:
                raise EnvironmentCameraError("camera_bad_response", status=502)
            device_id = observed_device_id
            boot_id = payload.get("boot_id")
            if not isinstance(boot_id, str) or not BOOT_ID_RE.fullmatch(boot_id):
                raise EnvironmentCameraError("camera_bad_response", status=502)
            expected_device_id = self.config.expected_device_id
            if expected_device_id is None or not hmac.compare_digest(
                device_id, expected_device_id
            ):
                raise EnvironmentCameraError("camera_identity_mismatch", status=502)
            camera = _object(payload.get("camera"))
            evidence = _status_evidence(
                payload, camera, expected_host=self.config.host
            )
            generation = _bounded_int(
                camera.get("generation"), "generation", 0, 2_147_483_647
            )
        except EnvironmentCameraError as error:
            await self._invalidate_authenticated_status(
                observed_device_id, error.code
            )
            raise
        async with self._mutation_lock:
            if not self._update_remote_generation(boot_id, generation):
                return self.status
            self._observed_device_id = device_id
            self._device_id = device_id
            self._identity_verified = True
            self._remote_enabled = _bounded_bool(camera.get("enabled"), "enabled")
            self._remote_initialized = _bounded_bool(
                camera.get("initialized"), "initialized"
            )
            self._remote_width = evidence["reported_frame"]["width"]
            self._remote_height = evidence["reported_frame"]["height"]
            self._evidence = evidence
            self._storage = _storage_view(payload.get("storage"))
            self._last_error = _optional_error_code(camera.get("last_error"))
            if not self._remote_enabled:
                self._latest = None
            self._reachable = True
            self._activity.set()
        return self.status

    async def _ensure_status(self) -> None:
        if not self._reachable or self._boot_id is None:
            await self.refresh_status()

    async def _publish_frame(
        self,
        data: bytes,
        sequence: int | None = None,
        *,
        expected_epoch: int | None = None,
    ) -> EnvironmentFrame:
        dimensions = jpeg_dimensions(data)
        if (
            dimensions != (V1_FRAME_WIDTH, V1_FRAME_HEIGHT)
            or len(data) > min(self.config.max_frame_bytes, V1_MAX_JPEG_BYTES)
        ):
            raise EnvironmentCameraError("camera_bad_frame", status=502)
        width, height = dimensions
        async with self._mutation_lock:
            if not self._remote_enabled or (
                expected_epoch is not None and expected_epoch != self._connection_epoch
            ):
                raise EnvironmentCameraError("camera_generation_changed", status=409)
            if sequence is None:
                self._sequence += 1
            else:
                self._sequence = max(self._sequence + 1, sequence)
            frame = EnvironmentFrame(
                data=data,
                sequence=self._sequence,
                captured_at=self._clock(),
                generation=self._public_generation,
                width=width,
                height=height,
            )
            async with self._condition:
                self._latest = frame
                self._condition.notify_all()
        self._last_error = None
        return frame

    async def get_frame(self) -> EnvironmentFrame:
        self._require_configured()
        await self._ensure_status()
        if not self._remote_enabled:
            raise EnvironmentCameraError("camera_disabled", status=409)
        if not self._identity_verified:
            raise EnvironmentCameraError("camera_identity_mismatch", status=502)
        if not self._evidence["hardware_verified"]:
            raise EnvironmentCameraError("camera_hardware_unverified", status=503)
        if not self._evidence["network_verified"]:
            raise EnvironmentCameraError("camera_network_unverified", status=503)
        if self.ready and self._latest is not None:
            return self._latest
        if self._session is None:
            raise EnvironmentCameraError("camera_not_started", status=503)
        timeout = aiohttp.ClientTimeout(
            total=self.config.request_timeout_seconds,
            connect=self.config.connect_timeout_seconds,
        )
        expected_epoch = self._connection_epoch
        try:
            async with self._session.get(
                self._base_url() + _SNAPSHOT_PATH,
                headers=self._headers("image/jpeg"),
                allow_redirects=False,
                timeout=timeout,
            ) as response:
                if 300 <= response.status < 400:
                    raise EnvironmentCameraError("camera_redirect_rejected", status=502)
                if response.status == 409:
                    raise EnvironmentCameraError("camera_disabled", status=409)
                if response.status != 200 or response.content_type != "image/jpeg":
                    raise EnvironmentCameraError("camera_bad_response", status=502)
                data = await self._read_limited(
                    response, min(self.config.max_frame_bytes, V1_MAX_JPEG_BYTES)
                )
                raw_sequence = response.headers.get("X-NOOB-Frame-Sequence")
                sequence = None
                if raw_sequence is not None:
                    try:
                        sequence = int(raw_sequence)
                    except ValueError:
                        raise EnvironmentCameraError(
                            "camera_bad_response", status=502
                        ) from None
                    if not 0 <= sequence <= 1 << 62:
                        raise EnvironmentCameraError("camera_bad_response", status=502)
                boot_id = response.headers.get("X-NOOB-Boot-ID")
                if self._boot_id is not None and boot_id != self._boot_id:
                    raise EnvironmentCameraError("camera_restarted", status=409)
                self._reachable = True
                return await self._publish_frame(
                    data, sequence, expected_epoch=expected_epoch
                )
        except TimeoutError:
            raise EnvironmentCameraError("camera_timeout", status=504) from None
        except aiohttp.ClientError:
            raise EnvironmentCameraError("camera_unavailable", status=503) from None

    async def _consume_stream(self) -> None:
        assert self._session is not None
        timeout = aiohttp.ClientTimeout(
            total=None,
            connect=self.config.connect_timeout_seconds,
            sock_read=self.config.stream_read_timeout_seconds,
        )
        expected_epoch = self._connection_epoch
        parser = JPEGStreamParser(
            min(self.config.max_frame_bytes, V1_MAX_JPEG_BYTES)
        )
        try:
            async with self._session.get(
                self._base_url() + _STREAM_PATH,
                headers=self._headers("multipart/x-mixed-replace"),
                allow_redirects=False,
                timeout=timeout,
            ) as response:
                if 300 <= response.status < 400:
                    raise EnvironmentCameraError("camera_redirect_rejected", status=502)
                if response.status == 409:
                    raise EnvironmentCameraError("camera_disabled", status=409)
                if (
                    response.status != 200
                    or response.content_type != "multipart/x-mixed-replace"
                ):
                    raise EnvironmentCameraError("camera_bad_response", status=502)
                self._reachable = True
                async for chunk in response.content.iter_chunked(16 * 1024):
                    if (
                        self._stop.is_set()
                        or not self._reachable
                        or self._viewer_count == 0
                        or not self._remote_enabled
                        or expected_epoch != self._connection_epoch
                    ):
                        break
                    for frame in parser.feed(chunk):
                        await self._publish_frame(frame, expected_epoch=expected_epoch)
        except TimeoutError:
            raise EnvironmentCameraError("camera_stream_timeout", status=504) from None
        except aiohttp.ClientError:
            raise EnvironmentCameraError("camera_unavailable", status=503) from None

    async def acquire_viewer(self) -> None:
        self._require_configured()
        async with self._viewer_lock:
            if self._viewer_count >= self.config.max_clients:
                raise EnvironmentCameraViewerLimit()
            self._viewer_count += 1
        self._activity.set()

    async def release_viewer(self) -> None:
        async with self._viewer_lock:
            self._viewer_count = max(0, self._viewer_count - 1)
        self._activity.set()

    async def wait_for_frame(
        self, after_sequence: int, timeout: float = 5.0
    ) -> EnvironmentFrame | None:
        async with self._condition:
            if (
                self.ready
                and self._latest is not None
                and self._latest.sequence > after_sequence
            ):
                return self._latest
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(
                        lambda: (
                            self.ready
                            and self._latest is not None
                            and self._latest.sequence > after_sequence
                        )
                    ),
                    timeout,
                )
            except TimeoutError:
                return None
            return self._latest

    def _require_public_generation(self, expected_generation: int) -> int:
        if expected_generation != self._public_generation:
            raise EnvironmentCameraGenerationConflict()
        return self._remote_generation

    async def set_enabled(
        self, enabled: bool, expected_generation: int
    ) -> dict[str, Any]:
        self._require_configured()
        await self._ensure_status()
        async with self._mutation_lock:
            remote_generation = self._require_public_generation(expected_generation)
            payload, _status = await self._request_json(
                "PUT",
                _STATE_PATH,
                body={"enabled": enabled, "expected_generation": remote_generation},
            )
            returned_enabled = _bounded_bool(payload.get("enabled"), "enabled")
            returned_initialized = _bounded_bool(
                payload.get("initialized"), "initialized"
            )
            returned_generation = _bounded_int(
                payload.get("generation"), "generation", 0, 2_147_483_647
            )
            if returned_enabled != enabled:
                raise EnvironmentCameraError("camera_bad_response", status=502)
            boot_id = self._boot_id
            if boot_id is None:
                raise EnvironmentCameraError("camera_status_required", status=503)
            if not self._update_remote_generation(boot_id, returned_generation):
                raise EnvironmentCameraError("camera_bad_response", status=502)
            self._remote_enabled = returned_enabled
            self._remote_initialized = returned_initialized
            reported_frame = dict(self._evidence["reported_frame"])
            reported_frame.update(
                {
                    "sequence": None,
                    "width": None,
                    "height": None,
                    "pixel_format": None,
                    "last_frame_age_ms": None,
                    "fresh": False,
                    "v1_fresh_verified": False,
                }
            )
            self._evidence = {**self._evidence, "reported_frame": reported_frame}
            self._remote_width = None
            self._remote_height = None
            if not returned_enabled:
                self._latest = None
            self._activity.set()
            return self.status

    async def storage_status(self) -> dict[str, Any]:
        payload, _status = await self._request_json("GET", _STORAGE_PATH)
        self._storage = _storage_view(payload)
        return dict(self._storage)

    async def list_media(self, *, cursor: str | None, limit: int) -> dict[str, Any]:
        if cursor is not None and not valid_cursor(cursor):
            raise EnvironmentCameraError("bad_cursor", status=400)
        if not 1 <= limit <= self.config.max_page_size:
            raise EnvironmentCameraError("bad_range", status=400)
        params = {"limit": str(limit)}
        if cursor is not None:
            params["cursor"] = cursor
        payload, _status = await self._request_json("GET", _MEDIA_PATH, params=params)
        items_raw = payload.get("items")
        if not isinstance(items_raw, list) or len(items_raw) > limit:
            raise EnvironmentCameraError("camera_bad_response", status=502)
        next_cursor = payload.get("next_cursor")
        if next_cursor is not None and (
            not isinstance(next_cursor, str) or not valid_cursor(next_cursor)
        ):
            raise EnvironmentCameraError("camera_bad_response", status=502)
        return {
            "items": [
                _media_item(item, max_media_bytes=self.config.max_media_bytes)
                for item in items_raw
            ],
            "next_cursor": next_cursor,
        }

    async def get_media(self, media_id: str) -> dict[str, Any]:
        if not valid_media_id(media_id):
            raise EnvironmentCameraError("bad_media_id", status=400)
        payload, _status = await self._request_json("GET", f"{_MEDIA_PATH}/{media_id}")
        return _media_item(
            payload.get("item"), max_media_bytes=self.config.max_media_bytes
        )

    async def _get_jpeg_path(self, path: str) -> bytes:
        if self._session is None:
            raise EnvironmentCameraError("camera_not_started", status=503)
        timeout = aiohttp.ClientTimeout(
            total=self.config.request_timeout_seconds,
            connect=self.config.connect_timeout_seconds,
        )
        try:
            async with self._session.get(
                self._base_url() + path,
                headers=self._headers("image/jpeg"),
                allow_redirects=False,
                timeout=timeout,
            ) as response:
                if 300 <= response.status < 400:
                    raise EnvironmentCameraError("camera_redirect_rejected", status=502)
                if response.status == 404:
                    raise EnvironmentCameraError("camera_object_not_found", status=404)
                if response.status != 200 or response.content_type != "image/jpeg":
                    raise EnvironmentCameraError("camera_bad_response", status=502)
                data = await self._read_limited(
                    response, min(self.config.max_frame_bytes, V1_MAX_JPEG_BYTES)
                )
                if jpeg_dimensions(data) != (V1_FRAME_WIDTH, V1_FRAME_HEIGHT):
                    raise EnvironmentCameraError("camera_bad_frame", status=502)
                self._reachable = True
                return data
        except TimeoutError:
            raise EnvironmentCameraError("camera_timeout", status=504) from None
        except aiohttp.ClientError:
            raise EnvironmentCameraError("camera_unavailable", status=503) from None

    async def get_snapshot_content(self, media_id: str) -> bytes:
        item = await self.get_media(media_id)
        if item["kind"] != "snapshot":
            raise EnvironmentCameraError("media_not_snapshot", status=409)
        return await self._get_jpeg_path(f"{_MEDIA_PATH}/{media_id}/content")

    async def get_clip_frame(self, media_id: str, frame_index: int) -> bytes:
        if not valid_media_id(media_id):
            raise EnvironmentCameraError("bad_media_id", status=400)
        if not 0 <= frame_index <= 149:
            raise EnvironmentCameraError("bad_range", status=400)
        item = await self.get_media(media_id)
        if item["kind"] != "clip" or frame_index >= item["frame_count"]:
            raise EnvironmentCameraError("media_frame_not_found", status=404)
        return await self._get_jpeg_path(
            f"{_MEDIA_PATH}/{media_id}/frames/{frame_index}.jpg"
        )

    async def create_snapshot(self, expected_generation: int) -> dict[str, Any]:
        self._require_configured()
        await self._ensure_status()
        async with self._mutation_lock:
            remote_generation = self._require_public_generation(expected_generation)
            payload, _status = await self._request_json(
                "POST",
                _SNAPSHOT_STORAGE_PATH,
                expected_status={201},
                body={"expected_generation": remote_generation},
            )
            return _media_item(
                payload.get("item"), max_media_bytes=self.config.max_media_bytes
            )

    async def create_clip(
        self, *, duration_seconds: int, fps: int, expected_generation: int
    ) -> dict[str, Any]:
        self._require_configured()
        if not 1 <= duration_seconds <= self.config.max_clip_seconds:
            raise EnvironmentCameraError("bad_range", status=400)
        if not 1 <= fps <= self.config.max_clip_fps:
            raise EnvironmentCameraError("bad_range", status=400)
        if duration_seconds * fps > 150:
            raise EnvironmentCameraError("bad_range", status=400)
        await self._ensure_status()
        async with self._mutation_lock:
            remote_generation = self._require_public_generation(expected_generation)
            payload, _status = await self._request_json(
                "POST",
                _CLIP_STORAGE_PATH,
                expected_status={202},
                body={
                    "duration_ms": duration_seconds * 1000,
                    "fps": fps,
                    "expected_generation": remote_generation,
                },
            )
            job_id = payload.get("job_id")
            if (
                not isinstance(job_id, str)
                or not valid_job_id(job_id)
                or payload.get("state") != "queued"
            ):
                raise EnvironmentCameraError("camera_bad_response", status=502)
            return {"job_id": job_id, "state": "queued"}

    async def get_job(self, job_id: str) -> dict[str, Any]:
        if not valid_job_id(job_id):
            raise EnvironmentCameraError("bad_job_id", status=400)
        payload, _status = await self._request_json("GET", f"/api/v1/jobs/{job_id}")
        return _job_view(payload)

    async def stop_job(self, job_id: str) -> dict[str, Any]:
        if not valid_job_id(job_id):
            raise EnvironmentCameraError("bad_job_id", status=400)
        payload, _status = await self._request_json("DELETE", f"/api/v1/jobs/{job_id}")
        returned_id = payload.get("job_id")
        state = payload.get("state")
        if returned_id != job_id or state not in {"cancelling", "cancelled"}:
            raise EnvironmentCameraError("camera_bad_response", status=502)
        return {"job_id": job_id, "state": state}
