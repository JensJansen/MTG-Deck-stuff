"""
Thread-safe token-bucket rate limiter.

A single instance is shared by every worker thread in a node process, so the
node never exceeds Moxfield's per-IP request budget regardless of how the work
is split between discovery sweeps and deck fetches. This replaces V1's
per-request sleep, which could not coordinate across threads.
"""

import threading
import time


class RateLimiter:
    def __init__(self, rate_per_sec: float, burst: float | None = None) -> None:
        self.rate = rate_per_sec
        self.capacity = burst if burst is not None else rate_per_sec
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until one token is available, then consume it."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.rate
            time.sleep(wait)
