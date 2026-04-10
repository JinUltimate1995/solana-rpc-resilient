<div align="center">
  <h1>solana-rpc-resilient</h1>
  <p><strong>production-grade solana rpc client that handles the real world.</strong></p>
  <p>rate limiting. circuit breaking. provider rotation. retry on 429. zero config.</p>

  <br/>

  <a href="https://github.com/JinUltimate1995/solana-rpc-resilient/actions"><img src="https://img.shields.io/github/actions/workflow/status/JinUltimate1995/solana-rpc-resilient/ci.yml?branch=main&style=flat-square&label=tests" /></a>
  <a href="https://pypi.org/project/solana-rpc-resilient/"><img src="https://img.shields.io/pypi/v/solana-rpc-resilient?style=flat-square" /></a>
  <img src="https://img.shields.io/pypi/pyversions/solana-rpc-resilient?style=flat-square" />
  <img src="https://img.shields.io/badge/typed-py.typed-blue?style=flat-square" />
  <img src="https://img.shields.io/badge/async-first-blue?style=flat-square" />
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" />
</div>

---

> extracted from a production solana trading system. battle-tested with millions of rpc calls.

## 🛡️ Why this library?

every solana dev hits the same problems:

- **429 rate limits** — your rpc provider throttles you and your app crashes
- **dead providers** — your single rpc endpoint goes down and everything stops
- **no failover** — you hardcode one url and pray

this library solves all three with zero config:

```python
from solana_rpc_resilient import ResilientRPCClient

client = ResilientRPCClient([
    {"name": "helius", "url": "https://mainnet.helius-rpc.com/?api-key=YOUR_KEY", "weight": 3},
    {"name": "quicknode", "url": "https://your-quicknode-url.com", "weight": 2},
    {"name": "public", "url": "https://api.mainnet-beta.solana.com", "weight": 1},
])

async with client:
    balance = await client.get_balance("So11111111111111111111111111111111111111112")
    print(balance)  # Ok(489293423) — balance in lamports
```

if helius returns 429 → automatically rotates to quicknode.
if quicknode is down → circuit breaker opens, falls back to public.
if all providers are down → auto-recovers the healthiest one after 10s.

**you never think about rpc reliability again.**

## 📦 Install

```bash
pip install solana-rpc-resilient
```

requires python 3.11+

> **You need your own RPC endpoints.** Get a free key from [Helius](https://helius.dev), [QuickNode](https://quicknode.com), or any Solana RPC provider. The public endpoint (`api.mainnet-beta.solana.com`) works but is heavily rate-limited.

## ⚡ Features

| feature | description |
|---|---|
| **adaptive rate limiter** | token bucket that self-tunes. backs off on 429, climbs back after stable success. |
| **circuit breaker** | CLOSED → OPEN → HALF_OPEN state machine. stops hammering dead endpoints. |
| **provider rotation** | weighted random selection from healthy providers. auto-failover on 429. |
| **auto-recovery** | unhealthy providers are re-probed after configurable timeout. never permanently locked out. |
| **request dedup** | concurrent calls for the same data share one in-flight request. |
| **ttl cache** | configurable per-method caching to reduce unnecessary calls. |
| **bypass mode** | emergency calls skip circuit breaker (e.g., checking balance during a sell). |

## 🔧 Components

### Rate Limiter

```python
from solana_rpc_resilient import TokenBucketRateLimiter

limiter = TokenBucketRateLimiter(rate=10.0, burst=20.0)

result = await limiter.acquire(timeout=5.0)
if result.is_ok:
    # make your api call
    limiter.record_success()
else:
    # timed out waiting for a token
    pass

# on 429 response:
limiter.record_rate_limit(retry_after=2.0)
# rate automatically decreases. cooldown kicks in.
```

### Circuit Breaker

```python
from solana_rpc_resilient import CircuitBreaker

breaker = CircuitBreaker(
    name="helius",
    failure_threshold=5,    # open after 5 failures
    recovery_seconds=60.0,  # try again after 60s
)

result = await breaker.call(
    my_rpc_function,
    is_retriable=lambda err: err.code == "RATE_LIMITED",  # 429s don't trip the breaker
)
```

### Full Client

```python
from solana_rpc_resilient import ResilientRPCClient

client = ResilientRPCClient(
    providers=[
        {"name": "primary", "url": "https://...", "weight": 3, "tier": "paid"},
        {"name": "backup", "url": "https://...", "weight": 1, "tier": "free"},
    ],
    rate_limit=10.0,       # requests/second
    burst=20.0,            # burst capacity
    failure_threshold=5,   # circuit breaker threshold
    recovery_seconds=60.0, # circuit breaker recovery
)

async with client:
    # all standard solana rpc methods
    balance = await client.get_balance(address)
    info = await client.get_account_info(address)
    sig = await client.send_transaction(tx_bytes, skip_preflight=True)
    statuses = await client.get_signature_statuses(signatures)
    accounts = await client.get_token_accounts(owner)
    slot = await client.get_slot()

    # helius enhanced (falls back to standard if unavailable)
    fees = await client.get_priority_fee_estimate(
        account_keys=["JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB"],
        priority_level="High",
    )
```

## 🎯 Result Type

all methods return `Result[T, RPCError]` — no exceptions to catch:

```python
result = await client.get_balance(address)

if result.is_ok:
    lamports = result.unwrap()
    print(f"balance: {lamports / 1e9:.4f} SOL")
else:
    error = result.unwrap_err()
    print(f"failed: {error.code} — {error.message}")
```

## 🏗️ Architecture

```
  your code
     │
     ▼
┌─────────────────────┐
│  ResilientRPCClient  │
├─────────────────────┤
│  ResponseCache       │ ← dedup + TTL
├─────────────────────┤
│  TokenBucketLimiter  │ ← adaptive rate control
├─────────────────────┤
│  CircuitBreaker      │ ← failure isolation
├─────────────────────┤
│  ProviderRotator     │ ← weighted failover
│    ├─ provider A ✅   │
│    ├─ provider B ✅   │
│    └─ provider C ❌   │ ← auto-recovers
└─────────────────────┘
```

---

## 🆚 Comparison

| | solana-rpc-resilient | raw `httpx` / `aiohttp` | solana-py |
|---|---|---|---|
| Rate limiting | ✅ adaptive token bucket | ❌ manual | ❌ none |
| Circuit breaker | ✅ automatic | ❌ manual | ❌ none |
| Provider failover | ✅ weighted rotation | ❌ single endpoint | ❌ single endpoint |
| 429 recovery | ✅ backoff + rotate | ❌ crash | ❌ crash |
| Request dedup | ✅ built-in | ❌ manual | ❌ none |
| Result type | ✅ `Ok` / `Err` | ❌ exceptions | ❌ exceptions |
| Async | ✅ native | ✅ | ⚠️ sync default |

## License

MIT

---

## 📦 Also by JinUltimate1995

- **[jupiter-swap-python](https://github.com/JinUltimate1995/jupiter-swap-python)** — Jupiter swap client for Python. Async. Typed.
- **[pumpfun-python](https://github.com/JinUltimate1995/pumpfun-python)** — PumpFun bonding curve + PumpSwap AMM. Direct swaps from Python.
- **[dexscreener-python](https://github.com/JinUltimate1995/dexscreener-python)** — DexScreener API client for Python.
