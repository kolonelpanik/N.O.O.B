"""Strict TOML configuration for the N.O.O.B. gateway."""

from __future__ import annotations

import ipaddress
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import KEY_NAMES


class ConfigError(ValueError):
    """Raised when configuration is missing, unknown, or unsafe."""


VIDEO_FRAME_HARD_CEILING = 16 * 1024 * 1024
ENVIRONMENT_CAMERA_MEDIA_HARD_CEILING = 128 * 1024 * 1024
_VIDEO_MODE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ENVIRONMENT_CAMERA_DEVICE_ID = re.compile(r"^cam_[0-9a-f]{16}$")
_CAMERA_ALLOWED_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


@dataclass(frozen=True, slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    allow_non_loopback: bool = False


@dataclass(frozen=True, slots=True)
class AuthConfig:
    token_file: str = "/etc/noob/auth.key"
    local_token_file: str | None = None


@dataclass(frozen=True, slots=True)
class SerialConfig:
    device: str = "/dev/noob-uart"
    baudrate: int = 115200
    ack_timeout_ms: int = 750
    heartbeat_ms: int = 500
    watchdog_ms: int = 2000
    reconnect_ms: int = 500
    max_pending_commands: int = 8


@dataclass(frozen=True, slots=True)
class VideoProfile:
    mode_id: str
    label: str
    width: int
    height: int
    fps: int
    max_frame_bytes: int
    validated: bool
    pixel_format: str = "MJPG"

    def public_view(self) -> dict[str, Any]:
        return {
            "id": self.mode_id,
            "label": self.label,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "pixel_format": self.pixel_format,
            "max_frame_bytes": self.max_frame_bytes,
            "validated": self.validated,
        }

    def requested_view(self) -> dict[str, Any]:
        view = self.public_view()
        view.pop("validated")
        return view


DEFAULT_VIDEO_PROFILES = (
    VideoProfile(
        mode_id="720p20",
        label="1280 x 720 - 20 fps",
        width=1280,
        height=720,
        fps=20,
        max_frame_bytes=1_843_200,
        validated=True,
    ),
    VideoProfile(
        mode_id="720p30",
        label="1280 x 720 - 30 fps",
        width=1280,
        height=720,
        fps=30,
        max_frame_bytes=1_843_200,
        validated=False,
    ),
    VideoProfile(
        mode_id="1080p30",
        label="1920 x 1080 - 30 fps",
        width=1920,
        height=1080,
        fps=30,
        max_frame_bytes=4_147_200,
        validated=False,
    ),
    VideoProfile(
        mode_id="1440p30",
        label="2560 x 1440 - 30 fps",
        width=2560,
        height=1440,
        fps=30,
        max_frame_bytes=7_372_800,
        validated=False,
    ),
    VideoProfile(
        mode_id="1200p30",
        label="1920 x 1200 - 30 fps",
        width=1920,
        height=1200,
        fps=30,
        max_frame_bytes=4_608_000,
        validated=False,
    ),
    VideoProfile(
        mode_id="1600p30",
        label="2560 x 1600 - 30 fps",
        width=2560,
        height=1600,
        fps=30,
        max_frame_bytes=8_192_000,
        validated=False,
    ),
)


@dataclass(frozen=True, slots=True)
class VideoConfig:
    device: str = "/dev/noob-video"
    v4l2_ctl: str = "/usr/bin/v4l2-ctl"
    default_mode: str = "720p20"
    profiles: tuple[VideoProfile, ...] = DEFAULT_VIDEO_PROFILES
    max_frame_bytes: int = 8 * 1024 * 1024
    stale_seconds: float = 2.0
    switch_timeout_seconds: float = 5.0
    reconnect_ms: int = 1000
    max_clients: int = 4

    @property
    def default_profile(self) -> VideoProfile:
        for profile in self.profiles:
            if profile.mode_id == self.default_mode:
                return profile
        raise RuntimeError("validated configuration lost its default video profile")

    # Compatibility projections for callers which used the former fixed mode.
    @property
    def width(self) -> int:
        return self.default_profile.width

    @property
    def height(self) -> int:
        return self.default_profile.height

    @property
    def fps(self) -> int:
        return self.default_profile.fps


@dataclass(frozen=True, slots=True)
class EnvironmentCameraConfig:
    """Fixed, private-network upstream for the optional room camera.

    The caller never supplies a URL.  The gateway constructs every upstream
    request from this validated IP literal plus protocol-owned fixed paths.
    """

    enabled: bool = False
    host: str | None = None
    expected_device_id: str | None = None
    port: int = 80
    token_file: str | None = None
    connect_timeout_seconds: float = 1.5
    request_timeout_seconds: float = 4.0
    stream_read_timeout_seconds: float = 10.0
    status_interval_seconds: float = 2.0
    reconnect_ms: int = 1000
    stale_seconds: float = 3.0
    max_frame_bytes: int = 2 * 1024 * 1024
    max_metadata_bytes: int = 64 * 1024
    max_media_bytes: int = 64 * 1024 * 1024
    max_clients: int = 2
    max_page_size: int = 50
    max_clip_seconds: int = 30
    max_clip_fps: int = 5


@dataclass(frozen=True, slots=True)
class LimitsConfig:
    max_body_bytes: int = 4096
    max_type_chars: int = 512
    input_rate_per_second: int = 120
    input_burst: int = 60
    lease_ttl_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class LocalInputConfig:
    """Bounded physical controls built into the uConsole.

    Device paths deliberately default to stable evdev identities.  The input
    devices are opened for monitoring at startup but are only grabbed after an
    authenticated, explicit arm request.
    """

    enabled: bool = False
    keyboard_device: str = (
        "/dev/input/by-id/usb-ClockworkPI_uConsole_20230713-event-kbd"
    )
    pointer_device: str = (
        "/dev/input/by-id/usb-ClockworkPI_uConsole_20230713-event-mouse"
    )
    # Retained so an already-installed strict TOML file remains loadable after
    # local buttons switched to native down/up mapping. It is intentionally
    # ignored by LocalInputManager.
    long_press_ms: int = 650
    lease_idle_ms: int = 750
    reconnect_ms: int = 1000
    emergency_chord: tuple[str, ...] = (
        "LEFT_CONTROL",
        "LEFT_ALT",
        "ESCAPE",
    )


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    server: ServerConfig = ServerConfig()
    auth: AuthConfig = AuthConfig()
    serial: SerialConfig = SerialConfig()
    video: VideoConfig = VideoConfig()
    environment_camera: EnvironmentCameraConfig = EnvironmentCameraConfig()
    limits: LimitsConfig = LimitsConfig()
    local_input: LocalInputConfig = LocalInputConfig()


_SECTIONS = frozenset(
    (
        "server",
        "auth",
        "serial",
        "video",
        "environment_camera",
        "limits",
        "local_input",
    )
)


def _section(data: dict[str, Any], name: str, allowed: set[str]) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a table")
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"unknown {name} keys: {', '.join(sorted(unknown))}")
    return value


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} is outside its allowed range")
    return value


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be a number")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ConfigError(f"{name} is outside its allowed range")
    return result


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be a boolean")
    return value


