"""Tests for middleware stack: request ID, tenant context, structured logging."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.request_id import RequestIdMiddleware
from app.middleware.tenant import TenantContextMiddleware


def _minimal_app() -> FastAPI:
    """Create a minimal app with middleware for testing."""
    app = FastAPI()
    # Register in correct order (outermost first)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(TenantContextMiddleware)
    return app


def test_missing_tenant_headers_returns_400() -> None:
    """T3: Request without X-Tenant-ID returns 400 with MISSING_CONTEXT."""
    app = _minimal_app()

    @app.get("/test")
    async def test_route() -> dict:
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/test")
    assert response.status_code == 400
    data = response.json()
    assert data["ok"] is False
    assert data["error"]["code"] == "MISSING_CONTEXT"


def test_valid_tenant_headers_attach_context() -> None:
    """T4: Request with valid tenant headers attaches RequestContext to request.state."""
    app = _minimal_app()
    captured_ctx = {}

    @app.get("/test")
    async def test_route(request: Request) -> dict:
        ctx = request.state.context
        captured_ctx["tenant_id"] = ctx.tenant_id
        captured_ctx["actor_id"] = ctx.actor_id
        captured_ctx["actor_type"] = ctx.actor_type
        return {"ok": True}

    client = TestClient(app)
    response = client.get(
        "/test",
        headers={
            "X-Tenant-ID": "t_123",
            "X-Actor-ID": "a_456",
            "X-Actor-Type": "advisor",
        },
    )
    assert response.status_code == 200
    assert captured_ctx["tenant_id"] == "t_123"
    assert captured_ctx["actor_id"] == "a_456"
    assert captured_ctx["actor_type"] == "advisor"


def test_request_id_propagated() -> None:
    """T5: X-Request-ID header is propagated when provided."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    captured_id = {}

    @app.get("/test")
    async def test_route(request: Request) -> dict:
        captured_id["request_id"] = request.state.request_id
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/test", headers={"X-Request-ID": "custom-id-123"})
    assert response.status_code == 200
    assert captured_id["request_id"] == "custom-id-123"
    assert response.headers["X-Request-ID"] == "custom-id-123"


def test_request_id_generated_when_missing() -> None:
    """T5b: Missing X-Request-ID generates a UUID."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    captured_id = {}

    @app.get("/test")
    async def test_route(request: Request) -> dict:
        captured_id["request_id"] = request.state.request_id
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/test")
    assert response.status_code == 200
    # Should be a UUID-like string
    assert len(captured_id["request_id"]) == 36
    assert response.headers["X-Request-ID"] == captured_id["request_id"]


def test_health_path_skips_tenant_check() -> None:
    """Requests to /health bypass tenant context extraction."""
    app = FastAPI()
    app.add_middleware(TenantContextMiddleware)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    client = TestClient(app)
    # No tenant headers — should still pass
    response = client.get("/health")
    assert response.status_code == 200
