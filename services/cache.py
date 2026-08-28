from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from cachetools import TTLCache


@dataclass(slots=True)
class CacheStats:
    name: str
    size: int
    max_size: int
    ttl: int


class ManagedCache:
    def __init__(self, *, name: str, max_size: int, ttl: int) -> None:
        self.name = name
        self.ttl = ttl
        self._cache: TTLCache[Any, Any] = TTLCache(maxsize=max_size, ttl=ttl)
        self._lock = asyncio.Lock()

    async def get(self, key: Any, default: Any = None) -> Any:
        async with self._lock:
            return self._cache.get(key, default)

    async def set(self, key: Any, value: Any) -> None:
        async with self._lock:
            self._cache[key] = value

    async def delete(self, key: Any) -> bool:
        async with self._lock:
            if key not in self._cache:
                return False
            del self._cache[key]
            return True

    async def clear(self) -> int:
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    async def expire(self) -> int:
        async with self._lock:
            before = len(self._cache)
            self._cache.expire()
            return before - len(self._cache)

    async def stats(self) -> CacheStats:
        async with self._lock:
            self._cache.expire()
            return CacheStats(self.name, len(self._cache), self._cache.maxsize, self.ttl)


class CacheManager:
    def __init__(self) -> None:
        self._caches: dict[str, ManagedCache] = {
            "guild_settings": ManagedCache(name="guild_settings", max_size=250, ttl=600),
            "ticket_settings": ManagedCache(name="ticket_settings", max_size=250, ttl=600),
            "permissions": ManagedCache(name="permissions", max_size=1000, ttl=300),
            "profiles": ManagedCache(name="profiles", max_size=1000, ttl=300),
            "api": ManagedCache(name="api", max_size=500, ttl=120),
            "statistics": ManagedCache(name="statistics", max_size=500, ttl=60),
        }

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._caches)

    def get_cache(self, name: str) -> ManagedCache:
        if name not in self._caches:
            raise KeyError(f"Unknown cache: {name}")
        return self._caches[name]

    async def clear(self, name: str) -> int:
        return await self.get_cache(name).clear()

    async def clear_all(self) -> dict[str, int]:
        return {name: await cache.clear() for name, cache in self._caches.items()}

    async def expire_all(self) -> dict[str, int]:
        return {name: await cache.expire() for name, cache in self._caches.items()}

    async def stats(self) -> list[CacheStats]:
        return [await cache.stats() for cache in self._caches.values()]
