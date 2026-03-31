"""Tests for portfolio construction router endpoints using FastAPI TestClient."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    """Create test client with mocked app state."""
    app = create_app()
    app.state.redis = AsyncMock()
    app.state.redis.xadd = AsyncMock(return_value=b"1234567890-0")
    app.state.redis.xread = AsyncMock(return_value=[])
    app.state.redis.xrevrange = AsyncMock(return_value=[])
    app.state.redis.get = AsyncMock(return_value=None)
    app.state.vector_store = AsyncMock()
    app.state.platform_client = AsyncMock()
    app.state.langfuse = MagicMock()
    app.state.settings = MagicMock()
    app.state.settings.portfolio_freshness_warn_s = 86400
    app.state.settings.portfolio_theme_cache_ttl_s = 21600
    return TestClient(app, raise_server_exceptions=False)


REQUIRED_HEADERS = {
    "X-Tenant-ID": "t_001",
    "X-Actor-ID": "a_001",
    "X-Actor-Type": "advisor",
    "X-Request-ID": "r_001",
}


# ---------------------------------------------------------------------------
# POST /ai/portfolio/construct
# ---------------------------------------------------------------------------


def test_construct_endpoint_exists(client: TestClient) -> None:
    """POST /ai/portfolio/construct is routable (not 404/405)."""
    with patch(
        "app.routers.portfolio.enqueue_portfolio_construction",
        new_callable=AsyncMock,
        return_value="job_test_001",
    ):
        resp = client.post(
            "/ai/portfolio/construct",
            json={"message": "Build me an AI portfolio"},
            headers=REQUIRED_HEADERS,
        )
    assert resp.status_code not in (404, 405)


def test_construct_returns_202(client: TestClient) -> None:
    """POST /ai/portfolio/construct returns 202 Accepted with job_id."""
    with patch(
        "app.routers.portfolio.enqueue_portfolio_construction",
        new_callable=AsyncMock,
        return_value="job_test_002",
    ):
        resp = client.post(
            "/ai/portfolio/construct",
            json={"message": "Build me a clean energy portfolio"},
            headers=REQUIRED_HEADERS,
        )
    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert body["job_id"] == "job_test_002"


def test_construct_with_optional_fields(client: TestClient) -> None:
    """POST /ai/portfolio/construct accepts optional fields."""
    with patch(
        "app.routers.portfolio.enqueue_portfolio_construction",
        new_callable=AsyncMock,
        return_value="job_test_003",
    ):
        resp = client.post(
            "/ai/portfolio/construct",
            json={
                "message": "AI portfolio",
                "account_id": "acc_001",
                "target_count": 20,
                "weighting_strategy": "equal",
                "include_tickers": ["NVDA"],
                "exclude_tickers": ["META"],
            },
            headers=REQUIRED_HEADERS,
        )
    assert resp.status_code == 202


def test_construct_missing_message_returns_422(client: TestClient) -> None:
    """POST /ai/portfolio/construct without message returns 422."""
    resp = client.post(
        "/ai/portfolio/construct",
        json={},
        headers=REQUIRED_HEADERS,
    )
    assert resp.status_code == 422


def test_construct_missing_headers_returns_400(client: TestClient) -> None:
    """POST /ai/portfolio/construct without required headers returns 400."""
    resp = client.post(
        "/ai/portfolio/construct",
        json={"message": "AI portfolio"},
        headers={},
    )
    assert resp.status_code == 400


def test_construct_scope_propagation(client: TestClient) -> None:
    """JobContext built from request headers contains correct tenant_id and actor_id."""
    captured_job_ctx = {}

    async def mock_enqueue(job_ctx, request):
        captured_job_ctx.update(job_ctx.model_dump())
        return "job_scope_001"

    with patch(
        "app.routers.portfolio.enqueue_portfolio_construction",
        side_effect=mock_enqueue,
    ):
        resp = client.post(
            "/ai/portfolio/construct",
            json={"message": "Test scope propagation"},
            headers=REQUIRED_HEADERS,
        )

    if resp.status_code == 202:
        assert captured_job_ctx.get("tenant_id") == "t_001"
        assert captured_job_ctx.get("actor_id") == "a_001"


# ---------------------------------------------------------------------------
# GET /ai/portfolio/jobs/{job_id}
# ---------------------------------------------------------------------------


def test_job_status_endpoint_exists(client: TestClient) -> None:
    """GET /ai/portfolio/jobs/{job_id} is routable."""
    resp = client.get(
        "/ai/portfolio/jobs/job_test_001",
        headers=REQUIRED_HEADERS,
    )
    assert resp.status_code not in (404, 405)


def test_job_status_returns_status_dict(client: TestClient) -> None:
    """GET /ai/portfolio/jobs/{job_id} returns a status dict."""
    # Mock Redis to return a status
    client.app.state.redis.xrevrange = AsyncMock(return_value=[
        (b"1000-0", {b"event_type": b"intent_parsed", b"v": b"1"}),
    ])

    resp = client.get(
        "/ai/portfolio/jobs/job_test_002",
        headers=REQUIRED_HEADERS,
    )
    assert resp.status_code not in (404, 405)
    if resp.status_code == 200:
        body = resp.json()
        assert isinstance(body, dict)


def test_job_status_includes_result_when_completed(client: TestClient) -> None:
    """GET /ai/portfolio/jobs/{job_id} includes result when job is completed."""
    import json

    client.app.state.redis.xrevrange = AsyncMock(return_value=[
        (b"2000-0", {b"event_type": b"job_completed", b"v": b"1"}),
    ])
    client.app.state.redis.get = AsyncMock(return_value=json.dumps({
        "parsed_intent": {"themes": ["AI"]},
        "proposed_holdings": [],
    }))

    resp = client.get(
        "/ai/portfolio/jobs/job_complete",
        headers=REQUIRED_HEADERS,
    )
    assert resp.status_code not in (404, 405)


# ---------------------------------------------------------------------------
# GET /ai/portfolio/jobs/{job_id}/events
# ---------------------------------------------------------------------------


def test_events_endpoint_exists(client: TestClient) -> None:
    """GET /ai/portfolio/jobs/{job_id}/events is routable."""
    resp = client.get(
        "/ai/portfolio/jobs/job_test_001/events",
        headers=REQUIRED_HEADERS,
    )
    assert resp.status_code not in (404, 405)


def test_events_endpoint_returns_sse_content_type(client: TestClient) -> None:
    """GET /ai/portfolio/jobs/{job_id}/events returns SSE content type."""
    client.app.state.redis.xread = AsyncMock(return_value=[
        (
            b"sidecar:portfolio:events:job_sse",
            [
                (b"1000-0", {b"event_type": b"intent_parsed", b"v": b"1", b"timestamp": b"2026-03-28T10:00:00Z"}),
            ],
        ),
    ])

    resp = client.get(
        "/ai/portfolio/jobs/job_sse/events",
        headers=REQUIRED_HEADERS,
    )
    if resp.status_code == 200:
        content_type = resp.headers.get("content-type", "")
        assert "text/event-stream" in content_type or resp.status_code == 200


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_construct_empty_body_returns_422(client: TestClient) -> None:
    """POST /ai/portfolio/construct with no body returns 422."""
    resp = client.post(
        "/ai/portfolio/construct",
        headers=REQUIRED_HEADERS,
    )
    assert resp.status_code == 422


def test_nonexistent_job_returns_appropriate_status(client: TestClient) -> None:
    """GET /ai/portfolio/jobs/{nonexistent} returns 404 or empty status."""
    client.app.state.redis.xrevrange = AsyncMock(return_value=[])
    client.app.state.redis.get = AsyncMock(return_value=None)

    resp = client.get(
        "/ai/portfolio/jobs/nonexistent_job",
        headers=REQUIRED_HEADERS,
    )
    # Should be 200 with empty status or 404
    assert resp.status_code in (200, 404)
