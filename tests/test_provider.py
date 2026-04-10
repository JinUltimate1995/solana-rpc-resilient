"""Tests for ProviderRegistry and ProviderRotator."""

import pytest

from solana_rpc_resilient import ProviderRegistry, ProviderRotator


def test_registry_creation():
    registry = ProviderRegistry([
        {"name": "a", "url": "https://a.example.com", "weight": 3},
        {"name": "b", "url": "https://b.example.com", "weight": 1},
    ])
    assert len(registry.get_all()) == 2
    assert len(registry.get_healthy()) == 2


def test_mark_unhealthy():
    registry = ProviderRegistry([
        {"name": "a", "url": "https://a.example.com"},
    ])
    registry.mark_unhealthy("a")
    assert len(registry.get_healthy()) == 0


def test_mark_healthy():
    registry = ProviderRegistry([
        {"name": "a", "url": "https://a.example.com"},
    ])
    registry.mark_unhealthy("a")
    registry.mark_healthy("a")
    assert len(registry.get_healthy()) == 1


def test_to_dict_no_urls():
    registry = ProviderRegistry([
        {"name": "a", "url": "https://secret-rpc.example.com/key123"},
    ])
    info = registry.to_dict()
    # URL must NOT be in diagnostics output
    assert "url" not in str(info)
    assert "secret-rpc" not in str(info)
    assert "key123" not in str(info)


def test_rotator_selects_healthy():
    registry = ProviderRegistry([
        {"name": "a", "url": "https://a.example.com", "weight": 1},
        {"name": "b", "url": "https://b.example.com", "weight": 1},
    ])
    registry.mark_unhealthy("a")
    rotator = ProviderRotator(registry)
    result = rotator.get_next()
    assert result.is_ok
    assert result.unwrap().name == "b"


def test_rotator_force_recover():
    registry = ProviderRegistry([
        {"name": "a", "url": "https://a.example.com"},
    ])
    registry.mark_unhealthy("a")
    rotator = ProviderRotator(registry)
    # Should force-recover the only provider
    result = rotator.get_next()
    assert result.is_ok
    assert result.unwrap().name == "a"


def test_rotator_report_failure_threshold():
    registry = ProviderRegistry([
        {"name": "a", "url": "https://a.example.com"},
        {"name": "b", "url": "https://b.example.com"},
    ])
    rotator = ProviderRotator(registry)
    # 3 consecutive failures should mark unhealthy
    for _ in range(3):
        rotator.report_failure("a")
    provider_a = registry.get_by_name("a")
    assert not provider_a.is_healthy


def test_update_latency():
    registry = ProviderRegistry([
        {"name": "a", "url": "https://a.example.com"},
    ])
    registry.update_latency("a", 123.45)
    provider = registry.get_by_name("a")
    assert provider.latency_ms == 123.45
