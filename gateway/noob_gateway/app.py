"""aiohttp application for authenticated OOB video and HID control."""

from __future__ import annotations

import asyncio
import json
import math
from importlib import resources
from typing import Any

from aiohttp import web

from .auth import bearer_middleware, load_token
from .config import GatewayConfig
from .control_lease import ControlLease, LeaseBusy, LeaseInvalid, LeaseReleaseRequired
from .local_input import (
    LocalInputDisabled,
    LocalInputManager,
    LocalInputUnavailable,
)
from .models import InputValidationError, validate_input_command
from .rate_limit import TokenBucket
from .serial_link import (
    SerialBusy,
    SerialInterrupted,
    SerialLink,
    SerialLinkError,
    SerialNack,
    SerialTimeout,
    SerialUnavailable,
)
from .video import (
    TooManyViewers,
    V4L2Capture,
    VideoModeInvalid,
    VideoModeStale,
    VideoSwitchFailed,
    VideoSwitchInProgress,
    VideoUnavailable,
)


class StrictJSONError(ValueError):
    pass


def _reject_constant(_value: str) -> None:
    raise StrictJSONError("non-standard JSON number")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJSONError("duplicate JSON key")
        result[key] = value
    return result


async def _strict_json(request: web.Request, max_bytes: int) -> dict[str, Any]:
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(
            text='{"ok":false,"error":"content_type"}', content_type="application/json"
        )
    length = request.content_length
    if length is not None and length > max_bytes:
        raise web.HTTPRequestEntityTooLarge(max_size=max_bytes, actual_size=length)
    raw = await request.read()
    if not raw or len(raw) > max_bytes:
        if len(raw) > max_bytes:
            raise web.HTTPRequestEntityTooLarge(max_size=max_bytes, actual_size=len(raw))
        raise web.HTTPBadRequest(
            text='{"ok":false,"error":"bad_json"}', content_type="application/json"
        )
    try:
        obj = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError):
        raise web.HTTPBadRequest(
            text='{"ok":false,"error":"bad_json"}', content_type="application/json"
        ) from None
    if not isinstance(obj, dict):
        raise web.HTTPBadRequest(
            text='{"ok":false,"error":"bad_json"}', content_type="application/json"
        )
    return obj


def _require_empty_object(obj: dict[str, Any]) -> None:
    if obj:
        raise web.HTTPBadRequest(
            text='{"ok":false,"error":"bad_field"}', content_type="application/json"
        )


def _video_mode_request(obj: dict[str, Any]) -> tuple[str, int]:
    if set(obj) != {"mode_id", "expected_generation"}:
        raise web.HTTPBadRequest(
            text='{"ok":false,"error":"bad_field"}', content_type="application/json"
        )
    mode_id = obj["mode_id"]
    generation = obj["expected_generation"]
    if not isinstance(mode_id, str):
        raise web.HTTPBadRequest(
            text='{"ok":false,"error":"bad_field"}', content_type="application/json"
        )
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or not 0 <= generation <= 2_147_483_647
    ):
        raise web.HTTPBadRequest(
            text='{"ok":false,"error":"bad_range"}', content_type="application/json"
        )
    return mode_id, generation


def _lease_header(request: web.Request) -> str:
    values = request.headers.getall("X-NOOB-Lease", [])
    if len(values) != 1:
        raise web.HTTPConflict(
            text='{"ok":false,"error":"lease_required"}', content_type="application/json"
        )
    value = values[0]
    if len(value) != 32 or any(char not in "0123456789abcdef" for char in value):
        raise web.HTTPConflict(
            text='{"ok":false,"error":"lease_invalid"}', content_type="application/json"
        )
    return value


@web.middleware
async def _error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        return web.json_response({"ok": False, "error": "internal_error"}, status=500)


async def _security_headers(_request: web.Request, response: web.StreamResponse) -> None:
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    )


