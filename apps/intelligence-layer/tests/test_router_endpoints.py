"""Tests for router endpoint wiring using FastAPI TestClient."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    """Create test client with mocked app state."""
    app = create_app()
    app.state.redis = AsyncMock()
    app.state.vector_store = AsyncMock()
    app.state.platform_client = AsyncMock()
    app.state.langfuse = MagicMock()
    app.state.settings = MagicMock()
    return TestClient(
        app, raise_server_exceptions=False
    )


REQUIRED_HEADERS = {
    "X-Tenant-ID": "t_001",
    "X-Actor-ID": "a_001",
    "X-Actor-Type": "advisor",
    "X-Request-ID": "r_001",
}


def test_digest_generate_accepts_post(
    client: TestClient,
) -> None:
    """POST /ai/digest/generate is routable."""
    resp = client.post(
        "/ai/digest/generate",
        json={"advisor_id": "adv_001"},
        headers=REQUIRED_HEADERS,
    )
    # Not 404 or 405 — endpoint exists
    assert resp.status_code not in (404, 405)


def test_digest_latest_returns_404_when_empty(
    client: TestClient,
) -> None:
    """GET /ai/digest/latest returns 404 when empty."""
    client.app.state.redis.get = AsyncMock(
        return_value=None
    )
    resp = client.get(
        "/ai/digest/latest",
        headers=REQUIRED_HEADERS,
    )
    assert resp.status_code == 404


def test_email_draft_endpoint_exists(
    client: TestClient,
) -> None:
    """POST /ai/email/draft is routable."""
    resp = client.post(
        "/ai/email/draft",
        json={
            "client_id": "cl_001",
            "intent": "follow_up",
            "context": "Meeting yesterday",
        },
        headers=REQUIRED_HEADERS,
    )
    assert resp.status_code not in (404, 405)


def test_meetings_transcribe_accepts_post(
    client: TestClient,
) -> None:
    """POST /ai/meetings/transcribe is routable."""
    resp = client.post(
        "/ai/meetings/transcribe",
        json={
            "meeting_id": "mtg_001",
            "audio_storage_ref": "s3://bucket/audio.wav",
            "duration_seconds": 3600,
        },
        headers=REQUIRED_HEADERS,
    )
    assert resp.status_code not in (404, 405)


def test_documents_classify_endpoint_exists(
    client: TestClient,
) -> None:
    """POST /ai/documents/classify is routable."""
    resp = client.post(
        "/ai/documents/classify",
        json={
            "document_id": "doc_001",
            "filename": "tax_return_2025.pdf",
            "content_preview": "Form 1040...",
        },
        headers=REQUIRED_HEADERS,
    )
    assert resp.status_code not in (404, 405)


def test_missing_tenant_header_returns_400(
    client: TestClient,
) -> None:
    """Missing X-Tenant-ID returns 400 from middleware."""
    resp = client.post(
        "/ai/email/draft",
        json={
            "client_id": "cl_001",
            "intent": "follow_up",
            "context": "test",
        },
        headers={},
    )
    assert resp.status_code == 400
