"""In-process request limits.

Counters live in this process's memory. That is correct while the API runs as a
single process (the same assumption as the quote cache, ADR-011) and is the reason
the limits reset on restart. Moving to several API processes means moving these
counters to a shared store (ADR-016).
"""

import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import HTTPException, status

from app.core.config import settings

_MAX_TRACKED_KEYS = 50_000


class RateLimitExceeded(HTTPException):
    def __init__(self, retry_after: float, detail: str = "Too many requests") -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers={"Retry-After": str(max(1, int(retry_after + 0.999)))},
        )


class SlidingWindowLimiter:
    """Allows ``limit`` events per ``window`` seconds for each key."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, window: float) -> deque[float]:
        events = self._events[key]
        cutoff = self._clock() - window
        while events and events[0] <= cutoff:
            events.popleft()
        return events

    def retry_after(self, key: str, limit: int, window: float) -> float:
        """Seconds until another event is allowed; 0 when one is allowed now."""
        events = self._prune(key, window)
        if len(events) < limit:
            if not events:
                self._events.pop(key, None)
            return 0.0
        return max(0.0, events[len(events) - limit] + window - self._clock())

    def record(self, key: str) -> None:
        if len(self._events) >= _MAX_TRACKED_KEYS and key not in self._events:
            self._evict()
        self._events[key].append(self._clock())

    def hit(self, key: str, limit: int, window: float) -> float:
        """Record an event if allowed. Returns 0, or the wait in seconds if refused."""
        wait = self.retry_after(key, limit, window)
        if wait == 0.0:
            self.record(key)
        return wait

    def clear(self, key: str) -> None:
        self._events.pop(key, None)

    def reset(self) -> None:
        self._events.clear()

    def _evict(self) -> None:
        # Drop the keys whose latest event is oldest. Only reached under a flood of
        # distinct keys, where forgetting old ones is the safe direction for memory.
        oldest = sorted(self._events, key=lambda k: self._events[k][-1] if self._events[k] else 0)
        for key in oldest[: _MAX_TRACKED_KEYS // 10]:
            del self._events[key]


class ConcurrencyLimiter:
    """Caps how many operations one key may have in flight."""

    def __init__(self) -> None:
        self._active: dict[str, int] = defaultdict(int)

    @asynccontextmanager
    async def slot(self, key: str, limit: int) -> AsyncIterator[None]:
        if settings.rate_limit_enabled and self._active[key] >= limit:
            raise RateLimitExceeded(
                5, detail=f"Too many runs in progress (limit {limit}); wait for one to finish"
            )
        self._active[key] += 1
        try:
            yield
        finally:
            self._active[key] -= 1
            if self._active[key] <= 0:
                del self._active[key]

    def reset(self) -> None:
        self._active.clear()


limiter = SlidingWindowLimiter()
run_slots = ConcurrencyLimiter()
