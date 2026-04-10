"""
Circuit breaker — protects against cascading failures.

States:
  CLOSED    → normal operation, track failures
  OPEN      → reject all calls, wait for recovery
  HALF_OPEN → allow one probe call
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from typing import Any, TypeVar

from .types import Err, ErrorSeverity, Ok, RPCError, Result

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitBreakerState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker with CLOSED → OPEN → HALF_OPEN state transitions.

    Example::

        breaker = CircuitBreaker("helius", failure_threshold=5, recovery_seconds=60)

        result = await breaker.call(
            my_rpc_function,
            is_retriable=lambda err: err.code == "RATE_LIMITED",
        )
    """

    __slots__ = (
        "_name",
        "_failure_threshold",
        "_recovery_seconds",
        "_state",
        "_failure_count",
        "_last_failure_time",
        "_lock",
    )

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_seconds: float = 60.0,
    ) -> None:
        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._lock = asyncio.Lock()

    # -- Properties ----------------------------------------------------------

    @property
    def state(self) -> CircuitBreakerState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def last_failure_time(self) -> float:
        return self._last_failure_time

    # -- Public API ----------------------------------------------------------

    async def call(
        self,
        func: Any,
        *args: Any,
        is_retriable: Any | None = None,
        bypass_breaker: bool = False,
        **kwargs: Any,
    ) -> Result[T, RPCError]:
        """Execute *func* through the circuit breaker.

        Args:
            func: Async callable to execute.
            is_retriable: Optional callback ``(RPCError) -> bool``.  If it
                returns True for an error, the failure is **not** recorded
                against the circuit breaker (e.g., 429 rate-limits should
                not trip the breaker).
            bypass_breaker: If True, skip the OPEN state check and do not
                record failures/successes.  Used for emergency calls that
                must go through regardless of breaker state.
        """
        if not bypass_breaker:
            async with self._lock:
                if self._state == CircuitBreakerState.OPEN:
                    elapsed = time.monotonic() - self._last_failure_time
                    if elapsed >= self._recovery_seconds:
                        self._state = CircuitBreakerState.HALF_OPEN
                        logger.info(
                            "circuit breaker %s → HALF_OPEN after %.1fs",
                            self._name,
                            elapsed,
                        )
                    else:
                        return Err(RPCError(
                            code="CIRCUIT_OPEN",
                            message=f"Circuit breaker '{self._name}' is OPEN",
                            severity=ErrorSeverity.HIGH,
                            source="circuit_breaker",
                            details={
                                "breaker": self._name,
                                "recovery_remaining": round(
                                    self._recovery_seconds - elapsed, 1,
                                ),
                            },
                        ))

        try:
            result = await func(*args, **kwargs)
        except Exception as exc:
            if not bypass_breaker:
                await self._record_failure(str(exc))
            return Err(RPCError(
                code="CIRCUIT_CALL_FAILED",
                message=f"Call through '{self._name}' failed: {exc}",
                severity=ErrorSeverity.MEDIUM,
                source="circuit_breaker",
                details={"breaker": self._name, "error": str(exc)},
            ))

        if isinstance(result, Err):
            if is_retriable and is_retriable(result.error):
                return result
            if not bypass_breaker:
                await self._record_failure(result.error.message)
            return result

        if not bypass_breaker:
            await self._record_success()
        return result

    # -- Internal helpers ----------------------------------------------------

    async def _record_success(self) -> None:
        async with self._lock:
            if self._state == CircuitBreakerState.HALF_OPEN:
                logger.info("circuit breaker %s → CLOSED", self._name)
            self._failure_count = 0
            self._state = CircuitBreakerState.CLOSED

    async def _record_failure(self, reason: str) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitBreakerState.HALF_OPEN:
                self._state = CircuitBreakerState.OPEN
                logger.warning(
                    "circuit breaker %s re-opened: %s",
                    self._name,
                    reason,
                )
            elif self._failure_count >= self._failure_threshold:
                self._state = CircuitBreakerState.OPEN
                logger.warning(
                    "circuit breaker %s opened after %d failures: %s",
                    self._name,
                    self._failure_count,
                    reason,
                )
