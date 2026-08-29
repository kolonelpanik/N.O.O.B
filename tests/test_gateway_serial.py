import asyncio
import json
import queue
import sys
import unittest
from pathlib import Path

import serial as pyserial


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

from noob_gateway.config import SerialConfig  # noqa: E402
from noob_gateway.serial_link import (  # noqa: E402
    SerialBusy,
    SerialInterrupted,
    SerialLink,
    SerialLinkError,
    SerialNack,
    SerialTimeout,
    SerialUnavailable,
)


class MemorySerial:
    def __init__(
        self,
        *,
        nack_op=None,
        drop_first_op=None,
        drop_all_op=None,
        raise_once_op=None,
        fragment_once_op=None,
        event_before_ack_op=None,
        event_with_ack_op=None,
        malformed_before_ack_op=None,
        malformed_with_ack_op=None,
        protocol_before_ack_op=None,
        event_after_op=None,
        nack_first_op=None,
        drop_after_first_op=None,
        **_kwargs,
    ):
        self.nack_op = nack_op
        self.drop_first_op = drop_first_op
        self.drop_all_op = drop_all_op
        self.raise_once_op = raise_once_op
        self.fragment_once_op = fragment_once_op
        self.event_before_ack_op = event_before_ack_op
        self.event_with_ack_op = event_with_ack_op
        self.malformed_before_ack_op = malformed_before_ack_op
        self.malformed_with_ack_op = malformed_with_ack_op
        self.protocol_before_ack_op = protocol_before_ack_op
        self.event_after_op = event_after_op
        self.nack_first_op = nack_first_op
        self.drop_after_first_op = drop_after_first_op
        self.dropped = False
        self.raised = False
        self.fragmented = False
        self.responses = queue.Queue()
        self.writes = []
        self.write_calls = []
        self.executed = []
        self._executed_ids = set()
        self._op_counts = {}
        self.closed = False

    def reset_input_buffer(self):
        return None

    def read_until(self, _expected, _size):
        if self.closed:
            return b""
        try:
            return self.responses.get(timeout=0.05)
        except queue.Empty:
            return b""

    def write(self, data):
        self.write_calls.append(data)
        for line in data.decode("ascii").splitlines():
            self._handle_message(json.loads(line))
        return len(data)

    def _handle_message(self, message):
        self.writes.append(message)
        self._op_counts[message["op"]] = self._op_counts.get(message["op"], 0) + 1
        op_count = self._op_counts[message["op"]]
        if message["op"] == self.raise_once_op and not self.raised:
            self.raised = True
            raise pyserial.SerialException("simulated serial write failure")
        command_id = (message["sid"], message["seq"])
        if command_id not in self._executed_ids:
            self._executed_ids.add(command_id)
            self.executed.append(message)
        if message["op"] == self.drop_all_op:
            return
        if message["op"] == self.drop_first_op and not self.dropped:
            self.dropped = True
            return
        if message["op"] == self.drop_after_first_op and op_count > 1:
            return
        if message["op"] == self.nack_op or (
            message["op"] == self.nack_first_op and op_count == 1
        ):
            response = {
                "v": 1,
                "kind": "nack",
                "sid": message["sid"],
                "seq": message["seq"],
                "code": "test_nack",
                "released": False,
            }
        else:
            response = {
                "v": 1,
                "kind": "ack",
                "sid": message["sid"],
                "seq": message["seq"],
            }
        event = {
            "v": 1,
            "kind": "event",
            "event": "released",
            "reason": "watchdog",
        }
        encoded = (json.dumps(response) + "\n").encode("utf-8")
        event_encoded = (json.dumps(event) + "\n").encode("utf-8")
        if message["op"] == self.event_before_ack_op:
            encoded = event_encoded + encoded
        elif message["op"] == self.event_with_ack_op:
            encoded = encoded + event_encoded
        if message["op"] == self.malformed_before_ack_op:
            encoded = b"{stale-uart-tail\n" + encoded
        elif message["op"] == self.malformed_with_ack_op:
            encoded = encoded + b"{malformed-after-ack\n"
        if message["op"] == self.protocol_before_ack_op:
            wrong_version = (json.dumps({"v": 2, "kind": "event"}) + "\n").encode(
                "utf-8"
            )
            encoded = wrong_version + encoded
        if message["op"] == self.fragment_once_op and not self.fragmented:
            self.fragmented = True
            split = max(1, len(encoded) // 2)
            self.responses.put(encoded[:split])
            self.responses.put(encoded[split:])
        else:
            self.responses.put(encoded)
        if message["op"] == self.event_after_op:
            self.responses.put((json.dumps(event) + "\n").encode("utf-8"))

    def close(self):
        self.closed = True


class ObservedLock:
    """Async lock that proves a task reached a contended acquisition."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self.waiting = asyncio.Event()

    async def acquire(self):
        if self._lock.locked():
            self.waiting.set()
        return await self._lock.acquire()

    def release(self):
        self._lock.release()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        self.release()


class SerialLinkTests(unittest.IsolatedAsyncioTestCase):
    def config(self, **updates):
        values = {
            "device": "/dev/fake",
            "baudrate": 115200,
            "ack_timeout_ms": 60,
            "heartbeat_ms": 1000,
            "watchdog_ms": 3000,
            "reconnect_ms": 100,
        }
        values.update(updates)
        return SerialConfig(**values)

    async def test_session_chunks_type_and_releases(self):
        fake = MemorySerial()
        link = SerialLink(self.config(), serial_factory=lambda **kwargs: fake)
        await link.start()
        await link.wait_ready(1.0)
        result = await link.send_command({"op": "type", "text": "x" * 65, "interval_ms": 0})
        self.assertEqual(result["chunks"], 3)
        type_writes = [item for item in fake.writes if item["op"] == "type"]
        self.assertEqual([len(item["text"]) for item in type_writes], [32, 32, 1])
        await link.emergency_release()
        self.assertIn("release_all", [item["op"] for item in fake.writes])
        await link.stop()

    async def test_mouse_movement_is_pipelined_in_one_bounded_uart_write(self):
        fake = MemorySerial()
        link = SerialLink(self.config(), serial_factory=lambda **kwargs: fake)
        await link.start()
        await link.wait_ready(1.0)

        result = await link.send_command(
            {"op": "mouse_move", "dx": 300, "dy": -260, "wheel": 127}
        )

        movements = [item for item in fake.writes if item["op"] == "mouse_move"]
        self.assertEqual(
            [(item["dx"], item["dy"], item["wheel"]) for item in movements],
            [(127, -127, 127), (127, -127, 0), (46, -6, 0)],
        )
        self.assertEqual(len({item["seq"] for item in movements}), 3)
        self.assertEqual(result["chunks"], 3)
        movement_calls = [
            call
            for call in fake.write_calls
            if b'"op":"mouse_move"' in call
        ]
        self.assertEqual(len(movement_calls), 1)
        self.assertEqual(movement_calls[0].count(b"\n"), 3)
        await link.stop()

    async def test_mouse_batch_retry_reuses_only_the_unresolved_sequence(self):
        fake = MemorySerial(drop_first_op="mouse_move")
        link = SerialLink(self.config(), serial_factory=lambda **kwargs: fake)
        await link.start()
        await link.wait_ready(1.0)

        result = await link.send_command(
            {"op": "mouse_move", "dx": 254, "dy": 0, "wheel": 0}
        )

        movements = [item for item in fake.writes if item["op"] == "mouse_move"]
        self.assertEqual(len(movements), 3)
        self.assertEqual(movements[0]["seq"], movements[2]["seq"])
        self.assertNotEqual(movements[0]["seq"], movements[1]["seq"])
        executed = [item for item in fake.executed if item["op"] == "mouse_move"]
        self.assertEqual(len(executed), 2)
        self.assertEqual(result["chunks"], 2)
        self.assertTrue(link.ready)
        await link.stop()

    async def test_mouse_batch_timeout_fails_closed_and_cleans_pending(self):
        fake = MemorySerial(drop_all_op="mouse_move")
        link = SerialLink(
            self.config(reconnect_ms=5000),
            serial_factory=lambda **kwargs: fake,
        )
        await link.start()
        await link.wait_ready(1.0)
        generation = link.generation

        with self.assertRaises(SerialTimeout):
            await link.send_command(
                {"op": "mouse_move", "dx": 254, "dy": 0, "wheel": 0}
            )

        movements = [item for item in fake.writes if item["op"] == "mouse_move"]
        self.assertEqual(len(movements), 4)
        self.assertEqual(
            [(item["sid"], item["seq"]) for item in movements[:2]],
            [(item["sid"], item["seq"]) for item in movements[2:]],
        )
        self.assertFalse(link.ready)
        self.assertGreater(link.generation, generation)
        self.assertEqual(link._pending, {})
        await link.stop()

    async def test_mouse_batch_nack_is_bounded_and_cleans_pending(self):
        fake = MemorySerial(nack_op="mouse_move")
        link = SerialLink(self.config(), serial_factory=lambda **kwargs: fake)
        await link.start()
        await link.wait_ready(1.0)

        with self.assertRaisesRegex(SerialNack, "test_nack"):
            await link.send_command(
                {"op": "mouse_move", "dx": 254, "dy": 0, "wheel": 0}
            )

        self.assertEqual(link._pending, {})
        self.assertTrue(link.ready)
        self.assertEqual(
            len([item for item in fake.writes if item["op"] == "mouse_move"]),
            2,
        )
        await link.stop()

    async def test_partial_batch_nack_with_missing_ack_fails_as_timeout(self):
        fake = MemorySerial(
            nack_first_op="mouse_move",
            drop_after_first_op="mouse_move",
        )
        link = SerialLink(
            self.config(reconnect_ms=5000),
            serial_factory=lambda **kwargs: fake,
        )
        await link.start()
        await link.wait_ready(1.0)

        with self.assertRaises(SerialTimeout):
            await link.send_command(
                {"op": "mouse_move", "dx": 254, "dy": 0, "wheel": 0}
            )

        movements = [item for item in fake.writes if item["op"] == "mouse_move"]
        self.assertEqual(len(movements), 3)
        self.assertNotEqual(movements[0]["seq"], movements[1]["seq"])
        self.assertEqual(movements[1]["seq"], movements[2]["seq"])
        self.assertFalse(link.ready)
        self.assertEqual(link._pending, {})
        await link.stop()

    async def test_emergency_release_interrupts_mouse_batch_before_retry(self):
        fake = MemorySerial(drop_all_op="mouse_move")
        link = SerialLink(self.config(), serial_factory=lambda **kwargs: fake)
        await link.start()
        await link.wait_ready(1.0)

        movement = asyncio.create_task(
            link.send_command(
                {"op": "mouse_move", "dx": 254, "dy": 0, "wheel": 0}
            )
        )
        for _ in range(50):
            if len(
                [item for item in fake.writes if item["op"] == "mouse_move"]
            ) == 2:
                break
            await asyncio.sleep(0.005)
        release = asyncio.create_task(link.emergency_release())

        with self.assertRaises(SerialInterrupted):
            await movement
        await release

        operations = [item["op"] for item in fake.writes if item["op"] != "session"]
        self.assertEqual(operations, ["mouse_move", "mouse_move", "release_all"])
        self.assertEqual(link._pending, {})
        await link.stop()

    async def test_retry_reuses_sequence(self):
        fake = MemorySerial(drop_first_op="ping")
        link = SerialLink(self.config(), serial_factory=lambda **kwargs: fake)
        await link.start()
        await link.wait_ready(1.0)
        await link.send_command({"op": "ping"})
        pings = [item for item in fake.writes if item["op"] == "ping"]
        self.assertEqual(len(pings), 2)
        self.assertEqual(pings[0]["sid"], pings[1]["sid"])
        self.assertEqual(pings[0]["seq"], pings[1]["seq"])
        await link.stop()

    async def test_fragmented_combo_ack_is_buffered_without_reconnect(self):
        fake = MemorySerial(fragment_once_op="combo")
        link = SerialLink(self.config(), serial_factory=lambda **kwargs: fake)
        await link.start()
        await link.wait_ready(1.0)

        await link.send_command(
            {"op": "combo", "keys": ["LEFT_SHIFT"], "hold_ms": 20}
        )
        await link.send_command({"op": "ping"})

        self.assertTrue(link.ready)
        self.assertEqual(link.status["reconnects"], 0)
        self.assertIsNone(link.status["last_disconnect_code"])
        await link.stop()

    async def test_pre_ready_release_event_is_ignored_as_stale(self):
        fake = MemorySerial(event_before_ack_op="session")
        link = SerialLink(self.config(), serial_factory=lambda **kwargs: fake)
        await link.start()

        await link.wait_ready(1.0)

        self.assertTrue(link.ready)
        self.assertEqual(link.status["reconnects"], 0)
        self.assertIsNone(link.status["last_disconnect_code"])
        self.assertEqual(
            len([item for item in fake.writes if item["op"] == "session"]), 1
        )
        await link.stop()

    async def test_release_after_session_ack_is_fatal_before_ready_publish(self):
        fake = MemorySerial(event_with_ack_op="session")
        link = SerialLink(
            self.config(reconnect_ms=5000),
            serial_factory=lambda **kwargs: fake,
        )
        await link.start()

        for _ in range(50):
            if link.status["last_disconnect_code"] is not None:
                break
            await asyncio.sleep(0.01)

        self.assertFalse(link.ready)
        self.assertEqual(
            link.status["last_disconnect_code"], "pico_watchdog_release"
        )
        await link.stop()

    async def test_pre_ack_malformed_json_is_ignored_as_stale(self):
        fake = MemorySerial(malformed_before_ack_op="session")
        link = SerialLink(self.config(), serial_factory=lambda **kwargs: fake)
        await link.start()

        await link.wait_ready(1.0)

        self.assertTrue(link.ready)
        self.assertEqual(link.status["reconnects"], 0)
        self.assertIsNone(link.status["last_disconnect_code"])
        self.assertEqual(
            len([item for item in fake.writes if item["op"] == "session"]), 1
        )
        await link.stop()

    async def test_malformed_json_after_session_ack_is_fatal_before_ready_publish(self):
        fake = MemorySerial(malformed_with_ack_op="session")
        link = SerialLink(
            self.config(reconnect_ms=5000),
            serial_factory=lambda **kwargs: fake,
        )
        await link.start()

        for _ in range(50):
            if link.status["last_disconnect_code"] is not None:
                break
            await asyncio.sleep(0.01)

        self.assertFalse(link.ready)
        self.assertEqual(link.status["last_disconnect_code"], "response_json")
        await link.stop()

    async def test_pre_ack_non_json_protocol_fault_remains_fatal(self):
        fake = MemorySerial(protocol_before_ack_op="session")
        link = SerialLink(
            self.config(reconnect_ms=5000),
            serial_factory=lambda **kwargs: fake,
        )
        await link.start()

        for _ in range(50):
            if link.status["last_disconnect_code"] is not None:
                break
            await asyncio.sleep(0.01)

        self.assertFalse(link.ready)
        self.assertEqual(link.status["last_disconnect_code"], "response_protocol")
        await link.stop()

    async def test_released_event_exposes_persistent_bounded_reason(self):
        initial = MemorySerial(event_after_op="ping")
        recovered = MemorySerial()
        ports = iter((initial, recovered))
        link = SerialLink(
            self.config(reconnect_ms=10),
            serial_factory=lambda **kwargs: next(ports),
        )
        await link.start()
        await link.wait_ready(1.0)

        await link.send_command({"op": "ping"})
        for _ in range(200):
            if link.ready and link.status["reconnects"] == 1:
                break
            await asyncio.sleep(0.01)

        self.assertTrue(link.ready)
        self.assertEqual(link.status["reconnects"], 1)
        self.assertEqual(
            link.status["last_disconnect_code"], "pico_watchdog_release"
        )
        self.assertNotIn("watchdog released", str(link.status))
        self.assertEqual(
            len([item for item in recovered.writes if item["op"] == "session"]), 1
        )
        await link.stop()

    async def test_nack_is_exposed_as_bounded_code(self):
        fake = MemorySerial(nack_op="key")
        link = SerialLink(self.config(), serial_factory=lambda **kwargs: fake)
        await link.start()
        await link.wait_ready(1.0)
        with self.assertRaisesRegex(SerialNack, "test_nack"):
            await link.send_command({"op": "key", "event": "down", "key": "A"})
        await link.stop()

    async def test_pending_public_commands_are_bounded(self):
        fake = MemorySerial(drop_all_op="key")
        link = SerialLink(
            self.config(ack_timeout_ms=500, max_pending_commands=1),
            serial_factory=lambda **kwargs: fake,
        )
        await link.start()
        await link.wait_ready(1.0)
        blocked = asyncio.create_task(
            link.send_command({"op": "key", "event": "down", "key": "A"})
        )
        for _ in range(20):
            if any(item["op"] == "key" for item in fake.writes):
                break
            await asyncio.sleep(0.01)
        with self.assertRaises(SerialBusy):
            await link.send_command({"op": "ping"})
        blocked.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await blocked
        await link.stop()

    async def test_stale_control_generation_cannot_send_after_release(self):
        fake = MemorySerial()
        link = SerialLink(self.config(), serial_factory=lambda **kwargs: fake)
        await link.start()
        await link.wait_ready(1.0)
        stale_generation = link.generation
        await link.emergency_release()
        with self.assertRaises(SerialInterrupted):
            await link.send_command({"op": "ping"}, expected_generation=stale_generation)
        await link.stop()

    async def test_queued_command_rechecks_generation_after_disconnect(self):
        fake = MemorySerial()
        link = SerialLink(
            self.config(reconnect_ms=5000),
            serial_factory=lambda **kwargs: fake,
        )
        await link.start()
        await link.wait_ready(1.0)
        generation = link.generation

        observed_lock = ObservedLock()
        link._wire_lock = observed_lock
        await observed_lock.acquire()
        pending = asyncio.create_task(
            link.send_command({"op": "ping"}, expected_generation=generation)
        )
        await asyncio.wait_for(observed_lock.waiting.wait(), 1.0)
        link._mark_transport_unhealthy("TestDisconnect", "serial_read_failed")
        observed_lock.release()

        with self.assertRaises(SerialInterrupted):
            await pending
        self.assertFalse(any(item["op"] == "ping" for item in fake.writes))
        self.assertEqual(link.status["last_disconnect_code"], "serial_read_failed")
        await link.stop()

    async def test_queued_internal_operation_rechecks_session_identity(self):
        fake = MemorySerial()
        link = SerialLink(self.config(), serial_factory=lambda **kwargs: fake)
        await link.start()
        await link.wait_ready(1.0)
        original_session = link._session_id

        observed_lock = ObservedLock()
        link._wire_lock = observed_lock
        await observed_lock.acquire()
        pending = asyncio.create_task(link._send_operation({"op": "ping"}))
        await asyncio.wait_for(observed_lock.waiting.wait(), 1.0)
        link._session_id = "replacement-session"
        observed_lock.release()

        with self.assertRaises(SerialUnavailable):
            await pending
        self.assertFalse(any(item["op"] == "ping" for item in fake.writes))
        link._session_id = original_session
        await link.stop()

    async def test_raw_pyserial_release_error_is_normalized_and_disconnects(self):
        fake = MemorySerial(raise_once_op="release_all")
        link = SerialLink(
            self.config(reconnect_ms=5000),
            serial_factory=lambda **kwargs: fake,
        )
        await link.start()
        await link.wait_ready(1.0)

        with self.assertRaises(SerialLinkError) as caught:
            await link.emergency_release()

        self.assertNotIsInstance(caught.exception, pyserial.SerialException)
        self.assertIsInstance(caught.exception, SerialUnavailable)
        self.assertFalse(link.ready)
        await link.stop()

    async def test_exhausted_ack_marks_transport_unhealthy_and_executes_once(self):
        fake = MemorySerial(drop_all_op="key")
        link = SerialLink(
            self.config(reconnect_ms=5000),
            serial_factory=lambda **kwargs: fake,
        )
        await link.start()
        await link.wait_ready(1.0)
        initial_generation = link.generation

        with self.assertRaises(SerialTimeout):
            await link.send_command({"op": "key", "event": "down", "key": "A"})

        writes = [item for item in fake.writes if item["op"] == "key"]
        executed = [item for item in fake.executed if item["op"] == "key"]
        self.assertEqual(len(writes), 2)
        self.assertEqual((writes[0]["sid"], writes[0]["seq"]), (writes[1]["sid"], writes[1]["seq"]))
        self.assertEqual(len(executed), 1)
        self.assertFalse(link.ready)
        self.assertGreater(link.generation, initial_generation)
        with self.assertRaises(SerialUnavailable):
            await link.emergency_release()
        await link.stop()


if __name__ == "__main__":
    unittest.main()
