"""Single-controller deadman lease."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import secrets
import time


class LeaseBusy(RuntimeError):
    pass


class LeaseInvalid(RuntimeError):
    pass


class LeaseReleaseRequired(RuntimeError):
    """Raised when a new controller must wait for a pending HID release."""


@dataclass(frozen=True, slots=True)
class LeaseSnapshot:
    active: bool
    expires_in_ms: int
    release_required: bool


class ControlLease:
    def __init__(self, ttl_seconds: float, *, clock=time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._lease_id: str | None = None
        self._expires_at = 0.0
        self._release_required = False
        self._lock = asyncio.Lock()

    def _expired(self, now: float) -> bool:
        return self._lease_id is not None and now >= self._expires_at

    def _transition_expired(self, now: float) -> None:
        if self._expired(now):
            self._lease_id = None
            self._expires_at = 0.0
            self._release_required = True

    async def claim(self) -> str:
        """Create a lease only when no prior HID release remains pending."""

        async with self._lock:
            now = self._clock()
            self._transition_expired(now)
            if self._lease_id is not None:
                raise LeaseBusy("controller already active")
            if self._release_required:
                raise LeaseReleaseRequired("expired controller input must be released first")
            self._lease_id = secrets.token_hex(16)
            self._expires_at = now + self._ttl
            return self._lease_id

    async def renew(self, lease_id: str) -> None:
        async with self._lock:
            now = self._clock()
            self._transition_expired(now)
            if self._lease_id is None or not secrets.compare_digest(self._lease_id, lease_id):
                raise LeaseInvalid("invalid or expired controller lease")
            self._expires_at = now + self._ttl

    async def validate(self, lease_id: str) -> None:
        async with self._lock:
            now = self._clock()
            self._transition_expired(now)
            if self._lease_id is None or not secrets.compare_digest(self._lease_id, lease_id):
                raise LeaseInvalid("invalid or expired controller lease")

    async def release(self, lease_id: str) -> None:
        async with self._lock:
            self._transition_expired(self._clock())
            if self._lease_id is None or not secrets.compare_digest(self._lease_id, lease_id):
                raise LeaseInvalid("invalid controller lease")
            self._lease_id = None
            self._expires_at = 0.0

    async def force_clear(self) -> bool:
        async with self._lock:
            had_lease = self._lease_id is not None or self._release_required
            self._lease_id = None
            self._expires_at = 0.0
            self._release_required = False
            return had_lease

    async def expire_if_needed(self) -> bool:
        """Atomically consume one latched release obligation.

        Any method may notice expiry, but only this method clears the latch and
        returns ``True``. If the physical release fails, the caller must call
        :meth:`mark_release_required` before allowing a new controller.
        """

        async with self._lock:
            self._transition_expired(self._clock())
            release_required = self._release_required
            self._release_required = False
            return release_required

    async def mark_release_required(self) -> None:
        """Re-latch a release obligation after a failed physical release."""

        async with self._lock:
            self._lease_id = None
            self._expires_at = 0.0
            self._release_required = True

    async def snapshot(self) -> LeaseSnapshot:
        async with self._lock:
            now = self._clock()
            self._transition_expired(now)
            remaining = max(0.0, self._expires_at - now) if self._lease_id else 0.0
            return LeaseSnapshot(
                self._lease_id is not None,
                int(remaining * 1000),
                self._release_required,
            )

    @property
    def ttl_seconds(self) -> float:
        return self._ttl
