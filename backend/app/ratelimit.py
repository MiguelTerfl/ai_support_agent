"""Per-IP request limit (in-memory sliding window). Enough for a single instance."""

from __future__ import annotations

import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """Returns (allowed, seconds_until_retry)."""
        now = time.monotonic() if now is None else now
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False, max(1, int(self.window - (now - q[0])) + 1)
        q.append(now)
        if len(self._hits) > 10_000:  # prevent unbounded growth
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window]:
                self._hits.pop(k, None)
        return True, 0
