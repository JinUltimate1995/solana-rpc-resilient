"""
Weighted provider rotation with health awareness.
"""

from __future__ import annotations

import logging
import random
import time

from .provider import ProviderRegistry, RPCProvider
from .types import Err, ErrorSeverity, Ok, RPCError, Result

logger = logging.getLogger(__name__)


class ProviderRotator:
    """Selects the next RPC provider using weighted random from healthy set.

    Auto-recovers providers that have been unhealthy past the recovery
    timeout — prevents permanent dead-lock when using a single provider.
    """

    __slots__ = ("_registry",)

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    def get_next(self) -> Result[RPCProvider, RPCError]:
        """Pick a healthy provider via weighted random selection."""
        healthy = self._registry.get_healthy()

        if not healthy:
            recovered = self._registry.recover_stale_unhealthy()
            if recovered:
                healthy = recovered
                logger.info(
                    "providers auto-recovered: %s",
                    [p.name for p in recovered],
                )

        if not healthy:
            # Last resort: force-recover the provider unhealthy the longest
            all_providers = self._registry.get_all()
            unhealthy = [p for p in all_providers if not p.is_healthy]
            if unhealthy:
                unhealthy.sort(key=lambda p: p.unhealthy_since)
                oldest = unhealthy[0]
                elapsed = (
                    time.monotonic() - oldest.unhealthy_since
                    if oldest.unhealthy_since > 0
                    else 0
                )
                oldest.is_healthy = True
                oldest.consecutive_failures = 0
                oldest.unhealthy_since = 0.0
                healthy = [oldest]
                logger.warning(
                    "force-recovered provider %s (unhealthy %.1fs)",
                    oldest.name,
                    elapsed,
                )

        if not healthy:
            return Err(RPCError(
                code="NO_HEALTHY_PROVIDERS",
                message="All RPC providers are unhealthy",
                severity=ErrorSeverity.CRITICAL,
                source="provider_rotator",
            ))

        weights = [p.weight for p in healthy]
        selected = random.choices(healthy, weights=weights, k=1)[0]  # noqa: S311
        return Ok(selected)

    def report_success(self, provider_name: str) -> None:
        """Record a successful call to a provider."""
        self._registry.mark_healthy(provider_name)

    def report_failure(self, provider_name: str) -> None:
        """Record a failed call; mark unhealthy after 3 consecutive failures."""
        provider = self._registry.get_by_name(provider_name)
        if provider is None:
            return
        provider.consecutive_failures += 1
        if provider.consecutive_failures >= 3:
            self._registry.mark_unhealthy(provider_name)
