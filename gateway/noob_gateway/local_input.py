"""Fail-closed uConsole keyboard and trackball adapter.

This module reads only two explicitly configured Linux evdev identities.  It
does not emit HID directly: every translated event is submitted to the gateway
runtime, which validates it and sends it through the same exclusive control
lease and acknowledged Pico UART path as network input.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import fcntl
import os
import struct
import time
from typing import Any, Awaitable, Callable

from .config import LocalInputConfig


# linux/input-event-codes.h (the uConsole appliance is Linux/aarch64).
EV_SYN = 0
EV_KEY = 1
EV_REL = 2
SYN_REPORT = 0
REL_X = 0
REL_Y = 1
BTN_LEFT = 272
BTN_RIGHT = 273
BTN_MIDDLE = 274

# ClockworkPi's stock firmware exposes three native mouse buttons: the dedicated
# left/right keys and the physical trackball press as middle.  Preserve those
# identities exactly so click, hold, and drag semantics remain target-native.
POINTER_BUTTONS = {
    BTN_LEFT: "left",
    BTN_RIGHT: "right",
    BTN_MIDDLE: "middle",
}

# _IOW('E', 0x90, int).  Grabbing is mandatory while armed so the same built-in
# controls cannot accidentally operate the uConsole desktop and target at once.
EVIOCGRAB = 0x40044590

_INPUT_EVENT = struct.Struct("@llHHi")
_READ_EVENTS = 64
_MAX_ACCUMULATED_MOTION = 4096


# Exact Linux keycode -> bounded N.O.O.B protocol name mapping.  Unsupported
# consumer, power, Fn, media, and keypad keys are intentionally ignored.
LINUX_KEY_TO_NOOB: dict[int, str] = {
    1: "ESCAPE",
    2: "ONE",
    3: "TWO",
    4: "THREE",
    5: "FOUR",
    6: "FIVE",
    7: "SIX",
    8: "SEVEN",
    9: "EIGHT",
    10: "NINE",
    11: "ZERO",
    12: "MINUS",
    13: "EQUALS",
    14: "BACKSPACE",
    15: "TAB",
    16: "Q",
    17: "W",
    18: "E",
    19: "R",
    20: "T",
    21: "Y",
    22: "U",
    23: "I",
    24: "O",
    25: "P",
    26: "LEFT_BRACKET",
    27: "RIGHT_BRACKET",
    28: "ENTER",
    29: "LEFT_CONTROL",
    30: "A",
    31: "S",
    32: "D",
    33: "F",
    34: "G",
    35: "H",
    36: "J",
    37: "K",
    38: "L",
    39: "SEMICOLON",
    40: "QUOTE",
    41: "GRAVE_ACCENT",
    42: "LEFT_SHIFT",
    43: "BACKSLASH",
    44: "Z",
    45: "X",
    46: "C",
    47: "V",
    48: "B",
    49: "N",
    50: "M",
    51: "COMMA",
    52: "PERIOD",
    53: "FORWARD_SLASH",
    54: "RIGHT_SHIFT",
    56: "LEFT_ALT",
    57: "SPACE",
    58: "CAPS_LOCK",
    59: "F1",
    60: "F2",
    61: "F3",
    62: "F4",
    63: "F5",
    64: "F6",
    65: "F7",
    66: "F8",
    67: "F9",
    68: "F10",
    70: "SCROLL_LOCK",
    87: "F11",
    88: "F12",
    97: "RIGHT_CONTROL",
    99: "PRINT_SCREEN",
    100: "RIGHT_ALT",
    102: "HOME",
    103: "UP_ARROW",
    104: "PAGE_UP",
    105: "LEFT_ARROW",
    106: "RIGHT_ARROW",
    107: "END",
    108: "DOWN_ARROW",
    109: "PAGE_DOWN",
    110: "INSERT",
    111: "DELETE",
    119: "PAUSE",
    125: "LEFT_GUI",
    126: "RIGHT_GUI",
    127: "APPLICATION",
}


class LocalInputError(RuntimeError):
    """Base error for bounded local input control."""


class LocalInputDisabled(LocalInputError):
    pass


class LocalInputUnavailable(LocalInputError):
    pass


@dataclass(frozen=True, slots=True)
class InputEvent:
    event_type: int
    code: int
    value: int


def unpack_input_events(
    data: bytes, remainder: bytes = b""
) -> tuple[list[InputEvent], bytes]:
    """Decode complete native ``struct input_event`` records.

    A remainder is returned because reads from an evdev character device are
    normally record-aligned but the safety boundary does not assume that.
    """

    payload = remainder + data
    complete = len(payload) - (len(payload) % _INPUT_EVENT.size)
    events = [
        InputEvent(event_type, code, value)
        for _sec, _usec, event_type, code, value in _INPUT_EVENT.iter_unpack(
            payload[:complete]
        )
    ]
    return events, payload[complete:]


SubmitCommand = Callable[[dict[str, Any]], Awaitable[bool]]
ReleaseControl = Callable[[], Awaitable[bool]]
GrabDevice = Callable[[int, bool], None]
DrainDevice = Callable[[int], None]


def _grab_device(fd: int, enabled: bool) -> None:
    fcntl.ioctl(fd, EVIOCGRAB, 1 if enabled else 0)


def _drain_device(fd: int) -> None:
    """Discard any event records queued before the explicit arm boundary."""

    while True:
        try:
            data = os.read(fd, _INPUT_EVENT.size * _READ_EVENTS)
        except BlockingIOError:
            return
        if not data:
            raise OSError("evdev device returned end of file")


class LocalInputManager:
    """Own the explicitly armed uConsole keyboard/trackball input mode."""

    def __init__(
        self,
        config: LocalInputConfig,
        submit: SubmitCommand,
        release_control: ReleaseControl,
        *,
        clock: Callable[[], float] = time.monotonic,
        grab_device: GrabDevice = _grab_device,
        drain_device: DrainDevice = _drain_device,
    ) -> None:
        self.config = config
        self._submit = submit
        self._release_control = release_control
        self._clock = clock
        self._grab_device = grab_device
        self._drain_device = drain_device
        self._closing = asyncio.Event()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._fds: dict[str, int] = {}
        self._grabbed: set[str] = set()
        self._device_errors: dict[str, str | None] = {
            "keyboard": None,
            "pointer": None,
        }
        self._armed = False
        self._input_generation = 0
        self._event_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._physical_keys: set[str] = set()
        self._forwarded_keys: set[str] = set()
        self._pending_chord: list[str] = []
        self._physical_buttons: set[str] = set()
        self._forwarded_buttons: set[str] = set()
        self._idle_task: asyncio.Task[None] | None = None
        self._motion_x = 0
        self._motion_y = 0
        self._last_event_at = 0.0
        self._last_error: str | None = None
        self._disarm_reason: str | None = None
        self._dropped_events = 0

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def ready(self) -> bool:
        return not self.enabled or set(self._fds) == {"keyboard", "pointer"}

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def status(self) -> dict[str, Any]:
        age_ms = None
        if self._last_event_at:
            age_ms = max(0, int((self._clock() - self._last_event_at) * 1000))
        return {
            "enabled": self.enabled,
            "ready": self.ready,
            "armed": self._armed,
            "exclusive_grab": bool(self._grabbed),
            "keyboard_ready": "keyboard" in self._fds,
            "pointer_ready": "pointer" in self._fds,
            "last_event_age_ms": age_ms,
            "last_error": self._last_error,
            "disarm_reason": self._disarm_reason,
            "dropped_events": self._dropped_events,
        }

    async def start(self) -> None:
        if not self.enabled or self._tasks:
            return
        self._closing.clear()
        paths = {
            "keyboard": self.config.keyboard_device,
            "pointer": self.config.pointer_device,
        }
        self._tasks = {
            kind: asyncio.create_task(
                self._supervise_device(kind, path), name=f"noob-local-{kind}"
            )
            for kind, path in paths.items()
        }

    async def stop(self) -> None:
        await self.disarm(reason="shutdown")
        self._closing.set()
        tasks, self._tasks = list(self._tasks.values()), {}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self._state_lock:
            fds, self._fds = list(self._fds.values()), {}
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass

    async def arm(self) -> None:
        """Exclusively grab both controls; never arm only half the appliance."""

        if not self.enabled:
            raise LocalInputDisabled("local input is disabled")
        async with self._event_lock:
            async with self._state_lock:
                if self._armed:
                    return
                if set(self._fds) != {"keyboard", "pointer"}:
                    raise LocalInputUnavailable("both local input devices must be ready")
                grabbed: list[str] = []
                try:
                    for kind in ("keyboard", "pointer"):
                        self._grab_device(self._fds[kind], True)
                        grabbed.append(kind)
                    for kind in ("keyboard", "pointer"):
                        self._drain_device(self._fds[kind])
                except OSError as exc:
                    for kind in reversed(grabbed):
                        try:
                            self._grab_device(self._fds[kind], False)
                        except OSError:
                            pass
                    self._last_error = "exclusive_grab_failed"
                    raise LocalInputUnavailable("exclusive evdev grab failed") from exc
                self._grabbed.update(grabbed)
                self._reset_input_state_locked()
                self._input_generation += 1
                self._armed = True
                self._disarm_reason = None
                self._last_error = None

    async def disarm(self, *, reason: str = "operator") -> bool:
        async with self._event_lock:
            return await self._disarm_locked(reason)

    async def feed_event(
        self, source: str, event_type: int, code: int, value: int
    ) -> None:
        """Translate one event; public to support deterministic host-side tests."""

        if source not in ("keyboard", "pointer"):
            return
        async with self._event_lock:
            if not self._armed:
                return
            self._last_event_at = self._clock()
            if source == "keyboard":
                await self._handle_keyboard_locked(event_type, code, value)
            else:
                await self._handle_pointer_locked(event_type, code, value)

    async def device_failed(self, source: str, error: str = "device_lost") -> None:
        """Fail closed when either half of the physical controller disappears."""

        async with self._event_lock:
            self._device_errors[source] = error
            self._last_error = error
            await self._disarm_locked("device_lost")

    async def _supervise_device(self, kind: str, path: str) -> None:
        while not self._closing.is_set():
            fd: int | None = None
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                async with self._state_lock:
                    self._fds[kind] = fd
                    self._device_errors[kind] = None
                    if not any(self._device_errors.values()):
                        self._last_error = None
                await self._read_loop(kind, fd)
                raise OSError("evdev device returned end of file")
            except asyncio.CancelledError:
                raise
            except OSError as exc:
                await self.device_failed(kind, type(exc).__name__)
            finally:
                if fd is not None:
                    async with self._state_lock:
                        if self._fds.get(kind) == fd:
                            self._fds.pop(kind, None)
                        self._grabbed.discard(kind)
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            if not self._closing.is_set():
                await asyncio.sleep(self.config.reconnect_ms / 1000.0)

    async def _read_loop(self, kind: str, fd: int) -> None:
        remainder = b""
        while not self._closing.is_set():
            armed_at_read = self._armed
            generation_at_read = self._input_generation
            try:
                data = os.read(fd, _INPUT_EVENT.size * _READ_EVENTS)
            except BlockingIOError:
                await asyncio.sleep(0.005)
                continue
            if not data:
                return
            events, remainder = unpack_input_events(data, remainder)
            if (
                not armed_at_read
                or generation_at_read != self._input_generation
                or not self._armed
            ):
                remainder = b""
                continue
            for event in events:
                await self.feed_event(
                    kind, event.event_type, event.code, event.value
                )

    async def _handle_keyboard_locked(
        self, event_type: int, code: int, value: int
    ) -> None:
        if event_type != EV_KEY or value not in (0, 1, 2):
            return
        name = LINUX_KEY_TO_NOOB.get(code)
        if name is None:
            return
        if value == 2:  # Kernel autorepeat never creates an extra target key-down.
            return

        if value == 1:
            if name in self._physical_keys:
                return
            self._physical_keys.add(name)
            if name in self.config.emergency_chord:
                if name not in self._pending_chord and name not in self._forwarded_keys:
                    self._pending_chord.append(name)
                if set(self.config.emergency_chord).issubset(self._physical_keys):
                    # All chord members were buffered.  None of the emergency
                    # chord reaches the target before local control is disarmed.
                    await self._disarm_locked("emergency_chord")
                return

            if not await self._flush_pending_chord_locked():
                return
            await self._forward_key_locked(name, "down")
            return

        # value == 0
        self._physical_keys.discard(name)
        if name in self._pending_chord:
            self._pending_chord.remove(name)
            if not await self._forward_key_locked(name, "down"):
                return
            await self._forward_key_locked(name, "up")
        elif name in self._forwarded_keys:
            await self._forward_key_locked(name, "up")

    async def _flush_pending_chord_locked(self) -> bool:
        pending, self._pending_chord = self._pending_chord, []
        for name in pending:
            if not await self._forward_key_locked(name, "down"):
                return False
        return True

    async def _forward_key_locked(self, name: str, event: str) -> bool:
        accepted = await self._submit_locked(
            {"op": "key", "event": event, "key": name}
        )
        if accepted:
            if event == "down":
                self._forwarded_keys.add(name)
            else:
                self._forwarded_keys.discard(name)
            self._schedule_idle_release_locked()
        return accepted

    async def _handle_pointer_locked(
        self, event_type: int, code: int, value: int
    ) -> None:
        if event_type == EV_REL and code in (REL_X, REL_Y):
            if code == REL_X:
                self._motion_x = max(
                    -_MAX_ACCUMULATED_MOTION,
                    min(_MAX_ACCUMULATED_MOTION, self._motion_x + value),
                )
            else:
                self._motion_y = max(
                    -_MAX_ACCUMULATED_MOTION,
                    min(_MAX_ACCUMULATED_MOTION, self._motion_y + value),
                )
            return
        if event_type == EV_SYN and code == SYN_REPORT:
            await self._flush_motion_locked()
            return
        if event_type != EV_KEY or value not in (0, 1, 2):
            return
        button = POINTER_BUTTONS.get(code)
        if button is None:
            return
        if value == 2:
            return
        if value == 1:
            if button in self._physical_buttons:
                return
            self._physical_buttons.add(button)
            if await self._submit_locked(
                {"op": "mouse_button", "button": button, "event": "down"}
            ):
                self._forwarded_buttons.add(button)
                self._schedule_idle_release_locked()
            return

        if (
            button not in self._physical_buttons
            and button not in self._forwarded_buttons
        ):
            return
        self._physical_buttons.discard(button)
        if button in self._forwarded_buttons:
            if not await self._submit_locked(
                {"op": "mouse_button", "button": button, "event": "up"}
            ):
                return
            self._forwarded_buttons.discard(button)
        self._schedule_idle_release_locked()

    async def _flush_motion_locked(self) -> None:
        dx, dy = self._motion_x, self._motion_y
        self._motion_x = 0
        self._motion_y = 0
        while dx or dy:
            chunk_x = max(-127, min(127, dx))
            chunk_y = max(-127, min(127, dy))
            if not await self._submit_locked(
                {"op": "mouse_move", "dx": chunk_x, "dy": chunk_y, "wheel": 0}
            ):
                return
            dx -= chunk_x
            dy -= chunk_y
        self._schedule_idle_release_locked()

    async def _submit_locked(self, command: dict[str, Any]) -> bool:
        try:
            accepted = await self._submit(command)
        except asyncio.CancelledError:
            raise
        except Exception:
            accepted = False
        if not accepted:
            self._dropped_events += 1
            self._last_error = "control_unavailable"
            await self._disarm_locked("control_unavailable")
            return False
        self._last_event_at = self._clock()
        return True

    def _schedule_idle_release_locked(self) -> None:
        self._cancel_task(self._idle_task)
        self._idle_task = None
        if (
            not self._armed
            or self._forwarded_keys
            or self._pending_chord
            or self._physical_buttons
            or self._forwarded_buttons
        ):
            return
        self._idle_task = asyncio.create_task(
            self._release_after_idle(), name="noob-local-idle-release"
        )

    async def _release_after_idle(self) -> None:
        try:
            await asyncio.sleep(self.config.lease_idle_ms / 1000.0)
            async with self._event_lock:
                if (
                    self._armed
                    and not self._forwarded_keys
                    and not self._pending_chord
                    and not self._physical_buttons
                    and not self._forwarded_buttons
                ):
                    if not await self._release_control():
                        await self._disarm_locked(
                            "release_unconfirmed", release_control=False
                        )
        except asyncio.CancelledError:
            raise

    async def _disarm_locked(
        self, reason: str, *, release_control: bool = True
    ) -> bool:
        async with self._state_lock:
            was_armed = self._armed
            self._armed = False
            if was_armed:
                self._input_generation += 1
            self._disarm_reason = reason
            self._reset_input_state_locked()
            for kind in ("pointer", "keyboard"):
                if kind not in self._grabbed:
                    continue
                fd = self._fds.get(kind)
                if fd is not None:
                    try:
                        self._grab_device(fd, False)
                    except OSError:
                        self._last_error = "exclusive_ungrab_failed"
                self._grabbed.discard(kind)
        release_confirmed = True
        if was_armed and release_control:
            # The runtime only releases a lease owned by this local controller;
            # it never releases an unrelated HTTP/Electron controller lease.
            try:
                release_confirmed = await self._release_control()
            except asyncio.CancelledError:
                raise
            except Exception:
                release_confirmed = False
            if not release_confirmed:
                self._last_error = "release_unconfirmed"
        return release_confirmed

    def _reset_input_state_locked(self) -> None:
        current = asyncio.current_task()
        for task in (self._idle_task,):
            if task is not None and task is not current:
                task.cancel()
        self._idle_task = None
        self._physical_keys.clear()
        self._forwarded_keys.clear()
        self._pending_chord.clear()
        self._physical_buttons.clear()
        self._forwarded_buttons.clear()
        self._motion_x = 0
        self._motion_y = 0

    @staticmethod
    def _cancel_task(task: asyncio.Task[None] | None) -> None:
        if task is not None and task is not asyncio.current_task():
            task.cancel()
