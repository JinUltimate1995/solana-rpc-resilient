"""
Token-bucket rate limiter.

Limits outbound API calls to a configured rate (tokens/second)
with burst capacity. Self-tunes rate based on success/failure streaks.
"""

from __future__ import annotations

import asyncio
import logging
import time

from .types import Err, ErrorSeverity, Ok, RPCError, Result

logger = logging.getLogger(__name__)


class TokenBucketRateLimiter:
    """Async token-bucket rate limiter with adaptive headroom control.

    On 429 responses, the rate automatically decreases and a brief
    cooldown is applied.  After a streak of successes, the rate climbs
    back toward the target.

    Example::

        limiter = TokenBucketRateLimiter(rate=10.0, burst=20.0)

        result = await limiter.acquire(timeout=5.0)
        if result.is_ok:
            # make your API call
            limiter.record_success()

        # on 429:
        limiter.record_rate_limit(retry_after=2.0)
    """

    __slots__ = (
        "_rate",
        "_ceiling_rate",
        "_target_rate",
        "_min_rate",
        "_burst",
        "_tokens",
        "_last_refill",
        "_cooldown_until",
        "_increase_step",
        "_decrease_factor",
        "_success_streak",
        "_acquired",
        "_timeouts",
        "_rate_limit_hits",
        "_last_backoff_seconds",
        "_lock",
    )

    def __init__(
        self,
        rate: float = 10.0,
        burst: float = 20.0,
        *,
        min_rate: float | None = None,
        target_utilization: float = 0.85,
        increase_step: float = 0.05,
        decrease_factor: float = 0.75,
    ) -> None:
        """
        Args:
            rate: Safe ceiling for tokens added per second.
            burst: Maximum tokens (bucket capacity).
            min_rate: Lower bound for adaptive backoff.
            target_utilization: Target share of safe ceiling to approach.
            increase_step: Fraction of ceiling restored after stable success.
            decrease_factor: Multiplicative rate cut on 429.
        """
        safe_rate = max(float(rate), 0.01)
        self._ceiling_rate = safe_rate
        self._target_rate = max(0.01, safe_rate * max(0.5, min(target_utilization, 0.95)))
        self._min_rate = max(0.05, min_rate if min_rate is not None else safe_rate * 0.25)
        self._rate = max(self._min_rate, safe_rate * 0.7)
        self._burst = max(float(burst), 1.0)
        self._tokens = min(self._burst, max(self._target_rate, 1.0))
        self._last_refill = time.monotonic()
        self._cooldown_until = 0.0
        self._increase_step = max(0.01, float(increase_step))
        self._decrease_factor = min(max(float(decrease_factor), 0.2), 0.95)
        self._success_streak = 0
        self._acquired = 0
        self._timeouts = 0
        self._rate_limit_hits = 0
        self._last_backoff_seconds = 0.0
        self._lock = asyncio.Lock()

    # -- Properties ----------------------------------------------------------

    @property
    def available_tokens(self) -> float:
        """Current token count (approximate without lock)."""
        self._refill()
        return self._tokens

    @property
    def current_rate(self) -> float:
        """Current adaptive token refill rate."""
        return self._rate

    @property
    def target_rate(self) -> float:
        """Steady-state target rate (< configured safe ceiling)."""
        return self._target_rate

    @property
    def safe_ceiling_rate(self) -> float:
        """Configured safe ceiling for this API."""
        return self._ceiling_rate

    # -- Public API ----------------------------------------------------------

    async def acquire(self, timeout: float = 5.0) -> Result[None, RPCError]:
        """Acquire one token.  Wait up to *timeout* seconds.

        Returns ``Ok(None)`` on success, ``Err`` on timeout.
        """
        deadline = time.monotonic() + timeout

        while True:
            async with self._lock:
                now = time.monotonic()
                if now < self._cooldown_until:
                    wait = min(self._cooldown_until - now, max(0.0, deadline - now))
                else:
                    wait = 0.0
                self._refill()
                if wait <= 0 and self._tokens >= 1.0:
                    self._tokens -= 1.0
                    self._acquired += 1
                    return Ok(None)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._timeouts += 1
                return Err(RPCError(
                    code="RATE_LIMIT_EXCEEDED",
                    message=f"Rate limit: no token available within {timeout}s",
                    severity=ErrorSeverity.MEDIUM,
                    source="rate_limiter",
                    details={"timeout": timeout, "rate": self._rate},
                ))

            wait = min(
                max(wait, 1.0 / max(self._rate, 0.01)),
                remaining,
            )
            await asyncio.sleep(wait)

    def record_success(self) -> None:
        """Gently climb toward the target rate after stable success."""
        self._success_streak += 1
        if self._success_streak < 8:
            return
        self._success_streak = 0
        self._refill()
        if self._rate < self._target_rate:
            self._rate = min(
                self._target_rate,
                self._rate + (self._ceiling_rate * self._increase_step),
            )

    def record_rate_limit(self, retry_after: float | None = None) -> None:
        """Back off on 429 and pause briefly before the next acquire."""
        self._success_streak = 0
        self._rate_limit_hits += 1
        self._refill()
        self._rate = max(self._min_rate, self._rate * self._decrease_factor)
        backoff = (
            float(retry_after)
            if retry_after is not None
            else max(1.0, 2.0 / max(self._rate, 0.05))
        )
        self._last_backoff_seconds = backoff
        self._cooldown_until = max(self._cooldown_until, time.monotonic() + backoff)
        self._tokens = min(self._tokens, 0.0)

    def diagnostics(self) -> dict[str, float | int]:
        """Current adaptive limiter state for health endpoints."""
        self._refill()
        return {
            "available_tokens": round(self._tokens, 2),
            "current_rate": round(self._rate, 3),
            "target_rate": round(self._target_rate, 3),
            "safe_ceiling_rate": round(self._ceiling_rate, 3),
            "utilization_pct": round(
                (self._rate / max(self._ceiling_rate, 0.01)) * 100, 1,
            ),
            "acquired": self._acquired,
            "timeouts": self._timeouts,
            "rate_limit_hits": self._rate_limit_hits,
            "last_backoff_s": round(self._last_backoff_seconds, 2),
        }

    # -- Internal helpers ----------------------------------------------------

    def _refill(self) -> None:
        """Add tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last_refill = now
