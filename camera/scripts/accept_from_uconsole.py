#!/usr/bin/env python3
"""Run bounded, direct acceptance checks against one N.O.O.B. camera.

The bearer value is read from an owner-only file and used only in the
Authorization header.  Operational output is one compact, nonsecret JSON
document; raw responses, addresses, identifiers, tokens, and frames are never
printed.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Sequence


RESULT_SCHEMA: Final = "noob.camera.acceptance.v1"
DEVICE_ID = re.compile(r"^cam_[0-9a-f]{16}$")
BOOT_ID = re.compile(r"^b_[0-9a-f]{16}$")
MEDIA_ID = re.compile(r"^m_[0-9a-f]{32}$")
JOB_ID = re.compile(r"^j_[0-9a-f]{32}$")
TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,96}$")
EXPECTED_CAPABILITIES: Final = {"stream", "snapshot", "sensor_state", "sd_media"}
WELL_KNOWN_FIELDS: Final = {
    "api",
    "device_id",
    "role",
    "api_base",
    "authentication",
    "capabilities",
}
STATUS_FIELDS: Final = {
    "api",
    "device_id",
    "boot_id",
    "uptime_ms",
    "firmware",
    "provisioning",
    "wifi",
    "camera",
    "storage",
    "reset_reason",
    "heap",
}
CAMERA_FIELDS: Final = {
    "configured_pinmap",
    "pinmap_verified",
    "enabled",
    "initialized",
    "generation",
    "sensor",
    "psram",
    "width",
    "height",
    "pixel_format",
    "frame_sequence",
    "last_frame_age_ms",
    "fresh",
    "last_error",
}
SENSOR_FIELDS: Final = {
    "detected",
    "name",
    "pid",
    "ov2640_verified",
    "supported_sensor_verified",
}
SUPPORTED_SENSORS: Final = {("OV2640", 0x26), ("OV3660", 0x3660)}
PSRAM_FIELDS: Final = {"initialized", "size_bytes"}
DUMMY_BEARER: Final = "noob_acceptance_invalid_token_00000000"
DUMMY_QUERY: Final = "noob_acceptance_query_dummy"
MAX_JSON_BYTES: Final = 64 * 1024
MAX_JPEG_BYTES: Final = 262_144
MAX_TOKEN_BYTES: Final = 128


class AcceptanceError(RuntimeError):
    """A bounded failure that is safe to place in the result document."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise AcceptanceError("invalid_arguments")


@dataclass(frozen=True, slots=True)
class AcceptanceConfig:
    host: str
    port: int
    expected_device_id: str
    frame_count: int = 3
    request_timeout: float = 3.0
    progress_timeout: float = 6.0
    storage_test: bool = False
    delete_created_media: bool = False


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class Frame:
    sequence: int
    boot_id: str
    digest: bytes


@dataclass(frozen=True, slots=True)
class AcceptanceSummary:
    checks: int
    frames: int
    storage: str


def _fail(code: str) -> None:
    raise AcceptanceError(code)


def validate_private_host(host: str, *, allow_loopback: bool = False) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise AcceptanceError("invalid_host") from error
    if address.is_loopback:
        if allow_loopback:
            return address.compressed
        _fail("invalid_host")
    if address.is_unspecified or address.is_multicast or address.is_link_local:
        _fail("invalid_host")
    private_v4 = isinstance(address, ipaddress.IPv4Address) and any(
        address in network
        for network in (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )
    )
    private_v6 = isinstance(address, ipaddress.IPv6Address) and address in ipaddress.ip_network(
        "fc00::/7"
    )
    if not (private_v4 or private_v6):
        _fail("invalid_host")
    return address.compressed


