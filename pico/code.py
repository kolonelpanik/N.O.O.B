"""N.O.O.B Pico WH UART-to-USB-HID firmware."""

import json
import time

import board
import busio
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keyboard_layout_us import KeyboardLayoutUS
from adafruit_hid.keycode import Keycode
from adafruit_hid.mouse import Mouse

from protocol import MAX_LINE_BYTES, ProtocolError, parse_command


FIRMWARE_VERSION = "0.2.0"
DEFAULT_WATCHDOG_MS = 2000
MAX_HELD_SECONDS = 10.0
RESPONSE_CACHE_SIZE = 16

KEYCODES = {
    "A": Keycode.A, "B": Keycode.B, "C": Keycode.C, "D": Keycode.D,
    "E": Keycode.E, "F": Keycode.F, "G": Keycode.G, "H": Keycode.H,
    "I": Keycode.I, "J": Keycode.J, "K": Keycode.K, "L": Keycode.L,
    "M": Keycode.M, "N": Keycode.N, "O": Keycode.O, "P": Keycode.P,
    "Q": Keycode.Q, "R": Keycode.R, "S": Keycode.S, "T": Keycode.T,
    "U": Keycode.U, "V": Keycode.V, "W": Keycode.W, "X": Keycode.X,
    "Y": Keycode.Y, "Z": Keycode.Z,
    "ZERO": Keycode.ZERO, "ONE": Keycode.ONE, "TWO": Keycode.TWO,
    "THREE": Keycode.THREE, "FOUR": Keycode.FOUR, "FIVE": Keycode.FIVE,
    "SIX": Keycode.SIX, "SEVEN": Keycode.SEVEN, "EIGHT": Keycode.EIGHT,
    "NINE": Keycode.NINE, "ENTER": Keycode.ENTER, "ESCAPE": Keycode.ESCAPE,
    "BACKSPACE": Keycode.BACKSPACE, "TAB": Keycode.TAB, "SPACE": Keycode.SPACE,
    "MINUS": Keycode.MINUS, "EQUALS": Keycode.EQUALS,
    "LEFT_BRACKET": Keycode.LEFT_BRACKET, "RIGHT_BRACKET": Keycode.RIGHT_BRACKET,
    "BACKSLASH": Keycode.BACKSLASH, "SEMICOLON": Keycode.SEMICOLON,
    "QUOTE": Keycode.QUOTE, "GRAVE_ACCENT": Keycode.GRAVE_ACCENT,
    "COMMA": Keycode.COMMA, "PERIOD": Keycode.PERIOD,
    "FORWARD_SLASH": Keycode.FORWARD_SLASH, "CAPS_LOCK": Keycode.CAPS_LOCK,
    "F1": Keycode.F1, "F2": Keycode.F2, "F3": Keycode.F3, "F4": Keycode.F4,
    "F5": Keycode.F5, "F6": Keycode.F6, "F7": Keycode.F7, "F8": Keycode.F8,
    "F9": Keycode.F9, "F10": Keycode.F10, "F11": Keycode.F11, "F12": Keycode.F12,
    "PRINT_SCREEN": Keycode.PRINT_SCREEN, "SCROLL_LOCK": Keycode.SCROLL_LOCK,
    "PAUSE": Keycode.PAUSE, "INSERT": Keycode.INSERT, "HOME": Keycode.HOME,
    "PAGE_UP": Keycode.PAGE_UP, "DELETE": Keycode.DELETE, "END": Keycode.END,
    "PAGE_DOWN": Keycode.PAGE_DOWN, "RIGHT_ARROW": Keycode.RIGHT_ARROW,
    "LEFT_ARROW": Keycode.LEFT_ARROW, "DOWN_ARROW": Keycode.DOWN_ARROW,
    "UP_ARROW": Keycode.UP_ARROW, "APPLICATION": Keycode.APPLICATION,
    "LEFT_CONTROL": Keycode.LEFT_CONTROL, "LEFT_SHIFT": Keycode.LEFT_SHIFT,
    "LEFT_ALT": Keycode.LEFT_ALT, "LEFT_GUI": Keycode.LEFT_GUI,
    "RIGHT_CONTROL": Keycode.RIGHT_CONTROL, "RIGHT_SHIFT": Keycode.RIGHT_SHIFT,
    "RIGHT_ALT": Keycode.RIGHT_ALT, "RIGHT_GUI": Keycode.RIGHT_GUI,
}

MOUSE_BUTTONS = {
    "left": Mouse.LEFT_BUTTON,
    "right": Mouse.RIGHT_BUTTON,
    "middle": Mouse.MIDDLE_BUTTON,
}


uart = busio.UART(
    board.GP0,
    board.GP1,
    baudrate=115200,
    bits=8,
    parity=None,
    stop=1,
    timeout=0.01,
    receiver_buffer_size=2048,
)
keyboard = Keyboard(usb_hid.devices)
layout = KeyboardLayoutUS(keyboard)
mouse = Mouse(usb_hid.devices)

active_sid = None
watchdog_ms = DEFAULT_WATCHDOG_MS
last_valid_at = time.monotonic()
last_ready_at = 0.0
held_keys = {}
held_buttons = {}
response_cache = []
receive_buffer = bytearray()
discard_until_newline = False


def _write_object(obj):
    data = (json.dumps(obj) + "\n").encode("utf-8")
    uart.write(data)
    return data


def _write_raw(data):
    uart.write(data)


def _release_all():
    try:
        keyboard.release_all()
    finally:
        mouse.release_all()
        held_keys.clear()
        held_buttons.clear()


def _event(event, reason=None):
    payload = {"v": 1, "kind": "event", "event": event}
    if reason:
        payload["reason"] = reason
    _write_object(payload)


