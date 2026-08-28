#!/usr/bin/env python3
"""Appliance-local N.O.O.B. viewer and control-mode switch.

The application is intentionally a viewer, not a second remote controller. It
never claims an HTTP input lease. Its only mutating actions arm or disarm the
uConsole's built-in keyboard and trackball through the gateway's existing
local-input endpoints.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, replace
from datetime import datetime
import fcntl
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import queue
import re
import stat
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable
from urllib import error, parse, request


APP_TITLE = "N.O.O.B Local Console"
DEFAULT_GATEWAY = "http://127.0.0.1:8765"
DEFAULT_SSH_HOST_PUBLIC_KEY = Path("/etc/ssh/ssh_host_ed25519_key.pub")
PAIRING_CODE_DOMAIN_SEPARATOR = b"N.O.O.B. pairing code v1\0"
MAX_SSH_PUBLIC_KEY_BYTES = 16 * 1024
DEFAULT_TOKEN_COMMAND = (
    "/usr/bin/sudo",
    "-n",
    "-u",
    "noob",
    "/bin/cat",
    "/etc/noob/local-console.key",
)
ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MODE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
CURSOR_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MEDIA_ID = re.compile(r"^m_[0-9a-f]{32}$")
JOB_ID = re.compile(r"^j_[0-9a-f]{32}$")
MAX_FRAME_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_STORAGE_RESPONSE_BYTES = 128 * 1024
MAX_STORAGE_ITEMS = 50
MAX_CAMERA_CLIP_FRAME_INDEX = 149
VIDEO_MODE_REQUEST_TIMEOUT = 65.0
CAMERA_ACTION_TIMEOUT = 35.0
VIEW_SOURCES = frozenset(("target", "environment"))
ZOOM_MODES = ("FIT", "100%", "200%")
VIDEO_STATES = frozenset(
    {
        "starting",
        "ready",
        "switching",
        "reconnecting",
        "rolling_back",
        "rolled_back",
        "degraded",
        "stopped",
    }
)

BG = "#080d11"
SURFACE = "#0d1419"
SURFACE_RAISED = "#111a20"
BORDER = "#27343b"
TEXT = "#f3f7f8"
MUTED = "#8c9aa2"
SIGNAL = "#39e1df"
HEALTHY = "#29d17d"
DANGER = "#ff3a42"
WARN = "#f2b84b"


class LocalConsoleInstanceLock:
    def __init__(self, descriptor: int, path: Path) -> None:
        self._descriptor = descriptor
        self.path = path

    def close(self) -> None:
        descriptor, self._descriptor = self._descriptor, -1
        if descriptor < 0:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> LocalConsoleInstanceLock:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def user_runtime_directory(
    *,
    environ: dict[str, str] | None = None,
    uid: int | None = None,
) -> Path:
    values = os.environ if environ is None else environ
    current_uid = os.getuid() if uid is None else uid
    configured = values.get("XDG_RUNTIME_DIR", "").strip()
    try:
        runtime = Path(configured) if configured else Path(f"/run/user/{current_uid}")
    except (TypeError, ValueError) as exc:
        raise LocalConsoleError("instance_lock_unavailable") from exc
    if not runtime.is_absolute():
        raise LocalConsoleError("instance_lock_unavailable")
    try:
        details = runtime.lstat()
    except (OSError, ValueError) as exc:
        raise LocalConsoleError("instance_lock_unavailable") from exc
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != current_uid
        or stat.S_IMODE(details.st_mode) & 0o077
    ):
        raise LocalConsoleError("instance_lock_unavailable")
    return runtime


def acquire_local_console_instance_lock(
    runtime_directory: Path | None = None,
) -> LocalConsoleInstanceLock:
    runtime = user_runtime_directory() if runtime_directory is None else runtime_directory
    lock_path = runtime / "noob-local-console.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            raise LocalConsoleError("instance_lock_unavailable")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LocalConsoleError("already_running") from exc
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        return LocalConsoleInstanceLock(descriptor, lock_path)
    except LocalConsoleError:
        if "descriptor" in locals():
            os.close(descriptor)
        raise
    except OSError as exc:
        if "descriptor" in locals():
            os.close(descriptor)
        raise LocalConsoleError("instance_lock_unavailable") from exc


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _bounded_int(value: Any, minimum: int, maximum: int, code: str) -> int:
    if not _is_int(value) or not minimum <= value <= maximum:
        raise LocalConsoleError(code)
    return value


def _bounded_optional_int(
    value: Any, minimum: int, maximum: int, code: str
) -> int | None:
    if value is None:
        return None
    return _bounded_int(value, minimum, maximum, code)


class LocalConsoleError(RuntimeError):
    """Bounded error safe for the appliance UI."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code if ERROR_CODE.fullmatch(code) else "operation_failed"