class GatewayRuntime:
    def __init__(
        self,
        config: GatewayConfig,
        serial_link: Any,
        video: Any,
        local_input: Any | None = None,
    ) -> None:
        self.config = config
        self.serial = serial_link
        self.video = video
        self.lease = ControlLease(config.limits.lease_ttl_seconds)
        self.rate_limiter = TokenBucket(
            config.limits.input_rate_per_second, config.limits.input_burst
        )
        self._lease_task: asyncio.Task[None] | None = None
        self._closing = asyncio.Event()
        self.control_gate = asyncio.Lock()
        self._local_lease_id: str | None = None
        self._video_switching = False
        self.local_input = local_input or LocalInputManager(
            config.local_input,
            self.submit_local_input,
            self.release_local_input,
        )

    async def start(self) -> None:
        await self.serial.start()
        await self.video.start()
        await self.local_input.start()
        self._lease_task = asyncio.create_task(self._lease_watchdog(), name="noob-control-lease")

    async def stop(self) -> None:
        self._closing.set()
        if self._lease_task is not None:
            self._lease_task.cancel()
            await asyncio.gather(self._lease_task, return_exceptions=True)
            self._lease_task = None
        await self.local_input.stop()
        await self.lease.force_clear()
        await self.serial.stop()
        await self.video.stop()

    async def _lease_watchdog(self) -> None:
        try:
            while not self._closing.is_set():
                await asyncio.sleep(0.25)
                async with self.control_gate:
                    await self.release_expired_input()
        except asyncio.CancelledError:
            raise

    async def release_expired_input(self) -> bool:
        """Drain a latched lease expiry before another controller can act.

        Callers hold ``control_gate``. A failed serial release re-latches the
        obligation, so a later claim or input cannot silently bypass it.
        """

        if not await self.lease.expire_if_needed():
            return True
        self._local_lease_id = None
        success, _result = await self.attempt_emergency_release()
        return success

    async def attempt_emergency_release(self) -> tuple[bool, Any | None]:
        """Release HID state and preserve the obligation across every failure."""

        try:
            result = await self.serial.emergency_release()
        except asyncio.CancelledError:
            await self.lease.mark_release_required()
            raise
        except Exception:
            await self.lease.mark_release_required()
            return False, None
        return True, result

    async def invalidate_indeterminate_input(self) -> None:
        """Disarm a lease after a command whose execution result is unknown."""

        async with self.control_gate:
            self._local_lease_id = None
            await self.lease.force_clear()
            await self.lease.mark_release_required()
            await self.release_expired_input()

    async def submit_local_input(self, candidate: dict[str, Any]) -> bool:
        """Send one built-in control event through the public validation path.

        The local keyboard and trackball share the same exclusive lease as
        HTTP/Electron controllers.  They never preempt an active controller.
        """

        try:
            command = validate_input_command(
                candidate, max_type_chars=self.config.limits.max_type_chars
            )
        except InputValidationError:
            return False
        if not await self.rate_limiter.allow(1):
            return False

        async with self.control_gate:
            if self._video_switching:
                return False
            if not await self.release_expired_input():
                return False
            if self._local_lease_id is not None:
                try:
                    await self.lease.renew(self._local_lease_id)
                except LeaseInvalid:
                    self._local_lease_id = None
            if self._local_lease_id is None:
                try:
                    self._local_lease_id = await self.lease.claim()
                except (LeaseBusy, LeaseReleaseRequired):
                    return False
            generation = self.serial.generation

        try:
            await self.serial.send_command(command, expected_generation=generation)
        except (SerialNack, SerialInterrupted, SerialBusy):
            return False
        except (SerialTimeout, SerialUnavailable, SerialLinkError):
            await self.invalidate_indeterminate_input()
            return False
        return True

    async def release_local_input(self) -> bool:
        """Release only a lease currently owned by the built-in controls."""

        async with self.control_gate:
            lease_id, self._local_lease_id = self._local_lease_id, None
            if lease_id is None:
                return True
            if not await self.release_expired_input():
                return False
            try:
                await self.lease.release(lease_id)
            except LeaseInvalid:
                # Expiry may already have released this local session.  Never
                # release a newer lease that could belong to another operator.
                return True
            success, _result = await self.attempt_emergency_release()
            return success

    async def arm_local_input(self) -> None:
        """Arm only while no HTTP/Electron controller owns the lease."""

        async with self.control_gate:
            if self._video_switching:
                raise VideoSwitchInProgress("video mode switch is in progress")
            if not await self.release_expired_input():
                raise LocalInputUnavailable("serial release is pending")
            snapshot = await self.lease.snapshot()
            if snapshot.active and self._local_lease_id is None:
                raise LeaseBusy("another controller owns the lease")
            await self.local_input.arm()

    async def disarm_local_input(self, *, reason: str = "operator") -> bool:
        return await self.local_input.disarm(reason=reason)

    async def switch_video_mode(
        self, mode_id: str, expected_generation: int
    ) -> dict[str, Any]:
        """Reserve the eyes-only transition without racing a new HID owner."""

        async with self.control_gate:
            if self._video_switching:
                raise VideoSwitchInProgress("video mode switch is in progress")
            if not await self.release_expired_input():
                raise LeaseReleaseRequired("input release is still required")
            lease = await self.lease.snapshot()
            local_status = self.local_input.status
            if (
                lease.active
                or self.local_input.armed
                or local_status.get("exclusive_grab") is True
            ):
                raise LeaseBusy("control is active")
            self._video_switching = True
        try:
            # Degraded video remains recoverable through this path; only API
            # liveness and absence of HID ownership are preconditions.
            return await self.video.select_mode(mode_id, expected_generation)
        finally:
            async with self.control_gate:
                self._video_switching = False


