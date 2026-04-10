# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-04-11

### Added

- `ResilientRPCClient` — high-level async Solana RPC client with provider rotation
- `TokenBucketRateLimiter` — adaptive rate limiter with 429 backoff and auto-recovery
- `CircuitBreaker` — CLOSED → OPEN → HALF_OPEN failure isolation
- `ProviderRotator` — weighted random provider selection with health tracking
- `ProviderRegistry` — provider health, latency, and recovery management
- `ResponseCache` — async cache with TTL and request deduplication
- `Result` / `Ok` / `Err` — Rust-inspired result type (no exceptions)
- Built-in RPC methods: `get_balance`, `send_transaction`, `get_token_accounts`, `get_priority_fee_estimate`, and more
- Helius-enhanced priority fee estimation with standard RPC fallback
- `async with` context manager support
