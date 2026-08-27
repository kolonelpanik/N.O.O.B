"""Strict N.O.O.B UART protocol v1 validation.

This module deliberately avoids host-only dependencies so the same validation
can run under CircuitPython and CPython unit tests.
"""

import json


VERSION = 1
MAX_LINE_BYTES = 1024
MAX_SEQUENCE = 0xFFFFFFFF
KEY_NAMES = frozenset(
    (
        "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M",
        "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
        "ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE",
        "ENTER", "ESCAPE", "BACKSPACE", "TAB", "SPACE", "MINUS", "EQUALS",
        "LEFT_BRACKET", "RIGHT_BRACKET", "BACKSLASH", "SEMICOLON", "QUOTE",
        "GRAVE_ACCENT", "COMMA", "PERIOD", "FORWARD_SLASH", "CAPS_LOCK",
        "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
        "PRINT_SCREEN", "SCROLL_LOCK", "PAUSE", "INSERT", "HOME", "PAGE_UP",
        "DELETE", "END", "PAGE_DOWN", "RIGHT_ARROW", "LEFT_ARROW", "DOWN_ARROW", "UP_ARROW",
        "APPLICATION", "LEFT_CONTROL", "LEFT_SHIFT", "LEFT_ALT", "LEFT_GUI",
        "RIGHT_CONTROL", "RIGHT_SHIFT", "RIGHT_ALT", "RIGHT_GUI",
    )
)
MOUSE_BUTTONS = frozenset(("left", "right", "middle"))


class ProtocolError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _require_exact_fields(obj, required, optional=()):
    required = set(required)
    allowed = required | set(optional)
    if not required.issubset(obj):
        raise ProtocolError("bad_field")
    if set(obj) - allowed:
        raise ProtocolError("bad_field")


def _valid_sid(value):
    if not isinstance(value, str) or len(value) != 16:
        return False
    for char in value:
        if char not in "0123456789abcdefABCDEF":
            return False
    return True


def _validate_common(obj):
    if obj.get("v") != VERSION:
        raise ProtocolError("bad_version")
    if not _valid_sid(obj.get("sid")):
        raise ProtocolError("bad_session")
    sequence = obj.get("seq")
    if not _is_int(sequence) or sequence < 0 or sequence > MAX_SEQUENCE:
        raise ProtocolError("bad_seq")
    if not isinstance(obj.get("op"), str):
        raise ProtocolError("bad_op")


def parse_command(raw_line):
    if not isinstance(raw_line, (bytes, bytearray)):
        raise ProtocolError("bad_json")
    if len(raw_line) > MAX_LINE_BYTES:
        raise ProtocolError("line_too_long")
    try:
        text = bytes(raw_line).decode("utf-8")
        obj = json.loads(text)
    except (UnicodeError, ValueError):
        raise ProtocolError("bad_json")
    if not isinstance(obj, dict):
        raise ProtocolError("bad_json")

    _validate_common(obj)
    op = obj["op"]

    if op == "session":
        _require_exact_fields(obj, ("v", "sid", "seq", "op", "watchdog_ms"))
        value = obj["watchdog_ms"]
        if not _is_int(value) or value < 500 or value > 5000:
            raise ProtocolError("bad_range")
    elif op in ("heartbeat", "ping", "release_all"):
        _require_exact_fields(obj, ("v", "sid", "seq", "op"))
    elif op == "key":
        _require_exact_fields(obj, ("v", "sid", "seq", "op", "event", "key"))
        if obj["event"] not in ("down", "up"):
            raise ProtocolError("bad_field")
        if obj["key"] not in KEY_NAMES:
            raise ProtocolError("bad_key")
    elif op == "type":
        _require_exact_fields(obj, ("v", "sid", "seq", "op", "text", "interval_ms"))
        text_value = obj["text"]
        interval = obj["interval_ms"]
        if not isinstance(text_value, str) or not text_value or len(text_value) > 32:
            raise ProtocolError("bad_range")
        for char in text_value:
            code = ord(char)
            if code not in (9, 10, 13) and not 32 <= code <= 126:
                raise ProtocolError("bad_range")
        if not _is_int(interval) or interval < 0 or interval > 25:
            raise ProtocolError("bad_range")
    elif op == "combo":
        _require_exact_fields(obj, ("v", "sid", "seq", "op", "keys", "hold_ms"))
        keys = obj["keys"]
        hold = obj["hold_ms"]
        if not isinstance(keys, list) or not 1 <= len(keys) <= 6:
            raise ProtocolError("bad_range")
        if len(set(keys)) != len(keys) or any(key not in KEY_NAMES for key in keys):
            raise ProtocolError("bad_key")
        if not _is_int(hold) or hold < 20 or hold > 500:
            raise ProtocolError("bad_range")
    elif op == "mouse_move":
        _require_exact_fields(obj, ("v", "sid", "seq", "op", "dx", "dy", "wheel"))
        for name in ("dx", "dy", "wheel"):
            value = obj[name]
            if not _is_int(value) or value < -127 or value > 127:
                raise ProtocolError("bad_range")
    elif op == "mouse_button":
        _require_exact_fields(obj, ("v", "sid", "seq", "op", "button", "event"))
        if obj["button"] not in MOUSE_BUTTONS:
            raise ProtocolError("bad_field")
        if obj["event"] not in ("down", "up", "click"):
            raise ProtocolError("bad_field")
    else:
        raise ProtocolError("bad_op")

    return obj
