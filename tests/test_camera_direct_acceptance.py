from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "camera"
    / "scripts"
    / "accept_from_uconsole.py"
)
SPEC = importlib.util.spec_from_file_location("camera_direct_acceptance", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

TOKEN = "camera_api_token_fixture_12345678901234567890"
DEVICE_ID = "cam_0123456789abcdef"
BOOT_ID = "b_fedcba9876543210"
SNAPSHOT_ID = "m_11111111111111111111111111111111"
CLIP_ID = "m_22222222222222222222222222222222"
JOB_ID = "j_33333333333333333333333333333333"


def jpeg(sequence: int) -> bytes:
    comment = sequence.to_bytes(4, "big")
    app = b"\xff\xfe" + (len(comment) + 2).to_bytes(2, "big") + comment
    sof = (
        b"\xff\xc0\x00\x11\x08\x01\xe0\x02\x80\x03"
        b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    )
    return b"\xff\xd8" + app + sof + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\x00\xff\xd9"


class CameraState:
    def __init__(self) -> None:
        self.enabled = True
        self.initialized = True
        self.generation = 7
        self.sequence = 20
        self.sensor_name = "OV2640"
        self.sensor_pid = 38
        self.ov2640_verified = True
        self.supported_sensor_verified = True
        self.stream_mode = "fragmented"
        self.stream_delay_seconds = 0.2
        self.requests: list[tuple[str, str, str | None]] = []
        self.media: dict[str, str] = {}

    def status(self) -> dict[str, object]:
        return {
            "api": 1,
            "device_id": DEVICE_ID,
            "boot_id": BOOT_ID,
            "uptime_ms": 123456,
            "firmware": {
                "version": "0.2.0",
                "idf_version": "v6.0.2",
                "camera_component": "2.1.7",
            },
            "provisioning": {"provisioned": True, "active": False},
            "wifi": {"state": "connected", "rssi_dbm": -45, "ipv4": "192.168.50.94"},
            "camera": {
                "configured_pinmap": "ai_thinker_candidate",
                "pinmap_verified": True,
                "enabled": self.enabled,
                "initialized": self.initialized,
                "generation": self.generation,
                "sensor": {
                    "detected": True,
                    "name": self.sensor_name,
                    "pid": self.sensor_pid,
                    "ov2640_verified": self.ov2640_verified,
                    "supported_sensor_verified": self.supported_sensor_verified,
                },
                "psram": {"initialized": True, "size_bytes": 4 * 1024 * 1024},
                "width": 640 if self.enabled else None,
                "height": 480 if self.enabled else None,
                "pixel_format": "jpeg" if self.enabled else None,
                "frame_sequence": self.sequence,
                "last_frame_age_ms": 25 if self.enabled else None,
                "fresh": self.enabled,
                "last_error": None,
            },
            "storage": self.storage(),
            "reset_reason": "power_on",
            "heap": {
                "internal_free_bytes": 100000,
                "internal_min_free_bytes": 80000,
                "psram_free_bytes": 3000000,
            },
        }

    def storage(self) -> dict[str, object]:
        return {
            "state": "mounted",
            "mounted": True,
            "writable": True,
            "total_bytes": 8_000_000_000,
            "free_bytes": 7_000_000_000,
            "reserve_bytes": 64_000_000,
            "media_count": len(self.media),
            "active_job_id": None,
            "limits": {
                "max_media_items": 200,
                "max_total_bytes": 1_000_000_000,
                "max_clip_duration_ms": 30000,
                "max_clip_fps": 5,
                "max_clip_frames": 150,
            },
            "last_error": None,
        }

    def item(self, media_id: str, kind: str) -> dict[str, object]:
        return {
            "id": media_id,
            "kind": kind,
            "state": "complete",
            "created_at": "2026-08-28T00:00:00Z",
            "created_uptime_ms": 120000,
            "size_bytes": 1024,
            "width": 640,
            "height": 480,
            "frame_count": 1,
            "fps": 1 if kind == "clip" else None,
            "duration_ms": 1000 if kind == "clip" else None,
            "content_type": "image/jpeg" if kind == "snapshot" else "application/vnd.noob.clip+json",
        }


class Handler(BaseHTTPRequestHandler):
    server: "CameraServer"

    def log_message(self, format: str, *args) -> None:
        return

    def _json(self, status: int, value: dict[str, object], extra=None) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for key, item in extra.items():
                self.send_header(key, item)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str, extra=None) -> None:
        self._json(status, {"error": {"code": code, "message": "bounded fixture error"}}, extra)

    def _authorize(self) -> bool:
        query = urlsplit(self.path).query
        if "token=" in query or "access_token=" in query:
            self._error(400, "query_token_forbidden")
            return False
        authorization = self.headers.get("Authorization")
        if authorization != f"Bearer {TOKEN}":
            extra = {"WWW-Authenticate": "Bearer"} if authorization is None else None
            self._error(401, "unauthorized", extra)
            return False
        return True

    def _record(self) -> None:
        self.server.state.requests.append(
            (self.command, self.path, self.headers.get("Authorization"))
        )

    def do_GET(self) -> None:
        self._record()
        path = urlsplit(self.path).path
        if path == "/.well-known/noob-camera":
            self._json(
                200,
                {
                    "api": 1,
                    "device_id": DEVICE_ID,
                    "role": "environment",
                    "api_base": "/api/v1",
                    "authentication": "bearer",
                    "capabilities": ["stream", "snapshot", "sensor_state", "sd_media"],
                },
            )
            return
        if not self._authorize():
            return
        state = self.server.state
        if path == "/api/v1/status":
            self._json(200, state.status())
        elif path == "/api/v1/camera/snapshot.jpg":
            if not state.enabled:
                self._error(409, "camera_disabled")
                return
            state.sequence += 1
            body = jpeg(state.sequence)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-NOOB-Frame-Sequence", str(state.sequence))
            self.send_header("X-NOOB-Boot-ID", BOOT_ID)
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/v1/camera/stream.mjpg":
            if not state.enabled:
                self._error(409, "camera_disabled")
                return
            state.sequence += 1
            body = jpeg(state.sequence)
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "multipart/x-mixed-replace; boundary=noob-camera-boundary",
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if state.stream_mode == "timeout":
                self.wfile.flush()
                time.sleep(state.stream_delay_seconds)
                return
            part_header = (
                b"\r\n--noob-camera-boundary\r\n"
                b"Content-Type: image/jpeg\r\n"
                + f"Content-Length: {len(body)}\r\n".encode("ascii")
                + f"X-NOOB-Frame-Sequence: {state.sequence}\r\n\r\n".encode(
                    "ascii"
                )
            )
            if state.stream_mode == "truncated":
                fragments = (part_header + body[:-2],)
            else:
                # Exercise split SOI and EOI markers as well as fragmented
                # multipart metadata during every successful acceptance run.
                fragments = (
                    part_header[:11],
                    part_header[11:] + body[:1],
                    body[1:-1],
                    body[-1:] + b"\r\n",
                )
            for fragment in fragments:
                try:
                    self.wfile.write(fragment)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
        elif path == "/api/v1/storage":
            self._json(200, state.storage())
        elif path == f"/api/v1/jobs/{JOB_ID}":
            self._json(
                200,
                {
                    "job_id": JOB_ID,
                    "kind": "clip",
                    "state": "complete",
                    "created_uptime_ms": 123000,
                    "frames_written": 1,
                    "frames_target": 1,
                    "media_id": CLIP_ID,
                    "error_code": None,
                },
            )
        elif path.startswith("/api/v1/media/"):
            media_id = path.rsplit("/", 1)[-1]
            kind = state.media.get(media_id)
            if kind is None:
                self._error(404, "media_not_found")
            else:
                self._json(200, {"item": state.item(media_id, kind)})
        else:
            self._error(404, "not_found")

    def _body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length))

    def do_PUT(self) -> None:
        self._record()
        if not self._authorize():
            return
        if urlsplit(self.path).path != "/api/v1/camera/state":
            self._error(404, "not_found")
            return
        request = self._body()
        state = self.server.state
        if request.get("expected_generation") != state.generation:
            self._error(409, "generation_conflict")
            return
        state.enabled = bool(request["enabled"])
        state.initialized = state.enabled
        state.generation += 1
        if state.enabled:
            state.sequence += 1
        self._json(
            200,
            {
                "enabled": state.enabled,
                "generation": state.generation,
                "initialized": state.initialized,
            },
        )

    def do_POST(self) -> None:
        self._record()
        if not self._authorize():
            return
        path = urlsplit(self.path).path
        request = self._body()
        state = self.server.state
        if request.get("expected_generation") != state.generation:
            self._error(409, "generation_conflict")
            return
        if path == "/api/v1/storage/snapshots":
            state.media[SNAPSHOT_ID] = "snapshot"
            self._json(201, {"item": state.item(SNAPSHOT_ID, "snapshot")})
        elif path == "/api/v1/storage/clips":
            if request != {
                "duration_ms": 1000,
                "fps": 1,
                "expected_generation": state.generation,
            }:
                self._error(400, "invalid_request")
                return
            state.media[CLIP_ID] = "clip"
            self._json(202, {"job_id": JOB_ID, "state": "queued"})
        else:
            self._error(404, "not_found")

    def do_DELETE(self) -> None:
        self._record()
        if not self._authorize():
            return
        media_id = urlsplit(self.path).path.rsplit("/", 1)[-1]
        if media_id not in self.server.state.media:
            self._error(404, "media_not_found")
            return
        del self.server.state.media[media_id]
        self._json(200, {"id": media_id, "deleted": True})


class CameraServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        self.state = CameraState()
        super().__init__(("127.0.0.1", 0), Handler)


class ServerContext:
    def __enter__(self) -> CameraServer:
        self.server = CameraServer()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.server

    def __exit__(self, *args) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def config(server: CameraServer, **updates) -> MODULE.AcceptanceConfig:
    values = {
        "host": "127.0.0.1",
        "port": server.server_port,
        "expected_device_id": DEVICE_ID,
        "frame_count": 3,
        "request_timeout": 1,
        "progress_timeout": 2,
    }
    values.update(updates)
    return MODULE.AcceptanceConfig(**values)


class CameraDirectAcceptanceTests(unittest.TestCase):
    def test_complete_nonstorage_acceptance_and_secret_placement(self) -> None:
        with ServerContext() as server:
            summary = MODULE.run_acceptance(
                config(server), TOKEN, allow_loopback_for_test=True
            )
        self.assertEqual(summary, MODULE.AcceptanceSummary(10, 5, "skipped"))
        rendered = MODULE.result_json("pass", summary=summary)
        for forbidden in (TOKEN, DEVICE_ID, "127.0.0.1"):
            self.assertNotIn(forbidden, rendered)
        self.assertTrue(server.state.enabled)
        self.assertTrue(
            any("?token=noob_acceptance_query_dummy" in path for _, path, _ in server.state.requests)
        )
        self.assertFalse(any(TOKEN in path for _, path, _ in server.state.requests))
        self.assertTrue(any(auth == f"Bearer {TOKEN}" for _, _, auth in server.state.requests))
        self.assertTrue(
            any(auth == f"Bearer {MODULE.DUMMY_BEARER}" for _, _, auth in server.state.requests)
        )
        self.assertTrue(
            any(path == "/api/v1/camera/stream.mjpg" for _, path, _ in server.state.requests)
        )

    def test_storage_is_opt_in_and_default_never_deletes(self) -> None:
        with ServerContext() as server:
            summary = MODULE.run_acceptance(
                config(server, storage_test=True), TOKEN, allow_loopback_for_test=True
            )
        self.assertEqual(summary.storage, "created")
        self.assertEqual(set(server.state.media), {SNAPSHOT_ID, CLIP_ID})
        self.assertFalse(any(method == "DELETE" for method, _, _ in server.state.requests))

    def test_ov3660_acceptance_never_claims_ov2640(self) -> None:
        with ServerContext() as server:
            server.state.sensor_name = "OV3660"
            server.state.sensor_pid = 0x3660
            server.state.ov2640_verified = False
            summary = MODULE.run_acceptance(
                config(server), TOKEN, allow_loopback_for_test=True
            )
        self.assertEqual(summary, MODULE.AcceptanceSummary(10, 5, "skipped"))

    def test_fragmented_mjpeg_frame_is_bounded_and_validated(self) -> None:
        with ServerContext() as server:
            client = MODULE.CameraClient(config(server), TOKEN)
            digest = MODULE.fetch_mjpeg_frame(client)
            expected = jpeg(server.state.sequence)
        self.assertEqual(digest, hashlib.sha256(expected).digest())

    def test_truncated_mjpeg_frame_fails_closed(self) -> None:
        with ServerContext() as server:
            server.state.stream_mode = "truncated"
            client = MODULE.CameraClient(config(server), TOKEN)
            with self.assertRaisesRegex(
                MODULE.AcceptanceError, "stream_frame_incomplete"
            ):
                MODULE.fetch_mjpeg_frame(client)

    def test_mjpeg_frame_timeout_is_bounded(self) -> None:
        with ServerContext() as server:
            server.state.stream_mode = "timeout"
            client = MODULE.CameraClient(config(server, stream_timeout=0.05), TOKEN)
            with self.assertRaisesRegex(
                MODULE.AcceptanceError, "stream_frame_timeout"
            ):
                MODULE.fetch_mjpeg_frame(client)

    def test_unknown_or_contradictory_sensor_evidence_fails_closed(self) -> None:
        with ServerContext() as server:
            server.state.sensor_name = "OV3660"
            server.state.sensor_pid = 0x3660
            server.state.ov2640_verified = True
            with self.assertRaisesRegex(MODULE.AcceptanceError, "camera_not_ready"):
                MODULE.run_acceptance(
                    config(server), TOKEN, allow_loopback_for_test=True
                )

        with ServerContext() as server:
            server.state.sensor_name = "UNKNOWN"
            server.state.sensor_pid = 0x1234
            server.state.ov2640_verified = False
            server.state.supported_sensor_verified = False
            with self.assertRaisesRegex(MODULE.AcceptanceError, "camera_not_ready"):
                MODULE.run_acceptance(
                    config(server), TOKEN, allow_loopback_for_test=True
                )

    def test_separate_delete_flag_deletes_only_created_objects(self) -> None:
        with ServerContext() as server:
            summary = MODULE.run_acceptance(
                config(server, storage_test=True, delete_created_media=True),
                TOKEN,
                allow_loopback_for_test=True,
            )
        self.assertEqual(summary.storage, "created_and_deleted")
        self.assertEqual(server.state.media, {})
        deleted = [path for method, path, _ in server.state.requests if method == "DELETE"]
        self.assertEqual(
            deleted,
            [f"/api/v1/media/{SNAPSHOT_ID}", f"/api/v1/media/{CLIP_ID}"],
        )

    def test_failure_result_is_compact_and_nonsecret(self) -> None:
        with ServerContext() as server:
            bad = config(server, expected_device_id="cam_aaaaaaaaaaaaaaaa")
            with self.assertRaises(MODULE.AcceptanceError) as caught:
                MODULE.run_acceptance(bad, TOKEN, allow_loopback_for_test=True)
        rendered = MODULE.result_json("fail", error_code=caught.exception.code)
        self.assertEqual(
            json.loads(rendered),
            {"schema": MODULE.RESULT_SCHEMA, "status": "fail", "code": "well_known_identity"},
        )
        self.assertNotIn(TOKEN, rendered)
        self.assertNotIn(DEVICE_ID, rendered)

    def test_private_literal_and_delete_gates(self) -> None:
        self.assertEqual(MODULE.validate_private_host("192.168.4.1"), "192.168.4.1")
        for invalid in ("camera.local", "8.8.8.8", "127.0.0.1", "169.254.1.2"):
            with self.assertRaises(MODULE.AcceptanceError):
                MODULE.validate_private_host(invalid)
        with self.assertRaises(MODULE.AcceptanceError):
            MODULE.validate_config(
                MODULE.AcceptanceConfig(
                    "192.168.4.1", 80, DEVICE_ID, delete_created_media=True
                )
            )

    def test_owner_only_token_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "token"
            path.write_text(TOKEN + "\n", encoding="ascii")
            path.chmod(0o600)
            self.assertEqual(MODULE.load_token(path, expected_uid=os.getuid()), TOKEN)
            path.chmod(0o640)
            with self.assertRaises(MODULE.AcceptanceError):
                MODULE.load_token(path, expected_uid=os.getuid())
            self.assertNotEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_token_file_rejects_symlink_and_result_never_renders_details(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            token = directory / "token"
            token.write_text(TOKEN, encoding="ascii")
            token.chmod(0o600)
            alias = directory / "alias"
            alias.symlink_to(token)
            with self.assertRaises(MODULE.AcceptanceError):
                MODULE.load_token(alias, expected_uid=os.getuid())
        rendered = MODULE.result_json("fail", error_code="transport_failed")
        self.assertEqual(
            json.loads(rendered),
            {
                "schema": MODULE.RESULT_SCHEMA,
                "status": "fail",
                "code": "transport_failed",
            },
        )


if __name__ == "__main__":
    unittest.main()
