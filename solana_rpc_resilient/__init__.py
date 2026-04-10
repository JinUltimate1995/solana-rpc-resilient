"""solana-rpc-resilient — production-grade Solana RPC client."""

from .cache import ResponseCache
from .circuit_breaker import CircuitBreaker, CircuitBreakerState
from .client import ResilientRPCClient
from .provider import ProviderRegistry, RPCProvider
from .rate_limiter import TokenBucketRateLimiter
from .rotation import ProviderRotator
from .types import Err, Ok, Result, RPCError

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerState",
    "Err",
    "Ok",
    "ProviderRegistry",
    "ProviderRotator",
    "RPCError",
    "RPCProvider",
    "ResilientRPCClient",
    "ResponseCache",
    "Result",
    "TokenBucketRateLimiter",
]

__version__ = "0.1.0"
