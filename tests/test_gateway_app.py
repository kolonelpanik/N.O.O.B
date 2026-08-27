import asyncio
import sys
import time
import unittest
from pathlib import Path

import serial as pyserial


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from noob_gateway.app import RUNTIME_KEY, create_app  # noqa: E402
from noob_gateway.config import GatewayConfig, LocalInputConfig  # noqa: E402
from noob_gateway.control_lease import ControlLease  # noqa: E402
from noob_gateway.serial_link import SerialTimeout, SerialUnavailable  # noqa: E402
from noob_gateway.video import (  # noqa: E402
    FrameSnapshot,
    VideoModeInvalid,
    VideoModeStale,
    VideoSwitchFailed,
)


TOKEN = "t" * 48
LOCAL_TOKEN = "l" * 48


class FakeSerial:
    def __init__(self):
        self.ready = True
        self.sent = []
        self.releases = 0
        self.generation = 0
        self.events = []
        self.release_gate = None
        self.release_started = asyncio.Event()
        self.release_attempts = 0
        self.release_failures = []
        self.input_failure = None

    @property
    def status(self):
        return {"ready": self.ready, "device": "/dev/fake", "last_error": None}

    async def start(self):
        return None

    async def stop(self):
        return None

    async def send_command(self, command, *, expected_generation=None):
        if expected_generation != self.generation:
            raise AssertionError("gateway passed stale serial generation")
        self.events.append(("send", command["op"]))
        self.sent.append(command)
        if self.input_failure is not None:
            failure, self.input_failure = self.input_failure, None
            if isinstance(failure, (SerialTimeout, SerialUnavailable)):
                self.ready = False
                self.generation += 1
            raise failure
        return {"chunks": 1, "pico": {"kind": "ack", "seq": len(self.sent)}}

    async def emergency_release(self):
        self.release_attempts += 1
        self.events.append(("release", "started"))
        self.release_started.set()
        if self.release_failures:
            raise self.release_failures.pop(0)
        if not self.ready:
            raise SerialUnavailable("serial session is not ready")
        if self.release_gate is not None:
            await self.release_gate.wait()
        self.generation += 1
        self.releases += 1
        self.events.append(("release", "complete"))
        return {"kind": "ack", "seq": self.releases}


class FakeVideo:
    def __init__(self):
        self.ready = True
        self.generation = 1
        self.frame = FrameSnapshot(
            b"\xff\xd8test\xff\xd9", 1, time.monotonic(), generation=1
        )
        self.viewer_count = 0
        self.mode_id = "720p20"
        self.mode_calls = []
        self.mode_error = None
        self.switch_started = asyncio.Event()
        self.switch_gate = None
        self.modes = [
            {
                "id": "720p20",
                "label": "1280 x 720 - 20 fps",
                "width": 1280,
                "height": 720,
                "fps": 20,
                "pixel_format": "MJPG",
                "max_frame_bytes": 1843200,
                "validated": True,
            },
            {
                "id": "1080p30",
                "label": "1920 x 1080 - 30 fps",
                "width": 1920,
                "height": 1080,
                "fps": 30,
                "pixel_format": "MJPG",
                "max_frame_bytes": 4147200,
                "validated": True,
            },
        ]

    @property
    def status(self):
        selected = next(item for item in self.modes if item["id"] == self.mode_id)
        negotiated = {
            key: selected[key] for key in ("width", "height", "fps", "pixel_format")
        }
        requested = {
            key: selected[key]
            for key in (
                "id",
                "label",
                "width",
                "height",
                "fps",
                "pixel_format",
                "max_frame_bytes",
            )
        }
        return {
            "ready": self.ready,
            "state": "ready" if self.ready else "degraded",
            "generation": self.generation,
            "active_mode_id": self.mode_id if self.ready else None,
            "requested": requested,
            "negotiated": negotiated if self.ready else None,
            "width": negotiated["width"] if self.ready else None,
            "height": negotiated["height"] if self.ready else None,
            "fps": negotiated["fps"] if self.ready else None,
            "sequence": self.frame.sequence,
        }

    def mode_catalog(self):
        status = self.status
        return {
            "ok": True,
            "generation": self.generation,
            "active_mode_id": status["active_mode_id"],
            "requested": status["requested"],
            "negotiated": status["negotiated"],
            "state": status["state"],
            "modes": self.modes,
        }

    async def select_mode(self, mode_id, expected_generation):
        self.mode_calls.append((mode_id, expected_generation))
        self.switch_started.set()
        if self.switch_gate is not None:
            await self.switch_gate.wait()
        if self.mode_error is not None:
            raise self.mode_error
        if expected_generation != self.generation:
            raise VideoModeStale()
        if mode_id not in {item["id"] for item in self.modes}:
            raise VideoModeInvalid()
        self.mode_id = mode_id
        self.generation += 1
        self.ready = True
        self.frame = FrameSnapshot(
            self.frame.data,
            self.frame.sequence + 1,
            time.monotonic(),
            generation=self.generation,
        )
        return self.status

    async def start(self):
        return None

    async def stop(self):
        return None

    def latest(self):
        return self.frame

    async def acquire_viewer(self):
        self.viewer_count += 1

    async def release_viewer(self):
        self.viewer_count -= 1

    async def wait_for_frame(self, _after_sequence, timeout=5.0):
        return self.frame


