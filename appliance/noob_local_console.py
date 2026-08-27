#!/usr/bin/env python3
"""Appliance-local N.O.O.B. viewer and control-mode switch.

The application is intentionally a viewer, not a second remote controller. It
never claims an HTTP input lease. Its only mutating actions arm or disarm the
uConsole's built-in keyboard and trackball through the gateway's existing
local-input endpoints.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from io import BytesIO
import json
import queue
import re
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable
from urllib import error, parse, request


APP_TITLE = "N.O.O.B Local Console"
DEFAULT_GATEWAY = "http://127.0.0.1:8765"
DEFAULT_TOKEN_COMMAND = (
    "/usr/bin/sudo",
    "-n",
    "-u",
    "noob",
    "/bin/cat",
    "/etc/noob/local-console.key",
)
ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MODE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_FRAME_RESPONSE_BYTES = 16 * 1024 * 1024
VIDEO_MODE_REQUEST_TIMEOUT = 65.0
VIDEO_STATES = frozenset(
    {
        "starting",
        "ready",
        "switching",
        "reconnecting",
        "rolling_back",
        "rolled_back",
        "degraded",
        "stopped",
    }
)

BG = "#080d11"
SURFACE = "#0d1419"
SURFACE_RAISED = "#111a20"
BORDER = "#27343b"
TEXT = "#f3f7f8"
MUTED = "#8c9aa2"
SIGNAL = "#39e1df"
HEALTHY = "#29d17d"
DANGER = "#ff3a42"
WARN = "#f2b84b"


class LocalConsoleError(RuntimeError):
    """Bounded error safe for the appliance UI."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code if ERROR_CODE.fullmatch(code) else "operation_failed"


class NoRedirectHandler(request.HTTPRedirectHandler):
    """Never forward the Authorization header beyond the fixed loopback origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class ActionGate:
    """Serialize arm/disarm with shutdown so the final action is always disarm."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._closing = False

    def run(self, action: Callable[[], Any]) -> Any | None:
        with self._lock:
            if self._closing:
                return None
            return action()

    def close(self, disarm: Callable[[], Any]) -> Any:
        with self._lock:
            self._closing = True
            try:
                return disarm()
            except Exception:
                # A failed close must remain retryable.  Otherwise an
                # unconfirmed disarm would permanently seal the gate while the
                # window stays open.
                self._closing = False
                raise


