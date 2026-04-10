"""
Async response cache with TTL and request deduplication.

Prevents duplicate in-flight requests for the same key.
Concurrent callers for the same cache key share a single fetch.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from .types import Err, ErrorSeverity, Ok, RPCError, Result

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(slots=True)
class _CacheEntry:
    """Internal cache entry with value and expiry."""

    value: Any
    expires_at: float
    created_at: float = field(default_factory=time.monotonic)

    @property
    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at


class ResponseCache:
    """Async cache with TTL expiry and request deduplication.

    Example::

        cache = ResponseCache()

        async def fetch_balance():
            return Ok(await rpc.get_balance(...))

        result = await cache.get_or_fetch("balance:addr", fetch_balance, ttl_seconds=5.0)
    """

    __slots__ = ("_cache", "_in_flight", "_lock", "_hit_count")

    def __init__(self) -> None:
        self._cache: dict[str, _CacheEntry] = {}
        self._in_flight: dict[str, asyncio.Future[Any]] = {}
        self._lock = asyncio.Lock()
        self._hit_count: int = 0

    @property
    def hit_count(self) -> int:
        """Total cache hits since creation."""
        return self._hit_count

    @property
    def size(self) -> int:
        """Number of entries in the cache."""
        return len(self._cache)

    async def get_or_fetch(
        self,
        key: str,
        fetch_fn: Callable[[], Any],
        ttl_seconds: float = 5.0,
    ) -> Result[T, RPCError]:
        """Return cached value if fresh, otherwise call *fetch_fn*.

        Concurrent calls for the same key share a single in-flight request.
        """
        is_owner = False
        async with self._lock:
            entry = self._cache.get(key)
            if entry is not None and not entry.is_expired:
                self._hit_count += 1
                return Ok(entry.value)

            if key in self._in_flight:
                future = self._in_flight[key]
            else:
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._in_flight[key] = future
                is_owner = True

        if not is_owner:
            return await future  # type: ignore[return-value]

        try:
            result = await fetch_fn()
        except Exception as exc:
            async with self._lock:
                self._in_flight.pop(key, None)
            err = Err(RPCError(
                code="CACHE_FETCH_FAILED",
                message=f"Cache fetch for '{key}' failed: {exc}",
                severity=ErrorSeverity.MEDIUM,
                source="cache",
                details={"key": key, "error": str(exc)},
            ))
            if not future.done():
                future.set_result(err)
            return err

        if isinstance(result, Err):
            async with self._lock:
                self._in_flight.pop(key, None)
            if not future.done():
                future.set_result(result)
            return result

        value = result.value if isinstance(result, Ok) else result
        now = time.monotonic()
        async with self._lock:
            self._cache[key] = _CacheEntry(
                value=value,
                expires_at=now + ttl_seconds,
                created_at=now,
            )
            self._in_flight.pop(key, None)
        ok_result: Result[T, RPCError] = Ok(value)
        if not future.done():
            future.set_result(ok_result)
        return ok_result

    def invalidate(self, key: str) -> None:
        """Remove a key from the cache."""
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Remove all entries from the cache."""
        self._cache.clear()