class FakeLocalInput:
    def __init__(self):
        self.enabled = True
        self.ready = True
        self.armed = False
        self.disarm_reason = None

    @property
    def status(self):
        return {
            "enabled": self.enabled,
            "ready": self.ready,
            "armed": self.armed,
            "exclusive_grab": self.armed,
            "disarm_reason": self.disarm_reason,
        }

    async def start(self):
        return None

    async def stop(self):
        self.armed = False

    async def arm(self):
        self.armed = True

    async def disarm(self, *, reason="operator"):
        self.armed = False
        self.disarm_reason = reason
        return True


class GatewayAppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.serial = FakeSerial()
        self.video = FakeVideo()
        self.app = create_app(
            GatewayConfig(), token=TOKEN, serial_link=self.serial, video=self.video
        )
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.runtime = self.app[RUNTIME_KEY]
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    async def asyncTearDown(self):
        await self.client.close()

    async def claim(self):
        response = await self.client.post(
            "/api/v1/control/claim", json={}, headers=self.auth
        )
        self.assertEqual(response.status, 200)
        return (await response.json())["lease"]

    async def use_deterministic_lease_clock(self):
        task = self.runtime._lease_task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.runtime._lease_task = None
        clock = FakeLeaseClock()
        self.runtime.lease = ControlLease(5.0, clock=clock)
        return clock

    async def test_bearer_auth_is_required(self):
        response = await self.client.get("/api/v1/status")
        self.assertEqual(response.status, 401)
        response = await self.client.get("/api/v1/status", headers=self.auth)
        self.assertEqual(response.status, 200)

    async def test_video_modes_are_allowlisted_and_switch_uses_generation(self):
        modes = await self.client.get("/api/v1/video/modes", headers=self.auth)
        self.assertEqual(modes.status, 200)
        payload = await modes.json()
        self.assertEqual([item["id"] for item in payload["modes"]], ["720p20", "1080p30"])
        self.assertNotIn("mode_id", payload["modes"][0])

        switched = await self.client.post(
            "/api/v1/video/mode",
            json={"mode_id": "1080p30", "expected_generation": 1},
            headers=self.auth,
        )
        self.assertEqual(switched.status, 200)
        result = await switched.json()
        self.assertEqual(result["video"]["active_mode_id"], "1080p30")
        self.assertEqual(result["video"]["generation"], 2)
        self.assertEqual(self.video.mode_calls, [("1080p30", 1)])

        frame = await self.client.get("/api/v1/frame.jpg", headers=self.auth)
        self.assertEqual(frame.headers["X-NOOB-Video-Generation"], "2")

    async def test_video_mode_request_is_strict_and_rejects_stale_or_unknown(self):
        for body in (
            {"mode_id": "1080p30"},
            {"mode_id": "1080p30", "expected_generation": True},
            {"mode_id": "1080p30", "expected_generation": 1, "extra": 1},
        ):
            with self.subTest(body=body):
                response = await self.client.post(
                    "/api/v1/video/mode", json=body, headers=self.auth
                )
                self.assertEqual(response.status, 400)

        stale = await self.client.post(
            "/api/v1/video/mode",
            json={"mode_id": "1080p30", "expected_generation": 0},
            headers=self.auth,
        )
        self.assertEqual(stale.status, 409)
        self.assertEqual((await stale.json())["error"], "video_mode_stale")
        invalid = await self.client.post(
            "/api/v1/video/mode",
            json={"mode_id": "4k", "expected_generation": 1},
            headers=self.auth,
        )
        self.assertEqual(invalid.status, 400)
        self.assertEqual((await invalid.json())["error"], "video_mode_invalid")

    async def test_video_mode_can_recover_degraded_capture(self):
        self.video.ready = False
        recovered = await self.client.post(
            "/api/v1/video/mode",
            json={"mode_id": "1080p30", "expected_generation": 1},
            headers=self.auth,
        )
        self.assertEqual(recovered.status, 200)
        self.assertTrue((await recovered.json())["video"]["ready"])

    async def test_video_mode_is_blocked_by_control_and_reserves_claim_boundary(self):
        lease = await self.claim()
        blocked = await self.client.post(
            "/api/v1/video/mode",
            json={"mode_id": "1080p30", "expected_generation": 1},
            headers=self.auth,
        )
        self.assertEqual(blocked.status, 409)
        self.assertEqual((await blocked.json())["error"], "control_active")
        await self.client.post(
            "/api/v1/control/release",
            json={},
            headers={**self.auth, "X-NOOB-Lease": lease},
        )

        self.video.switch_gate = asyncio.Event()
        switching = asyncio.create_task(
            self.client.post(
                "/api/v1/video/mode",
                json={"mode_id": "1080p30", "expected_generation": 1},
                headers=self.auth,
            )
        )
        await asyncio.wait_for(self.video.switch_started.wait(), timeout=1)
        claim = await self.client.post(
            "/api/v1/control/claim", json={}, headers=self.auth
        )
        self.assertEqual(claim.status, 409)
        self.assertEqual((await claim.json())["error"], "video_mode_switching")
        arm = await self.client.post(
            "/api/v1/local-input/arm", json={}, headers=self.auth
        )
        self.assertEqual(arm.status, 409)
        self.assertEqual((await arm.json())["error"], "video_mode_switching")
        local_callback = await self.runtime.submit_local_input(
            {"op": "key", "event": "down", "key": "A"}
        )
        self.assertFalse(local_callback)
        self.video.switch_gate.set()
        self.assertEqual((await switching).status, 200)

    async def test_video_mode_failure_reports_bounded_rollback_state(self):
        self.video.mode_error = VideoSwitchFailed(
            "video_mode_mismatch", rolled_back=True
        )
        response = await self.client.post(
            "/api/v1/video/mode",
            json={"mode_id": "1080p30", "expected_generation": 1},
            headers=self.auth,
        )
        self.assertEqual(response.status, 503)
        payload = await response.json()
        self.assertEqual(payload["error"], "video_mode_mismatch")
        self.assertTrue(payload["rolled_back"])

    async def test_local_console_token_is_strictly_scoped(self):
        local_input = FakeLocalInput()
        app = create_app(
            GatewayConfig(local_input=LocalInputConfig(enabled=True)),
            token=TOKEN,
            local_console_token=LOCAL_TOKEN,
            serial_link=FakeSerial(),
            video=FakeVideo(),
            local_input=local_input,
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        local_auth = {"Authorization": f"Bearer {LOCAL_TOKEN}"}
        try:
            for path in (
                "/api/v1/status",
                "/api/v1/frame.jpg",
                "/api/v1/video/modes",
            ):
                response = await client.get(path, headers=local_auth)
                self.assertEqual(response.status, 200, path)

            switched = await client.post(
                "/api/v1/video/mode",
                json={"mode_id": "1080p30", "expected_generation": 1},
                headers=local_auth,
            )
            self.assertEqual(switched.status, 200)

            armed = await client.post(
                "/api/v1/local-input/arm", json={}, headers=local_auth
            )
            self.assertEqual(armed.status, 200)
            disarmed = await client.post(
                "/api/v1/local-input/disarm", json={}, headers=local_auth
            )
            self.assertEqual(disarmed.status, 200)

            for path in (
                "/api/v1/control/claim",
                "/api/v1/release-all",
            ):
                response = await client.post(path, json={}, headers=local_auth)
                self.assertEqual(response.status, 403, path)
                self.assertEqual(
                    (await response.json())["error"], "insufficient_scope"
                )
        finally:
            await client.close()

    async def test_local_input_uses_same_exclusive_lease_as_http(self):
        accepted = await self.runtime.submit_local_input(
            {"op": "mouse_move", "dx": 4, "dy": -2, "wheel": 0}
        )
        self.assertTrue(accepted)
        self.assertEqual(self.serial.sent[-1]["op"], "mouse_move")

        blocked_http = await self.client.post(
            "/api/v1/control/claim", json={}, headers=self.auth
        )
        self.assertEqual(blocked_http.status, 409)

        await self.runtime.release_local_input()
        self.assertEqual(self.serial.releases, 1)
        http_lease = await self.claim()
        blocked_local = await self.runtime.submit_local_input(
            {"op": "key", "event": "down", "key": "A"}
        )
        self.assertFalse(blocked_local)
        self.assertEqual([item["op"] for item in self.serial.sent], ["mouse_move"])

        # Clean up the HTTP lease without relying on test-client shutdown.
        response = await self.client.post(
            "/api/v1/control/release",
            json={},
            headers={**self.auth, "X-NOOB-Lease": http_lease},
        )
        self.assertEqual(response.status, 200)

    async def test_local_input_arm_is_authenticated_and_disabled_by_default(self):
        response = await self.client.post("/api/v1/local-input/arm", json={})
        self.assertEqual(response.status, 401)
        response = await self.client.post(
            "/api/v1/local-input/arm", json={}, headers=self.auth
        )
        self.assertEqual(response.status, 409)
        self.assertEqual((await response.json())["error"], "local_input_disabled")

    async def test_enabled_local_input_arm_disarm_endpoints(self):
        local_input = FakeLocalInput()
        app = create_app(
            GatewayConfig(local_input=LocalInputConfig(enabled=True)),
            token=TOKEN,
            serial_link=FakeSerial(),
            video=FakeVideo(),
            local_input=local_input,
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            armed = await client.post(
                "/api/v1/local-input/arm", json={}, headers=self.auth
            )
            self.assertEqual(armed.status, 200)
            self.assertTrue((await armed.json())["local_input"]["armed"])

            # Arming owns the physical controls immediately.  A remote claim
            # must be rejected even before the first local evdev event creates
            # the shared control lease.
            blocked = await client.post(
                "/api/v1/control/claim", json={}, headers=self.auth
            )
            self.assertEqual(blocked.status, 409)
            self.assertEqual((await blocked.json())["error"], "local_input_armed")

            disarmed = await client.post(
                "/api/v1/local-input/disarm", json={}, headers=self.auth
            )
            self.assertEqual(disarmed.status, 200)
            payload = await disarmed.json()
            self.assertFalse(payload["local_input"]["armed"])
            self.assertEqual(payload["local_input"]["disarm_reason"], "operator")

            claim = await client.post(
                "/api/v1/control/claim", json={}, headers=self.auth
            )
            self.assertEqual(claim.status, 200)
        finally:
            await client.close()

    async def test_one_lease_strict_input_and_emergency_release(self):
        lease = await self.claim()
        second = await self.client.post("/api/v1/control/claim", json={}, headers=self.auth)
        self.assertEqual(second.status, 409)

        headers = {**self.auth, "X-NOOB-Lease": lease}
        invalid = await self.client.post(
            "/api/v1/input", json={"op": "ping", "surprise": True}, headers=headers
        )
        self.assertEqual(invalid.status, 400)
        valid = await self.client.post(
            "/api/v1/input",
            json={"op": "key", "event": "down", "key": "A"},
            headers=headers,
        )
        self.assertEqual(valid.status, 200)
        self.assertEqual(self.serial.sent[-1]["key"], "A")

        released = await self.client.post("/api/v1/release-all", json={}, headers=self.auth)
        self.assertEqual(released.status, 200)
        self.assertEqual(self.serial.releases, 1)
        stale = await self.client.post(
            "/api/v1/input", json={"op": "ping"}, headers=headers
        )
        self.assertEqual(stale.status, 409)

    async def test_duplicate_json_key_is_rejected(self):
        lease = await self.claim()
        headers = {
            **self.auth,
            "X-NOOB-Lease": lease,
            "Content-Type": "application/json",
        }
        response = await self.client.post(
            "/api/v1/input", data='{"op":"ping","op":"release_all"}', headers=headers
        )
        self.assertEqual(response.status, 400)

    async def test_exact_phase_four_action_payloads_reach_canonical_serial_api(self):
        lease = await self.claim()
        headers = {**self.auth, "X-NOOB-Lease": lease}

        typed = await self.client.post(
            "/api/v1/input",
            json={"action": "type", "text": "ls -la\n"},
            headers=headers,
        )
        self.assertEqual(typed.status, 200)
        self.assertIn("result", await typed.json())
        self.assertEqual(
            self.serial.sent[-1],
            {"op": "type", "text": "ls -la\n", "interval_ms": 0},
        )

        combo = await self.client.post(
            "/api/v1/input",
            json={"action": "combo", "keys": ["GUI", "SPACE"]},
            headers=headers,
        )
        self.assertEqual(combo.status, 200)
        self.assertIn("result", await combo.json())
        self.assertEqual(
            self.serial.sent[-1],
            {
                "op": "combo",
                "keys": ["LEFT_GUI", "SPACE"],
                "hold_ms": 50,
            },
        )

    async def test_action_payload_rejects_unknown_and_oversize_fields(self):
        lease = await self.claim()
        headers = {**self.auth, "X-NOOB-Lease": lease}

        unknown = await self.client.post(
            "/api/v1/input",
            json={"action": "type", "text": "ok", "unexpected": True},
            headers=headers,
        )
        self.assertEqual(unknown.status, 400)
        self.assertEqual((await unknown.json())["error"], "bad_field")

        oversize = await self.client.post(
            "/api/v1/input",
            json={"action": "type", "text": "x" * 513},
            headers=headers,
        )
        self.assertEqual(oversize.status, 400)
        self.assertEqual((await oversize.json())["error"], "bad_range")
        self.assertEqual(self.serial.sent, [])

    async def test_latest_frame_is_authenticated_jpeg(self):
        response = await self.client.get("/api/v1/frame.jpg")
        self.assertEqual(response.status, 401)
        response = await self.client.get("/api/v1/frame.jpg", headers=self.auth)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "image/jpeg")
        self.assertEqual(await response.read(), self.video.frame.data)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_status_snapshot_after_expiry_releases_input(self):
        clock = await self.use_deterministic_lease_clock()
        await self.claim()
        clock.advance(5.1)

        response = await self.client.get("/api/v1/status", headers=self.auth)
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertFalse(payload["control"]["active"])
        self.assertFalse(payload["control"]["release_required"])
        self.assertEqual(self.serial.releases, 1)

    async def test_validate_after_expiry_releases_before_rejecting_input(self):
        clock = await self.use_deterministic_lease_clock()
        lease = await self.claim()
        headers = {**self.auth, "X-NOOB-Lease": lease}
        pressed = await self.client.post(
            "/api/v1/input",
            json={"op": "key", "event": "down", "key": "A"},
            headers=headers,
        )
        self.assertEqual(pressed.status, 200)
        clock.advance(5.1)

        rejected = await self.client.post(
            "/api/v1/input", json={"op": "ping"}, headers=headers
        )
        self.assertEqual(rejected.status, 409)
        self.assertEqual(self.serial.releases, 1)
        self.assertEqual([command["op"] for command in self.serial.sent], ["key"])

    async def test_new_claim_waits_for_expired_controller_release(self):
        clock = await self.use_deterministic_lease_clock()
        old_lease = await self.claim()
        old_headers = {**self.auth, "X-NOOB-Lease": old_lease}
        pressed = await self.client.post(
            "/api/v1/input",
            json={"op": "key", "event": "down", "key": "LEFT_SHIFT"},
            headers=old_headers,
        )
        self.assertEqual(pressed.status, 200)
        clock.advance(5.1)

        self.serial.release_gate = asyncio.Event()
        replacement = asyncio.create_task(
            self.client.post("/api/v1/control/claim", json={}, headers=self.auth)
        )
        await asyncio.wait_for(self.serial.release_started.wait(), timeout=1.0)
        self.assertFalse(replacement.done())
        self.serial.release_gate.set()

        response = await asyncio.wait_for(replacement, timeout=1.0)
        self.assertEqual(response.status, 200)
        new_lease = (await response.json())["lease"]
        self.assertNotEqual(new_lease, old_lease)
        self.assertEqual(self.serial.releases, 1)
        self.assertEqual(
            self.serial.events[-2:],
            [("release", "started"), ("release", "complete")],
        )

    async def test_failed_raw_release_is_relatched_until_retry_is_confirmed(self):
        clock = await self.use_deterministic_lease_clock()
        old_lease = await self.claim()
        clock.advance(5.1)
        self.serial.release_failures.append(
            pyserial.SerialException("simulated release write failure")
        )

        failed = await self.client.post(
            "/api/v1/control/claim", json={}, headers=self.auth
        )
        self.assertEqual(failed.status, 503)
        snapshot = await self.runtime.lease.snapshot()
        self.assertFalse(snapshot.active)
        self.assertTrue(snapshot.release_required)
        self.assertEqual(self.serial.release_attempts, 1)
        self.assertEqual(self.serial.releases, 0)

        replacement = await self.client.post(
            "/api/v1/control/claim", json={}, headers=self.auth
        )
        self.assertEqual(replacement.status, 200)
        new_lease = (await replacement.json())["lease"]
        self.assertNotEqual(new_lease, old_lease)
        self.assertEqual(self.serial.release_attempts, 2)
        self.assertEqual(self.serial.releases, 1)
        snapshot = await self.runtime.lease.snapshot()
        self.assertTrue(snapshot.active)
        self.assertFalse(snapshot.release_required)

    async def test_ack_loss_blocks_claim_until_reconnect_and_release(self):
        old_lease = await self.claim()
        old_headers = {**self.auth, "X-NOOB-Lease": old_lease}
        self.serial.input_failure = SerialTimeout("both ACKs lost")

        uncertain = await self.client.post(
            "/api/v1/input",
            json={"op": "key", "event": "down", "key": "A"},
            headers=old_headers,
        )
        self.assertEqual(uncertain.status, 504)
        self.assertFalse(self.serial.ready)
        snapshot = await self.runtime.lease.snapshot()
        self.assertFalse(snapshot.active)
        self.assertTrue(snapshot.release_required)
        self.assertEqual(self.serial.releases, 0)

        blocked = await self.client.post(
            "/api/v1/control/claim", json={}, headers=self.auth
        )
        self.assertEqual(blocked.status, 503)
        snapshot = await self.runtime.lease.snapshot()
        self.assertTrue(snapshot.release_required)

        self.serial.ready = True
        self.serial.generation += 1
        replacement = await self.client.post(
            "/api/v1/control/claim", json={}, headers=self.auth
        )
        self.assertEqual(replacement.status, 200)
        new_lease = (await replacement.json())["lease"]
        self.assertNotEqual(new_lease, old_lease)
        self.assertEqual(self.serial.releases, 1)
        self.assertFalse((await self.runtime.lease.snapshot()).release_required)


class FakeLeaseClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


if __name__ == "__main__":
    unittest.main()
