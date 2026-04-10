"""Tests for ResponseCache."""

import asyncio

import pytest

from solana_rpc_resilient import Ok, ResponseCache


@pytest.mark.asyncio
async def test_cache_hit():
    cache = ResponseCache()
    call_count = 0

    async def fetch():
        nonlocal call_count
        call_count += 1
        return Ok("data")

    r1 = await cache.get_or_fetch("key", fetch, ttl_seconds=10.0)
    r2 = await cache.get_or_fetch("key", fetch, ttl_seconds=10.0)
    assert r1.is_ok and r2.is_ok
    assert r1.unwrap() == "data"
    assert call_count == 1  # second call was a cache hit
    assert cache.hit_count == 1


@pytest.mark.asyncio
async def test_cache_miss_after_expiry():
    cache = ResponseCache()
    call_count = 0

    async def fetch():
        nonlocal call_count
        call_count += 1
        return Ok(call_count)

    await cache.get_or_fetch("key", fetch, ttl_seconds=0.01)
    await asyncio.sleep(0.02)
    r2 = await cache.get_or_fetch("key", fetch, ttl_seconds=0.01)
    assert r2.unwrap() == 2
    assert call_count == 2


@pytest.mark.asyncio
async def test_request_dedup():
    cache = ResponseCache()
    call_count = 0

    async def slow_fetch():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return Ok("result")

    results = await asyncio.gather(
        cache.get_or_fetch("key", slow_fetch, ttl_seconds=10.0),
        cache.get_or_fetch("key", slow_fetch, ttl_seconds=10.0),
        cache.get_or_fetch("key", slow_fetch, ttl_seconds=10.0),
    )
    assert all(r.is_ok for r in results)
    assert call_count == 1  # only one actual fetch


@pytest.mark.asyncio
async def test_invalidate():
    cache = ResponseCache()
    await cache.get_or_fetch("key", lambda: asyncio.coroutine(lambda: Ok(1))(), ttl_seconds=10.0)
    cache.invalidate("key")
    assert cache.size == 0


@pytest.mark.asyncio
async def test_clear():
    cache = ResponseCache()

    async def fetch():
        return Ok("x")

    await cache.get_or_fetch("a", fetch, ttl_seconds=10.0)
    await cache.get_or_fetch("b", fetch, ttl_seconds=10.0)
    assert cache.size == 2
    cache.clear()
    assert cache.size == 0
