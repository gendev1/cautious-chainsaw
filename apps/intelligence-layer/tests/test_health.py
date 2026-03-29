"""Tests for health and readiness endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.main import create_app


def _make_client() -> TestClient:
    """Create a test client with mocked lifespan dependencies."""
    app = create_app()

    # Mock app.state dependencies that lifespan would normally set up
    app.state.redis = AsyncMock()
    app.state.redis.ping = AsyncMock(return_value=True)

    app.state.vector_store = MagicMock()
    app.state.vector_store.health_check = AsyncMock()

    app.state.platform_client = MagicMock()
    app.state.platform_client._http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    app.state.platform_client._http.get = AsyncMock(return_value=mock_resp)

    app.state.settings = MagicMock()
    app.state.settings.environment = "development"
    app.state.settings.anthropic_api_key = "sk-test"
    app.state.settings.openai_api_key = ""

    return TestClient(app, raise_server_exceptions=False)


def test_health_returns_ok() -> None:
    """T1: GET /health returns {"status": "ok"} with 200."""
    client = _make_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_returns_structured_checks() -> None:
    """T2: GET /ready returns structured checks JSON with mocked deps."""
    client = _make_client()
    response = client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert "checks" in data
    assert "redis" in data["checks"]
    assert "vector_store" in data["checks"]
    assert "platform_api" in data["checks"]
