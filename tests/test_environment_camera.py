from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

from noob_gateway.config import EnvironmentCameraConfig
from noob_gateway.environment_camera import (
    EnvironmentCamera,
    EnvironmentCameraError,
    EnvironmentCameraGenerationConflict,
    EnvironmentCameraNotConfigured,
    EnvironmentCameraViewerLimit,
    _status_evidence,
)

TOKEN = "c" * 48
MEDIA_ID = "m_" + "1" * 32
CLIP_ID = "m_" + "2" * 32
JOB_ID = "j_" + "3" * 32
JPEG = (
    b"\xff\xd8\xff\xc0\x00\x0b\x08\x01\xe0\x02\x80\x01\x01\x11\x00\xff\xd9"
)


def storage_payload() -> dict:
    return {
        "state": "mounted",
        "mounted": True,
        "writable": True,
        "total_bytes": 32_000_000,
        "free_bytes": 24_000_000,
        "reserve_bytes": 1_000_000,
        "media_count": 2,
        "active_job_id": None,
        "limits": {
            "max_media_items": 500,
            "max_total_bytes": 30_000_000,
            "max_clip_duration_ms": 30_000,
            "max_clip_fps": 5,
            "max_clip_frames": 150,
        },
        "last_error": None,
    }


def media_item(media_id: str, kind: str) -> dict:
    clip = kind == "clip"
    return {
        "id": media_id,
        "kind": kind,
        "state": "complete",
        "created_at": "2026-08-27T20:00:00Z",
        "created_uptime_ms": 12_345,
        "size_bytes": 4000 if clip else len(JPEG),
        "width": 640,
        "height": 480,
        "frame_count": 10 if clip else 1,
        "fps": 2 if clip else None,
        "duration_ms": 5000 if clip else 0,
        "content_type": ("application/vnd.noob.clip+json" if clip else "image/jpeg"),
    }