def validate_config(config: AcceptanceConfig, *, allow_loopback: bool = False) -> None:
    validate_private_host(config.host, allow_loopback=allow_loopback)
    if type(config.port) is not int or not 1 <= config.port <= 65535:
        _fail("invalid_port")
    if not DEVICE_ID.fullmatch(config.expected_device_id):
        _fail("invalid_device_id")
    if type(config.frame_count) is not int or not 2 <= config.frame_count <= 5:
        _fail("invalid_frame_count")
    if not 0.5 <= config.request_timeout <= 10 or not 2 <= config.progress_timeout <= 20:
        _fail("invalid_timeout")
    if config.delete_created_media and not config.storage_test:
        _fail("delete_requires_storage_test")


def load_token(path: Path, *, expected_uid: int) -> str:
    if not path.is_absolute():
        _fail("invalid_token_file")
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as error:
        raise AcceptanceError("invalid_token_file") from error
    if resolved != path or stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        _fail("invalid_token_file")
    if metadata.st_uid != expected_uid or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}:
        _fail("unsafe_token_file")
    if metadata.st_size <= 0 or metadata.st_size > MAX_TOKEN_BYTES:
        _fail("invalid_token_file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != expected_uid
            or stat.S_IMODE(opened.st_mode) not in {0o400, 0o600}
            or opened.st_size <= 0
            or opened.st_size > MAX_TOKEN_BYTES
        ):
            os.close(descriptor)
            _fail("unsafe_token_file")
        with os.fdopen(descriptor, "rb") as handle:
            raw = handle.read(MAX_TOKEN_BYTES + 1)
    except AcceptanceError:
        raise
    except OSError as error:
        raise AcceptanceError("invalid_token_file") from error
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if b"\n" in raw or b"\r" in raw or b"\x00" in raw:
        _fail("invalid_token_file")
    try:
        value = raw.decode("ascii")
    except UnicodeError as error:
        raise AcceptanceError("invalid_token_file") from error
    if not TOKEN.fullmatch(value):
        _fail("invalid_token_file")
    return value


