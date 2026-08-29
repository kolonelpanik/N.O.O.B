import asyncio
import struct
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

from noob_gateway.config import LocalInputConfig  # noqa: E402
from noob_gateway.local_input import (  # noqa: E402
    BTN_LEFT,
    BTN_MIDDLE,
    BTN_RIGHT,
    EV_KEY,
    EV_REL,
    EV_SYN,
    REL_X,
    REL_Y,
    SYN_REPORT,
    LocalInputManager,
    unpack_input_events,
)


class FakeLocalRuntime:
    def __init__(self) -> None:
        self.commands = []
        self.releases = 0
        self.accept = True
        self.release_ok = True

    async def submit(self, command):
        self.commands.append(command)
        return self.accept

    async def release(self):
        self.releases += 1
        return self.release_ok


class LocalInputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = FakeLocalRuntime()
        self.grabs = []
        self.manager = LocalInputManager(
            LocalInputConfig(enabled=True, long_press_ms=250),
            self.runtime.submit,
            self.runtime.release,
            grab_device=lambda fd, enabled: self.grabs.append((fd, enabled)),
            drain_device=lambda _fd: None,
        )
        # Deterministic unit tests inject already-open device descriptors. The
        # real supervisor obtains these from the configured stable evdev links.
        self.manager._fds = {"keyboard": 10, "pointer": 11}
        await self.manager.arm()

    async def asyncTearDown(self):
        await self.manager.disarm(reason="test_cleanup")

    async def test_arm_is_all_or_nothing_and_disarm_releases_control(self):
        self.assertTrue(self.manager.armed)
        self.assertEqual(self.grabs, [(10, True), (11, True)])

        await self.manager.disarm(reason="operator")

        self.assertFalse(self.manager.armed)
        self.assertEqual(self.runtime.releases, 1)
        self.assertEqual(self.grabs[-2:], [(11, False), (10, False)])

    async def test_keyboard_exact_down_up_mapping_and_repeat_suppression(self):
        # A normal key produces one bounded down and up; kernel repeat value 2
        # must never add another target key-down.
        await self.manager.feed_event("keyboard", EV_KEY, 30, 1)  # A down
        await self.manager.feed_event("keyboard", EV_KEY, 30, 2)  # repeat
        await self.manager.feed_event("keyboard", EV_KEY, 30, 0)  # A up
        await self.manager.feed_event("keyboard", EV_KEY, 116, 1)  # power ignored

        self.assertEqual(
            self.runtime.commands,
            [
                {"op": "key", "event": "down", "key": "A"},
                {"op": "key", "event": "up", "key": "A"},
            ],
        )

    async def test_modifier_is_buffered_then_preserves_ctrl_c_order(self):
        await self.manager.feed_event("keyboard", EV_KEY, 29, 1)  # Ctrl
        self.assertEqual(self.runtime.commands, [])
        await self.manager.feed_event("keyboard", EV_KEY, 46, 1)  # C
        await self.manager.feed_event("keyboard", EV_KEY, 46, 0)
        await self.manager.feed_event("keyboard", EV_KEY, 29, 0)

        self.assertEqual(
            self.runtime.commands,
            [
                {"op": "key", "event": "down", "key": "LEFT_CONTROL"},
                {"op": "key", "event": "down", "key": "C"},
                {"op": "key", "event": "up", "key": "C"},
                {"op": "key", "event": "up", "key": "LEFT_CONTROL"},
            ],
        )

    async def test_emergency_chord_is_never_forwarded_and_disarms(self):
        await self.manager.feed_event("keyboard", EV_KEY, 29, 1)  # Ctrl
        await self.manager.feed_event("keyboard", EV_KEY, 56, 1)  # Alt
        await self.manager.feed_event("keyboard", EV_KEY, 1, 1)  # Escape

        self.assertEqual(self.runtime.commands, [])
        self.assertFalse(self.manager.armed)
        self.assertEqual(self.manager.status["disarm_reason"], "emergency_chord")
        self.assertEqual(self.runtime.releases, 1)

    async def test_motion_is_syn_batched_and_protocol_chunked(self):
        await self.manager.feed_event("pointer", EV_REL, REL_X, 300)
        await self.manager.feed_event("pointer", EV_REL, REL_Y, -129)
        self.assertEqual(self.runtime.commands, [])
        await self.manager.feed_event("pointer", EV_SYN, SYN_REPORT, 0)

        self.assertEqual(
            self.runtime.commands,
            [
                {"op": "mouse_move", "dx": 300, "dy": -129, "wheel": 0},
            ],
        )

    async def test_all_physical_mouse_buttons_preserve_native_down_up(self):
        for code in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE):
            await self.manager.feed_event("pointer", EV_KEY, code, 1)
            await self.manager.feed_event("pointer", EV_KEY, code, 0)

        self.assertEqual(
            self.runtime.commands,
            [
                {"op": "mouse_button", "button": "left", "event": "down"},
                {"op": "mouse_button", "button": "left", "event": "up"},
                {"op": "mouse_button", "button": "right", "event": "down"},
                {"op": "mouse_button", "button": "right", "event": "up"},
                {"op": "mouse_button", "button": "middle", "event": "down"},
                {"op": "mouse_button", "button": "middle", "event": "up"},
            ],
        )

    async def test_mouse_button_duplicate_and_repeat_edges_are_suppressed(self):
        await self.manager.feed_event("pointer", EV_KEY, BTN_LEFT, 1)
        await self.manager.feed_event("pointer", EV_KEY, BTN_LEFT, 1)
        await self.manager.feed_event("pointer", EV_KEY, BTN_LEFT, 2)
        await self.manager.feed_event("pointer", EV_KEY, BTN_LEFT, 0)
        await self.manager.feed_event("pointer", EV_KEY, BTN_LEFT, 0)

        self.assertEqual(
            self.runtime.commands,
            [
                {"op": "mouse_button", "button": "left", "event": "down"},
                {"op": "mouse_button", "button": "left", "event": "up"},
            ],
        )

    async def test_native_left_drag_keeps_button_down_across_motion(self):
        await self.manager.feed_event("pointer", EV_KEY, BTN_LEFT, 1)
        await self.manager.feed_event("pointer", EV_REL, REL_X, 8)
        await self.manager.feed_event("pointer", EV_REL, REL_Y, -3)
        await self.manager.feed_event("pointer", EV_SYN, SYN_REPORT, 0)
        await self.manager.feed_event("pointer", EV_KEY, BTN_LEFT, 0)

        self.assertEqual(
            self.runtime.commands,
            [
                {"op": "mouse_button", "button": "left", "event": "down"},
                {"op": "mouse_move", "dx": 8, "dy": -3, "wheel": 0},
                {"op": "mouse_button", "button": "left", "event": "up"},
            ],
        )

    async def test_disarm_releases_held_button_and_stale_up_is_ignored(self):
        await self.manager.feed_event("pointer", EV_KEY, BTN_RIGHT, 1)
        self.assertEqual(self.runtime.commands[-1]["event"], "down")

        confirmed = await self.manager.disarm(reason="operator")
        self.assertTrue(confirmed)
        self.assertEqual(self.runtime.releases, 1)

        await self.manager.arm()
        await self.manager.feed_event("pointer", EV_KEY, BTN_RIGHT, 0)
        self.assertEqual(
            self.runtime.commands,
            [{"op": "mouse_button", "button": "right", "event": "down"}],
        )

    async def test_dispatch_failure_and_device_loss_both_fail_closed(self):
        self.runtime.accept = False
        await self.manager.feed_event("pointer", EV_REL, REL_X, 1)
        await self.manager.feed_event("pointer", EV_SYN, SYN_REPORT, 0)
        self.assertFalse(self.manager.armed)
        self.assertEqual(self.runtime.releases, 1)

        # Re-arm, forward a held key, then simulate a cable/device loss. The
        # runtime release callback is the only path used to clear Pico HID.
        self.runtime.accept = True
        await self.manager.arm()
        await self.manager.feed_event("keyboard", EV_KEY, 30, 1)
        await self.manager.device_failed("keyboard", "OSError")
        self.assertFalse(self.manager.armed)
        self.assertEqual(self.runtime.releases, 2)
        self.assertEqual(self.manager.status["disarm_reason"], "device_lost")

    async def test_unconfirmed_release_still_ungrabs_and_reports_failure(self):
        self.runtime.release_ok = False
        confirmed = await self.manager.disarm(reason="operator")

        self.assertFalse(confirmed)
        self.assertFalse(self.manager.armed)
        self.assertFalse(self.manager.status["exclusive_grab"])
        self.assertEqual(self.manager.status["last_error"], "release_unconfirmed")


class InputEventDecodeTests(unittest.TestCase):
    def test_partial_native_records_are_retained(self):
        record = struct.pack("@llHHi", 123, 456, EV_REL, REL_X, -9)
        events, remainder = unpack_input_events(record[:7])
        self.assertEqual(events, [])
        events, remainder = unpack_input_events(record[7:], remainder)
        self.assertEqual(events[0].event_type, EV_REL)
        self.assertEqual(events[0].code, REL_X)
        self.assertEqual(events[0].value, -9)
        self.assertEqual(remainder, b"")


if __name__ == "__main__":
    unittest.main()
