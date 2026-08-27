"""Single-owner, runtime-selectable V4L2 MJPEG capture."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import re
import time
from typing import Any

from .config import VideoConfig, VideoProfile


class VideoUnavailable(RuntimeError):
    pass


class TooManyViewers(RuntimeError):
    pass


class VideoModeInvalid(RuntimeError):
    pass


class VideoModeStale(RuntimeError):
    pass


class VideoSwitchInProgress(RuntimeError):
    pass


class VideoNegotiationError(RuntimeError):
    def __init__(self, code: str = "video_mode_mismatch") -> None:
        super().__init__(code)
        self.code = code


class VideoSwitchFailed(RuntimeError):
    def __init__(self, code: str, *, rolled_back: bool) -> None:
        super().__init__(code)
        self.code = code
        self.rolled_back = rolled_back


class _VideoStopping(RuntimeError):
    pass


class JPEGStreamParser:
    """Extract concatenated JPEG images while bounding retained input."""

    SOI = b"\xff\xd8"
    EOI = b"\xff\xd9"

    def __init__(self, max_frame_bytes: int) -> None:
        self.max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()
        self.dropped = 0

    def feed(self, data: bytes) -> list[bytes]:
        if not data:
            return []
        self._buffer.extend(data)
        frames: list[bytes] = []
        while True:
            start = self._buffer.find(self.SOI)
            if start < 0:
                if len(self._buffer) > 1:
                    keep = self._buffer[-1:] if self._buffer[-1] == 0xFF else b""
                    self._buffer.clear()
                    self._buffer.extend(keep)
                return frames
            if start:
                del self._buffer[:start]
            end = self._buffer.find(self.EOI, 2)
            if end < 0:
                if len(self._buffer) > self.max_frame_bytes:
                    self.dropped += 1
                    next_start = self._buffer.find(self.SOI, 2)
                    if next_start >= 0:
                        del self._buffer[:next_start]
                    else:
                        keep = self._buffer[-1:] if self._buffer[-1] == 0xFF else b""
                        self._buffer.clear()
                        self._buffer.extend(keep)
                return frames
            frame_end = end + 2
            if frame_end <= self.max_frame_bytes:
                frames.append(bytes(self._buffer[:frame_end]))
            else:
                self.dropped += 1
            del self._buffer[:frame_end]


_SOF_MARKERS = frozenset(
    (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF)
)


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Return JPEG SOF dimensions without decoding or accepting unbounded data."""

    if not data.startswith(JPEGStreamParser.SOI):
        return None
    offset = 2
    size = len(data)
    while offset + 4 <= size:
        marker_start = data.find(b"\xff", offset)
        if marker_start < 0:
            return None
        marker_at = marker_start + 1
        while marker_at < size and data[marker_at] == 0xFF:
            marker_at += 1
        if marker_at >= size:
            return None
        marker = data[marker_at]
        offset = marker_at + 1
        if marker == 0x00:
            continue
        if marker in (0xD9, 0xDA):
            return None
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > size:
            return None
        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > size:
            return None
        if marker in _SOF_MARKERS:
            if segment_length < 7:
                return None
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            if width <= 0 or height <= 0:
                return None
            return width, height
        offset += segment_length
    return None


@dataclass(frozen=True, slots=True)
class NegotiatedVideoMode:
    width: int
    height: int
    fps: float
    pixel_format: str

    def public_view(self) -> dict[str, Any]:
        fps: int | float = int(self.fps) if self.fps.is_integer() else self.fps
        return {
            "width": self.width,
            "height": self.height,
            "fps": fps,
            "pixel_format": self.pixel_format,
        }


@dataclass(frozen=True, slots=True)
class FrameSnapshot:
    data: bytes
    sequence: int
    captured_at: float
    generation: int = 0
    width: int | None = None
    height: int | None = None


_FORMAT_RE = re.compile(r"Width/Height\s*:\s*(\d+)\s*/\s*(\d+)")
_PIXEL_RE = re.compile(r"Pixel Format\s*:\s*'([A-Z0-9]{4})'")
_FPS_RATIO_RE = re.compile(
    r"Frames per second\s*:\s*[0-9.]+\s*\((\d+)\s*/\s*(\d+)\)"
)
_FPS_DECIMAL_RE = re.compile(r"Frames per second\s*:\s*([0-9]+(?:\.[0-9]+)?)")


