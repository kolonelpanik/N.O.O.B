from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
import queue
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "appliance" / "noob_local_console.py"
)
SPEC = importlib.util.spec_from_file_location("noob_local_console", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def environment_payload(**overrides):
    value = {
        "configured": True,
        "reachable": True,
        "stream_enabled": True,
        "sensor_enabled": True,
        "power_control": False,
        "frame_ready": True,
        "generation": 9,
        "last_frame_age_ms": 18,
        "viewers": 2,
        "storage": {
            "state": "mounted",
            "mounted": True,
            "writable": True,
            "total_bytes": 8_000_000_000,
            "free_bytes": 6_000_000_000,
            "reserve_bytes": 10_000_000,
            "media_count": 3,
            "active_job_id": None,
            "limits": {
                "max_media_items": 500,
                "max_total_bytes": 7_000_000_000,
                "max_clip_duration_ms": 30_000,
                "max_clip_fps": 5,
                "max_clip_frames": 150,
            },
            "last_error": None,
        },
        "last_error": None,
    }
    value.update(overrides)
    return value


class LocalConsoleContractTests(unittest.TestCase):
    def test_pairing_identity_matches_the_installed_helper_algorithm(self):
        helper_path = (
            Path(__file__).resolve().parents[1] / "scripts" / "noob_pairing_code.py"
        )
        helper_spec = importlib.util.spec_from_file_location(
            "noob_pairing_code_for_local_console_test", helper_path
        )
        assert helper_spec is not None and helper_spec.loader is not None
        helper = importlib.util.module_from_spec(helper_spec)
        helper_spec.loader.exec_module(helper)

        key = bytes([47]) * 32
        public = f"ssh-ed25519 {base64.b64encode(key).decode('ascii')} appliance\n"
        fingerprint = MODULE.fingerprint_for_ssh_host_public_key(public)
        self.assertEqual(fingerprint, helper.fingerprint_for_public_key(public))
        self.assertEqual(
            MODULE.pairing_code_for_ssh_fingerprint(fingerprint),
            helper.pairing_code_for_fingerprint(fingerprint),
        )

    def test_pairing_identity_loader_rejects_symlink_and_accepts_regular_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            key = bytes([47]) * 32
            public = f"ssh-ed25519 {base64.b64encode(key).decode('ascii')} appliance\n"
            key_path = directory / "ssh_host_ed25519_key.pub"
            key_path.write_text(public, encoding="ascii")
            code, fingerprint = MODULE.load_local_pairing_identity(key_path)
            self.assertRegex(code, r"^[0-9]{4}-[0-9]{4}$")
            self.assertTrue(fingerprint.startswith("SHA256:"))

            link_path = directory / "host-key-link.pub"
            link_path.symlink_to(key_path)
            with self.assertRaisesRegex(
                MODULE.LocalConsoleError, "pairing_identity_unavailable"
            ):
                MODULE.load_local_pairing_identity(link_path)

    def test_ui_installer_deploys_supported_pairing_helper(self):
        installer = (
            Path(__file__).resolve().parents[1] / "scripts" / "install_uconsole_ui.sh"
        ).read_text(encoding="utf-8")
        self.assertIn(
            '"$SOURCE_ROOT/scripts/noob_pairing_code.py" /opt/noob/appliance/noob_pairing_code.py',
            installer,
        )
        self.assertIn("/usr/local/bin/noob-pairing-code", installer)

    def test_single_instance_lock_is_owner_private_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            os.chmod(runtime, 0o700)
            first = MODULE.acquire_local_console_instance_lock(runtime)
            try:
                self.assertEqual(first.path, runtime / "noob-local-console.lock")
                self.assertEqual(stat.S_IMODE(first.path.stat().st_mode), 0o600)
                with self.assertRaisesRegex(
                    MODULE.LocalConsoleError, "already_running"
                ):
                    MODULE.acquire_local_console_instance_lock(runtime)
            finally:
                first.close()

            replacement = MODULE.acquire_local_console_instance_lock(runtime)
            replacement.close()

    def test_runtime_lock_directory_rejects_weak_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            os.chmod(runtime, 0o755)
            try:
                with self.assertRaisesRegex(
                    MODULE.LocalConsoleError, "instance_lock_unavailable"
                ):
                    MODULE.user_runtime_directory(
                        environ={"XDG_RUNTIME_DIR": str(runtime)},
                        uid=os.getuid(),
                    )
            finally:
                os.chmod(runtime, 0o700)

    def test_gateway_origin_is_loopback_only(self):
        self.assertEqual(
            MODULE.validate_loopback_gateway("http://127.0.0.1:8765/"),
            "http://127.0.0.1:8765",
        )
        self.assertEqual(
            MODULE.validate_loopback_gateway("http://localhost:18765"),
            "http://localhost:18765",
        )
        for candidate in (
            "https://127.0.0.1:8765",
            "http://192.0.2.83:8765",
            "http://user@127.0.0.1:8765",
            "http://127.0.0.1:8765/api",
            "http://127.0.0.1:8765?token=bad",
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    MODULE.validate_loopback_gateway(candidate)

    def test_token_loader_accepts_only_gateway_contract(self):
        token = b"a" * 32 + b"\n"

        def runner(*_args, **_kwargs):
            return subprocess.CompletedProcess([], 0, stdout=token)

        self.assertEqual(MODULE.load_local_token(runner=runner), "a" * 32)
        for invalid in (b"short", b"a" * 257, b"a" * 31 + b" "):
            with self.subTest(invalid=invalid):
                with self.assertRaises(MODULE.LocalConsoleError):
                    MODULE.validate_token_bytes(invalid)

    def test_local_and_remote_control_states_are_mutually_exclusive(self):
        base = {
            "ok": True,
            "serial": {"ready": True},
            "video": {"ready": True},
            "local_input": {
                "enabled": True,
                "ready": True,
                "armed": False,
                "exclusive_grab": False,
                "keyboard_ready": True,
                "pointer_ready": True,
            },
            "control": {
                "active": False,
                "release_required": False,
            },
        }
        available = MODULE.view_state_from_status(base)
        self.assertTrue(available.arm_allowed)
        self.assertFalse(available.remote_control_active)

        remote_payload = {
            **base,
            "control": {"active": True, "release_required": False},
        }
        remote = MODULE.view_state_from_status(remote_payload)
        self.assertFalse(remote.arm_allowed)
        self.assertTrue(remote.remote_control_active)

        local_payload = {
            **remote_payload,
            "local_input": {
                **base["local_input"],
                "armed": True,
                "exclusive_grab": True,
            },
        }
        local = MODULE.view_state_from_status(local_payload)
        self.assertFalse(local.arm_allowed)
        self.assertFalse(local.remote_control_active)

        armed_without_event_payload = {
            **base,
            "local_input": {
                **base["local_input"],
                "armed": True,
                "exclusive_grab": True,
            },
        }
        armed_without_event = MODULE.view_state_from_status(armed_without_event_payload)
        self.assertFalse(armed_without_event.arm_allowed)
        self.assertFalse(armed_without_event.control_active)

    def test_degraded_video_mode_remains_recoverable_without_input_owner(self):
        payload = {
            "ok": True,
            "serial": {"ready": True},
            "video": {
                "ready": False,
                "state": "degraded",
                "generation": 4,
                "active_mode_id": "1440p30",
                "requested": {
                    "width": 2560,
                    "height": 1440,
                    "fps": 30,
                    "pixel_format": "MJPG",
                },
                "negotiated": None,
                "source_timing_detectable": False,
            },
            "local_input": {
                "enabled": True,
                "armed": False,
                "exclusive_grab": False,
                "keyboard_ready": True,
                "pointer_ready": True,
            },
            "control": {"active": False, "release_required": False},
        }
        state = MODULE.view_state_from_status(payload)
        self.assertFalse(state.video_ready)
        self.assertTrue(state.mode_change_allowed)

        payload["control"] = {"active": True, "release_required": False}
        self.assertFalse(MODULE.view_state_from_status(payload).mode_change_allowed)

    def test_fractional_negotiated_fps_is_valid_status(self):
        payload = {
            "ok": True,
            "serial": {"ready": True},
            "video": {
                "ready": True,
                "state": "ready",
                "generation": 3,
                "active_mode_id": "1080p30",
                "requested": {
                    "width": 1920,
                    "height": 1080,
                    "fps": 30,
                    "pixel_format": "MJPG",
                },
                "negotiated": {
                    "width": 1920,
                    "height": 1080,
                    "fps": 29.97,
                    "pixel_format": "MJPG",
                },
                "source_timing_detectable": False,
            },
            "local_input": {
                "enabled": True,
                "armed": False,
                "exclusive_grab": False,
                "keyboard_ready": True,
                "pointer_ready": True,
            },
            "control": {"active": False, "release_required": False},
        }
        state = MODULE.view_state_from_status(payload)
        self.assertEqual(state.negotiated_signal, (1920, 1080, 29.97, "MJPG"))

    def test_malformed_status_fails_closed(self):
        for candidate in (None, {}, {"ok": False}, {"ok": True}):
            with self.subTest(candidate=candidate):
                with self.assertRaises(MODULE.LocalConsoleError):
                    MODULE.view_state_from_status(candidate)

    def test_environment_camera_status_is_independent_and_backward_compatible(self):
        base = {
            "ok": True,
            "serial": {"ready": True},
            "video": {"ready": True},
            "local_input": {
                "enabled": True,
                "armed": False,
                "exclusive_grab": False,
                "keyboard_ready": True,
                "pointer_ready": True,
            },
            "control": {"active": False, "release_required": False},
        }
        legacy = MODULE.view_state_from_status(base)
        self.assertFalse(legacy.environment_camera.configured)

        state = MODULE.view_state_from_status(
            {**base, "environment_camera": environment_payload()}
        )
        self.assertTrue(state.environment_camera.frame_ready)
        self.assertFalse(state.environment_camera.power_control)
        self.assertEqual(state.environment_camera.storage.media_count, 3)

        malformed = environment_payload(sensor_enabled="yes")
        with self.assertRaisesRegex(
            MODULE.LocalConsoleError, "camera_status_unavailable"
        ):
            MODULE.view_state_from_status(
                {**base, "environment_camera": malformed}
            )

    def test_environment_camera_and_storage_contracts_are_bounded(self):
        camera = MODULE.environment_camera_from_response(
            {"ok": True, "environment_camera": environment_payload()}
        )
        self.assertEqual(camera.generation, 9)
        self.assertEqual(camera.last_frame_age_ms, 18)

        item = {
            "id": "m_00000000000000000000000000000001",
            "kind": "snapshot",
            "state": "complete",
            "created_at": "2026-08-27T19:00:00Z",
            "created_uptime_ms": 12_000,
            "size_bytes": 123456,
            "width": 1600,
            "height": 1200,
            "frame_count": 1,
            "fps": None,
            "duration_ms": 0,
            "content_type": "image/jpeg",
        }
        catalog = MODULE.camera_storage_from_payload(
            {
                "ok": True,
                "storage": environment_payload()["storage"],
                "items": [item],
                "next_cursor": "cursor-2",
            }
        )
        self.assertEqual(catalog.items[0].item_id, item["id"])
        self.assertIn("SNAPSHOT", catalog.items[0].display_label)

        with self.assertRaisesRegex(
            MODULE.LocalConsoleError, "camera_storage_unavailable"
        ):
            MODULE.camera_storage_from_payload(
                {
                    "ok": True,
                    "storage": environment_payload()["storage"],
                    "items": [{**item, "id": "../escape"}],
                    "next_cursor": None,
                }
            )
        with self.assertRaisesRegex(
            MODULE.LocalConsoleError, "camera_storage_unavailable"
        ):
            MODULE.camera_storage_from_payload(
                {
                    "ok": True,
                    "storage": environment_payload()["storage"],
                    "items": [item, item],
                    "next_cursor": None,
                }
            )

    def test_close_waits_for_inflight_arm_and_disarms_last(self):
        gate = MODULE.ActionGate()
        arm_started = threading.Event()
        release_arm = threading.Event()
        actions = []

        def arm():
            actions.append("arm-start")
            arm_started.set()
            release_arm.wait(2)
            actions.append("arm-complete")
            return "armed"

        arm_thread = threading.Thread(target=lambda: gate.run(arm))
        arm_thread.start()
        self.assertTrue(arm_started.wait(1))

        close_thread = threading.Thread(
            target=lambda: gate.close(lambda: actions.append("disarm"))
        )
        close_thread.start()
        time.sleep(0.02)
        self.assertNotIn("disarm", actions)
        release_arm.set()
        arm_thread.join(1)
        close_thread.join(1)
        self.assertEqual(actions, ["arm-start", "arm-complete", "disarm"])

        # Once close begins, no later arm action can run.
        self.assertIsNone(gate.run(lambda: actions.append("late-arm")))
        self.assertNotIn("late-arm", actions)

    def test_failed_close_keeps_action_gate_retryable(self):
        gate = MODULE.ActionGate()
        attempts = []

        def failed_disarm():
            attempts.append("failed")
            raise MODULE.LocalConsoleError("release_unconfirmed")

        with self.assertRaises(MODULE.LocalConsoleError):
            gate.close(failed_disarm)

        self.assertEqual(gate.run(lambda: "retryable"), "retryable")
        self.assertEqual(gate.close(lambda: attempts.append("released")), None)
        self.assertEqual(attempts, ["failed", "released"])

    def test_frame_response_bound_covers_largest_supported_profile(self):
        self.assertGreaterEqual(MODULE.MAX_FRAME_RESPONSE_BYTES, 8_294_400)
        self.assertLessEqual(MODULE.MAX_FRAME_RESPONSE_BYTES, 16 * 1024 * 1024)

    def test_status_pressure_never_evicts_action_result(self):
        console = MODULE.NoobLocalConsole.__new__(MODULE.NoobLocalConsole)
        console.stop_event = threading.Event()
        console.events = queue.Queue(maxsize=2)
        console.frame_lock = threading.Lock()
        console.latest_frame = None

        action = ("action", "confirmed")
        console.events.put_nowait(action)
        console.events.put_nowait(("status", "older"))
        console._offer(("status", "newer"))
        console._offer(("error", "transient"))

        self.assertEqual(console.events.get_nowait(), action)

    def test_video_mode_catalog_is_bounded_and_validated(self):
        payload = {
            "ok": True,
            "generation": 2,
            "active_mode_id": "720p20",
            "state": "ready",
            "modes": [
                {
                    "id": "720p20",
                    "label": "Balanced",
                    "width": 1280,
                    "height": 720,
                    "fps": 20,
                    "pixel_format": "MJPG",
                    "max_frame_bytes": 1_843_200,
                    "validated": True,
                }
            ],
        }
        catalog = MODULE.video_modes_from_payload(payload)
        self.assertEqual(catalog.active_mode_id, "720p20")
        self.assertEqual(catalog.modes[0].display_label, "Balanced · 1280×720 @ 20")

        payload["modes"][0]["validated"] = False
        with self.assertRaises(MODULE.LocalConsoleError):
            MODULE.video_modes_from_payload(payload)

    def test_mode_switch_uses_id_and_optimistic_generation_once(self):
        status_payload = {
            "ok": True,
            "serial": {"ready": True},
            "video": {
                "ready": True,
                "state": "ready",
                "generation": 8,
                "active_mode_id": "1080p30",
                "requested": None,
                "negotiated": None,
                "source_timing_detectable": False,
            },
            "local_input": {
                "enabled": True,
                "armed": False,
                "exclusive_grab": False,
                "keyboard_ready": True,
                "pointer_ready": True,
            },
            "control": {"active": False, "release_required": False},
        }

        class ModeClient(MODULE.GatewayClient):
            def __init__(self):
                self.calls = []

            def _request(self, path, **kwargs):
                self.calls.append((path, kwargs))
                return b'{"ok":true}'

            def status(self):
                return MODULE.view_state_from_status(status_payload)

        client = ModeClient()
        state = client.set_video_mode("1080p30", 7)
        self.assertEqual(state.video_generation, 8)
        self.assertEqual(len(client.calls), 1)
        path, kwargs = client.calls[0]
        self.assertEqual(path, "/api/v1/video/mode")
        self.assertEqual(kwargs["method"], "POST")
        self.assertGreaterEqual(kwargs["timeout"], 60.0)
        self.assertEqual(
            json.loads(kwargs["body"]),
            {"mode_id": "1080p30", "expected_generation": 7},
        )

        with self.assertRaises(MODULE.LocalConsoleError):
            client.set_video_mode("../unsafe", 8)
        self.assertEqual(len(client.calls), 1)

    def test_environment_client_uses_exact_bounded_routes_and_payloads(self):
        snapshot_item = {
            "id": "m_00000000000000000000000000000001",
            "kind": "snapshot",
            "state": "complete",
            "created_at": "2026-08-27T19:00:00Z",
            "created_uptime_ms": 100,
            "size_bytes": 1111,
            "width": 640,
            "height": 480,
            "frame_count": 1,
            "fps": None,
            "duration_ms": 0,
            "content_type": "image/jpeg",
        }
        clip_item = {
            "id": "m_00000000000000000000000000000002",
            "kind": "clip",
            "state": "complete",
            "created_at": "2026-08-27T19:00:10Z",
            "created_uptime_ms": 200,
            "size_bytes": 2222,
            "width": 640,
            "height": 480,
            "frame_count": 20,
            "fps": 2,
            "duration_ms": 10_000,
            "content_type": "application/vnd.noob.clip+json",
        }
        job_id = "j_00000000000000000000000000000003"

        class CameraClient(MODULE.GatewayClient):
            def __init__(self):
                self.calls = []

            def _request(self, path, **kwargs):
                self.calls.append((path, kwargs))
                if (
                    path.endswith("/frame.jpg")
                    or path.endswith("/content")
                    or ("/frames/" in path and path.endswith(".jpg"))
                ):
                    return b"\xff\xd8frame\xff\xd9"
                if path.endswith("/state"):
                    return json.dumps(
                        {"ok": True, "environment_camera": environment_payload()}
                    ).encode()
                if "storage?" in path:
                    return json.dumps(
                        {
                            "ok": True,
                            "storage": environment_payload()["storage"],
                            "items": [snapshot_item],
                            "next_cursor": None,
                        }
                    ).encode()
                if path.endswith("/snapshot"):
                    return json.dumps({"ok": True, "item": snapshot_item}).encode()
                if path.endswith("/clip"):
                    return json.dumps(
                        {"ok": True, "job_id": job_id, "state": "queued"}
                    ).encode()
                if path.endswith("/stop"):
                    return json.dumps(
                        {"ok": True, "job_id": job_id, "state": "cancelling"}
                    ).encode()
                if "/jobs/" in path:
                    return json.dumps(
                        {
                            "ok": True,
                            "job": {
                                "job_id": job_id,
                                "kind": "clip",
                                "state": "complete",
                                "created_uptime_ms": 200,
                                "frames_written": 20,
                                "frames_target": 20,
                                "media_id": clip_item["id"],
                                "error_code": None,
                            },
                        }
                    ).encode()
                return json.dumps({"ok": True, "item": clip_item}).encode()

        client = CameraClient()
        self.assertEqual(client.frame("environment"), b"\xff\xd8frame\xff\xd9")
        self.assertTrue(client.set_environment_enabled(False, 9).configured)
        self.assertEqual(
            client.camera_storage(limit=20).items[0].kind, "snapshot"
        )
        self.assertEqual(client.camera_snapshot(9).item_id, snapshot_item["id"])
        self.assertEqual(
            client.start_camera_clip(9, duration_seconds=10), job_id
        )
        self.assertEqual(client.camera_clip_job(job_id).state, "complete")
        self.assertEqual(client.stop_camera_clip(job_id), "cancelling")
        self.assertEqual(client.camera_media(clip_item["id"]).duration_ms, 10_000)
        self.assertEqual(
            client.camera_snapshot_content(snapshot_item["id"]),
            b"\xff\xd8frame\xff\xd9",
        )
        self.assertEqual(
            client.camera_clip_frame(clip_item["id"], 19),
            b"\xff\xd8frame\xff\xd9",
        )
        calls_before_rejection = len(client.calls)
        with self.assertRaisesRegex(
            MODULE.LocalConsoleError, "camera_storage_unavailable"
        ):
            client.camera_snapshot_content("../../etc/passwd")
        with self.assertRaisesRegex(
            MODULE.LocalConsoleError, "camera_storage_unavailable"
        ):
            client.camera_clip_frame(clip_item["id"], 150)
        self.assertEqual(len(client.calls), calls_before_rejection)

        paths = [path for path, _kwargs in client.calls]
        self.assertEqual(
            paths,
            [
                "/api/v1/environment-camera/frame.jpg",
                "/api/v1/environment-camera/state",
                "/api/v1/environment-camera/storage?limit=20",
                "/api/v1/environment-camera/snapshot",
                "/api/v1/environment-camera/clip",
                f"/api/v1/environment-camera/jobs/{job_id}",
                f"/api/v1/environment-camera/jobs/{job_id}/stop",
                f"/api/v1/environment-camera/storage/{clip_item['id']}",
                f"/api/v1/environment-camera/storage/{snapshot_item['id']}/content",
                f"/api/v1/environment-camera/storage/{clip_item['id']}/frames/19.jpg",
            ],
        )
        self.assertEqual(
            json.loads(client.calls[1][1]["body"]),
            {"enabled": False, "expected_generation": 9},
        )
        self.assertEqual(
            json.loads(client.calls[4][1]["body"]),
            {"duration_seconds": 10, "fps": 2, "expected_generation": 9},
        )
        self.assertGreaterEqual(client.calls[4][1]["timeout"], 30.0)
        self.assertEqual(client.calls[6][1]["method"], "POST")
        self.assertEqual(client.calls[6][1]["body"], b"{}")

    def test_camera_clip_job_accepts_cancellation_states(self):
        job_id = "j_00000000000000000000000000000003"
        base = {
            "job_id": job_id,
            "kind": "clip",
            "created_uptime_ms": 200,
            "frames_written": 3,
            "frames_target": 20,
            "media_id": None,
            "error_code": None,
        }
        for state in ("cancelling", "cancelled"):
            with self.subTest(state=state):
                job = MODULE.camera_clip_job_from_payload(
                    {"ok": True, "job": {**base, "state": state}}
                )
                self.assertEqual(job.state, state)

    def test_local_media_preview_uses_catalog_items_and_bounded_clip_navigation(self):
        snapshot = MODULE.camera_storage_item_from_payload(
            {
                "id": "m_00000000000000000000000000000001",
                "kind": "snapshot",
                "state": "complete",
                "created_at": None,
                "created_uptime_ms": 100,
                "size_bytes": 1111,
                "width": 640,
                "height": 480,
                "frame_count": 1,
                "fps": None,
                "duration_ms": 0,
                "content_type": "image/jpeg",
            }
        )
        clip = MODULE.camera_storage_item_from_payload(
            {
                "id": "m_00000000000000000000000000000002",
                "kind": "clip",
                "state": "complete",
                "created_at": None,
                "created_uptime_ms": 200,
                "size_bytes": 2222,
                "width": 640,
                "height": 480,
                "frame_count": 3,
                "fps": 1,
                "duration_ms": 3000,
                "content_type": "application/vnd.noob.clip+json",
            }
        )

        class Message:
            def configure(self, **_kwargs):
                pass

        class Client:
            def __init__(self):
                self.calls = []

            def camera_snapshot_content(self, media_id):
                self.calls.append(("snapshot", media_id))
                return b"\xff\xd8snapshot\xff\xd9"

            def camera_clip_frame(self, media_id, frame_index):
                self.calls.append(("clip", media_id, frame_index))
                return b"\xff\xd8clip\xff\xd9"

        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        console = MODULE.NoobLocalConsole.__new__(MODULE.NoobLocalConsole)
        console.source = "environment"
        console.media_preview_inflight = False
        console.media_preview_request = None
        console.media_preview_item = None
        console.media_preview_frame_index = 0
        console.closing = False
        console.client = Client()
        console.message = Message()
        console._update_media_preview_detail = lambda: None
        console._set_buttons = lambda: None
        offered = []
        console._offer = offered.append

        with mock.patch.object(MODULE.threading, "Thread", ImmediateThread):
            console._request_media_preview(snapshot, 0)
        self.assertEqual(console.client.calls, [("snapshot", snapshot.item_id)])
        self.assertEqual(offered[0][0], "media_preview")
        self.assertEqual(offered[0][1][:2], (snapshot, 0))

        console.media_preview_inflight = False
        console.media_preview_request = None
        console.media_preview_item = clip
        console.media_preview_frame_index = 0
        with mock.patch.object(MODULE.threading, "Thread", ImmediateThread):
            console._navigate_media_preview(-1)
            console._navigate_media_preview(1)
        self.assertEqual(console.client.calls[-1], ("clip", clip.item_id, 1))

        console.media_preview_inflight = False
        console.media_preview_request = None
        console.media_preview_frame_index = clip.frame_count - 1
        calls_before_bound = list(console.client.calls)
        with mock.patch.object(MODULE.threading, "Thread", ImmediateThread):
            console._navigate_media_preview(1)
        self.assertEqual(console.client.calls, calls_before_bound)

        class Selection:
            def __init__(self, index):
                self.index = index

            def curselection(self):
                return (self.index,)

        console.storage_catalog = MODULE.CameraStorageCatalog(
            MODULE.CameraStorageState.unavailable(), (snapshot, clip), None
        )
        console.storage_list = Selection(1)
        self.assertIs(console._selected_storage_item(), clip)
        console.storage_list = Selection(2)
        self.assertIsNone(console._selected_storage_item())

    def test_screenshot_is_private_and_never_overwrites(self):
        data = b"\xff\xd8private-jpeg\xff\xd9"
        captured_at = MODULE.datetime(2026, 8, 27, 19, 0, 0)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "shots"
            first = MODULE.save_screenshot(
                data,
                "environment",
                directory=directory,
                captured_at=captured_at,
            )
            second = MODULE.save_screenshot(
                data,
                "environment",
                directory=directory,
                captured_at=captured_at,
            )
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), data)
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertTrue(second.name.endswith("-1.jpg"))

        with self.assertRaisesRegex(
            MODULE.LocalConsoleError, "screenshot_unavailable"
        ):
            MODULE.save_screenshot(b"not-jpeg", "target")

    def test_zoom_and_pan_geometry_is_bounded(self):
        self.assertEqual(
            MODULE.image_render_size(1920, 1080, 800, 600, "FIT"),
            (800, 450),
        )
        self.assertEqual(
            MODULE.image_render_size(1920, 1080, 800, 600, "200%"),
            (3840, 2160),
        )
        self.assertEqual(
            MODULE.clamp_pan(9999, -9999, 1600, 1200, 800, 600),
            (400, -300),
        )

    def test_fullscreen_controller_remaps_borderless_and_restores_geometry(self):
        class FakeRoot:
            def __init__(self):
                self.geometry_value = "1100x680+90+18"
                self.width = 1100
                self.height = 680
                self.x = 90
                self.y = 18
                self.mapped = True
                self.pending_unmap = False
                self.wm_fullscreen = False
                self.calls = []

            def update_idletasks(self):
                self.calls.append(("update",))
                if self.pending_unmap:
                    self.mapped = False
                    self.pending_unmap = False
                    self.calls.append(("flush-unmap",))

            def geometry(self, value=None):
                if value is None:
                    return self.geometry_value
                self.calls.append(("geometry", value))
                self.geometry_value = value
                match = MODULE.re.fullmatch(
                    r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", value
                )
                assert match is not None
                self.width, self.height, self.x, self.y = map(
                    int, match.groups()
                )

            def withdraw(self):
                self.calls.append(("withdraw",))
                self.mapped = False
                self.pending_unmap = True

            def deiconify(self):
                self.calls.append(("deiconify",))
                self.mapped = True

            def state(self, value):
                self.calls.append(("state", value))

            def overrideredirect(self, value):
                self.calls.append(("override", value))

            def attributes(self, name, value):
                self.calls.append(("attribute", name, value))

            def lift(self):
                self.calls.append(("lift",))

            def focus_force(self):
                self.calls.append(("focus",))

            def winfo_screenwidth(self):
                return 1280

            def winfo_screenheight(self):
                return 720

            def winfo_id(self):
                return 0x1234

            def winfo_rootx(self):
                return self.x

            def winfo_rooty(self):
                return self.y

            def winfo_width(self):
                return self.width

            def winfo_height(self):
                return self.height

            def winfo_ismapped(self):
                return self.mapped

            def winfo_viewable(self):
                return self.mapped

        root = FakeRoot()
        wm_calls = []

        def wm_runner(command, **_kwargs):
            wm_calls.append(command)
            root.calls.append(("wm", *command))
            if command[-1:] == ("add,fullscreen,above",):
                root.wm_fullscreen = True
            elif command[-1:] == ("remove,fullscreen,above",):
                root.wm_fullscreen = False
                # Model XFCE applying its fullscreen restore geometry when the
                # state hint is removed.  The controller must apply the saved
                # geometry *after* this transition or Escape leaves the window
                # at the physical-screen size.
                root.geometry_value = "1280x720+0+0"
                root.width, root.height, root.x, root.y = 1280, 720, 0, 0
            if command[:2] == ("/usr/bin/xdotool", "windowmap"):
                root.mapped = True
            return subprocess.CompletedProcess(command, 0)

        controller = MODULE.FullscreenController(root, command_runner=wm_runner)
        controller.enter()
        add_call = (
            "wm",
            "/usr/bin/wmctrl",
            "-i",
            "-r",
            "0x1234",
            "-b",
            "add,fullscreen,above",
        )
        self.assertLess(root.calls.index(add_call), root.calls.index(("withdraw",)))
        self.assertTrue(root.calls.index(("withdraw",)) < root.calls.index(("override", True)))
        self.assertNotIn(("attribute", "-fullscreen", True), root.calls)
        self.assertTrue(root.wm_fullscreen)
        self.assertIn(("geometry", "1280x720+0+0"), root.calls)
        self.assertTrue(MODULE.window_covers_screen(root))
        self.assertIn(
            (
                "/usr/bin/wmctrl",
                "-i",
                "-r",
                "0x1234",
                "-b",
                "add,fullscreen,above",
            ),
            wm_calls,
        )
        self.assertIn(
            ("/usr/bin/xdotool", "windowmap", "--sync", str(0x1234)),
            wm_calls,
        )
        map_call = (
            "wm",
            "/usr/bin/xdotool",
            "windowmap",
            "--sync",
            str(0x1234),
        )
        map_positions = [
            index for index, call in enumerate(root.calls) if call == map_call
        ]
        self.assertGreaterEqual(len(map_positions), 2)
        self.assertLess(map_positions[0], root.calls.index(("withdraw",)))
        self.assertLess(root.calls.index(("flush-unmap",)), map_positions[1])
        root.mapped = False
        self.assertFalse(MODULE.window_covers_screen(root))
        root.mapped = True

        exit_call_start = len(root.calls)
        controller.exit(topmost=False)
        exit_calls = root.calls[exit_call_start:]
        remove_call = (
            "wm",
            "/usr/bin/wmctrl",
            "-i",
            "-r",
            "0x1234",
            "-b",
            "remove,fullscreen,above",
        )
        self.assertLess(
            exit_calls.index(("override", False)),
            exit_calls.index(("attribute", "-fullscreen", False)),
        )
        self.assertLess(
            exit_calls.index(("attribute", "-fullscreen", False)),
            exit_calls.index(remove_call),
        )
        self.assertLess(
            exit_calls.index(remove_call),
            exit_calls.index(("geometry", "1100x680+90+18")),
        )
        self.assertLess(
            exit_calls.index(("geometry", "1100x680+90+18")),
            exit_calls.index(("attribute", "-topmost", False)),
        )
        self.assertFalse(root.wm_fullscreen)
        self.assertEqual(root.geometry_value, "1100x680+90+18")
        self.assertEqual((root.x, root.y, root.width, root.height), (90, 18, 1100, 680))
        self.assertIn(("attribute", "-topmost", False), root.calls)
        self.assertTrue(root.mapped)
        self.assertIn(
            (
                "/usr/bin/wmctrl",
                "-i",
                "-r",
                "0x1234",
                "-b",
                "remove,fullscreen,above",
            ),
            wm_calls,
        )
        self.assertGreaterEqual(
            wm_calls.count(
                ("/usr/bin/xdotool", "windowmap", "--sync", str(0x1234))
            ),
            3,
        )

    def test_leaving_target_source_disarms_before_source_event(self):
        status = MODULE.view_state_from_status(
            {
                "ok": True,
                "serial": {"ready": True},
                "video": {"ready": True},
                "local_input": {
                    "enabled": True,
                    "armed": False,
                    "exclusive_grab": False,
                    "keyboard_ready": True,
                    "pointer_ready": True,
                },
                "control": {"active": False, "release_required": False},
                "environment_camera": environment_payload(),
            }
        )

        class Variable:
            def __init__(self):
                self.value = "target"

            def set(self, value):
                self.value = value

        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        class Client:
            def __init__(self):
                self.calls = []

            def disarm(self):
                self.calls.append("disarm")
                return status

        console = MODULE.NoobLocalConsole.__new__(MODULE.NoobLocalConsole)
        console.source = "target"
        console.source_var = Variable()
        console.current_state = status
        console.action_inflight = False
        console.closing = False
        console.client = Client()
        console.action_gate = MODULE.ActionGate()
        console._set_buttons = lambda: None
        offered = []
        console._offer = offered.append
        with mock.patch.object(MODULE.threading, "Thread", ImmediateThread):
            console._choose_source("environment")
        self.assertEqual(console.client.calls, ["disarm"])
        self.assertEqual(offered[0][0], "source_action")
        self.assertEqual(offered[0][1][1], "environment")

    def test_partial_arm_success_is_reconciled_with_disarm(self):
        class PartialArmClient(MODULE.GatewayClient):
            def __init__(self):
                self.calls = []

            def _post_empty(self, path):
                self.calls.append(path)

            def status(self):
                raise MODULE.LocalConsoleError("gateway_unavailable")

        client = PartialArmClient()
        with self.assertRaisesRegex(MODULE.LocalConsoleError, "arm_unconfirmed"):
            client.arm()
        self.assertEqual(
            client.calls,
            [
                "/api/v1/local-input/arm",
                "/api/v1/local-input/disarm",
            ],
        )


if __name__ == "__main__":
    unittest.main()
