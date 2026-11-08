"""Tiny in-memory helpers. Fine for a single API instance (free-tier friendly)."""
import threading
import time
from collections import deque


class IpLimiter:
    """Sliding window: at most `max_calls` per `window` seconds per key."""

    def __init__(self, max_calls: int = 30, window: int = 60):
        self.max, self.window = max_calls, window
        self.hits: dict[str, deque] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self.lock:
            q = self.hits.setdefault(key, deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.max:
                return False
            q.append(now)
            if len(self.hits) > 5000:  # opportunistic cleanup
                for k in [k for k, v in self.hits.items() if not v or v[-1] <= now - self.window]:
                    self.hits.pop(k, None)
            return True


class TtlCache:
    def __init__(self, ttl: int = 180, max_items: int = 500):
        self.ttl, self.max = ttl, max_items
        self.data: dict[str, tuple[float, dict]] = {}
        self.lock = threading.Lock()

    def get(self, key: str):
        with self.lock:
            hit = self.data.get(key)
            if hit and hit[0] > time.time():
                return hit[1]
            self.data.pop(key, None)
            return None

    def set(self, key: str, value: dict):
        with self.lock:
            if len(self.data) >= self.max:
                now = time.time()
                for k in [k for k, v in self.data.items() if v[0] <= now] or list(self.data)[:50]:
                    self.data.pop(k, None)
            self.data[key] = (time.time() + self.ttl, value)