def parse_v4l2_mode(output: str) -> NegotiatedVideoMode:
    dimensions = _FORMAT_RE.search(output)
    pixel_format = _PIXEL_RE.search(output)
    fps_ratio = _FPS_RATIO_RE.search(output)
    fps_decimal = _FPS_DECIMAL_RE.search(output)
    if dimensions is None or pixel_format is None or (
        fps_ratio is None and fps_decimal is None
    ):
        raise VideoNegotiationError("video_mode_probe_failed")
    if fps_ratio is not None:
        numerator = int(fps_ratio.group(1))
        denominator = int(fps_ratio.group(2))
        if denominator == 0:
            raise VideoNegotiationError("video_mode_probe_failed")
        fps = numerator / denominator
    else:
        assert fps_decimal is not None
        fps = float(fps_decimal.group(1))
    return NegotiatedVideoMode(
        width=int(dimensions.group(1)),
        height=int(dimensions.group(2)),
        fps=fps,
        pixel_format=pixel_format.group(1),
    )


ModeProbe = Callable[[VideoProfile], Awaitable[NegotiatedVideoMode]]


class V4L2Capture:
    """Own exactly one streaming V4L2 process and fan its frames out in memory."""

    def __init__(
        self,
        config: VideoConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        mode_probe: ModeProbe | None = None,
    ) -> None:
        self.config = config
        self._clock = clock
        self._mode_probe = mode_probe
        self._profiles = {profile.mode_id: profile for profile in config.profiles}
        self._desired_profile = config.default_profile
        self._requested_profile = config.default_profile
        self._active_profile: VideoProfile | None = None
        self._negotiated: NegotiatedVideoMode | None = None
        self._latest: FrameSnapshot | None = None
        self._condition = asyncio.Condition()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._sequence = 0
        self._generation = 0
        self._restarts = 0
        self._last_error: str | None = None
        self._state = "stopped"
        self._viewer_count = 0
        self._viewer_lock = asyncio.Lock()
        self._switch_lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return bool(
            self._state in {"ready", "rolled_back"}
            and self._latest is not None
            and self._latest.generation == self._generation
            and self._clock() - self._latest.captured_at <= self.config.stale_seconds
        )

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def modes(self) -> list[dict[str, Any]]:
        return [
            profile.public_view()
            for profile in self.config.profiles
            if profile.validated
        ]

    @property
    def status(self) -> dict[str, Any]:
        age_ms = None
        if self._latest is not None:
            age_ms = max(0, int((self._clock() - self._latest.captured_at) * 1000))
        negotiated = (
            self._negotiated.public_view() if self._negotiated is not None else None
        )
        requested = self._requested_profile.requested_view()
        return {
            "ready": self.ready,
            "state": self._state,
            "device": self.config.device,
            "generation": self._generation,
            "active_mode_id": (
                self._active_profile.mode_id if self._active_profile is not None else None
            ),
            "requested": requested,
            "negotiated": negotiated,
            # Compatibility fields now report negotiated truth, never the request.
            "width": negotiated["width"] if negotiated is not None else None,
            "height": negotiated["height"] if negotiated is not None else None,
            "fps": negotiated["fps"] if negotiated is not None else None,
            "source_timing_detectable": False,
            "last_frame_age_ms": age_ms,
            "sequence": self._sequence,
            "restarts": self._restarts,
            "viewers": self._viewer_count,
            "last_error": self._last_error,
        }

    def mode_catalog(self) -> dict[str, Any]:
        status = self.status
        return {
            "ok": True,
            "generation": status["generation"],
            "active_mode_id": status["active_mode_id"],
            "requested": status["requested"],
            "negotiated": status["negotiated"],
            "state": status["state"],
            "modes": self.modes,
        }

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._state = "starting"
        self._desired_profile = self.config.default_profile
        self._requested_profile = self.config.default_profile
        self._start_supervisor(self._desired_profile, success_state="ready")

    async def stop(self) -> None:
        self._stop.set()
        await self._cancel_capture_task()
        await self._invalidate_frames(state="stopped")

    def latest(self) -> FrameSnapshot | None:
        return self._latest

    async def wait_for_frame(
        self, after_sequence: int, timeout: float = 5.0
    ) -> FrameSnapshot | None:
        async with self._condition:
            if (
                self._latest is not None
                and self._latest.sequence > after_sequence
                and self.ready
            ):
                return self._latest
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(
                        lambda: self._latest is not None
                        and self._latest.sequence > after_sequence
                        and self.ready
                    ),
                    timeout,
                )
            except asyncio.TimeoutError:
                return None
            return self._latest

    async def acquire_viewer(self) -> None:
        async with self._viewer_lock:
            if self._viewer_count >= self.config.max_clients:
                raise TooManyViewers("MJPEG viewer limit reached")
            self._viewer_count += 1

    async def release_viewer(self) -> None:
        async with self._viewer_lock:
            self._viewer_count = max(0, self._viewer_count - 1)

    async def select_mode(self, mode_id: str, expected_generation: int) -> dict[str, Any]:
        profile = self._profiles.get(mode_id)
        if profile is None or not profile.validated:
            raise VideoModeInvalid("video mode is not validated")
        if self._switch_lock.locked():
            raise VideoSwitchInProgress("video mode switch already in progress")
        async with self._switch_lock:
            if expected_generation != self._generation:
                raise VideoModeStale("video generation changed")
            if self.ready and self._active_profile is not None:
                if self._active_profile.mode_id == profile.mode_id:
                    self._requested_profile = profile
                    self._desired_profile = profile
                    self._state = "ready"
                    self._last_error = None
                    return self.status

            # A degraded capture is deliberately recoverable through this API.
            # The desired profile is the rollback target when no frame is active.
            previous_profile = self._active_profile or self._desired_profile
            self._requested_profile = profile
            self._desired_profile = profile
            self._state = "switching"
            self._last_error = None
            await self._cancel_capture_task()
            await self._invalidate_frames(state="switching")

            ready = asyncio.get_running_loop().create_future()
            self._start_supervisor(profile, success_state="ready", ready=ready)
            try:
                await self._wait_for_transition(ready)
                return self.status
            except _VideoStopping:
                await self._cancel_capture_task()
                await self._invalidate_frames(state="stopped")
                raise VideoUnavailable("video capture is stopping") from None
            except asyncio.CancelledError:
                await self._cancel_capture_task()
                await self._invalidate_frames(
                    state="stopped" if self._stop.is_set() else "degraded"
                )
                raise
            except Exception as exc:
                failure_code = self._failure_code(exc)

            await self._cancel_capture_task()
            self._desired_profile = previous_profile
            # Keep the public state "switching" until rollback is settled so
            # every client continues to disable competing mode actions.
            await self._invalidate_frames(state="switching")
            rollback_ready = asyncio.get_running_loop().create_future()
            self._start_supervisor(
                previous_profile,
                success_state="rolled_back",
                ready=rollback_ready,
            )
            try:
                await self._wait_for_transition(rollback_ready)
            except _VideoStopping:
                await self._cancel_capture_task()
                await self._invalidate_frames(state="stopped")
                raise VideoUnavailable("video capture is stopping") from None
            except asyncio.CancelledError:
                await self._cancel_capture_task()
                await self._invalidate_frames(
                    state="stopped" if self._stop.is_set() else "degraded"
                )
                raise
            except Exception:
                await self._cancel_capture_task()
                self._last_error = "video_mode_rollback_failed"
                await self._invalidate_frames(state="degraded")
                raise VideoSwitchFailed(
                    "video_mode_rollback_failed", rolled_back=False
                ) from None

            # Keep the failed operator request visible alongside the negotiated
            # rollback mode; this is more truthful than pretending it succeeded.
            self._requested_profile = profile
            self._last_error = failure_code
            raise VideoSwitchFailed(failure_code, rolled_back=True)

    async def _wait_for_transition(self, ready: asyncio.Future[None]) -> None:
        stop_waiter = asyncio.create_task(self._stop.wait())
        try:
            done, _pending = await asyncio.wait(
                {ready, stop_waiter},
                timeout=self.config.switch_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                ready.cancel()
                raise asyncio.TimeoutError
            if stop_waiter in done and self._stop.is_set():
                if not ready.done():
                    ready.cancel()
                raise _VideoStopping
            await ready
        finally:
            if not stop_waiter.done():
                stop_waiter.cancel()
            await asyncio.gather(stop_waiter, return_exceptions=True)

    def _start_supervisor(
        self,
        profile: VideoProfile,
        *,
        success_state: str,
        ready: asyncio.Future[None] | None = None,
    ) -> None:
        if self._task is not None:
            raise RuntimeError("capture supervisor overlap")
        self._task = asyncio.create_task(
            self._supervise(profile, success_state=success_state, ready=ready),
            name="noob-video",
        )

    async def _cancel_capture_task(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._terminate_process()

    async def _invalidate_frames(self, *, state: str) -> None:
        async with self._condition:
            self._latest = None
            self._active_profile = None
            self._negotiated = None
            self._state = state
            self._condition.notify_all()

    async def _supervise(
        self,
        profile: VideoProfile,
        *,
        success_state: str,
        ready: asyncio.Future[None] | None,
    ) -> None:
        while not self._stop.is_set():
            try:
                await self._capture_once(
                    profile,
                    success_state=success_state,
                    ready=ready,
                )
            except asyncio.CancelledError:
                raise
            except VideoNegotiationError as exc:
                self._last_error = exc.code
                await self._invalidate_frames(state="degraded")
                if ready is not None and not ready.done():
                    ready.set_exception(exc)
                return
            except Exception as exc:
                self._last_error = self._failure_code(exc)
                await self._invalidate_frames(state="reconnecting")
            finally:
                await self._terminate_process()
            if not self._stop.is_set():
                self._restarts += 1
                await asyncio.sleep(self.config.reconnect_ms / 1000.0)

    async def _capture_once(
        self,
        profile: VideoProfile,
        *,
        success_state: str,
        ready: asyncio.Future[None] | None,
    ) -> None:
        args = (
            self.config.v4l2_ctl,
            f"--device={self.config.device}",
            (
                "--set-fmt-video="
                f"width={profile.width},height={profile.height},"
                f"pixelformat={profile.pixel_format}"
            ),
            f"--set-parm={profile.fps}",
            "--stream-mmap=4",
            "--stream-to=-",
            "--stream-count=0",
        )
        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if self._process.stdout is None or self._process.stderr is None:
            raise VideoUnavailable("capture subprocess pipes unavailable")
        stderr_task = asyncio.create_task(self._drain_stderr(self._process.stderr))
        parser = JPEGStreamParser(profile.max_frame_bytes)
        last_frame_at = self._clock()
        committed = False
        try:
            while not self._stop.is_set():
                chunk = await asyncio.wait_for(
                    self._process.stdout.read(64 * 1024),
                    timeout=self.config.stale_seconds,
                )
                if not chunk:
                    return_code = await self._process.wait()
                    raise VideoUnavailable(
                        f"v4l2-ctl exited with status {return_code}"
                    )
                frames = parser.feed(chunk)
                for frame in frames:
                    dimensions = jpeg_dimensions(frame)
                    if dimensions != (profile.width, profile.height):
                        raise VideoNegotiationError("video_frame_dimensions_mismatch")
                    if not committed:
                        negotiated = await self._probe(profile)
                        self._verify_negotiated(profile, negotiated)
                        await self._commit_session(
                            profile,
                            negotiated,
                            frame,
                            success_state=success_state,
                        )
                        committed = True
                        if ready is not None and not ready.done():
                            ready.set_result(None)
                    else:
                        await self._publish(frame, profile)
                    last_frame_at = self._clock()
                if self._clock() - last_frame_at > self.config.stale_seconds:
                    raise VideoUnavailable(
                        "capture output contained no complete JPEG frames"
                    )
        finally:
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)

    async def _commit_session(
        self,
        profile: VideoProfile,
        negotiated: NegotiatedVideoMode,
        frame: bytes,
        *,
        success_state: str,
    ) -> None:
        async with self._condition:
            self._generation += 1
            self._sequence += 1
            self._active_profile = profile
            self._negotiated = negotiated
            self._latest = FrameSnapshot(
                frame,
                self._sequence,
                self._clock(),
                generation=self._generation,
                width=negotiated.width,
                height=negotiated.height,
            )
            self._state = success_state
            self._last_error = None
            self._condition.notify_all()

    async def _publish(self, frame: bytes, profile: VideoProfile) -> None:
        if len(frame) > profile.max_frame_bytes:
            return
        async with self._condition:
            if self._active_profile != profile or self._negotiated is None:
                return
            self._sequence += 1
            self._latest = FrameSnapshot(
                frame,
                self._sequence,
                self._clock(),
                generation=self._generation,
                width=self._negotiated.width,
                height=self._negotiated.height,
            )
            self._condition.notify_all()

    async def _probe(self, profile: VideoProfile) -> NegotiatedVideoMode:
        if self._mode_probe is not None:
            return await self._mode_probe(profile)
        return await self._probe_current_mode()

    async def _probe_current_mode(self) -> NegotiatedVideoMode:
        process = await asyncio.create_subprocess_exec(
            self.config.v4l2_ctl,
            f"--device={self.config.device}",
            "--get-fmt-video",
            "--get-parm",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=2.0)
        except asyncio.TimeoutError:
            raise VideoNegotiationError("video_mode_probe_failed") from None
        finally:
            # A switch cancellation or gateway shutdown must not orphan this
            # transient read-only query process.
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        if process.returncode != 0 or len(stdout) > 64 * 1024:
            raise VideoNegotiationError("video_mode_probe_failed")
        try:
            output = stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise VideoNegotiationError("video_mode_probe_failed") from None
        return parse_v4l2_mode(output)

    @staticmethod
    def _verify_negotiated(
        requested: VideoProfile, negotiated: NegotiatedVideoMode
    ) -> None:
        if (
            negotiated.width != requested.width
            or negotiated.height != requested.height
            or negotiated.pixel_format != requested.pixel_format
            or abs(negotiated.fps - requested.fps) > 0.01
        ):
            raise VideoNegotiationError("video_mode_mismatch")

    @staticmethod
    def _failure_code(exc: BaseException) -> str:
        if isinstance(exc, asyncio.TimeoutError):
            return "video_mode_timeout"
        if isinstance(exc, VideoNegotiationError):
            return exc.code
        if isinstance(exc, VideoUnavailable):
            return "video_unavailable"
        return "video_capture_failed"

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        while not self._stop.is_set():
            chunk = await stream.read(4096)
            if not chunk:
                return

    async def _terminate_process(self) -> None:
        process, self._process = self._process, None
        if process is None or process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
