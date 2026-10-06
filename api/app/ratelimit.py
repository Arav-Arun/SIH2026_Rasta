"""Per-caller rate limits on the writes that are cheap to send and costly to take."""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import Depends, Request

from app.errors import ApiError
from app.scope import WorkspaceScope, require_workspace_scope

#: bucket -> (requests allowed, per this many seconds)
LIMITS: dict[str, tuple[int, float]] = {
    "incident.create": (30, 60.0),
    "attachment.issue": (30, 60.0),
    "device.register": (10, 60.0),
    "tracking_grant.issue": (20, 60.0),
    # A phone that was offline for an hour uploads its backlog in bursts of
    # twenty points; this allows the whole 2,000-point queue in two minutes.
    "telemetry.batch": (120, 60.0),
    "push.subscribe": (10, 60.0),
    # Each one reaches a third-party push service.
    "push.test": (3, 60.0),
    "route_plan.create": (60, 60.0),
    "risk.recompute": (6, 60.0),
    # Generous: someone in trouble may press it more than once.
    "sos.raise": (10, 60.0),
}


class RateLimiter:
    """A sliding window of request times per (bucket, key)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, bucket: str, key: str, limit: int, window: float) -> float | None:
        """Record one request; return seconds to wait if it is over the limit."""

        now = self._clock()
        with self._lock:
            times = self._hits.setdefault((bucket, key), deque())
            while times and now - times[0] >= window:
                times.popleft()
            if len(times) >= limit:
                return max(0.0, window - (now - times[0]))
            times.append(now)
            return None


def _refuse(retry_after: float) -> ApiError:
    return ApiError(
        429,
        "rate_limited",
        "Too many requests of this kind. Wait and try again.",
        details={"retry_after_seconds": math.ceil(retry_after)},
        headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
    )


def _check(request: Request, bucket: str, key: str) -> None:
    limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return
    limit, window = LIMITS[bucket]
    wait = limiter.hit(bucket, key, limit, window)
    if wait is not None:
        raise _refuse(wait)


def rate_limit(bucket: str) -> Callable[..., object]:
    """A dependency limiting one verified caller's use of `bucket`."""

    if bucket not in LIMITS:
        raise ValueError(f"unknown rate-limit bucket {bucket!r}")

    async def dependency(
        request: Request,
        scope: WorkspaceScope = Depends(require_workspace_scope),
    ) -> None:
        _check(request, bucket, scope.profile_id)

    return dependency


def rate_limit_credential(request: Request, bucket: str, credential: str) -> None:
    """Limit by a credential's hash, for callers identified by a device token."""

    _check(request, bucket, hashlib.sha256(credential.encode()).hexdigest())


__all__ = ["LIMITS", "RateLimiter", "rate_limit", "rate_limit_credential"]