def _path(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise ConfigError(f"{name} must be an absolute path")
    return value


def _evdev_path(value: Any, name: str) -> str:
    value = _path(value, name)
    if not value.startswith(("/dev/input/by-id/", "/dev/input/by-path/")):
        raise ConfigError(f"{name} must use /dev/input/by-id or /dev/input/by-path")
    return value


def _video_profiles(
    value: Any,
    *,
    global_max_frame_bytes: int,
) -> tuple[VideoProfile, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise ConfigError("video.profiles must contain 1-16 profile tables")
    profiles: list[VideoProfile] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(value):
        prefix = f"video.profiles[{index}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"{prefix} must be a table")
        allowed = {
            "mode_id",
            "label",
            "width",
            "height",
            "fps",
            "pixel_format",
            "max_frame_bytes",
            "validated",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise ConfigError(
                f"unknown {prefix} keys: {', '.join(sorted(unknown))}"
            )
        required = allowed - {"pixel_format"}
        missing = required - set(raw)
        if missing:
            raise ConfigError(
                f"missing {prefix} keys: {', '.join(sorted(missing))}"
            )
        mode_id = raw["mode_id"]
        if not isinstance(mode_id, str) or not _VIDEO_MODE_ID.fullmatch(mode_id):
            raise ConfigError(f"{prefix}.mode_id is invalid")
        if mode_id in seen_ids:
            raise ConfigError("video profile mode_id values must be unique")
        seen_ids.add(mode_id)
        label = raw["label"]
        if (
            not isinstance(label, str)
            or not 1 <= len(label) <= 64
            or any(ord(char) < 32 or ord(char) == 127 for char in label)
        ):
            raise ConfigError(f"{prefix}.label is invalid")
        pixel_format = raw.get("pixel_format", "MJPG")
        if pixel_format != "MJPG":
            raise ConfigError(f"{prefix}.pixel_format must be MJPG")
        max_frame_bytes = _integer(
            raw["max_frame_bytes"],
            f"{prefix}.max_frame_bytes",
            64 * 1024,
            VIDEO_FRAME_HARD_CEILING,
        )
        if max_frame_bytes > global_max_frame_bytes:
            raise ConfigError(
                f"{prefix}.max_frame_bytes exceeds video.max_frame_bytes"
            )
        profiles.append(
            VideoProfile(
                mode_id=mode_id,
                label=label,
                width=_integer(raw["width"], f"{prefix}.width", 160, 7680),
                height=_integer(raw["height"], f"{prefix}.height", 120, 4320),
                fps=_integer(raw["fps"], f"{prefix}.fps", 1, 120),
                pixel_format=pixel_format,
                max_frame_bytes=max_frame_bytes,
                validated=_boolean(raw["validated"], f"{prefix}.validated"),
            )
        )
    return tuple(profiles)


def load_config(path: str | Path) -> GatewayConfig:
    """Load a strict configuration file, rejecting unsafe implicit exposure."""

    with Path(path).open("rb") as handle:
        raw = tomllib.load(handle)
    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be a table")
    unknown_sections = set(raw) - _SECTIONS
    if unknown_sections:
        raise ConfigError(f"unknown sections: {', '.join(sorted(unknown_sections))}")

    server_raw = _section(raw, "server", {"host", "port", "allow_non_loopback"})
    host = server_raw.get("host", "127.0.0.1")
    if not isinstance(host, str):
        raise ConfigError("server.host must be a string IP address")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ConfigError("server.host must be an IP address") from exc
    allow_non_loopback = _boolean(
        server_raw.get("allow_non_loopback", False), "server.allow_non_loopback"
    )
    if not address.is_loopback and not allow_non_loopback:
        raise ConfigError("non-loopback binding requires allow_non_loopback=true")
    server = ServerConfig(
        host=host,
        port=_integer(server_raw.get("port", 8765), "server.port", 1, 65535),
        allow_non_loopback=allow_non_loopback,
    )

    auth_raw = _section(raw, "auth", {"token_file", "local_token_file"})
    local_token_file_raw = auth_raw.get("local_token_file")
    auth = AuthConfig(
        token_file=_path(
            auth_raw.get("token_file", "/etc/noob/auth.key"), "auth.token_file"
        ),
        local_token_file=(
            _path(local_token_file_raw, "auth.local_token_file")
            if local_token_file_raw is not None
            else None
        ),
    )
    if auth.local_token_file == auth.token_file:
        raise ConfigError("local console token must use a distinct file")

    serial_raw = _section(
        raw,
        "serial",
        {
            "device",
            "baudrate",
            "ack_timeout_ms",
            "heartbeat_ms",
            "watchdog_ms",
            "reconnect_ms",
            "max_pending_commands",
        },
    )
    serial = SerialConfig(
        device=_path(serial_raw.get("device", "/dev/noob-uart"), "serial.device"),
        baudrate=_integer(serial_raw.get("baudrate", 115200), "serial.baudrate", 1200, 3_000_000),
        ack_timeout_ms=_integer(
            serial_raw.get("ack_timeout_ms", 750), "serial.ack_timeout_ms", 100, 5000
        ),
        heartbeat_ms=_integer(
            serial_raw.get("heartbeat_ms", 500), "serial.heartbeat_ms", 100, 2000
        ),
        watchdog_ms=_integer(
            serial_raw.get("watchdog_ms", 2000), "serial.watchdog_ms", 500, 5000
        ),
        reconnect_ms=_integer(
            serial_raw.get("reconnect_ms", 500), "serial.reconnect_ms", 100, 30_000
        ),
        max_pending_commands=_integer(
            serial_raw.get("max_pending_commands", 8),
            "serial.max_pending_commands",
            1,
            64,
        ),
    )
    if serial.heartbeat_ms * 2 >= serial.watchdog_ms:
        raise ConfigError("serial watchdog must exceed two heartbeat intervals")

    video_raw = _section(
        raw,
        "video",
        {
            "device",
            "v4l2_ctl",
            "default_mode",
            "profiles",
            # Strict compatibility aliases for the former fixed-mode schema.
            "width",
            "height",
            "fps",
            "max_frame_bytes",
            "stale_seconds",
            "switch_timeout_seconds",
            "reconnect_ms",
            "max_clients",
        },
    )
    global_max_frame_bytes = _integer(
        video_raw.get("max_frame_bytes", 8 * 1024 * 1024),
        "video.max_frame_bytes",
        64 * 1024,
        VIDEO_FRAME_HARD_CEILING,
    )
    legacy_keys = {"width", "height", "fps"} & set(video_raw)
    if legacy_keys and legacy_keys != {"width", "height", "fps"}:
        raise ConfigError("legacy video width, height, and fps must be supplied together")
    legacy: tuple[int, int, int] | None = None
    if legacy_keys:
        legacy = (
            _integer(video_raw["width"], "video.width", 160, 7680),
            _integer(video_raw["height"], "video.height", 120, 4320),
            _integer(video_raw["fps"], "video.fps", 1, 120),
        )

    if legacy is not None and "profiles" not in video_raw and "default_mode" not in video_raw:
        legacy_mode = f"legacy-{legacy[0]}x{legacy[1]}-{legacy[2]}"
        profiles = (
            VideoProfile(
                mode_id=legacy_mode,
                label=f"Legacy {legacy[0]} x {legacy[1]} - {legacy[2]} fps",
                width=legacy[0],
                height=legacy[1],
                fps=legacy[2],
                max_frame_bytes=global_max_frame_bytes,
                validated=True,
            ),
        )
        default_mode = legacy_mode
    else:
        profiles = (
            _video_profiles(
                video_raw["profiles"],
                global_max_frame_bytes=global_max_frame_bytes,
            )
            if "profiles" in video_raw
            else DEFAULT_VIDEO_PROFILES
        )
        default_mode = video_raw.get("default_mode", "720p20")

    if any(
        profile.max_frame_bytes > global_max_frame_bytes for profile in profiles
    ):
        raise ConfigError("video profile frame bound exceeds video.max_frame_bytes")
    if not isinstance(default_mode, str) or not _VIDEO_MODE_ID.fullmatch(default_mode):
        raise ConfigError("video.default_mode is invalid")
    default_profiles = [
        profile for profile in profiles if profile.mode_id == default_mode
    ]
    if len(default_profiles) != 1:
        raise ConfigError("video.default_mode must name exactly one configured profile")
    default_profile = default_profiles[0]
    if not default_profile.validated:
        raise ConfigError("video.default_mode must name a validated profile")
    if legacy is not None:
        expected = (
            default_profile.width,
            default_profile.height,
            default_profile.fps,
        )
        if legacy != expected:
            raise ConfigError(
                "legacy video width/height/fps must match the default_mode profile"
            )

    video = VideoConfig(
        device=_path(video_raw.get("device", "/dev/noob-video"), "video.device"),
        v4l2_ctl=_path(video_raw.get("v4l2_ctl", "/usr/bin/v4l2-ctl"), "video.v4l2_ctl"),
        default_mode=default_mode,
        profiles=profiles,
        max_frame_bytes=global_max_frame_bytes,
        stale_seconds=_number(
            video_raw.get("stale_seconds", 2.0), "video.stale_seconds", 0.5, 30.0
        ),
        switch_timeout_seconds=_number(
            video_raw.get("switch_timeout_seconds", 5.0),
            "video.switch_timeout_seconds",
            1.0,
            30.0,
        ),
        reconnect_ms=_integer(
            video_raw.get("reconnect_ms", 1000), "video.reconnect_ms", 100, 30_000
        ),
        max_clients=_integer(video_raw.get("max_clients", 4), "video.max_clients", 1, 32),
    )

    environment_raw = _section(
        raw,
        "environment_camera",
        {
            "enabled",
            "host",
            "expected_device_id",
            "port",
            "token_file",
            "connect_timeout_seconds",
            "request_timeout_seconds",
            "stream_read_timeout_seconds",
            "status_interval_seconds",
            "reconnect_ms",
            "stale_seconds",
            "max_frame_bytes",
            "max_metadata_bytes",
            "max_media_bytes",
            "max_clients",
            "max_page_size",
            "max_clip_seconds",
            "max_clip_fps",
        },
    )
    environment_enabled = _boolean(
        environment_raw.get("enabled", False), "environment_camera.enabled"
    )
    environment_host_raw = environment_raw.get("host")
    environment_host: str | None = None
    if environment_host_raw is not None:
        if not isinstance(environment_host_raw, str):
            raise ConfigError("environment_camera.host must be an IP address")
        try:
            environment_address = ipaddress.ip_address(environment_host_raw)
        except ValueError as exc:
            raise ConfigError(
                "environment_camera.host must be a private IP address literal"
            ) from exc
        if not any(
            environment_address in network
            for network in _CAMERA_ALLOWED_NETWORKS
            if network.version == environment_address.version
        ):
            raise ConfigError(
                "environment_camera.host must be an RFC1918 or unique-local IP address"
            )
        environment_host = str(environment_address)

    environment_token_raw = environment_raw.get("token_file")
    environment_token_file = (
        _path(environment_token_raw, "environment_camera.token_file")
        if environment_token_raw is not None
        else None
    )
    if environment_enabled and environment_host is None:
        raise ConfigError(
            "enabled environment camera requires environment_camera.host"
        )
    if environment_enabled and environment_token_file is None:
        raise ConfigError(
            "enabled environment camera requires environment_camera.token_file"
        )
    environment_device_id_raw = environment_raw.get("expected_device_id")
    environment_device_id: str | None = None
    if environment_device_id_raw is not None:
        if (
            not isinstance(environment_device_id_raw, str)
            or _ENVIRONMENT_CAMERA_DEVICE_ID.fullmatch(environment_device_id_raw)
            is None
        ):
            raise ConfigError(
                "environment_camera.expected_device_id must match cam_[0-9a-f]{16}"
            )
        environment_device_id = environment_device_id_raw
    if environment_enabled and environment_device_id is None:
        raise ConfigError(
            "enabled environment camera requires environment_camera.expected_device_id"
        )
    if environment_token_file is not None and environment_token_file in {
        auth.token_file,
        auth.local_token_file,
    }:
        raise ConfigError(
            "environment camera token must be distinct from gateway credentials"
        )
    environment_camera = EnvironmentCameraConfig(
        enabled=environment_enabled,
        host=environment_host,
        expected_device_id=environment_device_id,
        port=_integer(
            environment_raw.get("port", 80), "environment_camera.port", 1, 65535
        ),
        token_file=environment_token_file,
        connect_timeout_seconds=_number(
            environment_raw.get("connect_timeout_seconds", 1.5),
            "environment_camera.connect_timeout_seconds",
            0.2,
            10.0,
        ),
        request_timeout_seconds=_number(
            environment_raw.get("request_timeout_seconds", 4.0),
            "environment_camera.request_timeout_seconds",
            0.5,
            30.0,
        ),
        stream_read_timeout_seconds=_number(
            environment_raw.get("stream_read_timeout_seconds", 10.0),
            "environment_camera.stream_read_timeout_seconds",
            2.0,
            60.0,
        ),
        status_interval_seconds=_number(
            environment_raw.get("status_interval_seconds", 2.0),
            "environment_camera.status_interval_seconds",
            0.5,
            30.0,
        ),
        reconnect_ms=_integer(
            environment_raw.get("reconnect_ms", 1000),
            "environment_camera.reconnect_ms",
            100,
            30_000,
        ),
        stale_seconds=_number(
            environment_raw.get("stale_seconds", 3.0),
            "environment_camera.stale_seconds",
            0.5,
            30.0,
        ),
        max_frame_bytes=_integer(
            environment_raw.get("max_frame_bytes", 2 * 1024 * 1024),
            "environment_camera.max_frame_bytes",
            64 * 1024,
            VIDEO_FRAME_HARD_CEILING,
        ),
        max_metadata_bytes=_integer(
            environment_raw.get("max_metadata_bytes", 64 * 1024),
            "environment_camera.max_metadata_bytes",
            1024,
            1024 * 1024,
        ),
        max_media_bytes=_integer(
            environment_raw.get("max_media_bytes", 64 * 1024 * 1024),
            "environment_camera.max_media_bytes",
            64 * 1024,
            ENVIRONMENT_CAMERA_MEDIA_HARD_CEILING,
        ),
        max_clients=_integer(
            environment_raw.get("max_clients", 2),
            "environment_camera.max_clients",
            1,
            8,
        ),
        max_page_size=_integer(
            environment_raw.get("max_page_size", 50),
            "environment_camera.max_page_size",
            1,
            50,
        ),
        max_clip_seconds=_integer(
            environment_raw.get("max_clip_seconds", 30),
            "environment_camera.max_clip_seconds",
            1,
            30,
        ),
        max_clip_fps=_integer(
            environment_raw.get("max_clip_fps", 5),
            "environment_camera.max_clip_fps",
            1,
            5,
        ),
    )
    if environment_camera.max_media_bytes < environment_camera.max_frame_bytes:
        raise ConfigError(
            "environment_camera.max_media_bytes must cover max_frame_bytes"
        )

    limits_raw = _section(
        raw,
        "limits",
        {
            "max_body_bytes",
            "max_type_chars",
            "input_rate_per_second",
            "input_burst",
            "lease_ttl_seconds",
        },
    )
    limits = LimitsConfig(
        max_body_bytes=_integer(
            limits_raw.get("max_body_bytes", 4096), "limits.max_body_bytes", 512, 65_536
        ),
        max_type_chars=_integer(
            limits_raw.get("max_type_chars", 512), "limits.max_type_chars", 1, 4096
        ),
        input_rate_per_second=_integer(
            limits_raw.get("input_rate_per_second", 120),
            "limits.input_rate_per_second",
            1,
            1000,
        ),
        input_burst=_integer(
            limits_raw.get("input_burst", 60), "limits.input_burst", 1, 1000
        ),
        lease_ttl_seconds=_number(
            limits_raw.get("lease_ttl_seconds", 5.0),
            "limits.lease_ttl_seconds",
            1.0,
            60.0,
        ),
    )

    local_raw = _section(
        raw,
        "local_input",
        {
            "enabled",
            "keyboard_device",
            "pointer_device",
            "long_press_ms",
            "lease_idle_ms",
            "reconnect_ms",
            "emergency_chord",
        },
    )
    chord_raw = local_raw.get(
        "emergency_chord", ["LEFT_CONTROL", "LEFT_ALT", "ESCAPE"]
    )
    if (
        not isinstance(chord_raw, list)
        or not 2 <= len(chord_raw) <= 4
        or any(not isinstance(key, str) for key in chord_raw)
        or len(set(chord_raw)) != len(chord_raw)
        or any(key not in KEY_NAMES for key in chord_raw)
    ):
        raise ConfigError(
            "local_input.emergency_chord must contain 2-4 unique supported keys"
        )
    local_input = LocalInputConfig(
        enabled=_boolean(local_raw.get("enabled", False), "local_input.enabled"),
        keyboard_device=_evdev_path(
            local_raw.get(
                "keyboard_device",
                "/dev/input/by-id/usb-ClockworkPI_uConsole_20230713-event-kbd",
            ),
            "local_input.keyboard_device",
        ),
        pointer_device=_evdev_path(
            local_raw.get(
                "pointer_device",
                "/dev/input/by-id/usb-ClockworkPI_uConsole_20230713-event-mouse",
            ),
            "local_input.pointer_device",
        ),
        long_press_ms=_integer(
            local_raw.get("long_press_ms", 650),
            "local_input.long_press_ms",
            250,
            2000,
        ),
        lease_idle_ms=_integer(
            local_raw.get("lease_idle_ms", 750),
            "local_input.lease_idle_ms",
            100,
            4000,
        ),
        reconnect_ms=_integer(
            local_raw.get("reconnect_ms", 1000),
            "local_input.reconnect_ms",
            100,
            30_000,
        ),
        emergency_chord=tuple(chord_raw),
    )
    if local_input.keyboard_device == local_input.pointer_device:
        raise ConfigError("local input keyboard and pointer devices must be distinct")
    if local_input.lease_idle_ms >= int(limits.lease_ttl_seconds * 1000):
        raise ConfigError("local input idle release must be shorter than the control lease")

    return GatewayConfig(
        server=server,
        auth=auth,
        serial=serial,
        video=video,
        environment_camera=environment_camera,
        limits=limits,
        local_input=local_input,
    )
