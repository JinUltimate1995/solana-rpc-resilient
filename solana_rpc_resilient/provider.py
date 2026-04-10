"""
RPC provider registry — tracks health, latency, and weight.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_PROVIDER_RECOVERY_SECONDS = 10.0


@dataclass(slots=True)
class RPCProvider:
    """Runtime state for a single RPC provider."""

    name: str
    url: str
    weight: int = 1
    tier: str = "free"
    is_healthy: bool = True
    latency_ms: float = 0.0
    consecutive_failures: int = 0
    unhealthy_since: float = 0.0

    def __repr__(self) -> str:
        status = "healthy" if self.is_healthy else "unhealthy"
        return f"RPCProvider({self.name!r}, {status}, weight={self.weight})"


class ProviderRegistry:
    """Manages the set of available RPC providers.

    Example::

        registry = ProviderRegistry([
            {"name": "helius", "url": "https://...", "weight": 3},
            {"name": "public", "url": "https://api.mainnet-beta.solana.com", "weight": 1},
        ])
    """

    __slots__ = ("_providers", "_lock")

    def __init__(self, providers: list[dict[str, Any]]) -> None:
        self._providers: dict[str, RPCProvider] = {}
        self._lock = asyncio.Lock()
        for cfg in providers:
            name = cfg["name"]
            self._providers[name] = RPCProvider(
                name=name,
                url=cfg["url"],
                weight=cfg.get("weight", 1),
                tier=cfg.get("tier", "free"),
            )

    def get_healthy(self) -> list[RPCProvider]:
        """Return all healthy providers."""
        return [p for p in self._providers.values() if p.is_healthy]

    def get_all(self) -> list[RPCProvider]:
        """Return all providers."""
        return list(self._providers.values())

    def get_by_name(self, name: str) -> RPCProvider | None:
        """Lookup a provider by name."""
        return self._providers.get(name)

    def mark_healthy(self, name: str) -> None:
        """Set provider as healthy, reset failure counter."""
        provider = self._providers.get(name)
        if provider:
            provider.is_healthy = True
            provider.consecutive_failures = 0

    def mark_unhealthy(self, name: str) -> None:
        """Set provider as unhealthy."""
        provider = self._providers.get(name)
        if provider:
            provider.is_healthy = False
            provider.unhealthy_since = time.monotonic()
            logger.warning("provider %s marked unhealthy", name)

    def recover_stale_unhealthy(
        self,
        recovery_seconds: float = _PROVIDER_RECOVERY_SECONDS,
    ) -> list[RPCProvider]:
        """Re-enable providers that have been unhealthy longer than *recovery_seconds*."""
        now = time.monotonic()
        recovered: list[RPCProvider] = []
        for p in self._providers.values():
            if not p.is_healthy and p.unhealthy_since > 0:
                elapsed = now - p.unhealthy_since
                if elapsed >= recovery_seconds:
                    p.is_healthy = True
                    p.consecutive_failures = 0
                    p.unhealthy_since = 0.0
                    recovered.append(p)
                    logger.info(
                        "provider %s auto-recovered after %.1fs",
                        p.name,
                        elapsed,
                    )
        return recovered

    def update_latency(self, name: str, latency_ms: float) -> None:
        """Update the latency measurement for a provider."""
        provider = self._providers.get(name)
        if provider:
            provider.latency_ms = latency_ms

    def to_dict(self) -> dict[str, dict]:
        """Serialise registry state for diagnostics."""
        return {
            name: {
                "weight": p.weight,
                "tier": p.tier,
                "is_healthy": p.is_healthy,
                "latency_ms": round(p.latency_ms, 2),
                "consecutive_failures": p.consecutive_failures,
            }
            for name, p in self._providers.items()
        }
