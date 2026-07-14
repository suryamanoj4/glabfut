import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    def __init__(self, capacity: int = 256, ttl: int = 7200):
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._capacity = capacity
        self._ttl = ttl

    def get(self, key: str) -> Any | None:
        if key not in self._cache:
            return None
        expiry, value = self._cache[key]
        if time.monotonic() > expiry:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        while len(self._cache) >= self._capacity:
            self._cache.popitem(last=False)
        self._cache[key] = (time.monotonic() + self._ttl, value)

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def clear(self) -> None:
        self._cache.clear()
