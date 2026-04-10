"""Tests for the CircuitBreaker."""

import pytest

from solana_rpc_resilient import CircuitBreaker, Ok, Err, RPCError
from solana_rpc_resilient.circuit_breaker import CircuitBreakerState
from solana_rpc_resilient.types import ErrorSeverity


@pytest.mark.asyncio
async def test_closed_on_success():
    breaker = CircuitBreaker("test", failure_threshold=3, recovery_seconds=0.1)
    assert breaker.state == CircuitBreakerState.CLOSED

    async def ok_fn():
        return Ok(42)

    result = await breaker.call(ok_fn)
    assert result.is_ok
    assert result.unwrap() == 42
    assert breaker.state == CircuitBreakerState.CLOSED


@pytest.mark.asyncio
async def test_opens_after_threshold():
    breaker = CircuitBreaker("test", failure_threshold=2, recovery_seconds=60.0)
    err = RPCError(code="FAIL", message="failed", severity=ErrorSeverity.MEDIUM)

    async def fail_fn():
        return Err(err)

    for _ in range(2):
        await breaker.call(fail_fn)

    assert breaker.state == CircuitBreakerState.OPEN


@pytest.mark.asyncio
async def test_open_rejects_calls():
    breaker = CircuitBreaker("test", failure_threshold=1, recovery_seconds=60.0)

    async def fail_fn():
        return Err(RPCError(code="FAIL", message="x"))

    await breaker.call(fail_fn)
    assert breaker.state == CircuitBreakerState.OPEN

    async def ok_fn():
        return Ok("should not run")

    result = await breaker.call(ok_fn)
    assert result.is_err
    assert result.unwrap_err().code == "CIRCUIT_OPEN"


@pytest.mark.asyncio
async def test_half_open_after_recovery():
    import asyncio

    breaker = CircuitBreaker("test", failure_threshold=1, recovery_seconds=0.05)

    async def fail_fn():
        return Err(RPCError(code="FAIL", message="x"))

    await breaker.call(fail_fn)
    assert breaker.state == CircuitBreakerState.OPEN

    await asyncio.sleep(0.06)

    async def ok_fn():
        return Ok("recovered")

    result = await breaker.call(ok_fn)
    assert result.is_ok
    assert breaker.state == CircuitBreakerState.CLOSED


@pytest.mark.asyncio
async def test_retriable_does_not_trip():
    breaker = CircuitBreaker("test", failure_threshold=1, recovery_seconds=60.0)
    rate_err = RPCError(code="RATE_LIMITED", message="429")

    async def rate_limited_fn():
        return Err(rate_err)

    for _ in range(5):
        result = await breaker.call(
            rate_limited_fn,
            is_retriable=lambda e: e.code == "RATE_LIMITED",
        )
        assert result.is_err

    # Should still be CLOSED — retriable errors don't count
    assert breaker.state == CircuitBreakerState.CLOSED


@pytest.mark.asyncio
async def test_bypass_breaker():
    breaker = CircuitBreaker("test", failure_threshold=1, recovery_seconds=60.0)

    async def fail_fn():
        return Err(RPCError(code="FAIL", message="x"))

    await breaker.call(fail_fn)
    assert breaker.state == CircuitBreakerState.OPEN

    async def ok_fn():
        return Ok("bypass")

    result = await breaker.call(ok_fn, bypass_breaker=True)
    assert result.is_ok
    assert result.unwrap() == "bypass"
