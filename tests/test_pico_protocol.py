import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pico"))

from protocol import ProtocolError, parse_command  # noqa: E402


def encoded(**values):
    base = {"v": 1, "sid": "0123456789abcdef", "seq": 1}
    base.update(values)
    return json.dumps(base).encode("utf-8")


class PicoProtocolTests(unittest.TestCase):
    def test_uart_buffer_reset_is_circuitpython_compatible(self):
        firmware = (
            Path(__file__).resolve().parents[1] / "pico" / "code.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("receive_buffer.clear()", firmware)
        self.assertNotIn("del receive_buffer[:]", firmware)
        self.assertIn("receive_buffer = bytearray()", firmware)

    def test_session(self):
        result = parse_command(encoded(op="session", watchdog_ms=2000))
        self.assertEqual(result["op"], "session")

    def test_type_allows_bounded_ascii_and_controls(self):
        result = parse_command(encoded(op="type", text="hello\tworld\n", interval_ms=5))
        self.assertEqual(result["text"], "hello\tworld\n")

    def test_type_rejects_non_ascii(self):
        with self.assertRaisesRegex(ProtocolError, "bad_range"):
            parse_command(encoded(op="type", text="snowman \u2603", interval_ms=0))

    def test_combo_rejects_duplicate_keys(self):
        with self.assertRaisesRegex(ProtocolError, "bad_key"):
            parse_command(encoded(op="combo", keys=["LEFT_GUI", "LEFT_GUI"], hold_ms=50))

    def test_bool_is_not_integer(self):
        with self.assertRaisesRegex(ProtocolError, "bad_seq"):
            parse_command(encoded(op="ping", seq=True))

    def test_unknown_field_is_rejected(self):
        with self.assertRaisesRegex(ProtocolError, "bad_field"):
            parse_command(encoded(op="ping", surprise="no"))

    def test_mouse_range(self):
        with self.assertRaisesRegex(ProtocolError, "bad_range"):
            parse_command(encoded(op="mouse_move", dx=128, dy=0, wheel=0))

    def test_overlong_line(self):
        with self.assertRaisesRegex(ProtocolError, "line_too_long"):
            parse_command(b"{" + b"x" * 1024)


if __name__ == "__main__":
    unittest.main()
