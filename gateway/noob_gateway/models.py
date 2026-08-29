"""Strict public input models and constants shared by HTTP handlers."""

from __future__ import annotations

from typing import Any


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
ACTION_TYPE_INTERVAL_DEFAULT_MS = 0
ACTION_COMBO_HOLD_DEFAULT_MS = 50
HID_MOUSE_DELTA_LIMIT = 127
MOUSE_MOVE_BATCH_LIMIT = 8
GATEWAY_MOUSE_AXIS_LIMIT = HID_MOUSE_DELTA_LIMIT * MOUSE_MOVE_BATCH_LIMIT
GATEWAY_MOUSE_WHEEL_LIMIT = HID_MOUSE_DELTA_LIMIT


class InputValidationError(ValueError):
    def __init__(self, code: str = "bad_request") -> None:
        super().__init__(code)
        self.code = code


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _exact(obj: dict[str, Any], required: set[str]) -> None:
    if set(obj) != required:
        raise InputValidationError("bad_field")


def _normalize_input_envelope(obj: Any) -> Any:
    """Copy and normalize the bounded Phase-4 compatibility envelope."""

    if not isinstance(obj, dict):
        return obj
    if "action" in obj:
        if "op" in obj:
            raise InputValidationError("bad_field")
        action = obj.get("action")
        if not isinstance(action, str):
            raise InputValidationError("bad_request")
        normalized = {
            ("op" if name == "action" else name): value for name, value in obj.items()
        }
        if action == "type":
            normalized.setdefault("interval_ms", ACTION_TYPE_INTERVAL_DEFAULT_MS)
        elif action == "combo":
            normalized.setdefault("hold_ms", ACTION_COMBO_HOLD_DEFAULT_MS)
    else:
        normalized = dict(obj)

    op = normalized.get("op")
    if op == "key" and normalized.get("key") == "GUI":
        normalized["key"] = "LEFT_GUI"
    elif op == "combo" and isinstance(normalized.get("keys"), list):
        normalized["keys"] = [
            "LEFT_GUI" if key == "GUI" else key for key in normalized["keys"]
        ]
    return normalized


def validate_input_command(obj: Any, *, max_type_chars: int = 512) -> dict[str, Any]:
    """Normalize, validate, and copy a command before adding serial identifiers."""

    obj = _normalize_input_envelope(obj)
    if not isinstance(obj, dict) or not isinstance(obj.get("op"), str):
        raise InputValidationError("bad_request")
    op = obj["op"]

    if op == "key":
        _exact(obj, {"op", "event", "key"})
        if obj["event"] not in ("down", "up"):
            raise InputValidationError("bad_field")
        if obj["key"] not in KEY_NAMES:
            raise InputValidationError("bad_key")
    elif op == "type":
        _exact(obj, {"op", "text", "interval_ms"})
        text = obj["text"]
        interval = obj["interval_ms"]
        if not isinstance(text, str) or not text or len(text) > max_type_chars:
            raise InputValidationError("bad_range")
        for char in text:
            code = ord(char)
            if code not in (9, 10, 13) and not 32 <= code <= 126:
                raise InputValidationError("bad_range")
        if not _is_int(interval) or not 0 <= interval <= 25:
            raise InputValidationError("bad_range")
    elif op == "combo":
        _exact(obj, {"op", "keys", "hold_ms"})
        keys = obj["keys"]
        hold = obj["hold_ms"]
        if not isinstance(keys, list) or not 1 <= len(keys) <= 6:
            raise InputValidationError("bad_range")
        if any(not isinstance(key, str) for key in keys):
            raise InputValidationError("bad_key")
        if len(set(keys)) != len(keys) or any(key not in KEY_NAMES for key in keys):
            raise InputValidationError("bad_key")
        if not _is_int(hold) or not 20 <= hold <= 500:
            raise InputValidationError("bad_range")
    elif op == "mouse_move":
        _exact(obj, {"op", "dx", "dy", "wheel"})
        for name in ("dx", "dy"):
            value = obj[name]
            if (
                not _is_int(value)
                or not -GATEWAY_MOUSE_AXIS_LIMIT
                <= value
                <= GATEWAY_MOUSE_AXIS_LIMIT
            ):
                raise InputValidationError("bad_range")
        wheel = obj["wheel"]
        if (
            not _is_int(wheel)
            or not -GATEWAY_MOUSE_WHEEL_LIMIT
            <= wheel
            <= GATEWAY_MOUSE_WHEEL_LIMIT
        ):
            raise InputValidationError("bad_range")
    elif op == "mouse_button":
        _exact(obj, {"op", "button", "event"})
        if obj["button"] not in MOUSE_BUTTONS:
            raise InputValidationError("bad_field")
        if obj["event"] not in ("down", "up", "click"):
            raise InputValidationError("bad_field")
    elif op in ("release_all", "ping"):
        _exact(obj, {"op"})
    else:
        raise InputValidationError("bad_op")

    return dict(obj)


def mouse_move_chunk_count(command: dict[str, Any]) -> int:
    """Return the bounded signed-byte HID report cost of a logical movement."""

    if command.get("op") != "mouse_move":
        return 1
    largest = max(abs(command["dx"]), abs(command["dy"]), abs(command["wheel"]))
    return max(1, (largest + HID_MOUSE_DELTA_LIMIT - 1) // HID_MOUSE_DELTA_LIMIT)
