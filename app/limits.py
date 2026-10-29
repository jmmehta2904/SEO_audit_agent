"""Per-instance audit attempt limits; ASGI peer addresses are trusted by the app."""

import asyncio
import math
import time
from collections import deque


class RateLimitExceeded(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after


class SlidingWindowLimiter:
    def __init__(self, *, limit=5, window_seconds=3600, max_clients=4096, clock=time.monotonic):
        self.limit, self.window_seconds, self.max_clients = limit, window_seconds, max_clients
        self.clock = clock
        self._entries = {}
        self._lock = asyncio.Lock()

    async def check(self, peer: str):
        async with self._lock:
            now = self.clock()
            cutoff = now - self.window_seconds
            for key, entries in list(self._entries.items()):
                while entries and entries[0] <= cutoff:
                    entries.popleft()
                if not entries:
                    del self._entries[key]
            entries = self._entries.get(peer)
            if entries is None:
                if len(self._entries) >= self.max_clients:
                    raise RateLimitExceeded(self.window_seconds)
                entries = self._entries[peer] = deque()
            if len(entries) >= self.limit:
                raise RateLimitExceeded(max(1, math.ceil(entries[0] + self.window_seconds - now)))
            entries.append(now)