RUNTIME_KEY: web.AppKey[GatewayRuntime] = web.AppKey("noob_runtime", GatewayRuntime)


def _runtime(request: web.Request) -> GatewayRuntime:
    return request.app[RUNTIME_KEY]


async def _root(_request: web.Request) -> web.Response:
    content = resources.files("noob_gateway").joinpath("static/index.html").read_text(encoding="utf-8")
    return web.Response(text=content, content_type="text/html")


async def _static(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if name not in ("app.js", "style.css"):
        raise web.HTTPNotFound()
    content = resources.files("noob_gateway").joinpath(f"static/{name}").read_text(encoding="utf-8")
    return web.Response(
        text=content,
        content_type="text/javascript" if name.endswith(".js") else "text/css",
    )


async def _health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _ready(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    ready = bool(
        runtime.serial.ready
        and runtime.video.ready
        and (not runtime.local_input.enabled or runtime.local_input.ready)
    )
    return web.json_response({"ok": ready}, status=200 if ready else 503)


async def _status(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    async with runtime.control_gate:
        lease = await runtime.lease.snapshot()
        if lease.release_required:
            await runtime.release_expired_input()
            lease = await runtime.lease.snapshot()
    return web.json_response(
        {
            "ok": True,
            "serial": runtime.serial.status,
            "video": runtime.video.status,
            "local_input": runtime.local_input.status,
            "control": {
                "active": lease.active,
                "expires_in_ms": lease.expires_in_ms,
                "release_required": lease.release_required,
            },
        }
    )


async def _claim(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    _require_empty_object(await _strict_json(request, runtime.config.limits.max_body_bytes))
    async with runtime.control_gate:
        if runtime._video_switching:
            return web.json_response(
                {"ok": False, "error": "video_mode_switching"}, status=409
            )
        if not await runtime.release_expired_input():
            return web.json_response({"ok": False, "error": "serial_unavailable"}, status=503)
        # Local arming is itself an ownership boundary.  Do not leave a window
        # between EVIOCGRAB succeeding and the first physical event creating
        # the local lease: a raw HTTP client must not be able to claim control
        # during that interval.
        local_status = runtime.local_input.status
        if runtime.local_input.armed or local_status.get("exclusive_grab") is True:
            return web.json_response(
                {"ok": False, "error": "local_input_armed"}, status=409
            )
        try:
            lease_id = await runtime.lease.claim()
        except LeaseBusy:
            return web.json_response({"ok": False, "error": "lease_busy"}, status=409)
        except LeaseReleaseRequired:
            if not await runtime.release_expired_input():
                return web.json_response({"ok": False, "error": "serial_unavailable"}, status=503)
            lease_id = await runtime.lease.claim()
    return web.json_response(
        {
            "ok": True,
            "lease": lease_id,
            "ttl_ms": int(runtime.lease.ttl_seconds * 1000),
        }
    )


async def _renew(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    _require_empty_object(await _strict_json(request, runtime.config.limits.max_body_bytes))
    lease_id = _lease_header(request)
    async with runtime.control_gate:
        if not await runtime.release_expired_input():
            return web.json_response({"ok": False, "error": "serial_unavailable"}, status=503)
        try:
            await runtime.lease.renew(lease_id)
        except LeaseInvalid:
            if not await runtime.release_expired_input():
                return web.json_response(
                    {"ok": False, "error": "serial_unavailable"}, status=503
                )
            return web.json_response({"ok": False, "error": "lease_invalid"}, status=409)
    return web.json_response({"ok": True, "ttl_ms": int(runtime.lease.ttl_seconds * 1000)})


async def _release(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    _require_empty_object(await _strict_json(request, runtime.config.limits.max_body_bytes))
    lease_id = _lease_header(request)
    async with runtime.control_gate:
        if not await runtime.release_expired_input():
            return web.json_response({"ok": False, "error": "serial_unavailable"}, status=503)
        try:
            await runtime.lease.release(lease_id)
        except LeaseInvalid:
            if not await runtime.release_expired_input():
                return web.json_response(
                    {"ok": False, "error": "serial_unavailable"}, status=503
                )
            return web.json_response({"ok": False, "error": "lease_invalid"}, status=409)
        success, _result = await runtime.attempt_emergency_release()
        if not success:
            return web.json_response({"ok": False, "error": "serial_unavailable"}, status=503)
    return web.json_response({"ok": True, "released": True})


async def _input(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    lease_id = _lease_header(request)
    obj = await _strict_json(request, runtime.config.limits.max_body_bytes)
    try:
        command = validate_input_command(obj, max_type_chars=runtime.config.limits.max_type_chars)
    except InputValidationError as exc:
        return web.json_response({"ok": False, "error": exc.code}, status=400)
    cost = 1
    if command["op"] == "type":
        cost = max(1, math.ceil(len(command["text"]) / 16))
    if not await runtime.rate_limiter.allow(cost):
        return web.json_response({"ok": False, "error": "rate_limited"}, status=429)
    async with runtime.control_gate:
        if not await runtime.release_expired_input():
            return web.json_response({"ok": False, "error": "serial_unavailable"}, status=503)
        try:
            await runtime.lease.validate(lease_id)
        except LeaseInvalid:
            if not await runtime.release_expired_input():
                return web.json_response(
                    {"ok": False, "error": "serial_unavailable"}, status=503
                )
            return web.json_response({"ok": False, "error": "lease_invalid"}, status=409)
        generation = runtime.serial.generation
    try:
        result = await runtime.serial.send_command(command, expected_generation=generation)
    except SerialNack as exc:
        return web.json_response(
            {"ok": False, "error": exc.code, "released": exc.released}, status=502
        )
    except SerialTimeout:
        await runtime.invalidate_indeterminate_input()
        return web.json_response({"ok": False, "error": "serial_timeout"}, status=504)
    except SerialInterrupted:
        return web.json_response({"ok": False, "error": "interrupted"}, status=409)
    except SerialBusy:
        return web.json_response({"ok": False, "error": "serial_busy"}, status=429)
    except (SerialUnavailable, SerialLinkError):
        await runtime.invalidate_indeterminate_input()
        return web.json_response({"ok": False, "error": "serial_unavailable"}, status=503)
    return web.json_response({"ok": True, "result": result})


async def _emergency_release(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    _require_empty_object(await _strict_json(request, runtime.config.limits.max_body_bytes))
    await runtime.disarm_local_input(reason="emergency_api")
    async with runtime.control_gate:
        runtime._local_lease_id = None
        await runtime.lease.force_clear()
        success, result = await runtime.attempt_emergency_release()
        if not success:
            return web.json_response({"ok": False, "error": "serial_unavailable"}, status=503)
    return web.json_response({"ok": True, "released": True, "pico": result})


async def _local_input_arm(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    _require_empty_object(await _strict_json(request, runtime.config.limits.max_body_bytes))
    try:
        await runtime.arm_local_input()
    except LocalInputDisabled:
        return web.json_response({"ok": False, "error": "local_input_disabled"}, status=409)
    except LocalInputUnavailable:
        return web.json_response(
            {"ok": False, "error": "local_input_unavailable"}, status=503
        )
    except LeaseBusy:
        return web.json_response({"ok": False, "error": "lease_busy"}, status=409)
    except VideoSwitchInProgress:
        return web.json_response(
            {"ok": False, "error": "video_mode_switching"}, status=409
        )
    return web.json_response({"ok": True, "local_input": runtime.local_input.status})


async def _local_input_disarm(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    _require_empty_object(await _strict_json(request, runtime.config.limits.max_body_bytes))
    if not await runtime.disarm_local_input(reason="operator"):
        return web.json_response(
            {
                "ok": False,
                "error": "release_unconfirmed",
                "local_input": runtime.local_input.status,
            },
            status=503,
        )
    return web.json_response({"ok": True, "local_input": runtime.local_input.status})


async def _video_modes(request: web.Request) -> web.Response:
    return web.json_response(_runtime(request).video.mode_catalog())


async def _video_mode(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    mode_id, expected_generation = _video_mode_request(
        await _strict_json(request, runtime.config.limits.max_body_bytes)
    )
    try:
        status = await runtime.switch_video_mode(mode_id, expected_generation)
    except VideoModeInvalid:
        return web.json_response(
            {"ok": False, "error": "video_mode_invalid"}, status=400
        )
    except VideoModeStale:
        return web.json_response(
            {"ok": False, "error": "video_mode_stale"}, status=409
        )
    except VideoSwitchInProgress:
        return web.json_response(
            {"ok": False, "error": "video_mode_switching"}, status=409
        )
    except LeaseBusy:
        return web.json_response(
            {"ok": False, "error": "control_active"}, status=409
        )
    except LeaseReleaseRequired:
        return web.json_response(
            {"ok": False, "error": "release_unconfirmed"}, status=503
        )
    except VideoSwitchFailed as exc:
        return web.json_response(
            {
                "ok": False,
                "error": exc.code,
                "rolled_back": exc.rolled_back,
                "video": runtime.video.status,
            },
            status=503,
        )
    except VideoUnavailable:
        return web.json_response(
            {"ok": False, "error": "video_unavailable"}, status=503
        )
    return web.json_response({"ok": True, "video": status})


async def _frame(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    snapshot = runtime.video.latest()
    if snapshot is None or not runtime.video.ready:
        return web.json_response({"ok": False, "error": "video_unavailable"}, status=503)
    return web.Response(
        body=snapshot.data,
        content_type="image/jpeg",
        headers={
            "X-NOOB-Frame-Sequence": str(snapshot.sequence),
            "X-NOOB-Video-Generation": str(snapshot.generation),
            "Cache-Control": "no-store",
        },
    )


async def _stream(request: web.Request) -> web.StreamResponse:
    runtime = _runtime(request)
    try:
        await runtime.video.acquire_viewer()
    except TooManyViewers:
        return web.json_response({"ok": False, "error": "viewer_limit"}, status=503)
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "multipart/x-mixed-replace; boundary=noobframe",
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )
    try:
        await response.prepare(request)
        sequence = -1
        while True:
            snapshot = await runtime.video.wait_for_frame(sequence, timeout=5.0)
            if snapshot is None:
                continue
            sequence = snapshot.sequence
            part = (
                b"--noobframe\r\nContent-Type: image/jpeg\r\n"
                b"X-NOOB-Video-Generation: "
                + str(snapshot.generation).encode("ascii")
                + b"\r\nContent-Length: "
                + str(len(snapshot.data)).encode("ascii")
                + b"\r\n\r\n"
                + snapshot.data
                + b"\r\n"
            )
            await response.write(part)
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        await runtime.video.release_viewer()
    return response


async def _startup(app: web.Application) -> None:
    await app[RUNTIME_KEY].start()


async def _cleanup(app: web.Application) -> None:
    await app[RUNTIME_KEY].stop()


def create_app(
    config: GatewayConfig,
    *,
    token: str | None = None,
    local_console_token: str | None = None,
    serial_link: Any | None = None,
    video: Any | None = None,
    local_input: Any | None = None,
) -> web.Application:
    """Build an app; injected serial/video objects are intended for deterministic tests."""

    expected_token = token if token is not None else load_token(config.auth.token_file)
    expected_local_token = local_console_token
    if expected_local_token is None and config.auth.local_token_file is not None:
        expected_local_token = load_token(config.auth.local_token_file)
    runtime = GatewayRuntime(
        config,
        serial_link if serial_link is not None else SerialLink(config.serial),
        video if video is not None else V4L2Capture(config.video),
        local_input,
    )
    app = web.Application(
        middlewares=[
            _error_middleware,
            bearer_middleware(expected_token, expected_local_token),
        ],
        client_max_size=config.limits.max_body_bytes,
    )
    app[RUNTIME_KEY] = runtime
    app.on_response_prepare.append(_security_headers)
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    app.router.add_get("/", _root)
    app.router.add_get("/static/{name}", _static)
    app.router.add_get("/healthz", _health)
    app.router.add_get("/readyz", _ready)
    app.router.add_get("/api/v1/status", _status)
    app.router.add_post("/api/v1/control/claim", _claim)
    app.router.add_post("/api/v1/control/renew", _renew)
    app.router.add_post("/api/v1/control/release", _release)
    app.router.add_post("/api/v1/input", _input)
    app.router.add_post("/api/v1/release-all", _emergency_release)
    app.router.add_post("/api/v1/local-input/arm", _local_input_arm)
    app.router.add_post("/api/v1/local-input/disarm", _local_input_disarm)
    app.router.add_get("/api/v1/video/modes", _video_modes)
    app.router.add_post("/api/v1/video/mode", _video_mode)
    app.router.add_get("/api/v1/frame.jpg", _frame)
    app.router.add_get("/api/v1/stream.mjpeg", _stream)
    return app
