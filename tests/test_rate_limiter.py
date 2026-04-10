"""Tests for the TokenBucketRateLimiter."""

import asyncio

import pytest

from solana_rpc_resilient import TokenBucketRateLimiter


@pytest.mark.asyncio
async def test_acquire_success():
    limiter = TokenBucketRateLimiter(rate=100.0, burst=10.0)
    result = await limiter.acquire(timeout=1.0)
    assert result.is_ok


@pytest.mark.asyncio
async def test_acquire_multiple():
    limiter = TokenBucketRateLimiter(rate=100.0, burst=5.0)
    for _ in range(5):
        result = await limiter.acquire(timeout=1.0)
        assert result.is_ok


@pytest.mark.asyncio
async def test_acquire_timeout():
    limiter = TokenBucketRateLimiter(rate=0.1, burst=1.0)
    # Drain the single token
    r1 = await limiter.acquire(timeout=1.0)
    assert r1.is_ok
    # Next should time out at 0.1 tokens/sec with 0.05s timeout
    r2 = await limiter.acquire(timeout=0.05)
    assert r2.is_err
    assert r2.unwrap_err().code == "RATE_LIMIT_EXCEEDED"


def test_record_rate_limit_decreases_rate():
    limiter = TokenBucketRateLimiter(rate=10.0, burst=20.0)
    initial_rate = limiter.current_rate
    limiter.record_rate_limit(retry_after=1.0)
    assert limiter.current_rate < initial_rate


def test_record_success_increases_rate():
    limiter = TokenBucketRateLimiter(rate=10.0, burst=20.0)
    limiter.record_rate_limit()  # drop rate first
    low_rate = limiter.current_rate
    for _ in range(20):
        limiter.record_success()
    assert limiter.current_rate >= low_rate


def test_diagnostics():
    limiter = TokenBucketRateLimiter(rate=10.0, burst=20.0)
    diag = limiter.diagnostics()
    assert "current_rate" in diag
    assert "available_tokens" in diag
    assert "acquired" in diag
    assert diag["rate_limit_hits"] == 0