class CameraClient:
    def __init__(self, config: AcceptanceConfig, token: str) -> None:
        self.config = config
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        *,
        authentication: str = "real",
        payload: dict[str, object] | None = None,
        maximum: int = MAX_JSON_BYTES,
    ) -> Response:
        if not path.startswith("/") or "\r" in path or "\n" in path:
            _fail("unsafe_request_path")
        headers = {"Accept": "application/json", "Connection": "close"}
        if authentication == "real":
            headers["Authorization"] = f"Bearer {self.token}"
        elif authentication == "dummy":
            headers["Authorization"] = f"Bearer {DUMMY_BEARER}"
        elif authentication != "none":
            _fail("invalid_auth_mode")
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if len(body) > 256:
                _fail("request_body_too_large")
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection(
            self.config.host, self.config.port, timeout=self.config.request_timeout
        )
        try:
            connection.request(method, path, body=body, headers=headers)
            raw = connection.getresponse()
            content_length = raw.getheader("Content-Length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except ValueError:
                    _fail("invalid_content_length")
                if declared < 0 or declared > maximum:
                    _fail("response_too_large")
            response_body = raw.read(maximum + 1)
            if len(response_body) > maximum:
                _fail("response_too_large")
            return Response(
                status=raw.status,
                headers={key.lower(): value for key, value in raw.getheaders()},
                body=response_body,
            )
        except AcceptanceError:
            raise
        except (OSError, http.client.HTTPException) as error:
            raise AcceptanceError("transport_failed") from error
        finally:
            connection.close()


def _json(response: Response, expected_status: int) -> dict[str, object]:
    if response.status != expected_status:
        _fail("unexpected_http_status")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type != "application/json":
        _fail("unexpected_content_type")
    try:
        value = json.loads(response.body)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AcceptanceError("invalid_json") from error
    if not isinstance(value, dict):
        _fail("invalid_json")
    return value


def _expect_error(response: Response, status: int, code: str) -> None:
    payload = _json(response, status)
    if set(payload) != {"error"} or not isinstance(payload["error"], dict):
        _fail("invalid_error_envelope")
    error = payload["error"]
    if set(error) != {"code", "message"} or error.get("code") != code:
        _fail("unexpected_error_code")


def _require_exact_fields(value: object, fields: set[str], code: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        _fail(code)
    return value


def _integer(value: object, code: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail(code)
    return value


def validate_well_known(payload: dict[str, object], expected_device_id: str) -> None:
    if set(payload) != WELL_KNOWN_FIELDS:
        _fail("well_known_fields")
    if (
        payload.get("api") != 1
        or payload.get("device_id") != expected_device_id
        or payload.get("role") != "environment"
        or payload.get("api_base") != "/api/v1"
        or payload.get("authentication") != "bearer"
    ):
        _fail("well_known_identity")
    capabilities = payload.get("capabilities")
    if (
        not isinstance(capabilities, list)
        or any(not isinstance(item, str) for item in capabilities)
        or set(capabilities) != EXPECTED_CAPABILITIES
        or len(capabilities) != len(EXPECTED_CAPABILITIES)
    ):
        _fail("well_known_capabilities")


def validate_status_shape(
    payload: dict[str, object], expected_device_id: str, expected_boot_id: str | None = None
) -> tuple[str, dict[str, object]]:
    if set(payload) != STATUS_FIELDS or payload.get("api") != 1:
        _fail("status_fields")
    if payload.get("device_id") != expected_device_id:
        _fail("status_device_id")
    boot_id = payload.get("boot_id")
    if not isinstance(boot_id, str) or not BOOT_ID.fullmatch(boot_id):
        _fail("status_boot_id")
    if expected_boot_id is not None and boot_id != expected_boot_id:
        _fail("boot_id_changed")
    _integer(payload.get("uptime_ms"), "status_uptime")
    camera = _require_exact_fields(payload.get("camera"), CAMERA_FIELDS, "camera_fields")
    _require_exact_fields(camera.get("sensor"), SENSOR_FIELDS, "sensor_fields")
    _require_exact_fields(camera.get("psram"), PSRAM_FIELDS, "psram_fields")
    _integer(camera.get("generation"), "camera_generation")
    _integer(camera.get("frame_sequence"), "frame_sequence")
    return boot_id, camera


def validate_ready_camera(camera: dict[str, object]) -> None:
    sensor = camera["sensor"]
    psram = camera["psram"]
    assert isinstance(sensor, dict) and isinstance(psram, dict)
    identity = (sensor.get("name"), sensor.get("pid"))
    ov2640_identity = identity == ("OV2640", 0x26)
    if (
        camera.get("configured_pinmap") != "ai_thinker_candidate"
        or camera.get("pinmap_verified") is not True
        or camera.get("enabled") is not True
        or camera.get("initialized") is not True
        or sensor.get("detected") is not True
        or identity not in SUPPORTED_SENSORS
        or sensor.get("supported_sensor_verified") is not True
        or sensor.get("ov2640_verified") is not ov2640_identity
        or psram.get("initialized") is not True
        or _integer(psram.get("size_bytes"), "psram_size", 1) <= 0
        or camera.get("width") != 640
        or camera.get("height") != 480
        or camera.get("pixel_format") != "jpeg"
        or camera.get("fresh") is not True
        or camera.get("last_error") is not None
    ):
        _fail("camera_not_ready")
    age = _integer(camera.get("last_frame_age_ms"), "frame_age")
    if age > 2000:
        _fail("frame_stale")


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
        _fail("jpeg_markers")
    position = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while position + 4 <= len(data) - 2:
        if data[position] != 0xFF:
            _fail("jpeg_structure")
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            _fail("jpeg_structure")
        marker = data[position]
        position += 1
        if marker == 0xDA:
            break
        if marker in {0x01, *range(0xD0, 0xDA)}:
            continue
        if position + 2 > len(data):
            _fail("jpeg_structure")
        length = int.from_bytes(data[position : position + 2], "big")
        if length < 2 or position + length > len(data):
            _fail("jpeg_structure")
        if marker in sof_markers:
            if length < 7:
                _fail("jpeg_structure")
            height = int.from_bytes(data[position + 3 : position + 5], "big")
            width = int.from_bytes(data[position + 5 : position + 7], "big")
            if width <= 0 or height <= 0:
                _fail("jpeg_dimensions")
            return width, height
        position += length
    _fail("jpeg_dimensions")


def fetch_snapshot(client: CameraClient, expected_boot_id: str) -> Frame:
    response = client.request(
        "GET", "/api/v1/camera/snapshot.jpg", maximum=MAX_JPEG_BYTES
    )
    if response.status != 200:
        _fail("snapshot_status")
    if response.headers.get("content-type", "").split(";", 1)[0].strip() != "image/jpeg":
        _fail("snapshot_content_type")
    if response.headers.get("cache-control") != "no-store":
        _fail("snapshot_cache_policy")
    boot_id = response.headers.get("x-noob-boot-id", "")
    if boot_id != expected_boot_id:
        _fail("snapshot_boot_id")
    try:
        sequence = int(response.headers.get("x-noob-frame-sequence", ""))
    except ValueError as error:
        raise AcceptanceError("snapshot_sequence") from error
    if sequence <= 0:
        _fail("snapshot_sequence")
    if jpeg_dimensions(response.body) != (640, 480):
        _fail("snapshot_dimensions")
    return Frame(sequence, boot_id, hashlib.sha256(response.body).digest())


def collect_progress(
    client: CameraClient, expected_boot_id: str, count: int, initial: Frame | None = None
) -> list[Frame]:
    frames = [] if initial is None else [initial]
    deadline = time.monotonic() + client.config.progress_timeout
    requests = 0
    while len(frames) < count and time.monotonic() < deadline and requests < 20:
        frame = fetch_snapshot(client, expected_boot_id)
        requests += 1
        if not frames or (
            frame.sequence > frames[-1].sequence and frame.digest != frames[-1].digest
        ):
            frames.append(frame)
        else:
            time.sleep(0.1)
    if len(frames) != count:
        _fail("frame_progress_timeout")
    return frames


def _state_change(client: CameraClient, enabled: bool, generation: int) -> dict[str, object]:
    payload = _json(
        client.request(
            "PUT",
            "/api/v1/camera/state",
            payload={"enabled": enabled, "expected_generation": generation},
        ),
        200,
    )
    if set(payload) != {"enabled", "generation", "initialized"}:
        _fail("state_response_fields")
    if payload.get("enabled") is not enabled:
        _fail("state_transition")
    next_generation = _integer(payload.get("generation"), "state_generation")
    if next_generation <= generation:
        _fail("generation_did_not_advance")
    return payload


def _poll_ready_status(
    client: CameraClient,
    expected_device_id: str,
    expected_boot_id: str,
    generation: int,
    after_sequence: int,
) -> dict[str, object]:
    deadline = time.monotonic() + client.config.progress_timeout
    while time.monotonic() < deadline:
        payload = _json(client.request("GET", "/api/v1/status"), 200)
        _, camera = validate_status_shape(payload, expected_device_id, expected_boot_id)
        current_generation = _integer(camera.get("generation"), "camera_generation")
        if current_generation != generation:
            _fail("unexpected_generation")
        if camera.get("fresh") is True and _integer(
            camera.get("frame_sequence"), "frame_sequence"
        ) > after_sequence:
            validate_ready_camera(camera)
            return camera
        time.sleep(0.15)
    _fail("reenable_frame_timeout")


def _best_effort_reenable(
    client: CameraClient, expected_device_id: str, expected_boot_id: str
) -> None:
    try:
        payload = _json(client.request("GET", "/api/v1/status"), 200)
        _, camera = validate_status_shape(payload, expected_device_id, expected_boot_id)
        if camera.get("enabled") is False:
            _state_change(
                client, True, _integer(camera.get("generation"), "camera_generation")
            )
    except Exception:
        pass


def _validate_media_item(item: object, kind: str) -> str:
    if not isinstance(item, dict):
        _fail("invalid_media_item")
    media_id = item.get("id")
    if not isinstance(media_id, str) or not MEDIA_ID.fullmatch(media_id):
        _fail("invalid_media_id")
    if (
        item.get("kind") != kind
        or item.get("state") != "complete"
        or item.get("width") != 640
        or item.get("height") != 480
    ):
        _fail("invalid_media_item")
    return media_id


def run_storage_acceptance(
    client: CameraClient, generation: int, *, delete_created: bool
) -> str:
    storage = _json(client.request("GET", "/api/v1/storage"), 200)
    if storage.get("mounted") is not True or storage.get("writable") is not True:
        _fail("storage_not_writable")
    created: list[str] = []
    snapshot = _json(
        client.request(
            "POST",
            "/api/v1/storage/snapshots",
            payload={"expected_generation": generation},
        ),
        201,
    )
    if set(snapshot) != {"item"}:
        _fail("storage_snapshot_response")
    created.append(_validate_media_item(snapshot["item"], "snapshot"))
    clip = _json(
        client.request(
            "POST",
            "/api/v1/storage/clips",
            payload={"duration_ms": 1000, "fps": 1, "expected_generation": generation},
        ),
        202,
    )
    job_id = clip.get("job_id")
    if set(clip) != {"job_id", "state"} or clip.get("state") != "queued":
        _fail("storage_clip_response")
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        _fail("invalid_job_id")
    deadline = time.monotonic() + 10
    clip_media_id = None
    while time.monotonic() < deadline:
        job = _json(client.request("GET", f"/api/v1/jobs/{job_id}"), 200)
        state = job.get("state")
        if state == "complete":
            candidate = job.get("media_id")
            if not isinstance(candidate, str) or not MEDIA_ID.fullmatch(candidate):
                _fail("invalid_media_id")
            clip_media_id = candidate
            break
        if state in {"failed", "cancelled"}:
            _fail("storage_clip_failed")
        if state not in {"queued", "running"}:
            _fail("storage_job_state")
        time.sleep(0.2)
    if clip_media_id is None:
        _fail("storage_clip_timeout")
    created.append(clip_media_id)
    for media_id, kind in zip(created, ("snapshot", "clip"), strict=True):
        metadata = _json(client.request("GET", f"/api/v1/media/{media_id}"), 200)
        if set(metadata) != {"item"} or _validate_media_item(metadata["item"], kind) != media_id:
            _fail("storage_metadata")
    if delete_created:
        for media_id in created:
            deleted = _json(client.request("DELETE", f"/api/v1/media/{media_id}"), 200)
            if set(deleted) != {"id", "deleted"} or deleted != {
                "id": media_id,
                "deleted": True,
            }:
                _fail("storage_delete_failed")
        return "created_and_deleted"
    return "created"


def run_acceptance(
    config: AcceptanceConfig, token: str, *, allow_loopback_for_test: bool = False
) -> AcceptanceSummary:
    validate_config(config, allow_loopback=allow_loopback_for_test)
    if not TOKEN.fullmatch(token):
        _fail("invalid_token")
    client = CameraClient(config, token)
    checks = 0
    well_known = _json(
        client.request("GET", "/.well-known/noob-camera", authentication="none"), 200
    )
    validate_well_known(well_known, config.expected_device_id)
    checks += 1
    missing_auth = client.request("GET", "/api/v1/status", authentication="none")
    _expect_error(missing_auth, 401, "unauthorized")
    if missing_auth.headers.get("www-authenticate") != "Bearer":
        _fail("missing_auth_challenge")
    checks += 1
    unauthorized = client.request("GET", "/api/v1/status", authentication="dummy")
    _expect_error(unauthorized, 401, "unauthorized")
    checks += 1
    _expect_error(
        client.request(
            "GET",
            f"/api/v1/status?token={DUMMY_QUERY}",
            authentication="none",
        ),
        400,
        "query_token_forbidden",
    )
    checks += 1
    status = _json(client.request("GET", "/api/v1/status"), 200)
    boot_id, camera = validate_status_shape(status, config.expected_device_id)
    validate_ready_camera(camera)
    generation = _integer(camera.get("generation"), "camera_generation")
    checks += 1
    initial = fetch_snapshot(client, boot_id)
    frames = collect_progress(client, boot_id, config.frame_count, initial)
    checks += 2
    disable_attempted = False
    try:
        disable_attempted = True
        disabled_response = _state_change(client, False, generation)
        disabled_generation = _integer(disabled_response.get("generation"), "state_generation")
        if disabled_response.get("initialized") is not False:
            _fail("disable_not_deinitialized")
        disabled_status = _json(client.request("GET", "/api/v1/status"), 200)
        _, disabled_camera = validate_status_shape(
            disabled_status, config.expected_device_id, boot_id
        )
        if (
            disabled_camera.get("enabled") is not False
            or disabled_camera.get("initialized") is not False
            or disabled_camera.get("generation") != disabled_generation
            or disabled_camera.get("fresh") is not False
        ):
            _fail("disable_status")
        _expect_error(
            client.request("GET", "/api/v1/camera/snapshot.jpg"),
            409,
            "camera_disabled",
        )
        enabled_response = _state_change(client, True, disabled_generation)
        enabled_generation = _integer(enabled_response.get("generation"), "state_generation")
        ready = _poll_ready_status(
            client,
            config.expected_device_id,
            boot_id,
            enabled_generation,
            frames[-1].sequence,
        )
        after_toggle = collect_progress(client, boot_id, 2, frames[-1])[-1]
        if (
            after_toggle.sequence <= frames[-1].sequence
            or after_toggle.digest == frames[-1].digest
            or after_toggle.sequence < _integer(ready.get("frame_sequence"), "frame_sequence")
        ):
            _fail("reenable_frame_not_new")
        checks += 2
    finally:
        if disable_attempted:
            _best_effort_reenable(client, config.expected_device_id, boot_id)
    storage = "skipped"
    if config.storage_test:
        storage = run_storage_acceptance(
            client, enabled_generation, delete_created=config.delete_created_media
        )
        checks += 1
    return AcceptanceSummary(checks=checks, frames=config.frame_count + 1, storage=storage)


def result_json(
    status: str,
    *,
    summary: AcceptanceSummary | None = None,
    error_code: str | None = None,
) -> str:
    result: dict[str, object] = {"schema": RESULT_SCHEMA, "status": status}
    if summary is not None:
        result.update(
            {"checks": summary.checks, "frames": summary.frames, "storage": summary.storage}
        )
    if error_code is not None:
        result["code"] = error_code
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = SafeArgumentParser(add_help=False)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--expected-device-id", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=3)
    parser.add_argument("--storage-test", action="store_true")
    parser.add_argument("--delete-created-media", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        if os.geteuid() != 0:
            _fail("root_required")
        args = parse_args(argv)
        config = AcceptanceConfig(
            host=args.host,
            port=args.port,
            expected_device_id=args.expected_device_id,
            frame_count=args.frames,
            storage_test=args.storage_test,
            delete_created_media=args.delete_created_media,
        )
        validate_config(config)
        token = load_token(args.token_file, expected_uid=0)
        summary = run_acceptance(config, token)
        print(result_json("pass", summary=summary))
        return 0
    except AcceptanceError as error:
        print(result_json("fail", error_code=error.code))
        return 1
    except Exception:
        print(result_json("fail", error_code="internal_error"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