def validate_loopback_gateway(value: str) -> str:
    """Accept only an HTTP loopback gateway so the token cannot be exfiltrated."""

    candidate = value.rstrip("/")
    parsed = parse.urlsplit(candidate)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("gateway must be a loopback HTTP origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("gateway port is invalid") from exc
    if port is None or not 1 <= port <= 65535:
        raise ValueError("gateway must include a valid port")
    return candidate


def validate_token_bytes(raw: bytes) -> str:
    """Validate the same bounded ASCII token contract as the gateway."""

    token = raw.rstrip(b"\r\n")
    if not 32 <= len(token) <= 256:
        raise LocalConsoleError("token_unavailable")
    try:
        value = token.decode("ascii")
    except UnicodeDecodeError as exc:
        raise LocalConsoleError("token_unavailable") from exc
    if any(char.isspace() for char in value):
        raise LocalConsoleError("token_unavailable")
    return value


def load_local_token(
    command: tuple[str, ...] = DEFAULT_TOKEN_COMMAND,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> str:
    """Read the protected token without arguments, environment, clipboard, or logs."""

    try:
        completed = runner(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LocalConsoleError("token_unavailable") from exc
    if completed.returncode != 0:
        raise LocalConsoleError("token_unavailable")
    return validate_token_bytes(completed.stdout)


@dataclass(frozen=True, slots=True)
class ViewState:
    video_ready: bool
    serial_ready: bool
    keyboard_ready: bool
    pointer_ready: bool
    local_enabled: bool
    local_armed: bool
    exclusive_grab: bool
    control_active: bool
    release_required: bool
    video_state: str
    video_generation: int
    active_mode_id: str | None
    requested_signal: tuple[int, int, int | float, str] | None
    negotiated_signal: tuple[int, int, int | float, str] | None
    source_timing_detectable: bool

    @property
    def remote_control_active(self) -> bool:
        return self.control_active and not self.local_armed

    @property
    def arm_allowed(self) -> bool:
        return bool(
            self.video_ready
            and self.serial_ready
            and self.local_enabled
            and self.keyboard_ready
            and self.pointer_ready
            and not self.local_armed
            and not self.exclusive_grab
            and not self.control_active
            and not self.release_required
        )

    @property
    def mode_change_allowed(self) -> bool:
        # A degraded video mode must remain recoverable.  Only an active mode
        # transition or HID ownership blocks changing the global output.
        return bool(
            self.video_state not in {"switching", "rolling_back"}
            and not self.local_armed
            and not self.exclusive_grab
            and not self.control_active
            and not self.release_required
        )


@dataclass(frozen=True, slots=True)
class VideoMode:
    mode_id: str
    label: str
    width: int
    height: int
    fps: int
    pixel_format: str
    max_frame_bytes: int
    validated: bool

    @property
    def display_label(self) -> str:
        return f"{self.label} · {self.width}×{self.height} @ {self.fps}"


@dataclass(frozen=True, slots=True)
class VideoModeCatalog:
    generation: int
    active_mode_id: str | None
    state: str
    modes: tuple[VideoMode, ...]


def _signal_from_payload(value: Any) -> tuple[int, int, int | float, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LocalConsoleError("status_unavailable")
    width = value.get("width")
    height = value.get("height")
    fps = value.get("fps")
    pixel_format = value.get("pixel_format")
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or not 1 <= width <= 7680
        or isinstance(height, bool)
        or not isinstance(height, int)
        or not 1 <= height <= 4320
        or isinstance(fps, bool)
        or not isinstance(fps, (int, float))
        or not 1 <= fps <= 120
        or not isinstance(pixel_format, str)
        or not 1 <= len(pixel_format) <= 16
    ):
        raise LocalConsoleError("status_unavailable")
    return (width, height, fps, pixel_format)


def view_state_from_status(payload: Any) -> ViewState:
    """Reduce the status response to fields the local UI is allowed to render."""

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise LocalConsoleError("status_unavailable")
    serial = payload.get("serial")
    video = payload.get("video")
    local = payload.get("local_input")
    control = payload.get("control")
    if not all(isinstance(item, dict) for item in (serial, video, local, control)):
        raise LocalConsoleError("status_unavailable")
    generation = video.get("generation", 0)
    state = video.get("state", "ready" if video.get("ready") is True else "degraded")
    active_mode_id = video.get("active_mode_id")
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 0
        or state not in VIDEO_STATES
        or (
            active_mode_id is not None
            and (
                not isinstance(active_mode_id, str)
                or MODE_ID.fullmatch(active_mode_id) is None
            )
        )
    ):
        raise LocalConsoleError("status_unavailable")
    return ViewState(
        video_ready=video.get("ready") is True,
        serial_ready=serial.get("ready") is True,
        keyboard_ready=local.get("keyboard_ready") is True,
        pointer_ready=local.get("pointer_ready") is True,
        local_enabled=local.get("enabled") is True,
        local_armed=local.get("armed") is True,
        exclusive_grab=local.get("exclusive_grab") is True,
        control_active=control.get("active") is True,
        release_required=control.get("release_required") is True,
        video_state=state,
        video_generation=generation,
        active_mode_id=active_mode_id,
        requested_signal=_signal_from_payload(video.get("requested")),
        negotiated_signal=_signal_from_payload(video.get("negotiated")),
        source_timing_detectable=video.get("source_timing_detectable") is True,
    )


def video_modes_from_payload(payload: Any) -> VideoModeCatalog:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise LocalConsoleError("video_modes_unavailable")
    generation = payload.get("generation")
    active_mode_id = payload.get("active_mode_id")
    state = payload.get("state")
    raw_modes = payload.get("modes")
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 0
        or state not in VIDEO_STATES
        or not isinstance(raw_modes, list)
        or len(raw_modes) > 32
        or (
            active_mode_id is not None
            and (
                not isinstance(active_mode_id, str)
                or MODE_ID.fullmatch(active_mode_id) is None
            )
        )
    ):
        raise LocalConsoleError("video_modes_unavailable")
    modes: list[VideoMode] = []
    seen: set[str] = set()
    for item in raw_modes:
        if not isinstance(item, dict):
            raise LocalConsoleError("video_modes_unavailable")
        mode_id = item.get("id")
        label = item.get("label")
        width = item.get("width")
        height = item.get("height")
        fps = item.get("fps")
        pixel_format = item.get("pixel_format")
        max_frame_bytes = item.get("max_frame_bytes")
        validated = item.get("validated")
        if (
            not isinstance(mode_id, str)
            or MODE_ID.fullmatch(mode_id) is None
            or mode_id in seen
            or not isinstance(label, str)
            or not 1 <= len(label) <= 80
            or isinstance(width, bool)
            or not isinstance(width, int)
            or not 160 <= width <= 7680
            or isinstance(height, bool)
            or not isinstance(height, int)
            or not 120 <= height <= 4320
            or isinstance(fps, bool)
            or not isinstance(fps, int)
            or not 1 <= fps <= 120
            or pixel_format != "MJPG"
            or isinstance(max_frame_bytes, bool)
            or not isinstance(max_frame_bytes, int)
            or not 64 * 1024 <= max_frame_bytes <= MAX_FRAME_RESPONSE_BYTES
            or validated is not True
        ):
            raise LocalConsoleError("video_modes_unavailable")
        seen.add(mode_id)
        modes.append(
            VideoMode(
                mode_id,
                label,
                width,
                height,
                fps,
                pixel_format,
                max_frame_bytes,
                True,
            )
        )
    return VideoModeCatalog(generation, active_mode_id, state, tuple(modes))


class GatewayClient:
    """Small, loopback-only authenticated client with bounded responses."""

    def __init__(self, origin: str, token: str, *, timeout: float = 1.5) -> None:
        self.origin = validate_loopback_gateway(origin)
        self._authorization = f"Bearer {token}"
        self.timeout = timeout
        self._opener = request.build_opener(
            request.ProxyHandler({}),
            NoRedirectHandler(),
        )

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        max_bytes: int,
        timeout: float | None = None,
    ) -> bytes:
        headers = {
            "Authorization": self._authorization,
            "Accept": "application/json, image/jpeg",
            "Cache-Control": "no-store",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = request.Request(
            f"{self.origin}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(
                req, timeout=self.timeout if timeout is None else timeout
            ) as response:
                data = response.read(max_bytes + 1)
        except error.HTTPError as exc:
            code = "request_failed"
            try:
                data = exc.read(4097)
                candidate = json.loads(data.decode("utf-8")).get("error")
                if isinstance(candidate, str) and ERROR_CODE.fullmatch(candidate):
                    code = candidate
            except (UnicodeError, ValueError, AttributeError):
                pass
            raise LocalConsoleError(code) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise LocalConsoleError("gateway_unavailable") from exc
        if len(data) > max_bytes:
            raise LocalConsoleError("response_too_large")
        return data

    def status(self) -> ViewState:
        raw = self._request("/api/v1/status", max_bytes=65536)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise LocalConsoleError("status_unavailable") from exc
        return view_state_from_status(payload)

    def frame(self) -> bytes:
        raw = self._request(
            "/api/v1/frame.jpg", max_bytes=MAX_FRAME_RESPONSE_BYTES
        )
        if not (raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")):
            raise LocalConsoleError("frame_invalid")
        return raw

    def video_modes(self) -> VideoModeCatalog:
        raw = self._request("/api/v1/video/modes", max_bytes=65536)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise LocalConsoleError("video_modes_unavailable") from exc
        return video_modes_from_payload(payload)

    def set_video_mode(self, mode_id: str, expected_generation: int) -> ViewState:
        if (
            not isinstance(mode_id, str)
            or MODE_ID.fullmatch(mode_id) is None
            or isinstance(expected_generation, bool)
            or not isinstance(expected_generation, int)
            or expected_generation < 0
        ):
            raise LocalConsoleError("invalid_video_mode_request")
        body = json.dumps(
            {
                "mode_id": mode_id,
                "expected_generation": expected_generation,
            },
            separators=(",", ":"),
        ).encode("ascii")
        self._request(
            "/api/v1/video/mode",
            method="POST",
            body=body,
            max_bytes=65536,
            timeout=VIDEO_MODE_REQUEST_TIMEOUT,
        )
        try:
            return self.status()
        except LocalConsoleError as exc:
            # The server-side transition is transactional and generation
            # checked.  Never replay an ambiguous POST from the client.
            raise LocalConsoleError("video_mode_unconfirmed") from exc

    def arm(self) -> ViewState:
        try:
            self._post_empty("/api/v1/local-input/arm")
            return self.status()
        except LocalConsoleError as exc:
            # A lost POST response or failed follow-up status request can leave
            # the physical devices armed even though the UI saw an error.
            # Local disarm is scoped and cannot release a remote HTTP lease, so
            # it is always the safe reconciliation action.
            try:
                self._post_empty("/api/v1/local-input/disarm")
            except LocalConsoleError:
                pass
            raise LocalConsoleError("arm_unconfirmed") from exc

    def disarm(self) -> ViewState:
        self._post_empty("/api/v1/local-input/disarm")
        return self.status()

    def _post_empty(self, path: str) -> None:
        raw = self._request(path, method="POST", body=b"{}", max_bytes=65536)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise LocalConsoleError("request_failed") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise LocalConsoleError("request_failed")


class NoobLocalConsole:
    FRAME_INTERVAL = 0.12
    STATUS_INTERVAL = 0.8

    def __init__(self, root: tk.Tk, client: GatewayClient) -> None:
        self.root = root
        self.client = client
        self.stop_event = threading.Event()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=32)
        self.frame_lock = threading.Lock()
        self.latest_frame: bytes | None = None
        self.current_state: ViewState | None = None
        self.video_modes: tuple[VideoMode, ...] = ()
        self.mode_display_to_id: dict[str, str] = {}
        self.photo: Any | None = None
        self.fullscreen = False
        self.pinned = True
        self.action_inflight = False
        self.hide_after_disarm = False
        self.action_gate = ActionGate()
        self.closing = False

        self._configure_window()
        self._build_ui()
        self.root.after(40, self._drain_events)
        threading.Thread(target=self._poll_loop, daemon=True, name="noob-local-poll").start()

    def _configure_window(self) -> None:
        self.root.title(APP_TITLE)
        self.root.configure(bg=BG)
        self.root.geometry("1100x680+90+18")
        self.root.minsize(820, 540)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.bind("<F11>", lambda _event: self._toggle_fullscreen())
        self.root.bind("<Escape>", lambda _event: self._leave_fullscreen())

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Noob.TButton", background=SURFACE_RAISED, foreground=TEXT, bordercolor=BORDER, padding=(14, 9), font=("Sans", 10, "bold"))
        style.map("Noob.TButton", background=[("active", "#18242b"), ("disabled", SURFACE)], foreground=[("disabled", "#59656b")])
        style.configure("Arm.TButton", background="#123b34", foreground="#d8fff3", bordercolor="#206d5b", padding=(18, 10), font=("Sans", 10, "bold"))
        style.map("Arm.TButton", background=[("active", "#195043"), ("disabled", SURFACE)], foreground=[("disabled", "#59656b")])
        style.configure("Disarm.TButton", background="#431d23", foreground="#ffdfe2", bordercolor="#84343e", padding=(18, 10), font=("Sans", 10, "bold"))
        style.map("Disarm.TButton", background=[("active", "#59262e"), ("disabled", SURFACE)], foreground=[("disabled", "#59656b")])

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=BG, padx=18, pady=13)
        header.pack(fill="x")
        brand = tk.Frame(header, bg=BG)
        brand.pack(side="left")
        tk.Label(brand, text="N.O.O.B", bg=BG, fg=TEXT, font=("Sans", 16, "bold")).pack(anchor="w")
        tk.Label(brand, text="NEVER OUT OF BOUNDS · LOCAL CONSOLE", bg=BG, fg=SIGNAL, font=("Sans", 8, "bold")).pack(anchor="w")

        self.badges = tk.Frame(header, bg=BG)
        self.badges.pack(side="right")
        self.video_badge = self._badge(self.badges, "VIDEO")
        self.hid_badge = self._badge(self.badges, "HID")
        self.control_badge = self._badge(self.badges, "CONTROL")

        stage = tk.Frame(self.root, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        stage.pack(fill="both", expand=True, padx=16)
        self.image_label = tk.Label(
            stage,
            text="Waiting for a fresh HDMI frame…",
            bg="#020405",
            fg=MUTED,
            font=("Sans", 12),
        )
        self.image_label.pack(fill="both", expand=True, padx=8, pady=8)

        mode_strip = tk.Frame(self.root, bg=SURFACE_RAISED, padx=16, pady=8)
        mode_strip.pack(fill="x", padx=16, pady=(8, 0))
        tk.Label(
            mode_strip,
            text="CAPTURE OUTPUT",
            bg=SURFACE_RAISED,
            fg=TEXT,
            font=("Sans", 9, "bold"),
        ).pack(side="left")
        self.mode_var = tk.StringVar(self.root)
        self.mode_box = ttk.Combobox(
            mode_strip,
            textvariable=self.mode_var,
            state="disabled",
            width=34,
            font=("Sans", 9),
        )
        self.mode_box.pack(side="left", padx=(10, 12))
        self.mode_box.bind("<<ComboboxSelected>>", self._select_video_mode)
        self.mode_detail = tk.Label(
            mode_strip,
            text="Validated profiles load from the gateway · target timing is selected manually",
            bg=SURFACE_RAISED,
            fg=MUTED,
            font=("Sans", 9),
        )
        self.mode_detail.pack(side="left")

        controls = tk.Frame(self.root, bg=BG, padx=16, pady=12)
        controls.pack(fill="x")
        self.arm_button = ttk.Button(controls, text="ARM TARGET CONTROL", style="Arm.TButton", command=self._arm)
        self.arm_button.pack(side="left")
        self.disarm_button = ttk.Button(controls, text="DISARM", style="Disarm.TButton", command=self._disarm)
        self.disarm_button.pack(side="left", padx=(8, 0))
        self.desktop_button = ttk.Button(controls, text="RETURN TO DESKTOP", style="Noob.TButton", command=self._return_to_desktop)
        self.desktop_button.pack(side="left", padx=(8, 0))
        self.fullscreen_button = ttk.Button(controls, text="FULL SCREEN", style="Noob.TButton", command=self._toggle_fullscreen)
        self.fullscreen_button.pack(side="right")
        self.pin_button = ttk.Button(controls, text="UNPIN", style="Noob.TButton", command=self._toggle_pin)
        self.pin_button.pack(side="right", padx=(0, 8))

        footer = tk.Frame(self.root, bg=SURFACE_RAISED, padx=16, pady=8)
        footer.pack(fill="x")
        self.message = tk.Label(footer, text="Connecting to the local gateway…", bg=SURFACE_RAISED, fg=MUTED, font=("Sans", 10))
        self.message.pack(side="left")
        tk.Label(
            footer,
            text="Ctrl+Alt+Esc returns input locally · Super+N safely closes or opens this console",
            bg=SURFACE_RAISED,
            fg=MUTED,
            font=("Sans", 9),
        ).pack(side="right")
        self._set_buttons()

    def _badge(self, parent: tk.Widget, label: str) -> tuple[tk.Label, tk.Label]:
        frame = tk.Frame(parent, bg=BG, padx=8)
        frame.pack(side="left")
        dot = tk.Label(frame, text="●", bg=BG, fg=MUTED, font=("Sans", 10))
        dot.pack(side="left")
        text = tk.Label(frame, text=f"{label} · WAITING", bg=BG, fg=MUTED, font=("Sans", 9, "bold"))
        text.pack(side="left", padx=(5, 0))
        return dot, text

    def _set_badge(self, badge: tuple[tk.Label, tk.Label], label: str, state: str, color: str) -> None:
        badge[0].configure(fg=color)
        badge[1].configure(text=f"{label} · {state}", fg=color)

    def _offer(self, event: tuple[str, Any]) -> None:
        if event[0] == "frame":
            with self.frame_lock:
                self.latest_frame = event[1]
            return
        if event[0] in {"action", "action_error", "mode_action", "mode_action_error"}:
            while not self.stop_event.is_set():
                try:
                    self.events.put(event, timeout=0.1)
                    return
                except queue.Full:
                    continue
            return
        try:
            self.events.put_nowait(event)
        except queue.Full:
            # Status/error samples are disposable.  Never evict an older
            # action result: losing it would leave action_inflight latched and
            # could strand an operator between arm and disarm states.
            pass

    def _poll_loop(self) -> None:
        next_status = 0.0
        next_modes = 0.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now >= next_status:
                try:
                    self._offer(("status", self.client.status()))
                except LocalConsoleError as exc:
                    self._offer(("error", exc.code))
                next_status = now + self.STATUS_INTERVAL
            if now >= next_modes:
                try:
                    self._offer(("modes", self.client.video_modes()))
                except LocalConsoleError as exc:
                    self._offer(("error", exc.code))
                next_modes = now + 5.0
            try:
                self._offer(("frame", self.client.frame()))
            except LocalConsoleError as exc:
                if exc.code not in {"video_unavailable", "gateway_unavailable"}:
                    self._offer(("error", exc.code))
            self.stop_event.wait(self.FRAME_INTERVAL)

    def _drain_events(self) -> None:
        if self.stop_event.is_set():
            return
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self._apply_status(payload)
                elif kind == "modes":
                    self._apply_modes(payload)
                elif kind == "action":
                    self.action_inflight = False
                    self._apply_status(payload)
                    if self.hide_after_disarm:
                        self.hide_after_disarm = False
                        self.root.iconify()
                elif kind == "action_error":
                    self.action_inflight = False
                    self.hide_after_disarm = False
                    self._show_error(payload)
                elif kind == "mode_action":
                    self.action_inflight = False
                    self._apply_status(payload)
                    self.message.configure(
                        text="Capture output changed and a fresh video generation is active.",
                        fg=HEALTHY,
                    )
                elif kind == "mode_action_error":
                    self.action_inflight = False
                    self._show_error(payload)
                elif kind == "error":
                    self._show_error(payload)
        except queue.Empty:
            pass
        with self.frame_lock:
            latest_frame, self.latest_frame = self.latest_frame, None
        if latest_frame is not None:
            self._apply_frame(latest_frame)
        self._set_buttons()
        self.root.after(40, self._drain_events)

    def _apply_frame(self, data: bytes) -> None:
        try:
            from PIL import Image, ImageTk

            with Image.open(BytesIO(data)) as source:
                source.load()
                frame = source.convert("RGB")
            width = max(320, self.image_label.winfo_width() - 4)
            height = max(180, self.image_label.winfo_height() - 4)
            frame.thumbnail((width, height), Image.Resampling.BILINEAR)
            canvas = Image.new("RGB", (width, height), "#020405")
            canvas.paste(frame, ((width - frame.width) // 2, (height - frame.height) // 2))
            self.photo = ImageTk.PhotoImage(canvas)
            self.image_label.configure(image=self.photo, text="")
        except Exception:
            self._show_error("frame_invalid")

    def _apply_status(self, state: ViewState) -> None:
        self.current_state = state
        self._set_badge(self.video_badge, "VIDEO", "LIVE" if state.video_ready else "WAITING", HEALTHY if state.video_ready else WARN)
        hid_ready = state.serial_ready and state.keyboard_ready and state.pointer_ready
        self._set_badge(self.hid_badge, "HID", "READY" if hid_ready else "WAITING", HEALTHY if hid_ready else WARN)
        if state.local_armed and state.exclusive_grab:
            self._set_badge(self.control_badge, "CONTROL", "LOCAL", SIGNAL)
            self.message.configure(text="Target control is armed. Ctrl+Alt+Esc returns the keyboard and trackball to the uConsole.", fg=SIGNAL)
        elif state.remote_control_active:
            self._set_badge(self.control_badge, "CONTROL", "REMOTE", SIGNAL)
            self.message.configure(text="A remote operator owns input. Local video remains live and read-only.", fg=MUTED)
        elif state.release_required:
            self._set_badge(self.control_badge, "CONTROL", "RECOVERY", DANGER)
            self.message.configure(text="Input release is not yet confirmed. Local arming is blocked.", fg=DANGER)
        elif state.arm_allowed:
            self._set_badge(self.control_badge, "CONTROL", "AVAILABLE", HEALTHY)
            self.message.configure(text="Video is live. Arm target control when you are ready to leave the uConsole desktop.", fg=MUTED)
        else:
            self._set_badge(self.control_badge, "CONTROL", "UNAVAILABLE", WARN)
            self.message.configure(text="Waiting for the video, UART, keyboard, and trackball proof layers.", fg=WARN)
        self._sync_mode_selection(state.active_mode_id)
        self._update_mode_detail(state)

    def _apply_modes(self, catalog: VideoModeCatalog) -> None:
        self.video_modes = catalog.modes
        self.mode_display_to_id = {
            mode.display_label: mode.mode_id for mode in self.video_modes
        }
        self.mode_box.configure(values=tuple(self.mode_display_to_id))
        self._sync_mode_selection(catalog.active_mode_id)

    def _sync_mode_selection(self, active_mode_id: str | None) -> None:
        if active_mode_id is None:
            return
        for mode in self.video_modes:
            if mode.mode_id == active_mode_id:
                self.mode_var.set(mode.display_label)
                return

    def _update_mode_detail(self, state: ViewState) -> None:
        if (
            state.requested_signal is not None
            and state.negotiated_signal is not None
            and state.requested_signal != state.negotiated_signal
        ):
            requested = state.requested_signal
            negotiated = state.negotiated_signal
            self.mode_detail.configure(
                text=(
                    f"Requested {requested[0]}×{requested[1]} @ {requested[2]} · "
                    f"negotiated {negotiated[0]}×{negotiated[1]} @ {negotiated[2]}"
                ),
                fg=WARN,
            )
        elif state.video_state in {"switching", "rolling_back"}:
            self.mode_detail.configure(text="Capture transition in progress…", fg=WARN)
        elif not state.source_timing_detectable:
            self.mode_detail.configure(
                text="Target timing is not detectable · choose capture output manually",
                fg=MUTED,
            )
        else:
            self.mode_detail.configure(text="Validated gateway capture output", fg=MUTED)

    def _show_error(self, code: str) -> None:
        messages = {
            "token_unavailable": "Local authentication is unavailable.",
            "gateway_unavailable": "The local gateway is unavailable.",
            "status_unavailable": "Gateway status is unavailable.",
            "video_unavailable": "Waiting for a fresh HDMI frame.",
            "frame_invalid": "The latest HDMI frame could not be decoded.",
            "lease_busy": "A remote operator currently owns target input.",
            "local_input_unavailable": "The uConsole keyboard or trackball is unavailable.",
            "local_input_disabled": "Appliance-local input is disabled in gateway configuration.",
            "arm_unconfirmed": "Arming was not confirmed; a fail-safe local disarm was requested.",
            "release_unconfirmed": "The gateway could not confirm that input was released.",
            "video_modes_unavailable": "Capture-output profiles are unavailable.",
            "invalid_video_mode_request": "The capture-output request was rejected locally.",
            "video_mode_unconfirmed": "The profile response was lost; refreshing authoritative video status.",
            "video_mode_stale": "Capture output changed elsewhere; the profile list will refresh.",
            "video_mode_switching": "Another capture-output transition is in progress.",
            "video_mode_invalid": "That capture-output profile is not validated on this appliance.",
            "control_active": "Disarm local or remote target control before changing capture output.",
            "video_mode_mismatch": "The device negotiated a different mode; the gateway attempted rollback.",
            "video_frame_dimensions_mismatch": "The captured frame did not match the requested mode; rollback was attempted.",
            "video_mode_probe_failed": "The capture device could not confirm its negotiated mode.",
            "video_mode_timeout": "The new capture output did not become ready before rollback.",
            "video_mode_rollback_failed": "Capture rollback also failed; choose the safe profile when the device returns.",
            "video_capture_failed": "The selected capture output failed; the gateway attempted rollback.",
            "operation_in_progress": "Wait for the current arm, disarm, or capture-output action to finish before closing.",
        }
        self.message.configure(text=messages.get(code, "The requested local-console action failed safely."), fg=DANGER)

    def _set_buttons(self) -> None:
        state = self.current_state
        armed = bool(state and state.local_armed)
        self.arm_button.configure(state="normal" if state and state.arm_allowed and not self.action_inflight else "disabled")
        self.disarm_button.configure(state="normal" if armed and not self.action_inflight else "disabled")
        self.desktop_button.configure(state="disabled" if self.action_inflight else "normal")
        mode_enabled = bool(
            state
            and state.mode_change_allowed
            and self.video_modes
            and not self.action_inflight
            and not self.closing
        )
        self.mode_box.configure(state="readonly" if mode_enabled else "disabled")

    def _action(self, name: str) -> None:
        if self.action_inflight or self.closing:
            return
        self.action_inflight = True
        self._set_buttons()

        def run() -> None:
            try:
                action = self.client.arm if name == "arm" else self.client.disarm
                result = self.action_gate.run(action)
                if result is not None:
                    self._offer(("action", result))
            except LocalConsoleError as exc:
                self._offer(("action_error", exc.code))

        threading.Thread(target=run, daemon=True, name=f"noob-local-{name}").start()

    def _arm(self) -> None:
        self._action("arm")

    def _disarm(self) -> None:
        self._action("disarm")

    def _select_video_mode(self, _event: Any = None) -> None:
        state = self.current_state
        mode_id = self.mode_display_to_id.get(self.mode_var.get())
        if (
            state is None
            or mode_id is None
            or mode_id == state.active_mode_id
            or not state.mode_change_allowed
            or self.action_inflight
            or self.closing
        ):
            self._sync_mode_selection(state.active_mode_id if state else None)
            return
        self.action_inflight = True
        self._set_buttons()

        def run() -> None:
            try:
                result = self.action_gate.run(
                    lambda: self.client.set_video_mode(
                        mode_id, state.video_generation
                    )
                )
                if result is not None:
                    self._offer(("mode_action", result))
            except LocalConsoleError as exc:
                self._offer(("mode_action_error", exc.code))

        threading.Thread(
            target=run,
            daemon=True,
            name="noob-local-video-mode",
        ).start()

    def _return_to_desktop(self) -> None:
        # Always confirm a local disarm before hiding.  This remains safe when
        # a remote controller owns the HTTP lease, and protects against a
        # stale status sample that has not yet observed local arming.
        if self.action_inflight or self.closing:
            return
        self.hide_after_disarm = True
        self._action("disarm")

    def _toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)
        self.fullscreen_button.configure(text="WINDOW" if self.fullscreen else "FULL SCREEN")

    def _leave_fullscreen(self) -> None:
        if self.fullscreen:
            self.fullscreen = False
            self.root.attributes("-fullscreen", False)
            self.fullscreen_button.configure(text="FULL SCREEN")

    def _toggle_pin(self) -> None:
        self.pinned = not self.pinned
        self.root.attributes("-topmost", self.pinned)
        self.pin_button.configure(text="UNPIN" if self.pinned else "PIN")

    def _close(self) -> None:
        if self.closing:
            return
        if self.action_inflight:
            # Keep the viewer visible until the authenticated mutation result
            # is known.  This avoids abandoning a server-side capture
            # transition or racing an arm request with the final disarm.
            self._show_error("operation_in_progress")
            return
        self.closing = True
        try:
            self.action_gate.close(self.client.disarm)
        except LocalConsoleError as exc:
            # Fail closed: when the gateway cannot confirm a release, keep the
            # viewer visible so the operator sees the recovery instruction and
            # can use Ctrl+Alt+Esc.  A later close remains retryable.
            self.closing = False
            self._show_error(exc.code)
            return
        self.stop_event.set()
        self.root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="N.O.O.B appliance-local target viewer")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY)
    args = parser.parse_args()
    try:
        origin = validate_loopback_gateway(args.gateway)
        token = load_local_token()
    except (ValueError, LocalConsoleError) as exc:
        code = exc.code if isinstance(exc, LocalConsoleError) else "gateway_invalid"
        raise SystemExit(f"N.O.O.B local console could not start ({code})") from None

    root = tk.Tk(className="NoobLocalConsole")
    NoobLocalConsole(root, GatewayClient(origin, token))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
