from __future__ import annotations

import importlib.util
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import unittest


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "appliance" / "noob_local_console.py"
)
SPEC = importlib.util.spec_from_file_location("noob_local_console", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class LocalConsoleContractTests(unittest.TestCase):
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
            __import__("json").loads(kwargs["body"]),
            {"mode_id": "1080p30", "expected_generation": 7},
        )

        with self.assertRaises(MODULE.LocalConsoleError):
            client.set_video_mode("../unsafe", 8)
        self.assertEqual(len(client.calls), 1)

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
