"""Reliable newline-delimited JSON session over a pyserial UART."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from typing import Any, Callable

import serial

from .config import SerialConfig
from .models import HID_MOUSE_DELTA_LIMIT, MOUSE_MOVE_BATCH_LIMIT


PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 1024


_DIAGNOSTIC_CODES = frozenset(
    (
        "ack_timeout",
        "outbound_line_too_long",
        "pico_held_timeout",
        "pico_nack",
        "pico_restarted",
        "pico_unsolicited_release",
        "pico_watchdog_release",
        "response_framing",
        "response_json",
        "response_protocol",
        "serial_link_error",
        "serial_open_failed",
        "serial_read_failed",
        "serial_reset_failed",
        "serial_unavailable",
        "serial_write_failed",
        "short_serial_write",
        "supervisor_error",
        "unknown_response",
    )
)


class SerialLinkError(RuntimeError):
    diagnostic_code = "serial_link_error"


class SerialUnavailable(SerialLinkError):
    diagnostic_code = "serial_unavailable"


class SerialTimeout(SerialLinkError):
    diagnostic_code = "ack_timeout"


class SerialInterrupted(SerialLinkError):
    pass


class SerialBusy(SerialLinkError):
    pass


class SerialNack(SerialLinkError):
    diagnostic_code = "pico_nack"

    def __init__(self, code: str, *, released: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.released = released


class _ProtocolFault(SerialLinkError):
    """Internal fault with an allowlisted, content-free status code."""

    def __init__(self, diagnostic_code: str, message: str) -> None:
        if diagnostic_code not in _DIAGNOSTIC_CODES:
            raise ValueError("unknown serial diagnostic code")
        super().__init__(message)
        self.diagnostic_code = diagnostic_code


def _diagnostic_code(exc: BaseException, default: str = "supervisor_error") -> str:
    """Return only a fixed diagnostic label; never surface exception text."""

    code = getattr(exc, "diagnostic_code", None)
    return code if code in _DIAGNOSTIC_CODES else default


def _clamp_mouse_delta(value: int) -> int:
    return max(-HID_MOUSE_DELTA_LIMIT, min(HID_MOUSE_DELTA_LIMIT, value))


def _split_mouse_move(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """Decompose one bounded logical movement into Pico-safe HID reports."""

    remaining_x = operation["dx"]
    remaining_y = operation["dy"]
    remaining_wheel = operation["wheel"]
    chunks: list[dict[str, Any]] = []
    while remaining_x or remaining_y or remaining_wheel:
        chunk_x = _clamp_mouse_delta(remaining_x)
        chunk_y = _clamp_mouse_delta(remaining_y)
        chunk_wheel = _clamp_mouse_delta(remaining_wheel)
        chunks.append(
            {
                "op": "mouse_move",
                "dx": chunk_x,
                "dy": chunk_y,
                "wheel": chunk_wheel,
            }
        )
        remaining_x -= chunk_x
        remaining_y -= chunk_y
        remaining_wheel -= chunk_wheel
        if len(chunks) > MOUSE_MOVE_BATCH_LIMIT:
            raise SerialLinkError("mouse movement exceeds batch limit")
    return chunks or [dict(operation)]


class SerialLink:
    """Own one UART session and retry idempotently by reusing SID/sequence."""

    def __init__(
        self,
        config: SerialConfig,
        *,
        serial_factory: Callable[..., Any] = serial.Serial,
        clock=time.monotonic,
    ) -> None:
        self.config = config
        self._serial_factory = serial_factory
        self._clock = clock
        self._serial: Any | None = None
        self._session_id: str | None = None
        self._session_acknowledged = False
        self._sequence = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._wire_lock = asyncio.Lock()
        self._public_lock = asyncio.Lock()
        self._queue_count_lock = asyncio.Lock()
        self._queued_commands = 0
        self._ready = asyncio.Event()
        self._disconnect = asyncio.Event()
        self._closing = asyncio.Event()
        self._supervisor_task: asyncio.Task[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._abort_generation = 0
        self._last_ack_at = 0.0
        self._firmware: str | None = None
        self._reconnects = 0
        self._last_error: str | None = None
        self._last_disconnect_code: str | None = None

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def generation(self) -> int:
        return self._abort_generation

    @property
    def status(self) -> dict[str, Any]:
        age_ms = None
        if self._last_ack_at:
            age_ms = max(0, int((self._clock() - self._last_ack_at) * 1000))
        return {
            "ready": self.ready,
            "device": self.config.device,
            "firmware": self._firmware,
            "last_ack_age_ms": age_ms,
            "reconnects": self._reconnects,
            "last_error": self._last_error,
            "last_disconnect_code": self._last_disconnect_code,
            "queued_commands": self._queued_commands,
        }

    async def start(self) -> None:
        if self._supervisor_task is None:
            self._supervisor_task = asyncio.create_task(self._supervise(), name="noob-serial")

    async def stop(self) -> None:
        if self._supervisor_task is None:
            return
        if self.ready:
            try:
                await asyncio.wait_for(self.emergency_release(), timeout=1.5)
            except (SerialLinkError, asyncio.TimeoutError):
                pass
        self._closing.set()
        self._disconnect.set()
        self._supervisor_task.cancel()
        try:
            await self._supervisor_task
        except asyncio.CancelledError:
            pass
        self._supervisor_task = None
        await self._close_serial()

    async def wait_ready(self, timeout: float = 5.0) -> None:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError as exc:
            raise SerialUnavailable("serial session is not ready") from exc

    async def send_command(
        self,
        command: dict[str, Any],
        *,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        """Send one validated public command, batching bounded logical operations."""

        async with self._queue_count_lock:
            if self._queued_commands >= self.config.max_pending_commands:
                raise SerialBusy("serial command queue is full")
            self._queued_commands += 1
        try:
            await self.wait_ready(timeout=max(1.0, self.config.ack_timeout_ms / 1000.0 * 2))
            generation = (
                self._abort_generation if expected_generation is None else expected_generation
            )
            if generation != self._abort_generation:
                raise SerialInterrupted("input operation interrupted by emergency release")
            async with self._public_lock:
                if command["op"] == "type":
                    chunks = [
                        command["text"][index : index + 32]
                        for index in range(0, len(command["text"]), 32)
                    ]
                    last: dict[str, Any] = {}
                    for chunk in chunks:
                        if generation != self._abort_generation:
                            raise SerialInterrupted("input operation interrupted by emergency release")
                        operation = dict(command)
                        operation["text"] = chunk
                        last = await self._send_operation(
                            operation, expected_generation=generation
                        )
                    return {"chunks": len(chunks), "pico": last}
                if command["op"] == "mouse_move":
                    chunks = _split_mouse_move(command)
                    if generation != self._abort_generation:
                        raise SerialInterrupted(
                            "input operation interrupted by emergency release"
                        )
                    if len(chunks) == 1:
                        last = await self._send_operation(
                            chunks[0], expected_generation=generation
                        )
                    else:
                        acknowledgements = await self._send_mouse_batch(
                            chunks, expected_generation=generation
                        )
                        last = acknowledgements[-1]
                    return {"chunks": len(chunks), "pico": last}
                if generation != self._abort_generation:
                    raise SerialInterrupted("input operation interrupted by emergency release")
                return {
                    "chunks": 1,
                    "pico": await self._send_operation(
                        command, expected_generation=generation
                    ),
                }
        finally:
            async with self._queue_count_lock:
                self._queued_commands = max(0, self._queued_commands - 1)

    async def emergency_release(self) -> dict[str, Any]:
        """Invalidate queued type chunks and release HID state at the next wire boundary."""

        self._abort_generation += 1
        generation = self._abort_generation
        await self.wait_ready(timeout=max(0.5, self.config.ack_timeout_ms / 1000.0 * 2))
        return await self._send_operation(
            {"op": "release_all"}, expected_generation=generation
        )

    async def _supervise(self) -> None:
        while not self._closing.is_set():
            self._disconnect = asyncio.Event()
            self._session_acknowledged = False
            try:
                await self._open_serial()
                self._reader_task = asyncio.create_task(self._read_loop(), name="noob-serial-reader")
                self._session_id = secrets.token_hex(8)
                self._sequence = 0
                await self._exchange(
                    {"op": "session", "watchdog_ms": self.config.watchdog_ms},
                    sequence=0,
                    require_ready=False,
                )
                if self._disconnect.is_set():
                    raise SerialUnavailable("serial session ended during handshake")
                self._ready.set()
                self._last_error = None
                self._heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(), name="noob-serial-heartbeat"
                )
                await self._disconnect.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._disconnect.is_set():
                    self._mark_transport_unhealthy(
                        type(exc).__name__, _diagnostic_code(exc)
                    )
            finally:
                self._ready.clear()
                self._abort_generation += 1
                await self._cancel_connection_tasks()
                self._fail_pending(SerialUnavailable("serial session ended"))
                await self._close_serial()
                self._session_id = None
                self._session_acknowledged = False
            if not self._closing.is_set():
                self._reconnects += 1
                await asyncio.sleep(self.config.reconnect_ms / 1000.0)

    def _mark_transport_unhealthy(
        self, error_name: str, diagnostic_code: str = "serial_link_error"
    ) -> None:
        """Fail closed after an indeterminate read, write, or ACK result."""

        if diagnostic_code not in _DIAGNOSTIC_CODES:
            diagnostic_code = "supervisor_error"
        first_transition = self._ready.is_set() or not self._disconnect.is_set()
        self._ready.clear()
        if first_transition:
            self._abort_generation += 1
            self._last_error = error_name
            self._last_disconnect_code = diagnostic_code
        self._disconnect.set()

    async def _open_serial(self) -> None:
        def open_port():
            kwargs = {
                "port": self.config.device,
                "baudrate": self.config.baudrate,
                "bytesize": serial.EIGHTBITS,
                "parity": serial.PARITY_NONE,
                "stopbits": serial.STOPBITS_ONE,
                "timeout": 0.1,
                "write_timeout": 0.5,
            }
            try:
                try:
                    return self._serial_factory(exclusive=True, **kwargs)
                except TypeError:
                    return self._serial_factory(**kwargs)
            except (serial.SerialException, OSError) as exc:
                raise SerialUnavailable("serial open failed") from exc

        try:
            self._serial = await asyncio.to_thread(open_port)
        except SerialLinkError as exc:
            self._mark_transport_unhealthy(type(exc).__name__, "serial_open_failed")
            raise
        try:
            if hasattr(self._serial, "reset_input_buffer"):
                await asyncio.to_thread(self._serial.reset_input_buffer)
        except (serial.SerialException, OSError) as exc:
            self._mark_transport_unhealthy(type(exc).__name__, "serial_reset_failed")
            raise SerialUnavailable("serial initialization failed") from exc

    async def _close_serial(self) -> None:
        port, self._serial = self._serial, None
        if port is not None:
            try:
                await asyncio.to_thread(port.close)
            except Exception:
                pass

    async def _cancel_connection_tasks(self) -> None:
        current = asyncio.current_task()
        tasks = [task for task in (self._reader_task, self._heartbeat_task) if task and task is not current]
        self._reader_task = None
        self._heartbeat_task = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _fail_pending(self, exc: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    async def _read_loop(self) -> None:
        receive_buffer = bytearray()
        try:
            while not self._closing.is_set() and self._serial is not None:
                try:
                    line = await asyncio.to_thread(
                        self._serial.read_until, b"\n", MAX_LINE_BYTES + 1
                    )
                except (serial.SerialException, OSError) as exc:
                    raise SerialUnavailable("serial read failed") from exc
                if not line:
                    continue
                receive_buffer.extend(line)
                while True:
                    newline = receive_buffer.find(b"\n")
                    if newline < 0:
                        if len(receive_buffer) > MAX_LINE_BYTES:
                            raise _ProtocolFault(
                                "response_framing", "invalid response framing"
                            )
                        break
                    if newline + 1 > MAX_LINE_BYTES:
                        raise _ProtocolFault(
                            "response_framing", "invalid response framing"
                        )
                    framed = bytes(receive_buffer[: newline + 1])
                    del receive_buffer[: newline + 1]
                    try:
                        message = json.loads(framed.decode("utf-8"))
                    except (UnicodeError, ValueError):
                        # FTDI may surface one newline-terminated tail from the
                        # previous UART session after its input buffer reset.
                        # Only JSON/UTF-8 decode failure is stale before the
                        # current SID's session ACK; every other framing or
                        # protocol fault, and every post-ACK decode fault,
                        # remains fail-closed.
                        if not self._session_acknowledged:
                            continue
                        raise _ProtocolFault(
                            "response_json", "invalid JSON response"
                        ) from None
                    if not isinstance(message, dict) or message.get("v") != PROTOCOL_VERSION:
                        raise _ProtocolFault(
                            "response_protocol", "invalid protocol response"
                        )
                    if message.get("kind") == "event":
                        if message.get("event") == "ready":
                            firmware = message.get("fw")
                            if isinstance(firmware, str) and len(firmware) <= 32:
                                self._firmware = firmware
                            if self.ready:
                                raise _ProtocolFault("pico_restarted", "Pico restarted")
                        elif message.get("event") == "released":
                            # A release from the previous SID may already be in the
                            # FTDI receive path when a replacement session starts.
                            # Before the current SID's session ACK is observed there
                            # is no accepted HID session to invalidate, so the event
                            # is stale. After that ACK, every release remains fatal,
                            # even before the supervisor publishes ready state.
                            if not self._session_acknowledged:
                                continue
                            release_code = {
                                "watchdog": "pico_watchdog_release",
                                "held_timeout": "pico_held_timeout",
                            }.get(message.get("reason"), "pico_unsolicited_release")
                            raise _ProtocolFault(
                                release_code, "Pico released the active session"
                            )
                        continue
                    if message.get("kind") not in ("ack", "nack"):
                        raise _ProtocolFault(
                            "unknown_response", "unknown protocol response"
                        )
                    if message.get("sid") != self._session_id:
                        continue
                    sequence = message.get("seq")
                    if isinstance(sequence, bool) or not isinstance(sequence, int):
                        continue
                    if sequence == 0 and message.get("kind") == "ack":
                        self._session_acknowledged = True
                    future = self._pending.get(sequence)
                    if future is not None and not future.done():
                        future.set_result(message)
        except asyncio.CancelledError:
            raise
        except SerialLinkError as exc:
            code = (
                "serial_read_failed"
                if isinstance(exc, SerialUnavailable)
                else _diagnostic_code(exc)
            )
            self._mark_transport_unhealthy(type(exc).__name__, code)
        except Exception as exc:
            self._mark_transport_unhealthy(type(exc).__name__, "supervisor_error")
        finally:
            self._disconnect.set()

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._closing.is_set():
                await asyncio.sleep(self.config.heartbeat_ms / 1000.0)
                if not self.ready:
                    return
                await self._send_operation({"op": "heartbeat"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._mark_transport_unhealthy(
                type(exc).__name__, _diagnostic_code(exc)
            )

    def _next_sequence(self) -> int:
        self._sequence = (self._sequence + 1) & 0xFFFFFFFF
        if self._sequence == 0:
            self._sequence = 1
        return self._sequence

    async def _send_operation(
        self,
        operation: dict[str, Any],
        *,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        return await self._exchange(
            operation,
            sequence=self._next_sequence(),
            require_ready=True,
            expected_generation=expected_generation,
        )

    async def _send_mouse_batch(
        self,
        operations: list[dict[str, Any]],
        *,
        expected_generation: int,
    ) -> list[dict[str, Any]]:
        """Pipeline a bounded mouse-only burst while preserving public ordering."""

        if not 2 <= len(operations) <= MOUSE_MOVE_BATCH_LIMIT:
            raise SerialLinkError("invalid mouse batch size")
        if any(operation.get("op") != "mouse_move" for operation in operations):
            raise SerialLinkError("mouse batch contains an invalid operation")
        sequences = [self._next_sequence() for _ in operations]
        return await self._exchange_mouse_batch(
            operations,
            sequences=sequences,
            expected_generation=expected_generation,
        )

    async def _exchange_mouse_batch(
        self,
        operations: list[dict[str, Any]],
        *,
        sequences: list[int],
        expected_generation: int,
    ) -> list[dict[str, Any]]:
        """Write multiple idempotent mouse reports and await one shared ACK window."""

        if not self.ready:
            raise SerialUnavailable("serial session is not ready")
        if self._serial is None or self._session_id is None:
            raise SerialUnavailable("serial port is not open")
        if len(operations) != len(sequences):
            raise SerialLinkError("mouse batch sequence mismatch")

        serial_port = self._serial
        session_id = self._session_id
        timeout = self.config.ack_timeout_ms / 1000.0
        encoded_items: list[tuple[int, bytes]] = []
        for operation, sequence in zip(operations, sequences, strict=True):
            payload = {
                "v": PROTOCOL_VERSION,
                "sid": session_id,
                "seq": sequence,
                **operation,
            }
            encoded = (
                json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n"
            ).encode("ascii")
            if len(encoded) > MAX_LINE_BYTES:
                raise _ProtocolFault(
                    "outbound_line_too_long", "outbound protocol line is too long"
                )
            encoded_items.append((sequence, encoded))

        async with self._wire_lock:
            if expected_generation != self._abort_generation:
                raise SerialInterrupted("input operation interrupted by emergency release")
            if not self.ready:
                raise SerialUnavailable("serial session is not ready")
            if self._serial is not serial_port or self._session_id != session_id:
                raise SerialUnavailable("serial session changed while command was queued")

            loop = asyncio.get_running_loop()
            futures = {
                sequence: loop.create_future() for sequence, _encoded in encoded_items
            }
            self._pending.update(futures)
            try:
                for attempt in range(2):
                    if expected_generation != self._abort_generation:
                        raise SerialInterrupted(
                            "input operation interrupted by emergency release"
                        )
                    if self._serial is not serial_port or self._session_id != session_id:
                        raise SerialUnavailable("serial session changed during command")
                    if not self.ready:
                        raise SerialUnavailable("serial session is not ready")

                    unresolved = [
                        (sequence, encoded)
                        for sequence, encoded in encoded_items
                        if not futures[sequence].done()
                    ]
                    if not unresolved:
                        break
                    outbound = b"".join(encoded for _sequence, encoded in unresolved)
                    try:
                        written = await asyncio.to_thread(serial_port.write, outbound)
                    except (serial.SerialException, OSError) as exc:
                        self._mark_transport_unhealthy(
                            type(exc).__name__, "serial_write_failed"
                        )
                        raise SerialUnavailable("serial write failed") from exc
                    if written != len(outbound):
                        self._mark_transport_unhealthy(
                            "ShortSerialWrite", "short_serial_write"
                        )
                        raise SerialLinkError("short serial write")

                    waiting = [
                        future for future in futures.values() if not future.done()
                    ]
                    if waiting:
                        await asyncio.wait(
                            waiting,
                            timeout=timeout,
                            return_when=asyncio.ALL_COMPLETED,
                        )

                    if all(future.done() for future in futures.values()):
                        completed = [
                            futures[sequence].result() for sequence in sequences
                        ]
                        nack = next(
                            (
                                response
                                for response in completed
                                if response.get("kind") == "nack"
                            ),
                            None,
                        )
                        if nack is not None:
                            self._raise_nack(nack)
                        break
                    if expected_generation != self._abort_generation:
                        raise SerialInterrupted(
                            "input operation interrupted by emergency release"
                        )
                    if attempt == 1:
                        self._mark_transport_unhealthy("SerialTimeout", "ack_timeout")
                        raise SerialTimeout("Pico ACK timeout")
                else:  # pragma: no cover - loop always breaks or raises
                    raise SerialTimeout("Pico ACK timeout")

                if expected_generation != self._abort_generation:
                    raise SerialInterrupted(
                        "input operation interrupted by emergency release"
                    )
                for sequence in sequences:
                    futures[sequence].result()
            finally:
                for sequence, future in futures.items():
                    self._pending.pop(sequence, None)
                    if not future.done():
                        future.cancel()

        self._last_ack_at = self._clock()
        return [
            {"sid": session_id, "seq": sequence, "kind": "ack"}
            for sequence in sequences
        ]

    @staticmethod
    def _raise_nack(response: dict[str, Any]) -> None:
        code = response.get("code")
        if not isinstance(code, str) or len(code) > 32:
            code = "unknown_nack"
        raise SerialNack(code, released=response.get("released") is True)

    async def _exchange(
        self,
        operation: dict[str, Any],
        *,
        sequence: int,
        require_ready: bool,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        if require_ready and not self.ready:
            raise SerialUnavailable("serial session is not ready")
        if self._serial is None or self._session_id is None:
            raise SerialUnavailable("serial port is not open")
        serial_port = self._serial
        session_id = self._session_id
        timeout = self.config.ack_timeout_ms / 1000.0
        if operation["op"] == "type":
            timeout += len(operation["text"]) * operation["interval_ms"] / 1000.0
        elif operation["op"] == "combo":
            timeout += operation["hold_ms"] / 1000.0

        async with self._wire_lock:
            if expected_generation is not None and expected_generation != self._abort_generation:
                raise SerialInterrupted("input operation interrupted by emergency release")
            if require_ready and not self.ready:
                raise SerialUnavailable("serial session is not ready")
            if self._serial is not serial_port or self._session_id != session_id:
                raise SerialUnavailable("serial session changed while command was queued")
            payload = {
                "v": PROTOCOL_VERSION,
                "sid": session_id,
                "seq": sequence,
                **operation,
            }
            encoded = (
                json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n"
            ).encode("ascii")
            if len(encoded) > MAX_LINE_BYTES:
                raise _ProtocolFault(
                    "outbound_line_too_long", "outbound protocol line is too long"
                )
            loop = asyncio.get_running_loop()
            future: asyncio.Future[dict[str, Any]] = loop.create_future()
            self._pending[sequence] = future
            try:
                for attempt in range(2):
                    if self._serial is not serial_port or self._session_id != session_id:
                        raise SerialUnavailable("serial session changed during command")
                    try:
                        written = await asyncio.to_thread(serial_port.write, encoded)
                    except (serial.SerialException, OSError) as exc:
                        self._mark_transport_unhealthy(
                            type(exc).__name__, "serial_write_failed"
                        )
                        raise SerialUnavailable("serial write failed") from exc
                    if written != len(encoded):
                        self._mark_transport_unhealthy(
                            "ShortSerialWrite", "short_serial_write"
                        )
                        raise SerialLinkError("short serial write")
                    try:
                        response = await asyncio.wait_for(asyncio.shield(future), timeout)
                        break
                    except asyncio.TimeoutError:
                        if attempt == 1:
                            self._mark_transport_unhealthy(
                                "SerialTimeout", "ack_timeout"
                            )
                            raise SerialTimeout("Pico ACK timeout") from None
                else:  # pragma: no cover - loop always breaks or raises
                    raise SerialTimeout("Pico ACK timeout")
            finally:
                self._pending.pop(sequence, None)

        self._last_ack_at = self._clock()
        if response.get("kind") == "nack":
            self._raise_nack(response)
        return {"sid": session_id, "seq": sequence, "kind": "ack"}