def _ready():
    _write_object(
        {
            "v": 1,
            "kind": "event",
            "event": "ready",
            "fw": FIRMWARE_VERSION,
            "caps": ["keyboard", "mouse"],
            "max_line": MAX_LINE_BYTES,
        }
    )


def _cache_get(sid, sequence):
    for cached_sid, cached_sequence, response in response_cache:
        if cached_sid == sid and cached_sequence == sequence:
            return response
    return None


def _cache_put(sid, sequence, response):
    response_cache.append((sid, sequence, response))
    if len(response_cache) > RESPONSE_CACHE_SIZE:
        response_cache.pop(0)


def _ack(command):
    response = _write_object(
        {
            "v": 1,
            "kind": "ack",
            "sid": command["sid"],
            "seq": command["seq"],
        }
    )
    _cache_put(command["sid"], command["seq"], response)


def _nack(code, command=None, released=False):
    payload = {"v": 1, "kind": "nack", "code": code, "released": released}
    if command:
        sid = command.get("sid")
        sequence = command.get("seq")
        if isinstance(sid, str):
            payload["sid"] = sid
        if isinstance(sequence, int):
            payload["seq"] = sequence
    response = _write_object(payload)
    if command and "sid" in payload and "seq" in payload:
        _cache_put(payload["sid"], payload["seq"], response)


def _type_text(text, interval_ms):
    delay = interval_ms / 1000.0
    for char in text:
        if char in "\r\n":
            keyboard.press(Keycode.ENTER)
            keyboard.release(Keycode.ENTER)
        elif char == "\t":
            keyboard.press(Keycode.TAB)
            keyboard.release(Keycode.TAB)
        else:
            layout.write(char)
        if delay:
            time.sleep(delay)


def _execute(command):
    global active_sid, watchdog_ms, last_valid_at

    sid = command["sid"]
    sequence = command["seq"]
    cached = _cache_get(sid, sequence)
    if cached is not None:
        _write_raw(cached)
        return

    op = command["op"]
    # CDC development telemetry intentionally records only the operation name
    # and sequence number. It never prints typed text, keys, or mouse payloads.
    if op != "heartbeat":
        print("NOOB_UART_RX", op, sequence)
    if op == "session":
        _release_all()
        response_cache.clear()
        active_sid = sid
        watchdog_ms = command["watchdog_ms"]
        last_valid_at = time.monotonic()
        _ack(command)
        return

    if active_sid is None:
        _nack("no_session", command)
        return
    if sid != active_sid:
        _release_all()
        active_sid = None
        _nack("bad_session", command, released=True)
        return

    last_valid_at = time.monotonic()
    if op in ("heartbeat", "ping"):
        _ack(command)
    elif op == "release_all":
        _release_all()
        _ack(command)
    elif op == "key":
        name = command["key"]
        keycode = KEYCODES[name]
        if command["event"] == "down":
            if name not in held_keys:
                keyboard.press(keycode)
                held_keys[name] = time.monotonic()
        else:
            keyboard.release(keycode)
            held_keys.pop(name, None)
        _ack(command)
    elif op == "type":
        _type_text(command["text"], command["interval_ms"])
        _ack(command)
    elif op == "combo":
        _release_all()
        codes = [KEYCODES[name] for name in command["keys"]]
        keyboard.press(*codes)
        time.sleep(command["hold_ms"] / 1000.0)
        _release_all()
        _ack(command)
    elif op == "mouse_move":
        mouse.move(x=command["dx"], y=command["dy"], wheel=command["wheel"])
        _ack(command)
    elif op == "mouse_button":
        name = command["button"]
        button = MOUSE_BUTTONS[name]
        event = command["event"]
        if event == "down":
            if name not in held_buttons:
                mouse.press(button)
                held_buttons[name] = time.monotonic()
        elif event == "up":
            mouse.release(button)
            held_buttons.pop(name, None)
        else:
            mouse.click(button)
        _ack(command)


def _handle_line(line):
    command = None
    try:
        command = parse_command(line)
        _execute(command)
    except ProtocolError as exc:
        _release_all()
        _nack(exc.code, command, released=True)
    except (KeyError, ValueError, MemoryError, OSError):
        _release_all()
        _nack("exec_failed", command, released=True)


def _consume(data):
    global discard_until_newline, receive_buffer
    for byte in data:
        if discard_until_newline:
            if byte == 10:
                discard_until_newline = False
                _release_all()
                _nack("line_too_long", released=True)
            continue
        if byte == 10:
            if receive_buffer:
                line = bytes(receive_buffer)
                # CircuitPython's bytearray supports neither CPython's
                # ``clear`` method nor slice deletion. Rebinding is portable
                # and keeps the parser state deterministic after every line.
                receive_buffer = bytearray()
                _handle_line(line)
        else:
            receive_buffer.append(byte)
            if len(receive_buffer) > MAX_LINE_BYTES:
                receive_buffer = bytearray()
                discard_until_newline = True


_release_all()
_ready()
last_ready_at = time.monotonic()

while True:
    now = time.monotonic()
    waiting = uart.in_waiting
    if waiting:
        chunk = uart.read(min(waiting, 256))
        if chunk:
            _consume(chunk)

    if active_sid is not None:
        if (now - last_valid_at) * 1000.0 > watchdog_ms:
            _release_all()
            active_sid = None
            _event("released", "watchdog")
        elif any(now - started > MAX_HELD_SECONDS for started in held_keys.values()) or any(
            now - started > MAX_HELD_SECONDS for started in held_buttons.values()
        ):
            _release_all()
            active_sid = None
            _event("released", "held_timeout")
    elif now - last_ready_at >= 1.0:
        _ready()
        last_ready_at = now

    time.sleep(0.002)
