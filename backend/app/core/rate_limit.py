"""In-process fixed-window API rate limiter (PR-C).

Independent from the outreach daily_send_limit: the API limiter guards
HTTP request volume; the outreach limit controls outbound messages. They
never interact.

Limitation: in-process state is per-process; it is NOT suitable for
horizontally scaled multi-instance deployments. No external service
(Redis etc.) is introduced in PR-C.
"""

from __future__ import annotations

import threading
import time
from typing import Callable


class RateLimiter:
    """Fixed-window per-key counter with an injectable clock.

    `now` must be a zero-argument callable returning monotonic seconds;
    tests inject a fake clock for determinism.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: int,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._now = now
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, int]] = {}

    def check(self, key: str) -> tuple[bool, int]:
        """Record one request for `key`.

        Returns (allowed, retry_after_seconds); retry_after is 0 when
        allowed and >= 1 when rejected (seconds until the window resets).
        """
        now = self._now()
        with self._lock:
            window_start, count = self._buckets.get(key, (0.0, 0))
            if now - window_start >= self.window_seconds:
                window_start, count = now, 0
            count += 1
            self._buckets[key] = (window_start, count)
            if count <= self.max_requests:
                return True, 0
            retry_after = int(self.window_seconds - (now - window_start))
            if retry_after < 1:
                retry_after = 1
            return False, retry_after

    def reset(self) -> None:
        """Clear all buckets (used by tests / explicit resets)."""
        with self._lock:
            self._buckets.clear()