def fingerprint_for_ssh_host_public_key(text: str) -> str:
    """Return the OpenSSH SHA256 identity for an Ed25519 public host key."""

    fields = text.strip().split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise LocalConsoleError("pairing_identity_unavailable")
    try:
        key = base64.b64decode(fields[1], validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise LocalConsoleError("pairing_identity_unavailable") from exc
    if not 32 <= len(key) <= MAX_SSH_PUBLIC_KEY_BYTES:
        raise LocalConsoleError("pairing_identity_unavailable")
    encoded = base64.b64encode(hashlib.sha256(key).digest()).decode("ascii")
    return f"SHA256:{encoded.rstrip('=')}"


def pairing_code_for_ssh_fingerprint(fingerprint: str) -> str:
    """Derive the human comparison code without weakening the full key pin."""

    try:
        material = fingerprint.encode("ascii")
    except UnicodeEncodeError as exc:
        raise LocalConsoleError("pairing_identity_unavailable") from exc
    digest = hashlib.sha256(PAIRING_CODE_DOMAIN_SEPARATOR + material).digest()
    digits = f"{int.from_bytes(digest[:4], 'big') % 100_000_000:08d}"
    return f"{digits[:4]}-{digits[4:]}"


def load_local_pairing_identity(
    public_key_path: Path = DEFAULT_SSH_HOST_PUBLIC_KEY,
) -> tuple[str, str]:
    """Read a bounded, non-symlink host public key and return code + fingerprint."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(public_key_path, flags)
    except (OSError, TypeError, ValueError) as exc:
        raise LocalConsoleError("pairing_identity_unavailable") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or not 0 < details.st_size <= MAX_SSH_PUBLIC_KEY_BYTES:
            raise LocalConsoleError("pairing_identity_unavailable")
        raw = os.read(descriptor, MAX_SSH_PUBLIC_KEY_BYTES + 1)
        if len(raw) > MAX_SSH_PUBLIC_KEY_BYTES or os.read(descriptor, 1):
            raise LocalConsoleError("pairing_identity_unavailable")
    except OSError as exc:
        raise LocalConsoleError("pairing_identity_unavailable") from exc
    finally:
        os.close(descriptor)
    try:
        fingerprint = fingerprint_for_ssh_host_public_key(raw.decode("ascii"))
    except UnicodeDecodeError as exc:
        raise LocalConsoleError("pairing_identity_unavailable") from exc
    return pairing_code_for_ssh_fingerprint(fingerprint), fingerprint


class NoRedirectHandler(request.HTTPRedirectHandler):
    """Never forward the Authorization header beyond the fixed loopback origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class ActionGate:
    """Serialize arm/disarm with shutdown so the final action is always disarm."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._closing = False

    def run(self, action: Callable[[], Any]) -> Any | None:
        with self._lock:
            if self._closing:
                return None
            return action()

    def close(self, disarm: Callable[[], Any]) -> Any:
        with self._lock:
            self._closing = True
            try:
                return disarm()
            except Exception:
                # A failed close must remain retryable.  Otherwise an
                # unconfirmed disarm would permanently seal the gate while the
                # window stays open.
                self._closing = False
                raise


def validate_loopback_gateway(value: str) -> str:
    """Accept only an HTTP loopback gateway so the token cannot be exfiltrated."""

    candidate = value.rstrip("/")
    parsed = parse.urlsplit(candidate)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("gateway must be a loopback HTTP origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("gateway port is invalid") from exc
    if port is None or not 1 <= port <= 65535:
        raise ValueError("gateway must include a valid port")
    return candidate


def validate_token_bytes(raw: bytes) -> str:
    """Validate the same bounded ASCII token contract as the gateway."""

    token = raw.rstrip(b"\r\n")
    if not 32 <= len(token) <= 256:
        raise LocalConsoleError("token_unavailable")
    try:
        value = token.decode("ascii")
    except UnicodeDecodeError as exc:
        raise LocalConsoleError("token_unavailable") from exc
    if any(char.isspace() for char in value):
        raise LocalConsoleError("token_unavailable")
    return value


def load_local_token(
    command: tuple[str, ...] = DEFAULT_TOKEN_COMMAND,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> str:
    """Read the protected token without arguments, environment, clipboard, or logs."""

    try:
        completed = runner(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LocalConsoleError("token_unavailable") from exc
    if completed.returncode != 0:
        raise LocalConsoleError("token_unavailable")
    return validate_token_bytes(completed.stdout)


@dataclass(frozen=True, slots=True)
class CameraStorageState:
    state: str
    mounted: bool
    writable: bool
    total_bytes: int | None
    free_bytes: int | None
    reserve_bytes: int
    media_count: int
    active_job_id: str | None
    max_media_items: int
    max_total_bytes: int
    max_clip_duration_ms: int
    max_clip_fps: int
    max_clip_frames: int
    last_error: str | None

    @property
    def available(self) -> bool:
        return self.mounted and self.state in {"mounted", "read_only", "full"}

    @classmethod
    def unavailable(cls) -> CameraStorageState:
        return cls(
            "unconfigured",
            False,
            False,
            None,
            None,
            0,
            0,
            None,
            0,
            0,
            30_000,
            5,
            150,
            None,
        )


@dataclass(frozen=True, slots=True)
class EnvironmentCameraState:
    configured: bool
    reachable: bool
    stream_enabled: bool
    sensor_enabled: bool
    power_control: bool
    frame_ready: bool
    generation: int
    last_frame_age_ms: int | None
    viewers: int
    storage: CameraStorageState
    last_error: str | None

    @classmethod
    def unconfigured(cls) -> EnvironmentCameraState:
        return cls(
            configured=False,
            reachable=False,
            stream_enabled=False,
            sensor_enabled=False,
            power_control=False,
            frame_ready=False,
            generation=0,
            last_frame_age_ms=None,
            viewers=0,
            storage=CameraStorageState.unavailable(),
            last_error=None,
        )


@dataclass(frozen=True, slots=True)
class CameraStorageItem:
    item_id: str
    kind: str
    created_at: str | None
    created_uptime_ms: int
    size_bytes: int
    width: int
    height: int
    frame_count: int
    fps: int | None
    duration_ms: int
    content_type: str

    @property
    def display_label(self) -> str:
        size = format_byte_count(self.size_bytes)
        timestamp = self.created_at or f"uptime {self.created_uptime_ms} ms"
        duration = f" · {self.duration_ms / 1000:.1f}s" if self.kind == "clip" else ""
        return (
            f"{self.kind.upper()} · {timestamp} · "
            f"{self.width}×{self.height}{duration} · {size}"
        )


@dataclass(frozen=True, slots=True)
class CameraStorageCatalog:
    storage: CameraStorageState
    items: tuple[CameraStorageItem, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class CameraClipJob:
    job_id: str
    state: str
    frames_written: int
    frames_target: int
    media_id: str | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ViewState:
    video_ready: bool
    serial_ready: bool
    keyboard_ready: bool
    pointer_ready: bool
    local_enabled: bool
    local_armed: bool
    exclusive_grab: bool
    control_active: bool
    release_required: bool
    video_state: str
    video_generation: int
    active_mode_id: str | None
    requested_signal: tuple[int, int, int | float, str] | None
    negotiated_signal: tuple[int, int, int | float, str] | None
    source_timing_detectable: bool
    environment_camera: EnvironmentCameraState

    @property
    def remote_control_active(self) -> bool:
        return self.control_active and not self.local_armed

    @property
    def arm_allowed(self) -> bool:
        return bool(
            self.video_ready
            and self.serial_ready
            and self.local_enabled
            and self.keyboard_ready
            and self.pointer_ready
            and not self.local_armed
            and not self.exclusive_grab
            and not self.control_active
            and not self.release_required
        )

    @property
    def mode_change_allowed(self) -> bool:
        # A degraded video mode must remain recoverable.  Only an active mode
        # transition or HID ownership blocks changing the global output.
        return bool(
            self.video_state not in {"switching", "rolling_back"}
            and not self.local_armed
            and not self.exclusive_grab
            and not self.control_active
            and not self.release_required
        )


@dataclass(frozen=True, slots=True)
class VideoMode:
    mode_id: str
    label: str
    width: int
    height: int
    fps: int
    pixel_format: str
    max_frame_bytes: int
    validated: bool

    @property
    def display_label(self) -> str:
        return f"{self.label} · {self.width}×{self.height} @ {self.fps}"


@dataclass(frozen=True, slots=True)
class VideoModeCatalog:
    generation: int
    active_mode_id: str | None
    state: str
    modes: tuple[VideoMode, ...]


def _signal_from_payload(value: Any) -> tuple[int, int, int | float, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LocalConsoleError("status_unavailable")
    width = value.get("width")
    height = value.get("height")
    fps = value.get("fps")
    pixel_format = value.get("pixel_format")
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or not 1 <= width <= 7680
        or isinstance(height, bool)
        or not isinstance(height, int)
        or not 1 <= height <= 4320
        or isinstance(fps, bool)
        or not isinstance(fps, (int, float))
        or not 1 <= fps <= 120
        or not isinstance(pixel_format, str)
        or not 1 <= len(pixel_format) <= 16
    ):
        raise LocalConsoleError("status_unavailable")
    return (width, height, fps, pixel_format)


def format_byte_count(value: int | None) -> str:
    if value is None:
        return "—"
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024.0 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024.0
    return "—"


def camera_storage_state_from_payload(value: Any) -> CameraStorageState:
    if not isinstance(value, dict):
        raise LocalConsoleError("camera_storage_unavailable")
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
        raise LocalConsoleError("camera_storage_unavailable")
    if not isinstance(value.get("mounted"), bool) or not isinstance(
        value.get("writable"), bool
    ):
        raise LocalConsoleError("camera_storage_unavailable")
    limits = value.get("limits")
    if not isinstance(limits, dict):
        raise LocalConsoleError("camera_storage_unavailable")
    active_job_id = value.get("active_job_id")
    last_error = value.get("last_error")
    if active_job_id is not None and (
        not isinstance(active_job_id, str) or JOB_ID.fullmatch(active_job_id) is None
    ):
        raise LocalConsoleError("camera_storage_unavailable")
    if last_error is not None and (
        not isinstance(last_error, str) or ERROR_CODE.fullmatch(last_error) is None
    ):
        raise LocalConsoleError("camera_storage_unavailable")
    total_bytes = _bounded_optional_int(
        value.get("total_bytes"), 0, 1 << 50, "camera_storage_unavailable"
    )
    free_bytes = _bounded_optional_int(
        value.get("free_bytes"), 0, 1 << 50, "camera_storage_unavailable"
    )
    if total_bytes is not None and free_bytes is not None and free_bytes > total_bytes:
        raise LocalConsoleError("camera_storage_unavailable")
    return CameraStorageState(
        state=state,
        mounted=value["mounted"],
        writable=value["writable"],
        total_bytes=total_bytes,
        free_bytes=free_bytes,
        reserve_bytes=_bounded_int(
            value.get("reserve_bytes"), 0, 1 << 40, "camera_storage_unavailable"
        ),
        media_count=_bounded_int(
            value.get("media_count"), 0, 10_000_000, "camera_storage_unavailable"
        ),
        active_job_id=active_job_id,
        max_media_items=_bounded_int(
            limits.get("max_media_items"),
            0,
            10_000_000,
            "camera_storage_unavailable",
        ),
        max_total_bytes=_bounded_int(
            limits.get("max_total_bytes"),
            0,
            1 << 50,
            "camera_storage_unavailable",
        ),
        max_clip_duration_ms=_bounded_int(
            limits.get("max_clip_duration_ms"),
            1000,
            30_000,
            "camera_storage_unavailable",
        ),
        max_clip_fps=_bounded_int(
            limits.get("max_clip_fps"), 1, 5, "camera_storage_unavailable"
        ),
        max_clip_frames=_bounded_int(
            limits.get("max_clip_frames"), 1, 150, "camera_storage_unavailable"
        ),
        last_error=last_error,
    )


def environment_camera_from_payload(value: Any) -> EnvironmentCameraState:
    if not isinstance(value, dict):
        raise LocalConsoleError("camera_status_unavailable")
    storage = value.get("storage")
    if not isinstance(storage, dict):
        raise LocalConsoleError("camera_status_unavailable")

    bool_fields = (
        "configured",
        "reachable",
        "stream_enabled",
        "sensor_enabled",
        "power_control",
        "frame_ready",
    )
    if any(not isinstance(value.get(name), bool) for name in bool_fields):
        raise LocalConsoleError("camera_status_unavailable")

    last_error = value.get("last_error")
    if last_error is not None and (
        not isinstance(last_error, str) or ERROR_CODE.fullmatch(last_error) is None
    ):
        raise LocalConsoleError("camera_status_unavailable")

    generation = _bounded_int(
        value.get("generation"), 0, 2**63 - 1, "camera_status_unavailable"
    )
    viewers = _bounded_int(
        value.get("viewers"), 0, 1024, "camera_status_unavailable"
    )
    last_frame_age_ms = _bounded_optional_int(
        value.get("last_frame_age_ms"),
        0,
        24 * 60 * 60 * 1000,
        "camera_status_unavailable",
    )
    try:
        storage_state = camera_storage_state_from_payload(storage)
    except LocalConsoleError as exc:
        raise LocalConsoleError("camera_status_unavailable") from exc

    return EnvironmentCameraState(
        configured=value["configured"],
        reachable=value["reachable"],
        stream_enabled=value["stream_enabled"],
        sensor_enabled=value["sensor_enabled"],
        power_control=value["power_control"],
        frame_ready=value["frame_ready"],
        generation=generation,
        last_frame_age_ms=last_frame_age_ms,
        viewers=viewers,
        storage=storage_state,
        last_error=last_error,
    )


def environment_camera_from_response(payload: Any) -> EnvironmentCameraState:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise LocalConsoleError("camera_status_unavailable")
    return environment_camera_from_payload(payload.get("environment_camera"))


def camera_storage_item_from_payload(value: Any) -> CameraStorageItem:
    if not isinstance(value, dict):
        raise LocalConsoleError("camera_storage_unavailable")
    item_id = value.get("id")
    kind = value.get("kind")
    created_at = value.get("created_at")
    duration_ms = value.get("duration_ms")
    if (
        not isinstance(item_id, str)
        or MEDIA_ID.fullmatch(item_id) is None
        or kind not in {"snapshot", "clip"}
        or value.get("state") != "complete"
        or (
            created_at is not None
            and (
                not isinstance(created_at, str)
                or not 1 <= len(created_at) <= 64
                or any(ord(char) < 32 or ord(char) == 127 for char in created_at)
            )
        )
    ):
        raise LocalConsoleError("camera_storage_unavailable")
    frame_count = _bounded_int(
        value.get("frame_count"), 1, 150, "camera_storage_unavailable"
    )
    fps = _bounded_optional_int(
        value.get("fps"), 1, 5, "camera_storage_unavailable"
    )
    duration_ms = _bounded_int(
        duration_ms, 0, 30_000, "camera_storage_unavailable"
    )
    content_type = value.get("content_type")
    if kind == "snapshot" and (
        frame_count != 1
        or fps is not None
        or duration_ms != 0
        or content_type != "image/jpeg"
    ):
        raise LocalConsoleError("camera_storage_unavailable")
    if kind == "clip" and (
        fps is None
        or duration_ms < 1000
        or content_type != "application/vnd.noob.clip+json"
    ):
        raise LocalConsoleError("camera_storage_unavailable")
    return CameraStorageItem(
        item_id=item_id,
        kind=kind,
        created_at=created_at,
        created_uptime_ms=_bounded_int(
            value.get("created_uptime_ms"),
            0,
            1 << 62,
            "camera_storage_unavailable",
        ),
        size_bytes=_bounded_int(
            value.get("size_bytes"), 1, 1 << 50, "camera_storage_unavailable"
        ),
        width=_bounded_int(
            value.get("width"), 1, 8192, "camera_storage_unavailable"
        ),
        height=_bounded_int(
            value.get("height"), 1, 8192, "camera_storage_unavailable"
        ),
        frame_count=frame_count,
        fps=fps,
        duration_ms=duration_ms,
        content_type=content_type,
    )


def camera_storage_from_payload(payload: Any) -> CameraStorageCatalog:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise LocalConsoleError("camera_storage_unavailable")
    raw_items = payload.get("items")
    next_cursor = payload.get("next_cursor")
    if (
        not isinstance(raw_items, list)
        or len(raw_items) > MAX_STORAGE_ITEMS
        or (
            next_cursor is not None
            and (
                not isinstance(next_cursor, str)
                or CURSOR_ID.fullmatch(next_cursor) is None
            )
        )
    ):
        raise LocalConsoleError("camera_storage_unavailable")
    items = tuple(camera_storage_item_from_payload(item) for item in raw_items)
    if len({item.item_id for item in items}) != len(items):
        raise LocalConsoleError("camera_storage_unavailable")
    storage = camera_storage_state_from_payload(payload.get("storage"))
    return CameraStorageCatalog(storage, items, next_cursor)


def camera_clip_job_from_payload(payload: Any) -> CameraClipJob:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise LocalConsoleError("camera_clip_unconfirmed")
    value = payload.get("job")
    if not isinstance(value, dict):
        raise LocalConsoleError("camera_clip_unconfirmed")
    job_id = value.get("job_id")
    state = value.get("state")
    media_id = value.get("media_id")
    error_code = value.get("error_code")
    if (
        not isinstance(job_id, str)
        or JOB_ID.fullmatch(job_id) is None
        or value.get("kind") != "clip"
        or state not in {
            "queued",
            "running",
            "cancelling",
            "complete",
            "failed",
            "cancelled",
        }
        or (media_id is not None and (not isinstance(media_id, str) or MEDIA_ID.fullmatch(media_id) is None))
        or (error_code is not None and (not isinstance(error_code, str) or ERROR_CODE.fullmatch(error_code) is None))
    ):
        raise LocalConsoleError("camera_clip_unconfirmed")
    frames_written = _bounded_int(
        value.get("frames_written"), 0, 150, "camera_clip_unconfirmed"
    )
    frames_target = _bounded_int(
        value.get("frames_target"), 1, 150, "camera_clip_unconfirmed"
    )
    _bounded_int(
        value.get("created_uptime_ms"),
        0,
        1 << 62,
        "camera_clip_unconfirmed",
    )
    if frames_written > frames_target:
        raise LocalConsoleError("camera_clip_unconfirmed")
    if state == "complete" and media_id is None:
        raise LocalConsoleError("camera_clip_unconfirmed")
    if state == "failed" and error_code is None:
        raise LocalConsoleError("camera_clip_unconfirmed")
    return CameraClipJob(
        job_id, state, frames_written, frames_target, media_id, error_code
    )


def fullscreen_geometry(screen_width: int, screen_height: int) -> str:
    if not 320 <= screen_width <= 16_384 or not 240 <= screen_height <= 16_384:
        raise ValueError("screen dimensions are outside the supported range")
    return f"{screen_width}x{screen_height}+0+0"


def window_covers_screen(root: Any, *, tolerance: int = 3) -> bool:
    """Verify a viewable client area covers the physical screen edge to edge."""

    root.update_idletasks()
    screen_width = int(root.winfo_screenwidth())
    screen_height = int(root.winfo_screenheight())
    return bool(
        bool(root.winfo_ismapped())
        and bool(root.winfo_viewable())
        and abs(int(root.winfo_rootx())) <= tolerance
        and abs(int(root.winfo_rooty())) <= tolerance
        and abs(int(root.winfo_width()) - screen_width) <= tolerance
        and abs(int(root.winfo_height()) - screen_height) <= tolerance
    )


def xfce_fullscreen_commands(
    window_id: int,
    screen_width: int,
    screen_height: int,
    *,
    enabled: bool,
) -> tuple[tuple[str, ...], ...]:
    """Build fixed-argument EWMH commands; no title, shell, or user input."""

    if (
        not _is_int(window_id)
        or window_id <= 0
        or not isinstance(enabled, bool)
    ):
        raise ValueError("invalid window identity")
    fullscreen_geometry(screen_width, screen_height)
    identity = f"0x{window_id:x}"
    if enabled:
        return (
            (
                "/usr/bin/wmctrl",
                "-i",
                "-r",
                identity,
                "-b",
                "add,fullscreen,above",
            ),
            (
                "/usr/bin/wmctrl",
                "-i",
                "-r",
                identity,
                "-e",
                f"0,0,0,{screen_width},{screen_height}",
            ),
            # Tk/XFCE can leave an override-redirect toplevel unmapped after
            # withdraw/deiconify even though its geometry is correct.  Map the
            # already-validated numeric XID explicitly; this is idempotent for
            # a window that the WM already mapped.
            (
                "/usr/bin/xdotool",
                "windowmap",
                "--sync",
                str(window_id),
            ),
        )
    return (
        (
            "/usr/bin/wmctrl",
            "-i",
            "-r",
            identity,
            "-b",
            "remove,fullscreen,above",
        ),
        (
            "/usr/bin/xdotool",
            "windowmap",
            "--sync",
            str(window_id),
        ),
    )


def run_wm_commands(
    commands: tuple[tuple[str, ...], ...],
    *,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> bool:
    """Apply best-effort XFCE EWMH hints without weakening Tk's fallback."""

    successful = True
    for command in commands:
        try:
            completed = runner(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1,
            )
        except (OSError, subprocess.SubprocessError):
            successful = False
            continue
        successful = successful and completed.returncode == 0
    return successful


class FullscreenController:
    """Force true borderless fullscreen while retaining a restorable WM state.

    XFCE may apply Tk's asynchronous ``-fullscreen`` request after a later
    override-redirect transition.  That race can leave the *restored* window
    fullscreen even though the exit path already requested normal geometry.
    Apply the EWMH hint while the window is still managed, then use an
    override-redirect window plus explicit physical-screen geometry as the
    edge-to-edge fallback.  Exit performs the inverse transition and restores
    geometry only after the WM has removed its fullscreen state.
    """

    def __init__(
        self,
        root: Any,
        *,
        command_runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    ) -> None:
        self.root = root
        self.command_runner = command_runner
        self.active = False
        self.normal_geometry = ""

    def enter(self) -> None:
        if self.active:
            self.enforce()
            return
        self.root.update_idletasks()
        self.normal_geometry = str(self.root.geometry())
        window_id = int(self.root.winfo_id())
        screen_width = int(self.root.winfo_screenwidth())
        screen_height = int(self.root.winfo_screenheight())
        # Send the EWMH request while XFCE still manages the decorated window.
        # wmctrl cannot reliably change _NET_WM_STATE after override-redirect
        # has detached the toplevel from normal WM placement.  Do not also set
        # Tk's asynchronous -fullscreen=True flag: on XFCE that request can be
        # delivered only after exit re-parents the window, recreating the exact
        # fullscreen state we are trying to remove.
        run_wm_commands(
            xfce_fullscreen_commands(
                window_id,
                screen_width,
                screen_height,
                enabled=True,
            ),
            runner=self.command_runner,
        )
        self.root.update_idletasks()
        self.active = True
        # Re-map the window after changing override-redirect.  XFCE otherwise
        # may keep the old decorated frame and its panel work-area constraint.
        self.root.withdraw()
        self.root.update_idletasks()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry(
            fullscreen_geometry(screen_width, screen_height)
        )
        self.root.deiconify()
        self.root.state("normal")
        self.enforce()

    def enforce(self) -> None:
        if not self.active:
            return
        geometry = fullscreen_geometry(
            int(self.root.winfo_screenwidth()), int(self.root.winfo_screenheight())
        )
        self.root.geometry(geometry)
        # Flush Tk's queued withdraw/deiconify transition before asking X11 to
        # map the window.  If the external map runs first, XFCE can apply Tk's
        # pending unmap afterward and leave a correctly sized but invisible
        # toplevel.  Repeating these calls is safe during verification retries.
        self.root.deiconify()
        self.root.state("normal")
        self.root.update_idletasks()
        # The EWMH request was issued before override-redirect.  At this point
        # XFCE may no longer list the fallback window, so only perform the
        # idempotent X11 map needed after Tk flushes its queued transition.
        run_wm_commands(
            (
                (
                    "/usr/bin/xdotool",
                    "windowmap",
                    "--sync",
                    str(int(self.root.winfo_id())),
                ),
            ),
            runner=self.command_runner,
        )
        self.root.lift()
        self.root.focus_force()
        self.root.update_idletasks()

    def exit(self, *, topmost: bool) -> None:
        if not self.active:
            self.root.attributes("-topmost", topmost)
            return
        self.active = False
        window_id = int(self.root.winfo_id())
        screen_width = int(self.root.winfo_screenwidth())
        screen_height = int(self.root.winfo_screenheight())
        self.root.withdraw()
        # Finish the override-window unmap before re-parenting it.  Otherwise a
        # queued Tk unmap can run after xdotool maps the restored window.
        self.root.update_idletasks()
        self.root.overrideredirect(False)
        # Clearing is safe and synchronous here because the window has been
        # returned to normal WM management.  The controller never sets this
        # flag true; this defensive clear also recovers from an older process
        # or WM session that left the Tk attribute behind.
        self.root.attributes("-fullscreen", False)
        self.root.deiconify()
        self.root.state("normal")
        # First settle the managed window, then remove EWMH fullscreen/above.
        # Applying the saved geometry before this removal lets XFCE overwrite
        # it with the fullscreen restore geometry, which caused Escape to leave
        # a 1280x720 window on the reference uConsole.
        self.root.update_idletasks()
        run_wm_commands(
            xfce_fullscreen_commands(
                window_id,
                screen_width,
                screen_height,
                enabled=False,
            ),
            runner=self.command_runner,
        )
        self.root.update_idletasks()
        if self.normal_geometry:
            self.root.geometry(self.normal_geometry)
        self.root.update_idletasks()
        # wmctrl removes the temporary ``above`` hint together with
        # ``fullscreen``.  Reapply the operator's persisted pin choice last so
        # a pinned normal window remains pinned after Escape.
        self.root.attributes("-topmost", topmost)
        self.root.lift()
        self.root.focus_force()


def image_render_size(
    image_width: int,
    image_height: int,
    viewport_width: int,
    viewport_height: int,
    zoom_mode: str,
) -> tuple[int, int]:
    if (
        min(image_width, image_height, viewport_width, viewport_height) <= 0
        or zoom_mode not in ZOOM_MODES
    ):
        raise ValueError("invalid render geometry")
    if zoom_mode == "FIT":
        scale = min(viewport_width / image_width, viewport_height / image_height)
    else:
        scale = 1.0 if zoom_mode == "100%" else 2.0
    return (
        max(1, int(round(image_width * scale))),
        max(1, int(round(image_height * scale))),
    )


def clamp_pan(
    pan_x: int,
    pan_y: int,
    rendered_width: int,
    rendered_height: int,
    viewport_width: int,
    viewport_height: int,
) -> tuple[int, int]:
    maximum_x = max(0, (rendered_width - viewport_width) // 2)
    maximum_y = max(0, (rendered_height - viewport_height) // 2)
    return (
        max(-maximum_x, min(maximum_x, pan_x)),
        max(-maximum_y, min(maximum_y, pan_y)),
    )


def save_screenshot(
    data: bytes,
    source: str,
    *,
    directory: Path | None = None,
    captured_at: datetime | None = None,
) -> Path:
    """Persist one explicit JPEG with private permissions and no overwrite."""

    if source not in VIEW_SOURCES or not (
        data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")
    ):
        raise LocalConsoleError("screenshot_unavailable")
    target_dir = directory or (Path.home() / "Pictures" / "N.O.O.B Screenshots")
    try:
        target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        target_dir.chmod(0o700)
    except OSError as exc:
        raise LocalConsoleError("screenshot_unavailable") from exc
    stamp = (captured_at or datetime.now()).strftime("%Y%m%d-%H%M%S-%f")
    prefix = "target" if source == "target" else "environment"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    for suffix in range(100):
        extra = "" if suffix == 0 else f"-{suffix}"
        path = target_dir / f"noob-{prefix}-{stamp}{extra}.jpg"
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            continue
        except OSError as exc:
            raise LocalConsoleError("screenshot_unavailable") from exc
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            path.chmod(0o600)
            return path
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise LocalConsoleError("screenshot_unavailable") from None
    raise LocalConsoleError("screenshot_unavailable")


def view_state_from_status(payload: Any) -> ViewState:
    """Reduce the status response to fields the local UI is allowed to render."""

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise LocalConsoleError("status_unavailable")
    serial = payload.get("serial")
    video = payload.get("video")
    local = payload.get("local_input")
    control = payload.get("control")
    if not all(isinstance(item, dict) for item in (serial, video, local, control)):
        raise LocalConsoleError("status_unavailable")
    generation = video.get("generation", 0)
    state = video.get("state", "ready" if video.get("ready") is True else "degraded")
    active_mode_id = video.get("active_mode_id")
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 0
        or state not in VIDEO_STATES
        or (
            active_mode_id is not None
            and (
                not isinstance(active_mode_id, str)
                or MODE_ID.fullmatch(active_mode_id) is None
            )
        )
    ):
        raise LocalConsoleError("status_unavailable")
    raw_environment = payload.get("environment_camera")
    environment = (
        EnvironmentCameraState.unconfigured()
        if raw_environment is None
        else environment_camera_from_payload(raw_environment)
    )
    return ViewState(
        video_ready=video.get("ready") is True,
        serial_ready=serial.get("ready") is True,
        keyboard_ready=local.get("keyboard_ready") is True,
        pointer_ready=local.get("pointer_ready") is True,
        local_enabled=local.get("enabled") is True,
        local_armed=local.get("armed") is True,
        exclusive_grab=local.get("exclusive_grab") is True,
        control_active=control.get("active") is True,
        release_required=control.get("release_required") is True,
        video_state=state,
        video_generation=generation,
        active_mode_id=active_mode_id,
        requested_signal=_signal_from_payload(video.get("requested")),
        negotiated_signal=_signal_from_payload(video.get("negotiated")),
        source_timing_detectable=video.get("source_timing_detectable") is True,
        environment_camera=environment,
    )


def video_modes_from_payload(payload: Any) -> VideoModeCatalog:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise LocalConsoleError("video_modes_unavailable")
    generation = payload.get("generation")
    active_mode_id = payload.get("active_mode_id")
    state = payload.get("state")
    raw_modes = payload.get("modes")
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 0
        or state not in VIDEO_STATES
        or not isinstance(raw_modes, list)
        or len(raw_modes) > 32
        or (
            active_mode_id is not None
            and (
                not isinstance(active_mode_id, str)
                or MODE_ID.fullmatch(active_mode_id) is None
            )
        )
    ):
        raise LocalConsoleError("video_modes_unavailable")
    modes: list[VideoMode] = []
    seen: set[str] = set()
    for item in raw_modes:
        if not isinstance(item, dict):
            raise LocalConsoleError("video_modes_unavailable")
        mode_id = item.get("id")
        label = item.get("label")
        width = item.get("width")
        height = item.get("height")
        fps = item.get("fps")
        pixel_format = item.get("pixel_format")
        max_frame_bytes = item.get("max_frame_bytes")
        validated = item.get("validated")
        if (
            not isinstance(mode_id, str)
            or MODE_ID.fullmatch(mode_id) is None
            or mode_id in seen
            or not isinstance(label, str)
            or not 1 <= len(label) <= 80
            or isinstance(width, bool)
            or not isinstance(width, int)
            or not 160 <= width <= 7680
            or isinstance(height, bool)
            or not isinstance(height, int)
            or not 120 <= height <= 4320
            or isinstance(fps, bool)
            or not isinstance(fps, int)
            or not 1 <= fps <= 120
            or pixel_format != "MJPG"
            or isinstance(max_frame_bytes, bool)
            or not isinstance(max_frame_bytes, int)
            or not 64 * 1024 <= max_frame_bytes <= MAX_FRAME_RESPONSE_BYTES
            or validated is not True
        ):
            raise LocalConsoleError("video_modes_unavailable")
        seen.add(mode_id)
        modes.append(
            VideoMode(
                mode_id,
                label,
                width,
                height,
                fps,
                pixel_format,
                max_frame_bytes,
                True,
            )
        )
    return VideoModeCatalog(generation, active_mode_id, state, tuple(modes))


class GatewayClient:
    """Small, loopback-only authenticated client with bounded responses."""

    def __init__(self, origin: str, token: str, *, timeout: float = 1.5) -> None:
        self.origin = validate_loopback_gateway(origin)
        self._authorization = f"Bearer {token}"
        self.timeout = timeout
        self._opener = request.build_opener(
            request.ProxyHandler({}),
            NoRedirectHandler(),
        )

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        max_bytes: int,
        timeout: float | None = None,
    ) -> bytes:
        headers = {
            "Authorization": self._authorization,
            "Accept": "application/json, image/jpeg",
            "Cache-Control": "no-store",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = request.Request(
            f"{self.origin}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(
                req, timeout=self.timeout if timeout is None else timeout
            ) as response:
                data = response.read(max_bytes + 1)
        except error.HTTPError as exc:
            code = "request_failed"
            try:
                data = exc.read(4097)
                candidate = json.loads(data.decode("utf-8")).get("error")
                if isinstance(candidate, str) and ERROR_CODE.fullmatch(candidate):
                    code = candidate
            except (UnicodeError, ValueError, AttributeError):
                pass
            raise LocalConsoleError(code) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise LocalConsoleError("gateway_unavailable") from exc
        if len(data) > max_bytes:
            raise LocalConsoleError("response_too_large")
        return data

    def _json_request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        max_bytes: int = 65536,
        timeout: float | None = None,
        error_code: str = "request_failed",
    ) -> Any:
        raw = self._request(
            path,
            method=method,
            body=body,
            max_bytes=max_bytes,
            timeout=timeout,
        )
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise LocalConsoleError(error_code) from exc

    def status(self) -> ViewState:
        payload = self._json_request(
            "/api/v1/status", error_code="status_unavailable"
        )
        return view_state_from_status(payload)

    def frame(self, source: str = "target") -> bytes:
        if source not in VIEW_SOURCES:
            raise LocalConsoleError("frame_invalid")
        path = (
            "/api/v1/frame.jpg"
            if source == "target"
            else "/api/v1/environment-camera/frame.jpg"
        )
        raw = self._request(
            path, max_bytes=MAX_FRAME_RESPONSE_BYTES
        )
        if not (raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")):
            raise LocalConsoleError("frame_invalid")
        return raw

    def video_modes(self) -> VideoModeCatalog:
        payload = self._json_request(
            "/api/v1/video/modes", error_code="video_modes_unavailable"
        )
        return video_modes_from_payload(payload)

    def set_environment_enabled(
        self, enabled: bool, expected_generation: int
    ) -> EnvironmentCameraState:
        if not isinstance(enabled, bool) or not _is_int(expected_generation) or expected_generation < 0:
            raise LocalConsoleError("invalid_camera_state_request")
        body = json.dumps(
            {"enabled": enabled, "expected_generation": expected_generation},
            separators=(",", ":"),
        ).encode("ascii")
        payload = self._json_request(
            "/api/v1/environment-camera/state",
            method="POST",
            body=body,
            timeout=CAMERA_ACTION_TIMEOUT,
            error_code="camera_state_unconfirmed",
        )
        return environment_camera_from_response(payload)

    def camera_storage(self, *, limit: int = 20) -> CameraStorageCatalog:
        if not _is_int(limit) or not 1 <= limit <= MAX_STORAGE_ITEMS:
            raise LocalConsoleError("camera_storage_unavailable")
        query = parse.urlencode({"limit": limit})
        payload = self._json_request(
            f"/api/v1/environment-camera/storage?{query}",
            max_bytes=MAX_STORAGE_RESPONSE_BYTES,
            error_code="camera_storage_unavailable",
        )
        return camera_storage_from_payload(payload)

    def camera_snapshot(self, expected_generation: int) -> CameraStorageItem:
        return self._camera_capture(
            "/api/v1/environment-camera/snapshot",
            {"expected_generation": expected_generation},
        )

    def camera_clip(
        self,
        expected_generation: int,
        *,
        duration_seconds: int = 10,
        fps: int = 2,
    ) -> CameraStorageItem:
        job_id = self.start_camera_clip(
            expected_generation,
            duration_seconds=duration_seconds,
            fps=fps,
        )

        deadline = time.monotonic() + duration_seconds + 12.0
        while time.monotonic() < deadline:
            job = self.camera_clip_job(job_id)
            if job.state == "complete":
                assert job.media_id is not None
                return self.camera_media(job.media_id)
            if job.state == "failed":
                raise LocalConsoleError(job.error_code or "camera_clip_failed")
            if job.state == "cancelled":
                raise LocalConsoleError("camera_clip_cancelled")
            time.sleep(0.4)
        raise LocalConsoleError("camera_clip_timeout")

    def start_camera_clip(
        self,
        expected_generation: int,
        *,
        duration_seconds: int = 10,
        fps: int = 2,
    ) -> str:
        if (
            not _is_int(duration_seconds)
            or not 1 <= duration_seconds <= 30
            or not _is_int(fps)
            or not 1 <= fps <= 5
            or not _is_int(expected_generation)
            or expected_generation < 0
        ):
            raise LocalConsoleError("invalid_camera_capture_request")
        body = json.dumps(
            {
                "duration_seconds": duration_seconds,
                "fps": fps,
                "expected_generation": expected_generation,
            },
            separators=(",", ":"),
        ).encode("ascii")
        payload = self._json_request(
            "/api/v1/environment-camera/clip",
            method="POST",
            body=body,
            timeout=CAMERA_ACTION_TIMEOUT,
            error_code="camera_clip_unconfirmed",
        )
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise LocalConsoleError("camera_clip_unconfirmed")
        job_id = payload.get("job_id")
        if (
            not isinstance(job_id, str)
            or JOB_ID.fullmatch(job_id) is None
            or payload.get("state") != "queued"
        ):
            raise LocalConsoleError("camera_clip_unconfirmed")
        return job_id

    def camera_clip_job(self, job_id: str) -> CameraClipJob:
        if not isinstance(job_id, str) or JOB_ID.fullmatch(job_id) is None:
            raise LocalConsoleError("camera_clip_unconfirmed")
        payload = self._json_request(
            f"/api/v1/environment-camera/jobs/{job_id}",
            error_code="camera_clip_unconfirmed",
        )
        job = camera_clip_job_from_payload(payload)
        if job.job_id != job_id:
            raise LocalConsoleError("camera_clip_unconfirmed")
        return job

    def stop_camera_clip(self, job_id: str) -> str:
        if not isinstance(job_id, str) or JOB_ID.fullmatch(job_id) is None:
            raise LocalConsoleError("camera_clip_unconfirmed")
        payload = self._json_request(
            f"/api/v1/environment-camera/jobs/{job_id}/stop",
            method="POST",
            body=b"{}",
            timeout=CAMERA_ACTION_TIMEOUT,
            error_code="camera_clip_unconfirmed",
        )
        if (
            not isinstance(payload, dict)
            or payload.get("ok") is not True
            or payload.get("job_id") != job_id
            or payload.get("state") not in {"cancelling", "cancelled"}
        ):
            raise LocalConsoleError("camera_clip_unconfirmed")
        return payload["state"]

    def camera_media(self, media_id: str) -> CameraStorageItem:
        if not isinstance(media_id, str) or MEDIA_ID.fullmatch(media_id) is None:
            raise LocalConsoleError("camera_storage_unavailable")
        payload = self._json_request(
            f"/api/v1/environment-camera/storage/{media_id}",
            error_code="camera_storage_unavailable",
        )
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise LocalConsoleError("camera_storage_unavailable")
        return camera_storage_item_from_payload(payload.get("item"))

    def camera_snapshot_content(self, media_id: str) -> bytes:
        if not isinstance(media_id, str) or MEDIA_ID.fullmatch(media_id) is None:
            raise LocalConsoleError("camera_storage_unavailable")
        return self._camera_media_jpeg(
            f"/api/v1/environment-camera/storage/{media_id}/content"
        )

    def camera_clip_frame(self, media_id: str, frame_index: int) -> bytes:
        if (
            not isinstance(media_id, str)
            or MEDIA_ID.fullmatch(media_id) is None
            or not _is_int(frame_index)
            or not 0 <= frame_index <= MAX_CAMERA_CLIP_FRAME_INDEX
        ):
            raise LocalConsoleError("camera_storage_unavailable")
        return self._camera_media_jpeg(
            f"/api/v1/environment-camera/storage/{media_id}/frames/{frame_index}.jpg"
        )

    def _camera_media_jpeg(self, path: str) -> bytes:
        raw = self._request(path, max_bytes=MAX_FRAME_RESPONSE_BYTES)
        if not (raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")):
            raise LocalConsoleError("camera_media_invalid")
        return raw

    def _camera_capture(
        self, path: str, body_value: dict[str, Any]
    ) -> CameraStorageItem:
        generation = body_value.get("expected_generation")
        if not _is_int(generation) or generation < 0:
            raise LocalConsoleError("invalid_camera_capture_request")
        body = json.dumps(body_value, separators=(",", ":")).encode("ascii")
        payload = self._json_request(
            path,
            method="POST",
            body=body,
            timeout=CAMERA_ACTION_TIMEOUT,
            error_code="camera_capture_unconfirmed",
        )
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise LocalConsoleError("camera_capture_unconfirmed")
        return camera_storage_item_from_payload(payload.get("item"))

    def set_video_mode(self, mode_id: str, expected_generation: int) -> ViewState:
        if (
            not isinstance(mode_id, str)
            or MODE_ID.fullmatch(mode_id) is None
            or isinstance(expected_generation, bool)
            or not isinstance(expected_generation, int)
            or expected_generation < 0
        ):
            raise LocalConsoleError("invalid_video_mode_request")
        body = json.dumps(
            {
                "mode_id": mode_id,
                "expected_generation": expected_generation,
            },
            separators=(",", ":"),
        ).encode("ascii")
        self._request(
            "/api/v1/video/mode",
            method="POST",
            body=body,
            max_bytes=65536,
            timeout=VIDEO_MODE_REQUEST_TIMEOUT,
        )
        try:
            return self.status()
        except LocalConsoleError as exc:
            # The server-side transition is transactional and generation
            # checked.  Never replay an ambiguous POST from the client.
            raise LocalConsoleError("video_mode_unconfirmed") from exc

    def arm(self) -> ViewState:
        try:
            self._post_empty("/api/v1/local-input/arm")
            return self.status()
        except LocalConsoleError as exc:
            # A lost POST response or failed follow-up status request can leave
            # the physical devices armed even though the UI saw an error.
            # Local disarm is scoped and cannot release a remote HTTP lease, so
            # it is always the safe reconciliation action.
            try:
                self._post_empty("/api/v1/local-input/disarm")
            except LocalConsoleError:
                pass
            raise LocalConsoleError("arm_unconfirmed") from exc

    def disarm(self) -> ViewState:
        self._post_empty("/api/v1/local-input/disarm")
        return self.status()

    def _post_empty(self, path: str) -> None:
        raw = self._request(path, method="POST", body=b"{}", max_bytes=65536)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise LocalConsoleError("request_failed") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise LocalConsoleError("request_failed")


class NoobLocalConsole:
    FRAME_INTERVAL = 0.12
    STATUS_INTERVAL = 0.8

    def __init__(self, root: tk.Tk, client: GatewayClient) -> None:
        self.root = root
        self.client = client
        self.stop_event = threading.Event()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=32)
        self.frame_lock = threading.Lock()
        self.latest_frame: tuple[str, bytes] | None = None
        self.current_frame_bytes: bytes | None = None
        self.current_image: Any | None = None
        self.current_state: ViewState | None = None
        self.video_modes: tuple[VideoMode, ...] = ()
        self.mode_display_to_id: dict[str, str] = {}
        self.storage_catalog = CameraStorageCatalog(
            CameraStorageState.unavailable(), (), None
        )
        self.storage_inflight = False
        self.media_preview_inflight = False
        self.media_preview_request: tuple[str, int] | None = None
        self.media_preview_item: CameraStorageItem | None = None
        self.media_preview_frame_index = 0
        self.active_clip_job_id: str | None = None
        self.active_clip_job: CameraClipJob | None = None
        self.photo: Any | None = None
        self.canvas_image_id: int | None = None
        self.source = "target"
        self.pan_x = 0
        self.pan_y = 0
        self.pan_anchor: tuple[int, int] | None = None
        self.fullscreen = False
        self.pinned = True
        self.action_inflight = False
        self.hide_after_disarm = False
        self.action_gate = ActionGate()
        self.closing = False
        self.fullscreen_controller = FullscreenController(root)
        self.fullscreen_verify_attempt = 0
        try:
            self.pairing_code, self.ssh_fingerprint = load_local_pairing_identity()
        except LocalConsoleError:
            self.pairing_code, self.ssh_fingerprint = "UNAVAILABLE", ""

        self._configure_window()
        self._build_ui()
        self.root.after(40, self._drain_events)
        threading.Thread(target=self._poll_loop, daemon=True, name="noob-local-poll").start()

    def _configure_window(self) -> None:
        self.root.title(APP_TITLE)
        self.root.configure(bg=BG)
        self.root.geometry("1100x680+90+18")
        self.root.minsize(820, 540)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.bind("<F11>", lambda _event: self._toggle_fullscreen())
        self.root.bind("<Escape>", lambda _event: self._leave_fullscreen())

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Noob.TButton", background=SURFACE_RAISED, foreground=TEXT, bordercolor=BORDER, padding=(14, 9), font=("Sans", 10, "bold"))
        style.map("Noob.TButton", background=[("active", "#18242b"), ("disabled", SURFACE)], foreground=[("disabled", "#59656b")])
        style.configure("Arm.TButton", background="#123b34", foreground="#d8fff3", bordercolor="#206d5b", padding=(18, 10), font=("Sans", 10, "bold"))
        style.map("Arm.TButton", background=[("active", "#195043"), ("disabled", SURFACE)], foreground=[("disabled", "#59656b")])
        style.configure("Disarm.TButton", background="#431d23", foreground="#ffdfe2", bordercolor="#84343e", padding=(18, 10), font=("Sans", 10, "bold"))
        style.map("Disarm.TButton", background=[("active", "#59262e"), ("disabled", SURFACE)], foreground=[("disabled", "#59656b")])
        style.configure("Exit.TButton", background="#5a2228", foreground="#fff0f1", bordercolor="#b84752", padding=(16, 10), font=("Sans", 10, "bold"))
        style.map("Exit.TButton", background=[("active", "#793039")])

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=BG, padx=18, pady=13)
        header.pack(side="top", fill="x")
        brand = tk.Frame(header, bg=BG)
        brand.pack(side="left")
        tk.Label(brand, text="N.O.O.B", bg=BG, fg=TEXT, font=("Sans", 16, "bold")).pack(anchor="w")
        tk.Label(brand, text="NEVER OUT OF BOUNDS · LOCAL CONSOLE", bg=BG, fg=SIGNAL, font=("Sans", 8, "bold")).pack(anchor="w")
        tk.Label(
            brand,
            text=f"PAIRING · {self.pairing_code}",
            bg=BG,
            fg=HEALTHY if self.ssh_fingerprint else WARN,
            font=("Sans", 9, "bold"),
        ).pack(anchor="w", pady=(3, 0))

        self.badges = tk.Frame(header, bg=BG)
        self.badges.pack(side="right")
        self.video_badge = self._badge(self.badges, "VIDEO")
        self.camera_badge = self._badge(self.badges, "CAMERA")
        self.hid_badge = self._badge(self.badges, "HID")
        self.control_badge = self._badge(self.badges, "CONTROL")

        source_strip = tk.Frame(
            self.root, bg=SURFACE_RAISED, padx=16, pady=7
        )
        source_strip.pack(side="top", fill="x", padx=16, pady=(0, 8))
        tk.Label(
            source_strip,
            text="VIEW",
            bg=SURFACE_RAISED,
            fg=TEXT,
            font=("Sans", 9, "bold"),
        ).pack(side="left")
        self.source_var = tk.StringVar(self.root, value="target")
        self.target_source_button = ttk.Radiobutton(
            source_strip,
            text="TARGET HDMI",
            value="target",
            variable=self.source_var,
            command=lambda: self._choose_source("target"),
        )
        self.target_source_button.pack(side="left", padx=(10, 4))
        self.environment_source_button = ttk.Radiobutton(
            source_strip,
            text="ENVIRONMENT CAMERA",
            value="environment",
            variable=self.source_var,
            command=lambda: self._choose_source("environment"),
            state="disabled",
        )
        self.environment_source_button.pack(side="left", padx=4)
        self.screenshot_button = ttk.Button(
            source_strip,
            text="SCREENSHOT",
            style="Noob.TButton",
            command=self._screenshot,
        )
        self.screenshot_button.pack(side="right")
        self.zoom_var = tk.StringVar(self.root, value="FIT")
        self.zoom_box = ttk.Combobox(
            source_strip,
            textvariable=self.zoom_var,
            values=ZOOM_MODES,
            state="readonly",
            width=7,
            font=("Sans", 9),
        )
        self.zoom_box.pack(side="right", padx=(8, 10))
        self.zoom_box.bind("<<ComboboxSelected>>", self._change_zoom)
        tk.Label(
            source_strip,
            text="ZOOM",
            bg=SURFACE_RAISED,
            fg=MUTED,
            font=("Sans", 9, "bold"),
        ).pack(side="right")

        footer = tk.Frame(self.root, bg=SURFACE_RAISED, padx=16, pady=8)
        footer.pack(side="bottom", fill="x")
        self.message = tk.Label(
            footer,
            text="Connecting to the local gateway…",
            bg=SURFACE_RAISED,
            fg=MUTED,
            font=("Sans", 10),
        )
        self.message.pack(side="left")
        tk.Label(
            footer,
            text="Ctrl+Alt+Esc releases target input · Escape exits full screen · Super+N toggles the console",
            bg=SURFACE_RAISED,
            fg=MUTED,
            font=("Sans", 9, "bold"),
        ).pack(side="right")

        controls = tk.Frame(self.root, bg=BG, padx=16, pady=10)
        controls.pack(side="bottom", fill="x")
        self.arm_button = ttk.Button(
            controls,
            text="ARM TARGET CONTROL",
            style="Arm.TButton",
            command=self._arm,
        )
        self.arm_button.pack(side="left")
        self.disarm_button = ttk.Button(
            controls,
            text="DISARM",
            style="Disarm.TButton",
            command=self._disarm,
        )
        self.disarm_button.pack(side="left", padx=(8, 0))
        self.desktop_button = ttk.Button(
            controls,
            text="DESKTOP",
            style="Noob.TButton",
            command=self._return_to_desktop,
        )
        self.desktop_button.pack(side="left", padx=(8, 0))
        self.fullscreen_button = ttk.Button(
            controls,
            text="FULL SCREEN",
            style="Noob.TButton",
            command=self._toggle_fullscreen,
        )
        self.fullscreen_button.pack(side="right")
        self.pin_button = ttk.Button(
            controls,
            text="UNPIN",
            style="Noob.TButton",
            command=self._toggle_pin,
        )
        self.pin_button.pack(side="right", padx=(0, 8))

        self.settings_host = tk.Frame(self.root, bg=BG)
        self.settings_host.pack(side="bottom", fill="x", padx=16, pady=(8, 0))

        self.target_settings = tk.Frame(
            self.settings_host, bg=SURFACE_RAISED, padx=16, pady=8
        )
        self.target_settings.pack(fill="x")
        tk.Label(
            self.target_settings,
            text="CAPTURE OUTPUT",
            bg=SURFACE_RAISED,
            fg=TEXT,
            font=("Sans", 9, "bold"),
        ).pack(side="left")
        self.mode_var = tk.StringVar(self.root)
        self.mode_box = ttk.Combobox(
            self.target_settings,
            textvariable=self.mode_var,
            state="disabled",
            width=34,
            font=("Sans", 9),
        )
        self.mode_box.pack(side="left", padx=(10, 12))
        self.mode_box.bind("<<ComboboxSelected>>", self._select_video_mode)
        self.mode_detail = tk.Label(
            self.target_settings,
            text="Validated profiles load from the gateway · target timing is selected manually",
            bg=SURFACE_RAISED,
            fg=MUTED,
            font=("Sans", 9),
        )
        self.mode_detail.pack(side="left")

        self.environment_settings = tk.Frame(
            self.settings_host, bg=SURFACE_RAISED, padx=16, pady=8
        )
        camera_row = tk.Frame(self.environment_settings, bg=SURFACE_RAISED)
        camera_row.pack(fill="x")
        tk.Label(
            camera_row,
            text="ENVIRONMENT CAMERA",
            bg=SURFACE_RAISED,
            fg=TEXT,
            font=("Sans", 9, "bold"),
        ).pack(side="left")
        self.camera_toggle_button = ttk.Button(
            camera_row,
            text="ENABLE SENSOR",
            style="Noob.TButton",
            command=self._toggle_environment_camera,
        )
        self.camera_toggle_button.pack(side="left", padx=(10, 8))
        self.camera_snapshot_button = ttk.Button(
            camera_row,
            text="SNAPSHOT TO SD",
            style="Noob.TButton",
            command=self._camera_snapshot,
        )
        self.camera_snapshot_button.pack(side="left", padx=4)
        self.camera_clip_button = ttk.Button(
            camera_row,
            text="10S CLIP TO SD",
            style="Noob.TButton",
            command=self._camera_clip,
        )
        self.camera_clip_button.pack(side="left", padx=4)
        self.storage_refresh_button = ttk.Button(
            camera_row,
            text="REFRESH SD",
            style="Noob.TButton",
            command=self._refresh_storage,
        )
        self.storage_refresh_button.pack(side="right")
        self.camera_detail = tk.Label(
            self.environment_settings,
            text="Logical sensor control · USB power remains on",
            bg=SURFACE_RAISED,
            fg=MUTED,
            font=("Sans", 9),
        )
        self.camera_detail.pack(anchor="w", pady=(6, 3))
        self.storage_detail = tk.Label(
            self.environment_settings,
            text="microSD storage unavailable",
            bg=SURFACE_RAISED,
            fg=MUTED,
            font=("Sans", 9),
        )
        self.storage_detail.pack(anchor="w")
        self.storage_list = tk.Listbox(
            self.environment_settings,
            height=3,
            bg="#091015",
            fg=TEXT,
            selectbackground="#174743",
            selectforeground=TEXT,
            highlightbackground=BORDER,
            highlightthickness=1,
            borderwidth=0,
            font=("Sans", 9),
        )
        self.storage_list.pack(fill="x", pady=(5, 0))
        self.storage_list.bind(
            "<<ListboxSelect>>", lambda _event: self._set_buttons()
        )
        self.storage_list.bind(
            "<Double-Button-1>", lambda _event: self._open_selected_media()
        )
        media_controls = tk.Frame(
            self.environment_settings, bg=SURFACE_RAISED
        )
        media_controls.pack(fill="x", pady=(5, 0))
        self.storage_open_button = ttk.Button(
            media_controls,
            text="OPEN PREVIEW",
            style="Noob.TButton",
            command=self._open_selected_media,
        )
        self.storage_open_button.pack(side="left")
        self.media_previous_button = ttk.Button(
            media_controls,
            text="PREVIOUS FRAME",
            style="Noob.TButton",
            command=lambda: self._navigate_media_preview(-1),
        )
        self.media_previous_button.pack(side="left", padx=(5, 0))
        self.media_next_button = ttk.Button(
            media_controls,
            text="NEXT FRAME",
            style="Noob.TButton",
            command=lambda: self._navigate_media_preview(1),
        )
        self.media_next_button.pack(side="left", padx=(5, 0))
        self.media_live_button = ttk.Button(
            media_controls,
            text="RETURN LIVE",
            style="Noob.TButton",
            command=self._close_media_preview,
        )
        self.media_live_button.pack(side="right")
        self.media_preview_detail = tk.Label(
            self.environment_settings,
            text="Select a stored item to preview it without changing camera media.",
            bg=SURFACE_RAISED,
            fg=MUTED,
            font=("Sans", 9),
        )
        self.media_preview_detail.pack(anchor="w", pady=(5, 0))

        stage = tk.Frame(
            self.root,
            bg=SURFACE,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        stage.pack(side="top", fill="both", expand=True, padx=16)
        self.image_canvas = tk.Canvas(
            stage,
            bg="#020405",
            highlightthickness=0,
            cursor="fleur",
        )
        self.image_canvas.pack(fill="both", expand=True, padx=8, pady=8)
        self.canvas_message_id = self.image_canvas.create_text(
            550,
            260,
            text="Waiting for a fresh target HDMI frame…",
            fill=MUTED,
            font=("Sans", 12),
        )
        self.image_canvas.bind("<Configure>", lambda _event: self._render_current_frame())
        self.image_canvas.bind("<ButtonPress-1>", self._pan_start)
        self.image_canvas.bind("<B1-Motion>", self._pan_move)
        self.image_canvas.bind("<ButtonRelease-1>", lambda _event: self._pan_end())
        self._set_buttons()

    def _badge(self, parent: tk.Widget, label: str) -> tuple[tk.Label, tk.Label]:
        frame = tk.Frame(parent, bg=BG, padx=8)
        frame.pack(side="left")
        dot = tk.Label(frame, text="●", bg=BG, fg=MUTED, font=("Sans", 10))
        dot.pack(side="left")
        text = tk.Label(frame, text=f"{label} · WAITING", bg=BG, fg=MUTED, font=("Sans", 9, "bold"))
        text.pack(side="left", padx=(5, 0))
        return dot, text

    def _set_badge(self, badge: tuple[tk.Label, tk.Label], label: str, state: str, color: str) -> None:
        badge[0].configure(fg=color)
        badge[1].configure(text=f"{label} · {state}", fg=color)

    def _offer(self, event: tuple[str, Any]) -> None:
        if event[0] == "frame":
            with self.frame_lock:
                self.latest_frame = event[1]
            return
        if event[0] in {
            "action",
            "action_error",
            "mode_action",
            "mode_action_error",
            "source_action",
            "source_action_error",
            "camera_action",
            "camera_action_error",
            "capture_action",
            "capture_action_error",
            "clip_started",
            "clip_start_error",
            "clip_stop",
            "clip_stop_error",
            "clip_job",
            "media_preview",
            "media_preview_error",
            "screenshot",
            "screenshot_error",
        }:
            while not self.stop_event.is_set():
                try:
                    self.events.put(event, timeout=0.1)
                    return
                except queue.Full:
                    continue
            return
        try:
            self.events.put_nowait(event)
        except queue.Full:
            # Status/error samples are disposable.  Never evict an older
            # action result: losing it would leave action_inflight latched and
            # could strand an operator between arm and disarm states.
            pass

    def _poll_loop(self) -> None:
        next_status = 0.0
        next_modes = 0.0
        next_storage = 0.0
        next_clip_job = 0.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now >= next_status:
                try:
                    self._offer(("status", self.client.status()))
                except LocalConsoleError as exc:
                    self._offer(("error", exc.code))
                next_status = now + self.STATUS_INTERVAL
            if now >= next_modes:
                try:
                    self._offer(("modes", self.client.video_modes()))
                except LocalConsoleError as exc:
                    self._offer(("error", exc.code))
                next_modes = now + 5.0
            if self.source == "environment" and now >= next_storage:
                try:
                    self._offer(("storage", self.client.camera_storage(limit=20)))
                except LocalConsoleError as exc:
                    if exc.code not in {
                        "camera_not_configured",
                        "camera_unavailable",
                        "camera_storage_unavailable",
                    }:
                        self._offer(("error", exc.code))
                next_storage = now + 5.0
            active_clip_job_id = self.active_clip_job_id
            if active_clip_job_id is not None and now >= next_clip_job:
                try:
                    self._offer(
                        ("clip_job", self.client.camera_clip_job(active_clip_job_id))
                    )
                except LocalConsoleError as exc:
                    self._offer(("clip_job_error", exc.code))
                next_clip_job = now + 0.4
            source = self.source
            if self.media_preview_item is None and self.media_preview_request is None:
                try:
                    self._offer(("frame", (source, self.client.frame(source))))
                except LocalConsoleError as exc:
                    if exc.code not in {
                        "video_unavailable",
                        "gateway_unavailable",
                        "camera_not_configured",
                        "camera_unavailable",
                        "camera_stream_disabled",
                        "environment_camera_unavailable",
                    }:
                        self._offer(("error", exc.code))
            self.stop_event.wait(self.FRAME_INTERVAL)

    def _drain_events(self) -> None:
        if self.stop_event.is_set():
            return
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self._apply_status(payload)
                elif kind == "modes":
                    self._apply_modes(payload)
                elif kind == "action":
                    self.action_inflight = False
                    self._apply_status(payload)
                    if self.hide_after_disarm:
                        self.hide_after_disarm = False
                        self._leave_fullscreen()
                        self.root.iconify()
                elif kind == "action_error":
                    self.action_inflight = False
                    self.hide_after_disarm = False
                    self._show_error(payload)
                elif kind == "mode_action":
                    self.action_inflight = False
                    self._apply_status(payload)
                    self.message.configure(
                        text="Capture output changed and a fresh video generation is active.",
                        fg=HEALTHY,
                    )
                elif kind == "mode_action_error":
                    self.action_inflight = False
                    self._show_error(payload)
                elif kind == "source_action":
                    self.action_inflight = False
                    state, source = payload
                    self._apply_status(state)
                    self._apply_source(source)
                elif kind == "source_action_error":
                    self.action_inflight = False
                    self.source_var.set(self.source)
                    self._show_error(payload)
                elif kind == "camera_action":
                    self.action_inflight = False
                    self._apply_environment_state(payload)
                    state_text = "enabled" if payload.sensor_enabled else "disabled"
                    self.message.configure(
                        text=(
                            f"Environment camera sensor and stream {state_text}. "
                            "USB power remains on."
                        ),
                        fg=HEALTHY if payload.sensor_enabled else MUTED,
                    )
                elif kind == "camera_action_error":
                    self.action_inflight = False
                    self._show_error(payload)
                elif kind == "capture_action":
                    self.action_inflight = False
                    item = payload
                    self.message.configure(
                        text=(
                            f"Saved {item.kind} to camera microSD: "
                            f"{item.created_at or item.item_id}"
                        ),
                        fg=HEALTHY,
                    )
                    self._refresh_storage()
                elif kind == "capture_action_error":
                    self.action_inflight = False
                    self._show_error(payload)
                elif kind == "clip_started":
                    self.action_inflight = False
                    self.active_clip_job_id = payload
                    self.active_clip_job = None
                    self.message.configure(
                        text="Camera clip started · use STOP CLIP to cancel the unpublished partial.",
                        fg=SIGNAL,
                    )
                elif kind == "clip_start_error":
                    self.action_inflight = False
                    self._show_error(payload)
                elif kind == "clip_stop":
                    self.action_inflight = False
                    if payload == "cancelled":
                        self.active_clip_job_id = None
                        self.active_clip_job = None
                        self.message.configure(
                            text="Camera clip cancelled; no partial clip was published.",
                            fg=MUTED,
                        )
                        self._refresh_storage()
                    else:
                        if self.active_clip_job is not None:
                            self.active_clip_job = replace(
                                self.active_clip_job, state="cancelling"
                            )
                        self.message.configure(
                            text="Camera clip cancellation requested; waiting for cancelled status…",
                            fg=WARN,
                        )
                elif kind == "clip_stop_error":
                    self.action_inflight = False
                    self._show_error(payload)
                elif kind == "clip_job":
                    if self.active_clip_job_id != payload.job_id:
                        continue
                    self.active_clip_job = payload
                    if payload.state == "complete":
                        self.active_clip_job_id = None
                        self.active_clip_job = None
                        self.message.configure(
                            text=f"Camera clip stored on microSD: {payload.media_id}",
                            fg=HEALTHY,
                        )
                        self._refresh_storage()
                    elif payload.state == "failed":
                        self.active_clip_job_id = None
                        self.active_clip_job = None
                        self._show_error(payload.error_code or "camera_clip_failed")
                    elif payload.state == "cancelled":
                        self.active_clip_job_id = None
                        self.active_clip_job = None
                        self.message.configure(
                            text="Camera clip cancelled; no partial clip was published.",
                            fg=MUTED,
                        )
                        self._refresh_storage()
                    elif payload.state == "cancelling":
                        self.message.configure(
                            text="Camera clip cancellation is in progress…",
                            fg=WARN,
                        )
                    else:
                        self.message.configure(
                            text=(
                                f"Camera clip {payload.state} · "
                                f"{payload.frames_written}/{payload.frames_target} frames · "
                                "STOP CLIP cancels the unpublished partial."
                            ),
                            fg=SIGNAL,
                        )
                elif kind == "clip_job_error":
                    self._show_error(payload)
                elif kind == "storage":
                    self._apply_storage(payload)
                elif kind == "storage_error":
                    self.storage_inflight = False
                    self._show_error(payload)
                elif kind == "media_preview":
                    item, frame_index, data = payload
                    request_key = (item.item_id, frame_index)
                    if (
                        self.source == "environment"
                        and self.media_preview_request == request_key
                    ):
                        self.media_preview_request = None
                        self.media_preview_inflight = False
                        self.media_preview_item = item
                        self.media_preview_frame_index = frame_index
                        self._apply_frame("environment", data)
                        self._update_media_preview_detail()
                        self.message.configure(
                            text=(
                                f"Stored {item.kind} preview · "
                                f"frame {frame_index + 1} of {item.frame_count} · "
                                "camera media remains unchanged."
                            ),
                            fg=SIGNAL,
                        )
                elif kind == "media_preview_error":
                    request_key, error_code = payload
                    if self.media_preview_request == request_key:
                        self.media_preview_request = None
                        self.media_preview_inflight = False
                        self._show_error(error_code)
                elif kind == "screenshot":
                    self.action_inflight = False
                    self.message.configure(
                        text=f"Private screenshot saved: {payload}", fg=HEALTHY
                    )
                elif kind == "screenshot_error":
                    self.action_inflight = False
                    self._show_error(payload)
                elif kind == "error":
                    self._show_error(payload)
        except queue.Empty:
            pass
        with self.frame_lock:
            latest_frame, self.latest_frame = self.latest_frame, None
        if latest_frame is not None:
            source, data = latest_frame
            if (
                source == self.source
                and self.media_preview_item is None
                and self.media_preview_request is None
            ):
                self._apply_frame(source, data)
        self._set_buttons()
        self.root.after(40, self._drain_events)

    def _apply_frame(self, source_name: str, data: bytes) -> None:
        try:
            from PIL import Image

            with Image.open(BytesIO(data)) as image:
                image.load()
                frame = image.convert("RGB")
            self.current_frame_bytes = data
            self.current_image = frame
            self._render_current_frame()
        except Exception:
            self._show_error("frame_invalid")

    def _render_current_frame(self) -> None:
        image = self.current_image
        if image is None:
            self.image_canvas.itemconfigure(
                self.canvas_message_id,
                text=(
                    "Waiting for a fresh target HDMI frame…"
                    if self.source == "target"
                    else "Environment camera view is not producing a fresh frame."
                ),
                state="normal",
            )
            self.image_canvas.coords(
                self.canvas_message_id,
                max(1, self.image_canvas.winfo_width()) // 2,
                max(1, self.image_canvas.winfo_height()) // 2,
            )
            if self.canvas_image_id is not None:
                self.image_canvas.delete(self.canvas_image_id)
                self.canvas_image_id = None
            return
        try:
            from PIL import Image, ImageTk

            viewport_width = max(1, self.image_canvas.winfo_width())
            viewport_height = max(1, self.image_canvas.winfo_height())
            rendered_width, rendered_height = image_render_size(
                image.width,
                image.height,
                viewport_width,
                viewport_height,
                self.zoom_var.get(),
            )
            self.pan_x, self.pan_y = clamp_pan(
                self.pan_x,
                self.pan_y,
                rendered_width,
                rendered_height,
                viewport_width,
                viewport_height,
            )
            scale = rendered_width / image.width
            # Render only the visible viewport.  At 200% this avoids allocating
            # a transient full 5120x3200 bitmap for each 2560x1600 frame.
            inverse_scale = 1.0 / scale
            center_x = viewport_width / 2 + self.pan_x
            center_y = viewport_height / 2 + self.pan_y
            affine = (
                inverse_scale,
                0,
                image.width / 2 - center_x * inverse_scale,
                0,
                inverse_scale,
                image.height / 2 - center_y * inverse_scale,
            )
            rendered = image.transform(
                (viewport_width, viewport_height),
                Image.Transform.AFFINE,
                affine,
                resample=Image.Resampling.BILINEAR,
                fillcolor="#020405",
            )
            self.photo = ImageTk.PhotoImage(rendered)
            if self.canvas_image_id is None:
                self.canvas_image_id = self.image_canvas.create_image(
                    0, 0, anchor="nw", image=self.photo
                )
            else:
                self.image_canvas.itemconfigure(
                    self.canvas_image_id, image=self.photo
                )
                self.image_canvas.coords(self.canvas_image_id, 0, 0)
            self.image_canvas.itemconfigure(
                self.canvas_message_id, state="hidden"
            )
            pannable = (
                rendered_width > viewport_width
                or rendered_height > viewport_height
            )
            self.image_canvas.configure(cursor="fleur" if pannable else "arrow")
        except Exception:
            self._show_error("frame_invalid")

    def _change_zoom(self, _event: Any = None) -> None:
        if self.zoom_var.get() not in ZOOM_MODES:
            self.zoom_var.set("FIT")
        self.pan_x = 0
        self.pan_y = 0
        self._render_current_frame()

    def _pan_start(self, event: Any) -> None:
        self.pan_anchor = (int(event.x), int(event.y))

    def _pan_move(self, event: Any) -> None:
        if self.pan_anchor is None:
            return
        x = int(event.x)
        y = int(event.y)
        self.pan_x += x - self.pan_anchor[0]
        self.pan_y += y - self.pan_anchor[1]
        self.pan_anchor = (x, y)
        self._render_current_frame()

    def _pan_end(self) -> None:
        self.pan_anchor = None

    def _apply_status(self, state: ViewState) -> None:
        self.current_state = state
        self._set_badge(self.video_badge, "VIDEO", "LIVE" if state.video_ready else "WAITING", HEALTHY if state.video_ready else WARN)
        self._apply_environment_state(state.environment_camera, update_state=False)
        hid_ready = state.serial_ready and state.keyboard_ready and state.pointer_ready
        self._set_badge(self.hid_badge, "HID", "READY" if hid_ready else "WAITING", HEALTHY if hid_ready else WARN)
        if state.local_armed and state.exclusive_grab:
            self._set_badge(self.control_badge, "CONTROL", "LOCAL", SIGNAL)
            if self.source == "target":
                self.message.configure(text="Target control is armed. Ctrl+Alt+Esc returns the keyboard and trackball to the uConsole.", fg=SIGNAL)
        elif state.remote_control_active:
            self._set_badge(self.control_badge, "CONTROL", "REMOTE", SIGNAL)
            if self.source == "target":
                self.message.configure(text="A remote operator owns input. Local video remains live and read-only.", fg=MUTED)
        elif state.release_required:
            self._set_badge(self.control_badge, "CONTROL", "RECOVERY", DANGER)
            if self.source == "target":
                self.message.configure(text="Input release is not yet confirmed. Local arming is blocked.", fg=DANGER)
        elif state.arm_allowed:
            self._set_badge(self.control_badge, "CONTROL", "AVAILABLE", HEALTHY)
            if self.source == "target":
                self.message.configure(text="Video is live. Arm target control when you are ready to leave the uConsole desktop.", fg=MUTED)
        else:
            self._set_badge(self.control_badge, "CONTROL", "UNAVAILABLE", WARN)
            if self.source == "target":
                self.message.configure(text="Waiting for the video, UART, keyboard, and trackball proof layers.", fg=WARN)
        if self.source == "environment":
            camera = state.environment_camera
            if self.media_preview_item is not None:
                item = self.media_preview_item
                self.message.configure(
                    text=(
                        f"Stored {item.kind} preview · frame "
                        f"{self.media_preview_frame_index + 1} of "
                        f"{item.frame_count} · target input remains disarmed."
                    ),
                    fg=SIGNAL,
                )
            elif not camera.sensor_enabled:
                self.message.configure(
                    text="Environment camera view is logically off. USB power remains on.",
                    fg=MUTED,
                )
            elif camera.frame_ready:
                self.message.configure(
                    text="Environment camera is live. Target input remains safely disarmed.",
                    fg=HEALTHY,
                )
            else:
                self.message.configure(
                    text="Environment camera is enabled but waiting for a fresh frame.",
                    fg=WARN,
                )
        self._sync_mode_selection(state.active_mode_id)
        self._update_mode_detail(state)

    def _apply_environment_state(
        self,
        camera: EnvironmentCameraState,
        *,
        update_state: bool = True,
    ) -> None:
        if (
            self.active_clip_job_id is None
            and camera.storage.active_job_id is not None
        ):
            self.active_clip_job_id = camera.storage.active_job_id
            self.active_clip_job = None
        if update_state and self.current_state is not None:
            self.current_state = replace(
                self.current_state, environment_camera=camera
            )
        if (
            self.source == "environment"
            and self.media_preview_item is None
            and (not camera.sensor_enabled or not camera.stream_enabled)
        ):
            self.current_frame_bytes = None
            self.current_image = None
            self.photo = None
            self._render_current_frame()
        if not camera.configured:
            self._set_badge(self.camera_badge, "CAMERA", "NOT SET", MUTED)
            self.camera_detail.configure(
                text="No environment camera is configured on this appliance",
                fg=MUTED,
            )
            if self.source == "environment":
                self._apply_source("target")
            return
        if not camera.reachable:
            self._set_badge(self.camera_badge, "CAMERA", "UNREACHABLE", DANGER)
        elif not camera.sensor_enabled or not camera.stream_enabled:
            self._set_badge(self.camera_badge, "CAMERA", "LOGICAL OFF", MUTED)
        elif camera.frame_ready:
            self._set_badge(self.camera_badge, "CAMERA", "LIVE", HEALTHY)
        else:
            self._set_badge(self.camera_badge, "CAMERA", "WAITING", WARN)
        age = (
            "—"
            if camera.last_frame_age_ms is None
            else f"{camera.last_frame_age_ms} ms"
        )
        self.camera_detail.configure(
            text=(
                f"Logical sensor/stream control · USB power remains on · "
                f"frame age {age} · {camera.viewers} viewer(s)"
            ),
            fg=MUTED if camera.reachable else DANGER,
        )
        storage = camera.storage
        if storage.available:
            self.storage_detail.configure(
                text=(
                    f"microSD {storage.state} · "
                    f"{format_byte_count(storage.free_bytes)} free of "
                    f"{format_byte_count(storage.total_bytes)} · "
                    f"{storage.media_count} item(s) · "
                    f"{'writable' if storage.writable else 'read only'}"
                ),
                fg=MUTED if storage.writable else WARN,
            )
        else:
            self.storage_detail.configure(
                text="microSD storage unavailable", fg=WARN
            )

    def _apply_storage(self, catalog: CameraStorageCatalog) -> None:
        self.storage_catalog = catalog
        self.storage_inflight = False
        if (
            self.media_preview_item is not None
            and all(
                item.item_id != self.media_preview_item.item_id
                for item in catalog.items
            )
        ):
            self._close_media_preview()
        if self.current_state is not None:
            camera = replace(
                self.current_state.environment_camera,
                storage=catalog.storage,
            )
            self.current_state = replace(
                self.current_state, environment_camera=camera
            )
            self._apply_environment_state(camera, update_state=False)
        self.storage_list.delete(0, "end")
        if not catalog.items:
            self.storage_list.insert("end", "No snapshots or clips reported")
        else:
            for item in catalog.items:
                self.storage_list.insert("end", item.display_label)
        if catalog.next_cursor is not None:
            self.storage_list.insert("end", "More items are available in storage")
        self._update_media_preview_detail()

    def _apply_modes(self, catalog: VideoModeCatalog) -> None:
        self.video_modes = catalog.modes
        self.mode_display_to_id = {
            mode.display_label: mode.mode_id for mode in self.video_modes
        }
        self.mode_box.configure(values=tuple(self.mode_display_to_id))
        self._sync_mode_selection(catalog.active_mode_id)

    def _sync_mode_selection(self, active_mode_id: str | None) -> None:
        if active_mode_id is None:
            return
        for mode in self.video_modes:
            if mode.mode_id == active_mode_id:
                self.mode_var.set(mode.display_label)
                return

    def _update_mode_detail(self, state: ViewState) -> None:
        if (
            state.requested_signal is not None
            and state.negotiated_signal is not None
            and state.requested_signal != state.negotiated_signal
        ):
            requested = state.requested_signal
            negotiated = state.negotiated_signal
            self.mode_detail.configure(
                text=(
                    f"Requested {requested[0]}×{requested[1]} @ {requested[2]} · "
                    f"negotiated {negotiated[0]}×{negotiated[1]} @ {negotiated[2]}"
                ),
                fg=WARN,
            )
        elif state.video_state in {"switching", "rolling_back"}:
            self.mode_detail.configure(text="Capture transition in progress…", fg=WARN)
        elif not state.source_timing_detectable:
            self.mode_detail.configure(
                text="Target timing is not detectable · choose capture output manually",
                fg=MUTED,
            )
        else:
            self.mode_detail.configure(text="Validated gateway capture output", fg=MUTED)

    def _show_error(self, code: str) -> None:
        messages = {
            "token_unavailable": "Local authentication is unavailable.",
            "gateway_unavailable": "The local gateway is unavailable.",
            "status_unavailable": "Gateway status is unavailable.",
            "video_unavailable": "Waiting for a fresh HDMI frame.",
            "frame_invalid": "The latest HDMI frame could not be decoded.",
            "lease_busy": "A remote operator currently owns target input.",
            "local_input_unavailable": "The uConsole keyboard or trackball is unavailable.",
            "local_input_disabled": "Appliance-local input is disabled in gateway configuration.",
            "arm_unconfirmed": "Arming was not confirmed; a fail-safe local disarm was requested.",
            "release_unconfirmed": "The gateway could not confirm that input was released.",
            "video_modes_unavailable": "Capture-output profiles are unavailable.",
            "invalid_video_mode_request": "The capture-output request was rejected locally.",
            "video_mode_unconfirmed": "The profile response was lost; refreshing authoritative video status.",
            "video_mode_stale": "Capture output changed elsewhere; the profile list will refresh.",
            "video_mode_switching": "Another capture-output transition is in progress.",
            "video_mode_invalid": "That capture-output profile is not validated on this appliance.",
            "control_active": "Disarm local or remote target control before changing capture output.",
            "video_mode_mismatch": "The device negotiated a different mode; the gateway attempted rollback.",
            "video_frame_dimensions_mismatch": "The captured frame did not match the requested mode; rollback was attempted.",
            "video_mode_probe_failed": "The capture device could not confirm its negotiated mode.",
            "video_mode_timeout": "The new capture output did not become ready before rollback.",
            "video_mode_rollback_failed": "Capture rollback also failed; choose the safe profile when the device returns.",
            "video_capture_failed": "The selected capture output failed; the gateway attempted rollback.",
            "camera_not_configured": "No environment camera is configured on this appliance.",
            "camera_unavailable": "The environment camera is not reachable.",
            "environment_camera_unavailable": "The environment camera is not reachable.",
            "camera_stream_disabled": "The environment camera view is logically off; USB power remains on.",
            "camera_status_unavailable": "Environment-camera status is unavailable.",
            "camera_state_unconfirmed": "The camera state change was not confirmed; authoritative status will refresh.",
            "invalid_camera_state_request": "The environment-camera state request was rejected locally.",
            "camera_storage_unavailable": "Camera microSD storage is unavailable.",
            "camera_media_invalid": "The stored camera media could not be decoded safely.",
            "camera_capture_unconfirmed": "The camera did not confirm the microSD capture.",
            "camera_clip_unconfirmed": "The camera clip job could not be confirmed; it was not replayed.",
            "camera_clip_failed": "The camera reported that the bounded clip job failed.",
            "camera_clip_cancelled": "The camera clip was cancelled; no partial clip was published.",
            "camera_clip_timeout": "The camera clip job did not reach a terminal state before the local timeout.",
            "invalid_camera_capture_request": "The microSD capture request was rejected locally.",
            "screenshot_unavailable": "A private screenshot could not be saved.",
            "fullscreen_unconfirmed": "Full screen could not be verified edge to edge. Use DESKTOP or Escape and retry.",
            "operation_in_progress": "Wait for the current arm, disarm, or capture-output action to finish before closing.",
        }
        self.message.configure(text=messages.get(code, "The requested local-console action failed safely."), fg=DANGER)

    def _set_buttons(self) -> None:
        state = self.current_state
        armed = bool(state and state.local_armed)
        target_active = self.source == "target"
        self.arm_button.configure(state="normal" if state and target_active and state.arm_allowed and not self.action_inflight else "disabled")
        self.disarm_button.configure(state="normal" if armed and not self.action_inflight else "disabled")
        self.desktop_button.configure(state="disabled" if self.action_inflight else "normal")
        camera = state.environment_camera if state else EnvironmentCameraState.unconfigured()
        source_enabled = not self.action_inflight and not self.closing
        self.target_source_button.configure(
            state="normal" if source_enabled else "disabled"
        )
        self.environment_source_button.configure(
            state=(
                "normal"
                if source_enabled and camera.configured
                else "disabled"
            )
        )
        self.screenshot_button.configure(
            state=(
                "normal"
                if self.current_frame_bytes is not None
                and not self.action_inflight
                and not self.closing
                else "disabled"
            )
        )
        mode_enabled = bool(
            state
            and target_active
            and state.mode_change_allowed
            and self.video_modes
            and not self.action_inflight
            and not self.closing
        )
        self.mode_box.configure(state="readonly" if mode_enabled else "disabled")
        camera_controls = bool(
            self.source == "environment"
            and camera.configured
            and camera.reachable
            and not self.action_inflight
            and not self.closing
        )
        self.camera_toggle_button.configure(
            text=(
                "TURN CAMERA VIEW OFF"
                if camera.sensor_enabled and camera.stream_enabled
                else "TURN CAMERA VIEW ON"
            ),
            state="normal" if camera_controls else "disabled",
        )
        clip_active = self.active_clip_job_id is not None
        capture_enabled = bool(
            camera_controls
            and camera.sensor_enabled
            and camera.stream_enabled
            and camera.storage.writable
            and not clip_active
        )
        capture_state = "normal" if capture_enabled else "disabled"
        self.camera_snapshot_button.configure(state=capture_state)
        if clip_active:
            stopping = (
                self.active_clip_job is not None
                and self.active_clip_job.state == "cancelling"
            )
            self.camera_clip_button.configure(
                text="STOPPING…" if stopping else "STOP CLIP",
                state=(
                    "normal"
                    if camera_controls and not stopping
                    else "disabled"
                ),
            )
        else:
            self.camera_clip_button.configure(
                text="10S CLIP TO SD", state=capture_state
            )
        self.storage_refresh_button.configure(
            state=(
                "normal"
                if camera_controls
                and camera.storage.available
                and not self.storage_inflight
                else "disabled"
            )
        )
        selected_media = self._selected_storage_item()
        media_read_available = bool(
            camera_controls
            and camera.storage.available
            and not self.media_preview_inflight
        )
        self.storage_open_button.configure(
            state=(
                "normal"
                if media_read_available and selected_media is not None
                else "disabled"
            )
        )
        preview = self.media_preview_item
        self.media_previous_button.configure(
            state=(
                "normal"
                if media_read_available
                and preview is not None
                and preview.kind == "clip"
                and self.media_preview_frame_index > 0
                else "disabled"
            )
        )
        self.media_next_button.configure(
            state=(
                "normal"
                if media_read_available
                and preview is not None
                and preview.kind == "clip"
                and self.media_preview_frame_index + 1 < preview.frame_count
                else "disabled"
            )
        )
        self.media_live_button.configure(
            state=(
                "normal"
                if self.source == "environment"
                and preview is not None
                and not self.media_preview_inflight
                else "disabled"
            )
        )

    def _action(self, name: str) -> None:
        if self.action_inflight or self.closing:
            return
        self.action_inflight = True
        self._set_buttons()

        def run() -> None:
            try:
                action = self.client.arm if name == "arm" else self.client.disarm
                result = self.action_gate.run(action)
                if result is not None:
                    self._offer(("action", result))
            except LocalConsoleError as exc:
                self._offer(("action_error", exc.code))

        threading.Thread(target=run, daemon=True, name=f"noob-local-{name}").start()

    def _arm(self) -> None:
        if self.source == "target":
            self._action("arm")

    def _disarm(self) -> None:
        self._action("disarm")

    def _choose_source(self, desired: str) -> None:
        self.source_var.set(self.source)
        if (
            desired not in VIEW_SOURCES
            or desired == self.source
            or self.action_inflight
            or self.closing
        ):
            return
        if desired == "environment":
            state = self.current_state
            if state is None or not state.environment_camera.configured:
                self._show_error("camera_not_configured")
                return
            # Leaving the target view is an authenticated release boundary,
            # even when the last status sample said local input was unarmed.
            self.action_inflight = True
            self._set_buttons()

            def run() -> None:
                try:
                    result = self.action_gate.run(self.client.disarm)
                    if result is not None:
                        self._offer(("source_action", (result, desired)))
                except LocalConsoleError as exc:
                    self._offer(("source_action_error", exc.code))

            threading.Thread(
                target=run,
                daemon=True,
                name="noob-local-source-release",
            ).start()
            return
        self._apply_source("target")

    def _apply_source(self, source_name: str) -> None:
        if source_name not in VIEW_SOURCES:
            return
        self.source = source_name
        self.source_var.set(source_name)
        self.media_preview_request = None
        self.media_preview_inflight = False
        self.media_preview_item = None
        self.media_preview_frame_index = 0
        self.current_frame_bytes = None
        self.current_image = None
        self.photo = None
        self.pan_x = 0
        self.pan_y = 0
        if source_name == "target":
            self.environment_settings.pack_forget()
            self.target_settings.pack(fill="x")
        else:
            self.target_settings.pack_forget()
            self.environment_settings.pack(fill="x")
            self._refresh_storage()
        self._update_media_preview_detail()
        self._render_current_frame()
        self._set_buttons()

    def _toggle_environment_camera(self) -> None:
        state = self.current_state
        if (
            state is None
            or self.source != "environment"
            or not state.environment_camera.configured
            or self.action_inflight
            or self.closing
        ):
            return
        camera = state.environment_camera
        enabled = not (camera.sensor_enabled and camera.stream_enabled)
        self.action_inflight = True
        self._set_buttons()

        def run() -> None:
            try:
                result = self.action_gate.run(
                    lambda: self.client.set_environment_enabled(
                        enabled, camera.generation
                    )
                )
                if result is not None:
                    self._offer(("camera_action", result))
            except LocalConsoleError as exc:
                self._offer(("camera_action_error", exc.code))

        threading.Thread(
            target=run,
            daemon=True,
            name="noob-local-camera-state",
        ).start()

    def _camera_snapshot(self) -> None:
        self._camera_capture("snapshot")

    def _camera_clip(self) -> None:
        if self.active_clip_job_id is None:
            self._start_camera_clip()
        else:
            self._stop_camera_clip()

    def _start_camera_clip(self) -> None:
        state = self.current_state
        if (
            state is None
            or self.source != "environment"
            or self.active_clip_job_id is not None
            or self.action_inflight
            or self.closing
        ):
            return
        camera = state.environment_camera
        if (
            not camera.reachable
            or not camera.sensor_enabled
            or not camera.stream_enabled
            or not camera.storage.writable
        ):
            self._show_error("camera_storage_unavailable")
            return
        self.action_inflight = True
        self.message.configure(
            text="Starting a bounded 10-second, 2 fps microSD frame collection…",
            fg=SIGNAL,
        )
        self._set_buttons()

        def run() -> None:
            try:
                job_id = self.action_gate.run(
                    lambda: self.client.start_camera_clip(
                        camera.generation, duration_seconds=10, fps=2
                    )
                )
                if job_id is not None:
                    self._offer(("clip_started", job_id))
            except LocalConsoleError as exc:
                self._offer(("clip_start_error", exc.code))

        threading.Thread(
            target=run,
            daemon=True,
            name="noob-local-camera-clip-start",
        ).start()

    def _stop_camera_clip(self) -> None:
        job_id = self.active_clip_job_id
        if job_id is None or self.action_inflight or self.closing:
            return
        self.action_inflight = True
        self.message.configure(
            text="Requesting bounded camera-clip cancellation…", fg=WARN
        )
        self._set_buttons()

        def run() -> None:
            try:
                stop_state = self.action_gate.run(
                    lambda: self.client.stop_camera_clip(job_id)
                )
                if stop_state is not None:
                    self._offer(("clip_stop", stop_state))
            except LocalConsoleError as exc:
                self._offer(("clip_stop_error", exc.code))

        threading.Thread(
            target=run,
            daemon=True,
            name="noob-local-camera-clip-stop",
        ).start()

    def _camera_capture(self, kind: str) -> None:
        state = self.current_state
        if (
            kind != "snapshot"
            or state is None
            or self.source != "environment"
            or self.action_inflight
            or self.closing
        ):
            return
        camera = state.environment_camera
        if (
            not camera.reachable
            or not camera.sensor_enabled
            or not camera.stream_enabled
            or not camera.storage.writable
        ):
            self._show_error("camera_storage_unavailable")
            return
        self.action_inflight = True
        self.message.configure(
            text=(
                "Requesting one microSD snapshot…"
            ),
            fg=SIGNAL,
        )
        self._set_buttons()

        def run() -> None:
            try:
                action = lambda: self.client.camera_snapshot(camera.generation)
                result = self.action_gate.run(action)
                if result is not None:
                    self._offer(("capture_action", result))
            except LocalConsoleError as exc:
                self._offer(("capture_action_error", exc.code))

        threading.Thread(
            target=run,
            daemon=True,
            name=f"noob-local-camera-{kind}",
        ).start()

    def _refresh_storage(self) -> None:
        if self.storage_inflight or self.closing:
            return
        state = self.current_state
        if (
            state is None
            or self.source != "environment"
            or not state.environment_camera.storage.available
        ):
            return
        self.storage_inflight = True
        self._set_buttons()

        def run() -> None:
            try:
                self._offer(("storage", self.client.camera_storage(limit=20)))
            except LocalConsoleError as exc:
                self._offer(("storage_error", exc.code))

        threading.Thread(
            target=run,
            daemon=True,
            name="noob-local-camera-storage",
        ).start()

    def _selected_storage_item(self) -> CameraStorageItem | None:
        try:
            selection = self.storage_list.curselection()
        except tk.TclError:
            return None
        if len(selection) != 1:
            return None
        index = int(selection[0])
        if not 0 <= index < len(self.storage_catalog.items):
            return None
        return self.storage_catalog.items[index]

    def _open_selected_media(self) -> None:
        item = self._selected_storage_item()
        if item is not None:
            self._request_media_preview(item, 0)

    def _navigate_media_preview(self, offset: int) -> None:
        item = self.media_preview_item
        if item is None or item.kind != "clip" or offset not in {-1, 1}:
            return
        self._request_media_preview(item, self.media_preview_frame_index + offset)

    def _request_media_preview(
        self, item: CameraStorageItem, frame_index: int
    ) -> None:
        if (
            self.source != "environment"
            or self.media_preview_inflight
            or self.closing
            or not _is_int(frame_index)
            or not 0 <= frame_index < item.frame_count
            or frame_index > MAX_CAMERA_CLIP_FRAME_INDEX
            or (item.kind == "snapshot" and frame_index != 0)
        ):
            return
        request_key = (item.item_id, frame_index)
        self.media_preview_request = request_key
        self.media_preview_inflight = True
        self.message.configure(
            text=(
                f"Loading stored {item.kind} preview · "
                f"frame {frame_index + 1} of {item.frame_count}…"
            ),
            fg=SIGNAL,
        )
        self._update_media_preview_detail()
        self._set_buttons()

        def run() -> None:
            try:
                data = (
                    self.client.camera_snapshot_content(item.item_id)
                    if item.kind == "snapshot"
                    else self.client.camera_clip_frame(item.item_id, frame_index)
                )
                self._offer(("media_preview", (item, frame_index, data)))
            except LocalConsoleError as exc:
                self._offer(("media_preview_error", (request_key, exc.code)))

        threading.Thread(
            target=run,
            daemon=True,
            name="noob-local-camera-media-preview",
        ).start()

    def _close_media_preview(self) -> None:
        self.media_preview_request = None
        self.media_preview_inflight = False
        self.media_preview_item = None
        self.media_preview_frame_index = 0
        self.current_frame_bytes = None
        self.current_image = None
        self.photo = None
        self.pan_x = 0
        self.pan_y = 0
        self._update_media_preview_detail()
        self._render_current_frame()
        self._set_buttons()

    def _update_media_preview_detail(self) -> None:
        item = self.media_preview_item
        if self.media_preview_inflight and self.media_preview_request is not None:
            self.media_preview_detail.configure(
                text=(
                    "Loading stored media by opaque camera ID · "
                    "no paths or media changes are accepted."
                ),
                fg=SIGNAL,
            )
        elif item is None:
            self.media_preview_detail.configure(
                text=(
                    "Select a stored item to preview it without changing "
                    "camera media."
                ),
                fg=MUTED,
            )
        else:
            self.media_preview_detail.configure(
                text=(
                    f"Stored {item.kind} · frame "
                    f"{self.media_preview_frame_index + 1} of {item.frame_count} · "
                    "read-only preview"
                ),
                fg=SIGNAL,
            )

    def _screenshot(self) -> None:
        if (
            self.current_frame_bytes is None
            or self.action_inflight
            or self.closing
        ):
            return
        data = self.current_frame_bytes
        source_name = self.source
        self.action_inflight = True
        self._set_buttons()

        def run() -> None:
            try:
                self._offer(
                    ("screenshot", save_screenshot(data, source_name))
                )
            except LocalConsoleError as exc:
                self._offer(("screenshot_error", exc.code))

        threading.Thread(
            target=run,
            daemon=True,
            name="noob-local-screenshot",
        ).start()

    def _select_video_mode(self, _event: Any = None) -> None:
        state = self.current_state
        mode_id = self.mode_display_to_id.get(self.mode_var.get())
        if (
            state is None
            or mode_id is None
            or mode_id == state.active_mode_id
            or not state.mode_change_allowed
            or self.action_inflight
            or self.closing
        ):
            self._sync_mode_selection(state.active_mode_id if state else None)
            return
        self.action_inflight = True
        self._set_buttons()

        def run() -> None:
            try:
                result = self.action_gate.run(
                    lambda: self.client.set_video_mode(
                        mode_id, state.video_generation
                    )
                )
                if result is not None:
                    self._offer(("mode_action", result))
            except LocalConsoleError as exc:
                self._offer(("mode_action_error", exc.code))

        threading.Thread(
            target=run,
            daemon=True,
            name="noob-local-video-mode",
        ).start()

    def _return_to_desktop(self) -> None:
        # Always confirm a local disarm before hiding.  This remains safe when
        # a remote controller owns the HTTP lease, and protects against a
        # stale status sample that has not yet observed local arming.
        if self.action_inflight or self.closing:
            return
        self.hide_after_disarm = True
        self._action("disarm")

    def _toggle_fullscreen(self) -> None:
        if self.fullscreen:
            self._leave_fullscreen()
            return
        self.fullscreen = True
        self.fullscreen_verify_attempt = 0
        self.fullscreen_controller.enter()
        self.fullscreen_button.configure(
            text="EXIT FULLSCREEN", style="Exit.TButton"
        )
        self.message.configure(
            text="Entering borderless full screen… Escape and DESKTOP remain available.",
            fg=MUTED,
        )
        self.root.after(90, self._verify_fullscreen)

    def _verify_fullscreen(self) -> None:
        if not self.fullscreen:
            return
        if window_covers_screen(self.root):
            self.message.configure(
                text="Borderless full screen verified edge to edge. Escape or EXIT FULLSCREEN restores the window.",
                fg=HEALTHY,
            )
            return
        self.fullscreen_verify_attempt += 1
        if self.fullscreen_verify_attempt < 4:
            # Some XFCE configurations apply the panel/work-area geometry one
            # event-loop turn after mapping.  Reassert the override geometry.
            self.fullscreen_controller.enforce()
            self.root.after(120, self._verify_fullscreen)
            return
        self._show_error("fullscreen_unconfirmed")

    def _leave_fullscreen(self) -> None:
        if not self.fullscreen:
            return
        self.fullscreen = False
        self.fullscreen_controller.exit(topmost=self.pinned)
        self.fullscreen_button.configure(
            text="FULL SCREEN", style="Noob.TButton"
        )
        self._render_current_frame()

    def _toggle_pin(self) -> None:
        self.pinned = not self.pinned
        if not self.fullscreen:
            self.root.attributes("-topmost", self.pinned)
        self.pin_button.configure(
            text=(
                "UNPIN AFTER EXIT"
                if self.fullscreen and self.pinned
                else "PIN AFTER EXIT"
                if self.fullscreen
                else "UNPIN"
                if self.pinned
                else "PIN"
            )
        )

    def _close(self) -> None:
        if self.closing:
            return
        if self.action_inflight:
            # Keep the viewer visible until the authenticated mutation result
            # is known.  This avoids abandoning a server-side capture
            # transition or racing an arm request with the final disarm.
            self._show_error("operation_in_progress")
            return
        self.closing = True
        try:
            self.action_gate.close(self.client.disarm)
        except LocalConsoleError as exc:
            # Fail closed: when the gateway cannot confirm a release, keep the
            # viewer visible so the operator sees the recovery instruction and
            # can use Ctrl+Alt+Esc.  A later close remains retryable.
            self.closing = False
            self._show_error(exc.code)
            return
        self.stop_event.set()
        self._leave_fullscreen()
        self.root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="N.O.O.B appliance-local target and environment viewer"
    )
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY)
    args = parser.parse_args()
    instance_lock: LocalConsoleInstanceLock | None = None
    try:
        instance_lock = acquire_local_console_instance_lock()
        origin = validate_loopback_gateway(args.gateway)
        token = load_local_token()
    except (ValueError, LocalConsoleError) as exc:
        if instance_lock is not None:
            instance_lock.close()
        code = exc.code if isinstance(exc, LocalConsoleError) else "gateway_invalid"
        raise SystemExit(f"N.O.O.B local console could not start ({code})") from None

    try:
        root = tk.Tk(className="NoobLocalConsole")
        NoobLocalConsole(root, GatewayClient(origin, token))
        root.mainloop()
    finally:
        instance_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
