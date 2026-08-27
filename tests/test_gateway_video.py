import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

from noob_gateway.config import VideoConfig, VideoProfile  # noqa: E402
from noob_gateway.video import (  # noqa: E402
    JPEGStreamParser,
    NegotiatedVideoMode,
    V4L2Capture,
    VideoModeInvalid,
    VideoModeStale,
    VideoNegotiationError,
    VideoSwitchFailed,
    VideoSwitchInProgress,
    VideoUnavailable,
    jpeg_dimensions,
    parse_v4l2_mode,
)


def jpeg(width: int, height: int) -> bytes:
    sof = (
        b"\xff\xc0\x00\x11\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    )
    return b"\xff\xd8" + sof + b"\xff\xd9"


def capture_config() -> VideoConfig:
    return VideoConfig(
        default_mode="720p20",
        profiles=(
            VideoProfile(
                "720p20", "720p20", 1280, 720, 20, 1_843_200, True
            ),
            VideoProfile(
                "1080p30", "1080p30", 1920, 1080, 30, 4_147_200, True
            ),
            VideoProfile(
                "1440p30", "1440p30", 2560, 1440, 30, 7_372_800, False
            ),
        ),
        switch_timeout_seconds=0.25,
        reconnect_ms=10,
    )


class ScriptedCapture(V4L2Capture):
    def __init__(self, scripts=None):
        super().__init__(capture_config())
        self.scripts = {key: list(value) for key, value in (scripts or {}).items()}
        self.gates = {}
        self.started = {}
        self.events = []
        self.active_sessions = 0
        self.max_active_sessions = 0

    async def _capture_once(self, profile, *, success_state, ready):
        self.active_sessions += 1
        self.max_active_sessions = max(self.max_active_sessions, self.active_sessions)
        self.events.append(("start", profile.mode_id))
        self.started.setdefault(profile.mode_id, asyncio.Event()).set()
        outcome = (
            self.scripts.get(profile.mode_id, []).pop(0)
            if self.scripts.get(profile.mode_id)
            else "success"
        )
        try:
            if outcome == "wait":
                await self.gates.setdefault(profile.mode_id, asyncio.Event()).wait()
                outcome = "success"
            if outcome == "mismatch":
                raise VideoNegotiationError("video_mode_mismatch")
            negotiated = NegotiatedVideoMode(
                profile.width, profile.height, float(profile.fps), "MJPG"
            )
            await self._commit_session(
                profile,
                negotiated,
                jpeg(profile.width, profile.height),
                success_state=success_state,
            )
            if ready is not None and not ready.done():
                ready.set_result(None)
            await asyncio.Future()
        finally:
            self.active_sessions -= 1
            self.events.append(("stop", profile.mode_id))

    async def _terminate_process(self):
        return None


async def wait_ready(capture: V4L2Capture) -> None:
    for _ in range(50):
        if capture.ready:
            return
        await asyncio.sleep(0)
    raise AssertionError("capture did not become ready")


class JPEGStreamParserTests(unittest.TestCase):
    def test_extracts_split_and_concatenated_frames(self):
        parser = JPEGStreamParser(64)
        first = b"\xff\xd8abc\xff\xd9"
        second = b"\xff\xd8def\xff\xd9"
        self.assertEqual(parser.feed(b"noise" + first[:3]), [])
        self.assertEqual(parser.feed(first[3:] + second), [first, second])

    def test_drops_oversized_incomplete_frame_and_recovers(self):
        parser = JPEGStreamParser(8)
        parser.feed(b"\xff\xd8" + b"x" * 20)
        frame = b"\xff\xd8ok\xff\xd9"
        self.assertEqual(parser.feed(frame), [frame])
        self.assertGreaterEqual(parser.dropped, 1)

    def test_retains_split_start_marker(self):
        parser = JPEGStreamParser(64)
        self.assertEqual(parser.feed(b"junk\xff"), [])
        self.assertEqual(parser.feed(b"\xd8a\xff\xd9"), [b"\xff\xd8a\xff\xd9"])

    def test_reads_dimensions_from_bounded_sof_header(self):
        self.assertEqual(jpeg_dimensions(jpeg(2560, 1440)), (2560, 1440))
        self.assertIsNone(jpeg_dimensions(b"\xff\xd8bad\xff\xd9"))

    def test_parses_negotiated_v4l2_format_and_actual_fps(self):
        mode = parse_v4l2_mode(
            """
Format Video Capture:
    Width/Height      : 1280/720
    Pixel Format      : 'MJPG' (Motion-JPEG)
Streaming Parameters Video Capture:
    Frames per second: 20.000 (20/1)
"""
        )
        self.assertEqual(mode, NegotiatedVideoMode(1280, 720, 20.0, "MJPG"))


class VideoModeStateMachineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.capture = ScriptedCapture()
        await self.capture.start()
        await wait_ready(self.capture)

    async def asyncTearDown(self):
        await self.capture.stop()

    async def test_switch_is_stop_before_start_and_clears_stale_frame(self):
        self.capture.scripts["1080p30"] = ["wait"]
        switching = asyncio.create_task(self.capture.select_mode("1080p30", 1))
        while "1080p30" not in self.capture.started:
            await asyncio.sleep(0)
        self.assertFalse(self.capture.ready)
        self.assertIsNone(self.capture.latest())
        self.capture.gates["1080p30"].set()
        status = await switching
        self.assertEqual(status["active_mode_id"], "1080p30")
        self.assertEqual(status["generation"], 2)
        self.assertEqual(self.capture.max_active_sessions, 1)
        self.assertEqual(
            self.capture.events[:3],
            [("start", "720p20"), ("stop", "720p20"), ("start", "1080p30")],
        )

    async def test_mismatch_rolls_back_and_active_selection_clears_error(self):
        self.capture.scripts["1080p30"] = ["mismatch"]
        with self.assertRaises(VideoSwitchFailed) as failed:
            await self.capture.select_mode("1080p30", 1)
        self.assertTrue(failed.exception.rolled_back)
        status = self.capture.status
        self.assertEqual(status["state"], "rolled_back")
        self.assertEqual(status["active_mode_id"], "720p20")
        self.assertEqual(status["requested"]["id"], "1080p30")
        self.assertEqual(status["last_error"], "video_mode_mismatch")
        generation = status["generation"]

        recovered = await self.capture.select_mode("720p20", generation)
        self.assertEqual(recovered["state"], "ready")
        self.assertEqual(recovered["requested"]["id"], "720p20")
        self.assertIsNone(recovered["last_error"])
        self.assertEqual(recovered["generation"], generation)

    async def test_degraded_capture_can_be_recovered_by_mode_request(self):
        await self.capture._cancel_capture_task()
        await self.capture._invalidate_frames(state="degraded")
        recovered = await self.capture.select_mode("1080p30", 1)
        self.assertTrue(recovered["ready"])
        self.assertEqual(recovered["active_mode_id"], "1080p30")

    async def test_unvalidated_mode_is_not_selectable(self):
        with self.assertRaises(VideoModeInvalid):
            await self.capture.select_mode("1440p30", 1)

    async def test_stale_generation_and_overlapping_switch_are_rejected(self):
        with self.assertRaises(VideoModeStale):
            await self.capture.select_mode("1080p30", 0)

        self.capture.scripts["1080p30"] = ["wait"]
        switching = asyncio.create_task(self.capture.select_mode("1080p30", 1))
        while "1080p30" not in self.capture.started:
            await asyncio.sleep(0)
        with self.assertRaises(VideoSwitchInProgress):
            await self.capture.select_mode("720p20", 1)
        self.capture.gates["1080p30"].set()
        await switching

    async def test_failed_switch_and_failed_rollback_leave_degraded(self):
        self.capture.scripts["1080p30"] = ["mismatch"]
        self.capture.scripts["720p20"] = ["mismatch"]
        with self.assertRaises(VideoSwitchFailed) as failed:
            await self.capture.select_mode("1080p30", 1)
        self.assertFalse(failed.exception.rolled_back)
        self.assertEqual(failed.exception.code, "video_mode_rollback_failed")
        self.assertFalse(self.capture.ready)
        self.assertEqual(self.capture.status["state"], "degraded")
        self.assertIsNone(self.capture.latest())

    async def test_requested_and_negotiated_mismatch_is_fail_closed(self):
        requested = capture_config().profiles[0]
        with self.assertRaisesRegex(VideoNegotiationError, "video_mode_mismatch"):
            self.capture._verify_negotiated(
                requested,
                NegotiatedVideoMode(1280, 720, 30.0, "MJPG"),
            )

    async def test_shutdown_interrupts_switch_without_rollback(self):
        self.capture.scripts["1080p30"] = ["wait"]
        switching = asyncio.create_task(self.capture.select_mode("1080p30", 1))
        while "1080p30" not in self.capture.started:
            await asyncio.sleep(0)
        await asyncio.wait_for(self.capture.stop(), timeout=0.1)
        with self.assertRaises(VideoUnavailable):
            await asyncio.wait_for(switching, timeout=0.1)
        self.assertEqual(self.capture.status["state"], "stopped")
        self.assertEqual(self.capture.active_sessions, 0)
        self.assertEqual(
            [event for event in self.capture.events if event[0] == "start"],
            [("start", "720p20"), ("start", "1080p30")],
        )

    async def test_cancelled_negotiation_probe_reaps_query_process(self):
        class ProbeProcess:
            def __init__(self):
                self.returncode = None
                self.killed = False
                self.reaped = False
                self.release = asyncio.Event()

            async def communicate(self):
                await self.release.wait()
                return b"", b""

            def kill(self):
                self.killed = True
                self.returncode = -9
                self.release.set()

            async def wait(self):
                self.reaped = True
                return self.returncode

        process = ProbeProcess()
        with mock.patch(
            "noob_gateway.video.asyncio.create_subprocess_exec",
            return_value=process,
        ):
            probe = asyncio.create_task(self.capture._probe_current_mode())
            await asyncio.sleep(0)
            probe.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await probe
        self.assertTrue(process.killed)
        self.assertTrue(process.reaped)


if __name__ == "__main__":
    unittest.main()