class FakeCameraUpstream:
    def __init__(self) -> None:
        self.enabled = True
        self.initialized = True
        self.generation = 7
        self.device_id = "cam_0123456789abcdef"
        self.pinmap_verified = True
        self.sensor_name = "OV2640"
        self.sensor_pid = 0x26
        self.ov2640_verified = True
        self.psram_initialized = True
        self.psram_size_bytes = 4 * 1024 * 1024
        self.provisioned = True
        self.provisioning_active = False
        self.wifi_state = "connected"
        self.requests: list[tuple[str, str]] = []
        self.snapshot_redirect = False
        self.snapshot_oversized = False

    def _authorize(self, request: web.Request) -> None:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            raise web.HTTPUnauthorized(
                text='{"error":{"code":"unauthorized","message":"denied"}}',
                content_type="application/json",
            )
        self.requests.append((request.method, request.path))

    async def status(self, request: web.Request) -> web.Response:
        self._authorize(request)
        return web.json_response(
            {
                "api": 1,
                "device_id": self.device_id,
                "boot_id": "b_0123456789abcdef",
                "provisioning": {
                    "provisioned": self.provisioned,
                    "active": self.provisioning_active,
                },
                "wifi": {
                    "state": self.wifi_state,
                    "rssi_dbm": -48,
                    "ipv4": self.server_host,
                },
                "camera": {
                    "configured_pinmap": "ai_thinker_candidate",
                    "pinmap_verified": self.pinmap_verified,
                    "enabled": self.enabled,
                    "initialized": self.initialized,
                    "generation": self.generation,
                    "sensor": {
                        "detected": self.sensor_name is not None,
                        "name": self.sensor_name,
                        "pid": self.sensor_pid,
                        "ov2640_verified": self.ov2640_verified,
                    },
                    "psram": {
                        "initialized": self.psram_initialized,
                        "size_bytes": self.psram_size_bytes,
                    },
                    "width": 640 if self.initialized else None,
                    "height": 480 if self.initialized else None,
                    "pixel_format": "jpeg" if self.initialized else None,
                    "frame_sequence": 11,
                    "last_frame_age_ms": 10 if self.initialized else None,
                    "fresh": self.initialized,
                    "last_error": None,
                },
                "storage": storage_payload(),
            }
        )

    async def state(self, request: web.Request) -> web.Response:
        self._authorize(request)
        body = await request.json()
        if body.get("expected_generation") != self.generation:
            return web.json_response(
                {
                    "error": {
                        "code": "generation_conflict",
                        "message": "stale",
                    }
                },
                status=409,
            )
        if body.get("enabled") != self.enabled:
            self.enabled = body["enabled"]
            self.initialized = self.enabled
            self.generation += 1
        return web.json_response(
            {
                "enabled": self.enabled,
                "generation": self.generation,
                "initialized": self.initialized,
            }
        )

    async def snapshot(self, request: web.Request) -> web.Response:
        self._authorize(request)
        if self.snapshot_redirect:
            raise web.HTTPFound("http://203.0.113.1/secret")
        body = JPEG
        if self.snapshot_oversized:
            body = JPEG + b"x" * (256 * 1024)
        return web.Response(
            body=body,
            content_type="image/jpeg",
            headers={
                "X-NOOB-Frame-Sequence": "12",
                "X-NOOB-Boot-ID": "b_0123456789abcdef",
            },
        )

    async def stream(self, request: web.Request) -> web.StreamResponse:
        self._authorize(request)
        response = web.StreamResponse(
            headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"}
        )
        await response.prepare(request)
        await response.write(
            b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + JPEG + b"\r\n"
        )
        await asyncio.sleep(0.05)
        return response

    async def storage(self, request: web.Request) -> web.Response:
        self._authorize(request)
        return web.json_response(storage_payload())

    async def media(self, request: web.Request) -> web.Response:
        self._authorize(request)
        self.assert_query(request)
        return web.json_response(
            {
                "items": [
                    media_item(MEDIA_ID, "snapshot"),
                    media_item(CLIP_ID, "clip"),
                ],
                "next_cursor": "cursor_2",
            }
        )

    @staticmethod
    def assert_query(request: web.Request) -> None:
        if (
            request.query.get("limit") != "2"
            or request.query.get("cursor") != "cursor_1"
        ):
            raise web.HTTPBadRequest()

    async def media_item(self, request: web.Request) -> web.Response:
        self._authorize(request)
        media_id = request.match_info["media_id"]
        return web.json_response(
            {
                "item": media_item(
                    media_id, "clip" if media_id == CLIP_ID else "snapshot"
                )
            }
        )

    async def media_content(self, request: web.Request) -> web.Response:
        self._authorize(request)
        return web.Response(body=JPEG, content_type="image/jpeg")

    async def clip_frame(self, request: web.Request) -> web.Response:
        self._authorize(request)
        return web.Response(body=JPEG, content_type="image/jpeg")

    async def create_snapshot(self, request: web.Request) -> web.Response:
        self._authorize(request)
        body = await request.json()
        if body != {"expected_generation": self.generation}:
            raise web.HTTPBadRequest()
        return web.json_response({"item": media_item(MEDIA_ID, "snapshot")}, status=201)

    async def create_clip(self, request: web.Request) -> web.Response:
        self._authorize(request)
        body = await request.json()
        if body != {
            "duration_ms": 5000,
            "fps": 2,
            "expected_generation": self.generation,
        }:
            raise web.HTTPBadRequest()
        return web.json_response({"job_id": JOB_ID, "state": "queued"}, status=202)

    async def job(self, request: web.Request) -> web.Response:
        self._authorize(request)
        return web.json_response(
            {
                "job_id": JOB_ID,
                "kind": "clip",
                "state": "complete",
                "created_uptime_ms": 12_400,
                "frames_written": 10,
                "frames_target": 10,
                "media_id": CLIP_ID,
                "error_code": None,
            }
        )

    async def stop_job(self, request: web.Request) -> web.Response:
        self._authorize(request)
        return web.json_response(
            {"job_id": request.match_info["job_id"], "state": "cancelling"}
        )

    def application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/v1/status", self.status)
        app.router.add_put("/api/v1/camera/state", self.state)
        app.router.add_get("/api/v1/camera/snapshot.jpg", self.snapshot)
        app.router.add_get("/api/v1/camera/stream.mjpg", self.stream)
        app.router.add_get("/api/v1/storage", self.storage)
        app.router.add_get("/api/v1/media", self.media)
        app.router.add_get("/api/v1/media/{media_id}", self.media_item)
        app.router.add_get("/api/v1/media/{media_id}/content", self.media_content)
        app.router.add_get(
            "/api/v1/media/{media_id}/frames/{frame_index}.jpg", self.clip_frame
        )
        app.router.add_post("/api/v1/storage/snapshots", self.create_snapshot)
        app.router.add_post("/api/v1/storage/clips", self.create_clip)
        app.router.add_get("/api/v1/jobs/{job_id}", self.job)
        app.router.add_delete("/api/v1/jobs/{job_id}", self.stop_job)
        return app


class EnvironmentCameraTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.upstream = FakeCameraUpstream()
        self.server = TestServer(self.upstream.application())
        await self.server.start_server()
        self.upstream.server_host = self.server.host
        self.camera = EnvironmentCamera(
            EnvironmentCameraConfig(
                enabled=True,
                host=self.server.host,
                expected_device_id="cam_0123456789abcdef",
                port=self.server.port,
                token_file="/unused-in-test",
                status_interval_seconds=30.0,
                reconnect_ms=100,
                max_frame_bytes=128 * 1024,
                max_media_bytes=1024 * 1024,
                max_clients=1,
            ),
            upstream_token=TOKEN,
        )
        await self.camera.start()

    async def asyncTearDown(self) -> None:
        await self.camera.stop()
        await self.server.close()

    async def test_status_and_generation_checked_state_are_independent(self):
        status = await self.camera.refresh_status()
        self.assertTrue(status["configured"])
        self.assertTrue(status["reachable"])
        self.assertTrue(status["stream_enabled"])
        self.assertFalse(status["power_control"])
        self.assertEqual(status["generation"], 7)
        self.assertEqual(status["storage"]["media_count"], 2)
        self.assertTrue(status["identity_verified"])
        self.assertTrue(status["network_verified"])
        self.assertTrue(status["hardware_verified"])
        self.assertEqual(status["configured_pinmap"], "ai_thinker_candidate")
        self.assertEqual(
            status["sensor"],
            {
                "detected": True,
                "name": "OV2640",
                "pid": 0x26,
                "ov2640_verified": True,
            },
        )
        self.assertEqual(status["psram"]["size_bytes"], 4 * 1024 * 1024)
        self.assertTrue(status["reported_frame"]["v1_fresh_verified"])

        disabled = await self.camera.set_enabled(False, 7)
        self.assertFalse(disabled["stream_enabled"])
        self.assertEqual(disabled["generation"], 8)
        with self.assertRaises(EnvironmentCameraGenerationConflict):
            await self.camera.set_enabled(True, 7)

    async def test_snapshot_frame_is_bounded_validated_and_cached(self):
        await self.camera.refresh_status()
        frame = await self.camera.get_frame()
        self.assertEqual(frame.data, JPEG)
        self.assertEqual((frame.width, frame.height), (640, 480))
        self.assertEqual(frame.generation, 7)
        self.assertTrue(self.camera.status["frame_ready"])
        self.assertEqual(
            self.upstream.requests.count(("GET", "/api/v1/camera/snapshot.jpg")),
            1,
        )
        self.assertIs(await self.camera.get_frame(), frame)

    async def test_stale_frame_cannot_cross_a_generation_change(self):
        await self.camera.refresh_status()
        stale_epoch = self.camera._connection_epoch
        await self.camera.set_enabled(False, 7)

        with self.assertRaisesRegex(
            EnvironmentCameraError, "camera_generation_changed"
        ):
            await self.camera._publish_frame(JPEG, expected_epoch=stale_epoch)

        self.assertIsNone(self.camera._latest)
        self.assertFalse(self.camera.status["frame_ready"])

    async def test_redirect_and_oversized_frame_fail_closed(self):
        await self.camera.refresh_status()
        self.upstream.snapshot_redirect = True
        with self.assertRaisesRegex(EnvironmentCameraError, "camera_redirect_rejected"):
            await self.camera.get_frame()
        self.upstream.snapshot_redirect = False
        self.upstream.snapshot_oversized = True
        with self.assertRaisesRegex(
            EnvironmentCameraError, "camera_response_too_large"
        ):
            await self.camera.get_frame()

    async def test_viewer_limit_is_separate_and_upstream_stream_is_shared(self):
        await self.camera.refresh_status()
        await self.camera.acquire_viewer()
        with self.assertRaises(EnvironmentCameraViewerLimit):
            await self.camera.acquire_viewer()
        self.camera._activity.set()
        frame = await self.camera.wait_for_frame(-1, timeout=1.0)
        self.assertIsNotNone(frame)
        await self.camera.release_viewer()

    async def test_storage_media_snapshot_clip_and_job_use_opaque_ids(self):
        await self.camera.refresh_status()
        storage = await self.camera.storage_status()
        self.assertTrue(storage["writable"])
        page = await self.camera.list_media(cursor="cursor_1", limit=2)
        self.assertEqual([item["id"] for item in page["items"]], [MEDIA_ID, CLIP_ID])
        self.assertEqual(page["next_cursor"], "cursor_2")

        item = await self.camera.get_media(MEDIA_ID)
        self.assertEqual(item["kind"], "snapshot")
        self.assertEqual(await self.camera.get_snapshot_content(MEDIA_ID), JPEG)
        self.assertEqual(await self.camera.get_clip_frame(CLIP_ID, 0), JPEG)
        with self.assertRaisesRegex(EnvironmentCameraError, "bad_media_id"):
            await self.camera.get_media("../../etc/passwd")

        snapshot = await self.camera.create_snapshot(7)
        self.assertEqual(snapshot["id"], MEDIA_ID)
        clip = await self.camera.create_clip(
            duration_seconds=5, fps=2, expected_generation=7
        )
        self.assertEqual(clip, {"job_id": JOB_ID, "state": "queued"})
        job = await self.camera.get_job(JOB_ID)
        self.assertEqual(job["media_id"], CLIP_ID)
        stopped = await self.camera.stop_job(JOB_ID)
        self.assertEqual(stopped, {"job_id": JOB_ID, "state": "cancelling"})

    async def test_clip_bounds_reject_before_upstream_request(self):
        await self.camera.refresh_status()
        before = list(self.upstream.requests)
        for duration, fps in ((0, 1), (31, 1), (1, 0), (1, 6)):
            with (
                self.subTest(duration=duration, fps=fps),
                self.assertRaisesRegex(EnvironmentCameraError, "bad_range"),
            ):
                await self.camera.create_clip(
                    duration_seconds=duration,
                    fps=fps,
                    expected_generation=7,
                )
        self.assertEqual(self.upstream.requests, before)

    async def test_authenticated_status_device_id_must_match_configured_pin(self):
        await self.camera.refresh_status()
        self.upstream.device_id = "cam_fedcba9876543210"

        with self.assertRaisesRegex(
            EnvironmentCameraError, "camera_identity_mismatch"
        ):
            await self.camera.refresh_status()

        status = self.camera.status
        self.assertFalse(status["reachable"])
        self.assertFalse(status["identity_verified"])
        self.assertIsNone(status["device_id"])
        self.assertEqual(status["observed_device_id"], self.upstream.device_id)
        self.assertEqual(status["last_error"], "camera_identity_mismatch")
        self.assertFalse(status["frame_ready"])

    async def test_malformed_authenticated_device_id_invalidates_accepted_lane(self):
        await self.camera.refresh_status()
        self.upstream.device_id = "CAM_0123456789ABCDEF"

        with self.assertRaisesRegex(EnvironmentCameraError, "camera_bad_response"):
            await self.camera.refresh_status()

        status = self.camera.status
        self.assertFalse(status["reachable"])
        self.assertFalse(status["identity_verified"])
        self.assertIsNone(status["device_id"])
        self.assertIsNone(status["observed_device_id"])
        self.assertEqual(status["last_error"], "camera_bad_response")
        self.assertFalse(status["frame_ready"])

    async def test_unverified_hardware_is_reported_but_never_serves_frames(self):
        self.upstream.pinmap_verified = False
        self.upstream.enabled = False
        self.upstream.initialized = False
        status = await self.camera.refresh_status()

        self.assertTrue(status["reachable"])
        self.assertFalse(status["hardware_verified"])
        self.assertFalse(status["pinmap_verified"])
        await self.camera.set_enabled(True, 7)
        with self.assertRaisesRegex(
            EnvironmentCameraError, "camera_hardware_unverified"
        ):
            await self.camera.get_frame()

    async def test_non_v1_frame_claim_and_frame_bytes_fail_closed(self):
        payload = {
            "provisioning": {"provisioned": True, "active": False},
            "wifi": {
                "state": "connected",
                "rssi_dbm": -48,
                "ipv4": self.server.host,
            },
        }
        camera = {
            "configured_pinmap": "ai_thinker_candidate",
            "pinmap_verified": True,
            "enabled": True,
            "initialized": True,
            "sensor": {
                "detected": True,
                "name": "OV2640",
                "pid": 0x26,
                "ov2640_verified": True,
            },
            "psram": {"initialized": True, "size_bytes": 4 * 1024 * 1024},
            "width": 1600,
            "height": 1200,
            "pixel_format": "jpeg",
            "frame_sequence": 11,
            "last_frame_age_ms": 10,
            "fresh": True,
        }
        with self.assertRaisesRegex(EnvironmentCameraError, "camera_bad_response"):
            _status_evidence(payload, camera, expected_host=self.server.host)
        with self.assertRaisesRegex(EnvironmentCameraError, "camera_bad_frame"):
            await self.camera._publish_frame(
                b"\xff\xd8\xff\xc0\x00\x0b\x08\x04\xb0\x06\x40\x01\x01\x11\x00\xff\xd9"
            )

    async def test_stale_reported_frame_age_remains_diagnostic_evidence(self):
        payload = {
            "provisioning": {"provisioned": True, "active": False},
            "wifi": {"state": "connected", "rssi_dbm": -48, "ipv4": self.server.host},
        }
        camera = {
            "configured_pinmap": "ai_thinker_candidate",
            "pinmap_verified": True,
            "enabled": True,
            "initialized": True,
            "sensor": {
                "detected": True,
                "name": "OV2640",
                "pid": 0x26,
                "ov2640_verified": True,
            },
            "psram": {"initialized": True, "size_bytes": 4 * 1024 * 1024},
            "width": 640,
            "height": 480,
            "pixel_format": "jpeg",
            "frame_sequence": 11,
            "last_frame_age_ms": 7 * 86_400_000,
            "fresh": False,
        }

        evidence = _status_evidence(payload, camera, expected_host=self.server.host)

        self.assertEqual(
            evidence["reported_frame"]["last_frame_age_ms"], 7 * 86_400_000
        )
        self.assertFalse(evidence["reported_frame"]["v1_fresh_verified"])


class DisabledEnvironmentCameraTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_camera_is_status_only_and_never_opens_a_session(self):
        camera = EnvironmentCamera(EnvironmentCameraConfig())
        await camera.start()
        self.assertFalse(camera.status["configured"])
        self.assertFalse(camera.status["reachable"])
        with self.assertRaises(EnvironmentCameraNotConfigured):
            await camera.get_frame()
        await camera.stop()

    async def test_reused_gateway_credential_leaves_optional_lane_inert(self):
        camera = EnvironmentCamera(
            EnvironmentCameraConfig(
                enabled=True,
                host="192.168.50.84",
                expected_device_id="cam_0123456789abcdef",
                token_file="/unused-in-test",
            ),
            upstream_token=TOKEN,
            forbidden_tokens=(TOKEN,),
        )
        await camera.start()
        self.assertFalse(camera.status["reachable"])
        self.assertEqual(camera.status["last_error"], "camera_credential_reused")
        self.assertIsNone(camera._session)
        await camera.stop()


if __name__ == "__main__":
    unittest.main()
