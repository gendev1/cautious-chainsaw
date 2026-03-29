"""Tests for PlatformClient — typed read methods with circuit breaker + cache."""
from __future__ import annotations

import json

import httpx
import pytest

from app.errors import PlatformReadError
from app.models.access_scope import AccessScope
from app.services.circuit_breaker import CircuitOpenError
from app.services.platform_client import (
    PlatformClient,
    PlatformClientConfig,
)
from app.services.request_cache import RequestScopedCache


def _make_scope() -> AccessScope:
    return AccessScope(
        tenant_id="t_001",
        actor_id="a_001",
        actor_type="advisor",
        request_id="r_001",
        visibility_mode="scoped",
        household_ids=["hh_001"],
    )


def _make_config(**overrides) -> PlatformClientConfig:
    defaults = {
        "base_url": "http://platform:8000",
        "service_token": "test-token",
    }
    defaults.update(overrides)
    return PlatformClientConfig(**defaults)


@pytest.mark.asyncio
async def test_scope_headers_sent() -> None:
    """Every request includes identity and scope headers."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.headers))
        return httpx.Response(
            200,
            json={
                "household_id": "hh_001",
                "household_name": "Test",
                "primary_advisor_id": "adv_001",
                "accounts": [],
                "total_aum": "500000.00",
                "client_ids": [],
                "freshness": {
                    "as_of": "2026-03-26T12:00:00",
                    "source": "test",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    config = _make_config()
    client = PlatformClient(config)
    client._http = httpx.AsyncClient(
        transport=transport,
        base_url="http://platform:8000",
    )

    scope = _make_scope()
    await client.get_household_summary("hh_001", scope)

    assert captured["x-tenant-id"] == "t_001"
    assert captured["x-actor-id"] == "a_001"
    assert "x-access-scope" in captured
    scope_json = json.loads(captured["x-access-scope"])
    assert scope_json["tenant_id"] == "t_001"
    await client.close()


@pytest.mark.asyncio
async def test_cache_key_deterministic() -> None:
    """Same inputs produce same cache key."""
    config = _make_config()
    client = PlatformClient(config)
    k1 = client._cache_key("test", a="1", b="2")
    k2 = client._cache_key("test", a="1", b="2")
    assert k1 == k2
    k3 = client._cache_key("test", a="1", b="3")
    assert k1 != k3
    await client.close()


@pytest.mark.asyncio
async def test_cached_response_reused() -> None:
    """Second call to same method uses cache."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200,
            json={
                "household_id": "hh_001",
                "household_name": "Test",
                "primary_advisor_id": "adv_001",
                "accounts": [],
                "total_aum": "500000.00",
                "client_ids": [],
                "freshness": {
                    "as_of": "2026-03-26T12:00:00",
                    "source": "test",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    cache = RequestScopedCache()
    config = _make_config()
    client = PlatformClient(config, cache=cache)
    client._http = httpx.AsyncClient(
        transport=transport,
        base_url="http://platform:8000",
    )

    scope = _make_scope()
    await client.get_household_summary("hh_001", scope)
    await client.get_household_summary("hh_001", scope)
    assert call_count == 1  # second call was cached
    await client.close()


@pytest.mark.asyncio
async def test_circuit_opens_after_failures() -> None:
    """Circuit breaker opens after threshold failures."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(500, json={"detail": "error"})

    transport = httpx.MockTransport(handler)
    config = _make_config(circuit_failure_threshold=3)
    client = PlatformClient(config)
    client._http = httpx.AsyncClient(
        transport=transport,
        base_url="http://platform:8000",
    )

    scope = _make_scope()
    for _ in range(3):
        with pytest.raises(PlatformReadError):
            await client.get_household_summary("hh_001", scope)

    assert call_count == 3
    with pytest.raises(CircuitOpenError):
        await client.get_household_summary("hh_001", scope)
    assert call_count == 3  # no additional HTTP call
    await client.close()


@pytest.mark.asyncio
async def test_timeout_raises_platform_error() -> None:
    """Timeout raises PlatformReadError with TIMEOUT code."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    transport = httpx.MockTransport(handler)
    config = _make_config()
    client = PlatformClient(config)
    client._http = httpx.AsyncClient(
        transport=transport,
        base_url="http://platform:8000",
    )

    scope = _make_scope()
    with pytest.raises(PlatformReadError) as exc_info:
        await client.get_household_summary("hh_001", scope)
    assert exc_info.value.error_code == "TIMEOUT"
    await client.close()


@pytest.mark.asyncio
async def test_typed_method_parses_response() -> None:
    """get_client_profile returns parsed ClientProfile model."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "client_id": "cl_001",
                "first_name": "Jane",
                "last_name": "Smith",
                "household_id": "hh_001",
                "contact": {"email": "jane@example.com"},
                "account_ids": ["acc_001"],
                "freshness": {
                    "as_of": "2026-03-26T12:00:00",
                    "source": "test",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    config = _make_config()
    client = PlatformClient(config)
    client._http = httpx.AsyncClient(
        transport=transport,
        base_url="http://platform:8000",
    )

    scope = _make_scope()
    profile = await client.get_client_profile("cl_001", scope)
    assert profile.client_id == "cl_001"
    assert profile.first_name == "Jane"
    assert profile.contact.email == "jane@example.com"
    await client.close()
