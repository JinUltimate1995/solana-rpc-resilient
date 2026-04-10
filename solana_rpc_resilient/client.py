"""
High-level resilient Solana RPC client.

All calls flow through: rate limiter → circuit breaker → provider rotation → HTTP.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .cache import ResponseCache
from .circuit_breaker import CircuitBreaker
from .provider import ProviderRegistry
from .rate_limiter import TokenBucketRateLimiter
from .rotation import ProviderRotator
from .types import Err, ErrorSeverity, Ok, RPCError, Result

logger = logging.getLogger(__name__)

_MAX_PROVIDER_ROTATIONS = 4


class ResilientRPCClient:
    """Production-grade Solana RPC client with built-in resilience.

    Features:
        - Adaptive token-bucket rate limiting
        - Circuit breaker (isolates failing providers)
        - Weighted provider rotation with auto-failover on 429
        - Response caching with request deduplication
        - Auto-recovery for temporarily-down providers

    Example::

        client = ResilientRPCClient([
            {"name": "helius", "url": "https://mainnet.helius-rpc.com/?api-key=KEY", "weight": 3},
            {"name": "public", "url": "https://api.mainnet-beta.solana.com", "weight": 1},
        ])

        async with client:
            result = await client.get_balance("So111...")
            if result.is_ok:
                print(f"Balance: {result.unwrap()} lamports")
    """

    __slots__ = (
        "_rotator",
        "_breaker",
        "_rate_limiter",
        "_cache",
        "_http",
    )

    def __init__(
        self,
        providers: list[dict[str, Any]],
        *,
        rate_limit: float = 10.0,
        burst: float = 20.0,
        failure_threshold: int = 5,
        recovery_seconds: float = 60.0,
    ) -> None:
        """
        Args:
            providers: List of provider configs.  Each dict must have
                ``name`` and ``url``.  Optional: ``weight`` (int, default 1),
                ``tier`` (str, default "free").
            rate_limit: Maximum requests per second.
            burst: Burst capacity for the rate limiter.
            failure_threshold: Consecutive failures before circuit opens.
            recovery_seconds: Seconds before probing an open circuit.
        """
        registry = ProviderRegistry(providers)
        self._rotator = ProviderRotator(registry)
        self._breaker = CircuitBreaker(
            "rpc", failure_threshold=failure_threshold,
            recovery_seconds=recovery_seconds,
        )
        self._rate_limiter = TokenBucketRateLimiter(rate=rate_limit, burst=burst)
        self._cache = ResponseCache()
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> ResilientRPCClient:
        await self.startup()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.shutdown()

    async def startup(self) -> None:
        """Create the underlying HTTP client."""
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    async def shutdown(self) -> None:
        """Close the HTTP client."""
        if self._http:
            await self._http.aclose()
            self._http = None

    # -- Accessors -----------------------------------------------------------

    @property
    def rate_limiter(self) -> TokenBucketRateLimiter:
        return self._rate_limiter

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._breaker

    @property
    def cache(self) -> ResponseCache:
        return self._cache

    # -- Public RPC methods --------------------------------------------------

    async def get_balance(
        self,
        address: str,
        *,
        bypass_breaker: bool = False,
    ) -> Result[int, RPCError]:
        """Get SOL balance in lamports."""
        return await self._rpc_request(
            "getBalance", [address], bypass_breaker=bypass_breaker,
        )

    async def get_token_balance(
        self,
        owner_address: str,
        token_mint: str,
        *,
        bypass_breaker: bool = False,
    ) -> Result[int, RPCError]:
        """Get SPL token balance for an owner and mint.  Returns 0 if no account."""
        accounts_result = await self._rpc_request(
            "getTokenAccountsByOwner",
            [
                owner_address,
                {"mint": token_mint},
                {"encoding": "jsonParsed"},
            ],
            bypass_breaker=bypass_breaker,
        )
        if accounts_result.is_err:
            return accounts_result

        data = accounts_result.unwrap() or {}
        accounts = data.get("value", [])
        if not accounts:
            return Ok(0)

        total_balance = 0
        for acc in accounts:
            try:
                parsed = acc["account"]["data"]["parsed"]["info"]
                amount_str = parsed["tokenAmount"]["amount"]
                total_balance += int(amount_str)
            except (KeyError, TypeError, ValueError):
                pass

        return Ok(total_balance)

    async def get_account_info(
        self,
        address: str,
    ) -> Result[dict, RPCError]:
        """Get account info for an address."""
        return await self._rpc_request(
            "getAccountInfo",
            [address, {"encoding": "jsonParsed"}],
        )

    async def get_slot(self) -> Result[int, RPCError]:
        """Get the current slot."""
        return await self._rpc_request("getSlot", [])

    async def send_transaction(
        self,
        tx_bytes: str,
        *,
        bypass_breaker: bool = False,
        skip_preflight: bool = True,
    ) -> Result[str, RPCError]:
        """Submit a signed transaction (base64-encoded).

        Args:
            skip_preflight: If True (default), skip RPC preflight simulation.
        """
        opts: dict[str, Any] = {
            "encoding": "base64",
            "skipPreflight": skip_preflight,
            "preflightCommitment": "processed",
            "maxRetries": 0,
        }
        return await self._rpc_request(
            "sendTransaction",
            [tx_bytes, opts],
            bypass_breaker=bypass_breaker,
        )

    async def get_signatures_for_address(
        self,
        address: str,
        limit: int = 50,
        before: str | None = None,
        until: str | None = None,
    ) -> Result[list, RPCError]:
        """Get confirmed transaction signatures for an address."""
        opts: dict[str, Any] = {"limit": limit}
        if before:
            opts["before"] = before
        if until:
            opts["until"] = until
        return await self._rpc_request(
            "getSignaturesForAddress",
            [address, opts],
        )

    async def get_parsed_transaction(
        self,
        signature: str,
    ) -> Result[dict | None, RPCError]:
        """Get a parsed transaction by signature."""
        return await self._rpc_request(
            "getTransaction",
            [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
            ],
        )

    async def get_token_accounts(
        self,
        owner: str,
    ) -> Result[list, RPCError]:
        """Get all SPL token accounts for an owner (Token + Token-2022)."""
        classic = await self._rpc_request(
            "getTokenAccountsByOwner",
            [
                owner,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding": "jsonParsed"},
            ],
        )
        token22 = await self._rpc_request(
            "getTokenAccountsByOwner",
            [
                owner,
                {"programId": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"},
                {"encoding": "jsonParsed"},
            ],
        )
        combined: list[Any] = []
        if classic.is_ok:
            raw = classic.unwrap()
            items = (
                raw
                if isinstance(raw, list)
                else raw.get("value", []) if isinstance(raw, dict) else []
            )
            combined.extend(items)
        if token22.is_ok:
            raw = token22.unwrap()
            items = (
                raw
                if isinstance(raw, list)
                else raw.get("value", []) if isinstance(raw, dict) else []
            )
            combined.extend(items)
        if not combined and classic.is_err:
            return classic
        return Ok({"value": combined})

    async def get_token_largest_accounts(
        self,
        mint: str,
    ) -> Result[list, RPCError]:
        """Get the largest token holders for a mint address."""
        return await self._rpc_request("getTokenLargestAccounts", [mint])

    async def get_signature_statuses(
        self,
        signatures: list[str],
    ) -> Result[list, RPCError]:
        """Check confirmation status of one or more transactions."""
        return await self._rpc_request(
            "getSignatureStatuses",
            [signatures, {"searchTransactionHistory": True}],
        )

    async def get_priority_fee_estimate(
        self,
        transaction: str | None = None,
        account_keys: list[str] | None = None,
        priority_level: str = "Medium",
    ) -> Result[dict, RPCError]:
        """Estimate optimal priority fee (Helius-enhanced with standard fallback).

        Uses Helius ``getPriorityFeeEstimate`` if available, falls back to
        standard ``getRecentPrioritizationFees``.

        Args:
            priority_level: Min | Low | Medium | High | VeryHigh | UnsafeMax
        """
        params: dict[str, Any] = {
            "options": {
                "priorityLevel": priority_level,
                "includeAllPriorityFeeLevels": True,
            },
        }
        if transaction:
            params["transaction"] = transaction
        elif account_keys:
            params["accountKeys"] = account_keys

        result = await self._rpc_request("getPriorityFeeEstimate", [params])
        if result.is_ok:
            return result

        # Fallback: standard Solana getRecentPrioritizationFees
        fallback_keys = account_keys or []
        return await self._rpc_request(
            "getRecentPrioritizationFees",
            [fallback_keys] if fallback_keys else [],
        )

    async def rpc_request(
        self,
        method: str,
        params: list[Any],
        *,
        bypass_breaker: bool = False,
    ) -> Result[Any, RPCError]:
        """Execute an arbitrary JSON-RPC method (escape hatch)."""
        return await self._rpc_request(
            method, params, bypass_breaker=bypass_breaker,
        )

    # -- Internal pipeline ---------------------------------------------------

    @staticmethod
    def _is_rate_limit(err: RPCError) -> bool:
        """Return True for errors that should NOT trip the circuit breaker."""
        if (err.details or {}).get("status") == 429:
            return True
        msg = (err.message or "").lower()
        if "invalid param" in msg:
            return True
        return False

    async def _rpc_request(
        self,
        method: str,
        params: list[Any],
        *,
        bypass_breaker: bool = False,
    ) -> Result[Any, RPCError]:
        """Rate limit → circuit breaker → HTTP.

        On 429: rotates to a DIFFERENT provider instead of retrying the
        same rate-limited endpoint.
        """
        if not bypass_breaker:
            rl_result = await self._rate_limiter.acquire()
            if rl_result.is_err:
                return rl_result  # type: ignore[return-value]

        tried_providers: set[str] = set()
        last_result: Result[Any, RPCError] | None = None

        for rotation in range(_MAX_PROVIDER_ROTATIONS):
            provider_result = self._rotator.get_next()
            if provider_result.is_err:
                err = provider_result.unwrap_err()
                if err.code == "NO_HEALTHY_PROVIDERS":
                    for _recovery_attempt in range(3):
                        wait = min(3.0 * (_recovery_attempt + 1), 10.0)
                        logger.warning(
                            "all providers unhealthy, waiting %.1fs (attempt %d)",
                            wait,
                            _recovery_attempt + 1,
                        )
                        await asyncio.sleep(wait)
                        provider_result = self._rotator.get_next()
                        if provider_result.is_ok:
                            break
                if provider_result.is_err:
                    return provider_result  # type: ignore[return-value]
            provider = provider_result.unwrap()

            if provider.name in tried_providers:
                for _ in range(5):
                    alt = self._rotator.get_next()
                    if alt.is_ok and alt.unwrap().name not in tried_providers:
                        provider = alt.unwrap()
                        break
                else:
                    if last_result is not None:
                        return last_result
            tried_providers.add(provider.name)

            _provider = provider

            async def _do_call() -> Result[Any, RPCError]:
                return await self._http_rpc_call(
                    _provider.url, _provider.name, method, params,
                )

            result = await self._breaker.call(
                _do_call,
                is_retriable=self._is_rate_limit,
                bypass_breaker=bypass_breaker,
            )

            if result.is_ok:
                self._rotator.report_success(provider.name)
                return result

            err = result.unwrap_err()
            is_rate_limited = (
                err.details.get("status") == 429 if err.details else False
            )
            last_result = result

            if is_rate_limited:
                logger.warning(
                    "rpc 429 from %s, rotating (attempt %d/%d)",
                    provider.name,
                    rotation + 1,
                    _MAX_PROVIDER_ROTATIONS,
                )
                await asyncio.sleep(0.3)
                continue
            else:
                self._rotator.report_failure(provider.name)
                return result

        return last_result  # type: ignore[return-value]

    async def _http_rpc_call(
        self,
        url: str,
        provider_name: str,
        method: str,
        params: list[Any],
    ) -> Result[Any, RPCError]:
        """Execute a single JSON-RPC POST."""
        if self._http is None:
            return Err(RPCError(
                code="RPC_NOT_INITIALISED",
                message="ResilientRPCClient.startup() has not been called",
                severity=ErrorSeverity.HIGH,
                source="rpc_client",
            ))

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        start = time.monotonic()
        try:
            response = await self._http.post(url, json=payload)
            latency_ms = (time.monotonic() - start) * 1000
            self._rotator.registry.update_latency(provider_name, latency_ms)

            if response.status_code != 200:
                return Err(RPCError(
                    code="HTTP_ERROR",
                    message=f"RPC HTTP {response.status_code}",
                    severity=ErrorSeverity.MEDIUM,
                    source="rpc_client",
                    details={
                        "status": response.status_code,
                        "provider": provider_name,
                    },
                ))

            data = response.json()
            if "error" in data:
                return Err(RPCError(
                    code="RPC_ERROR",
                    message=data["error"].get("message", "Unknown RPC error"),
                    severity=ErrorSeverity.MEDIUM,
                    source="rpc_client",
                    details={
                        "rpc_error": data["error"],
                        "provider": provider_name,
                    },
                ))

            return Ok(data.get("result"))

        except httpx.TimeoutException:
            return Err(RPCError(
                code="RPC_TIMEOUT",
                message=f"RPC call to {provider_name} timed out",
                severity=ErrorSeverity.MEDIUM,
                source="rpc_client",
                details={"provider": provider_name, "method": method},
            ))
        except Exception as exc:
            return Err(RPCError(
                code="RPC_REQUEST_FAILED",
                message=f"RPC request failed: {exc}",
                severity=ErrorSeverity.MEDIUM,
                source="rpc_client",
                details={"provider": provider_name, "error": str(exc)},
            ))
