"""Bearer-token loading and constant-time request authentication."""

from __future__ import annotations

import hmac
import re
import stat
from pathlib import Path

from aiohttp import web


class AuthConfigError(ValueError):
    pass


LOCAL_CONSOLE_ROUTES = frozenset(
    {
        ("GET", "/api/v1/status"),
        ("GET", "/api/v1/frame.jpg"),
        ("GET", "/api/v1/stream.mjpeg"),
        ("GET", "/api/v1/video/modes"),
        ("POST", "/api/v1/video/mode"),
        ("POST", "/api/v1/local-input/arm"),
        ("POST", "/api/v1/local-input/disarm"),
        ("GET", "/api/v1/environment-camera/status"),
        ("POST", "/api/v1/environment-camera/state"),
        ("GET", "/api/v1/environment-camera/frame.jpg"),
        ("GET", "/api/v1/environment-camera/stream.mjpeg"),
        ("GET", "/api/v1/environment-camera/storage"),
        ("POST", "/api/v1/environment-camera/snapshot"),
        ("POST", "/api/v1/environment-camera/clip"),
    }
)

_LOCAL_CAMERA_DYNAMIC_ROUTES = (
    ("GET", re.compile(r"^/api/v1/environment-camera/jobs/j_[0-9a-f]{32}$")),
    (
        "POST",
        re.compile(r"^/api/v1/environment-camera/jobs/j_[0-9a-f]{32}/stop$"),
    ),
    ("GET", re.compile(r"^/api/v1/environment-camera/storage/m_[0-9a-f]{32}$")),
    (
        "GET",
        re.compile(r"^/api/v1/environment-camera/storage/m_[0-9a-f]{32}/content$"),
    ),
    (
        "GET",
        re.compile(
            r"^/api/v1/environment-camera/storage/m_[0-9a-f]{32}/frames/(?:0|[1-9][0-9]{0,2})\.jpg$"
        ),
    ),
)


def _local_console_route_allowed(method: str, path: str) -> bool:
    if (method, path) in LOCAL_CONSOLE_ROUTES:
        return True
    return any(
        method == allowed_method and pattern.fullmatch(path)
        for allowed_method, pattern in _LOCAL_CAMERA_DYNAMIC_ROUTES
    )


def load_token(path: str) -> str:
    token_path = Path(path)
    mode = stat.S_IMODE(token_path.stat().st_mode)
    if mode & 0o077:
        raise AuthConfigError(
            "bearer token file must not be accessible by group or others"
        )
    token = token_path.read_text(encoding="ascii").strip()
    if not 32 <= len(token) <= 256 or any(char.isspace() for char in token):
        raise AuthConfigError(
            "bearer token must be 32-256 non-whitespace ASCII characters"
        )
    return token


def bearer_middleware(
    expected_token: str, local_console_token: str | None = None
) -> web.middleware:
    if not 32 <= len(expected_token) <= 256:
        raise AuthConfigError("invalid in-memory bearer token")
    if local_console_token is not None:
        if not 32 <= len(local_console_token) <= 256:
            raise AuthConfigError("invalid in-memory local console token")
        if hmac.compare_digest(expected_token, local_console_token):
            raise AuthConfigError("local console token must be distinct")

    @web.middleware
    async def authenticate(request: web.Request, handler):
        if not request.path.startswith("/api/v1/"):
            return await handler(request)
        values = request.headers.getall("Authorization", [])
        authorized = False
        local_authorized = False
        if len(values) == 1 and values[0].startswith("Bearer "):
            candidate = values[0][7:]
            if len(candidate) <= 256:
                authorized = hmac.compare_digest(candidate, expected_token)
                if local_console_token is not None:
                    local_authorized = hmac.compare_digest(
                        candidate, local_console_token
                    )
        if local_authorized and not authorized:
            if _local_console_route_allowed(request.method, request.path):
                return await handler(request)
            raise web.HTTPForbidden(
                text='{"ok":false,"error":"insufficient_scope"}',
                content_type="application/json",
            )
        if not authorized:
            raise web.HTTPUnauthorized(
                text='{"ok":false,"error":"unauthorized"}',
                content_type="application/json",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await handler(request)

    return authenticate
