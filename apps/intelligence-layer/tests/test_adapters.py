"""Tests for email, CRM, and calendar adapters."""
from __future__ import annotations

import httpx
import pytest

from app.models.access_scope import AccessScope
from app.tools.calendar_adapter import CalendarAdapter
from app.tools.crm_adapter import CRMAdapter
from app.tools.email_adapter import EmailAdapter


def _scope() -> AccessScope:
    return AccessScope(
        tenant_id="t_001",
        actor_id="a_001",
        actor_type="advisor",
        request_id="r_001",
        visibility_mode="full_tenant",
    )


@pytest.mark.asyncio
async def test_email_adapter_search() -> None:
    """EmailAdapter.search_emails builds correct request."""
    captured_url = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_url["path"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "msg_001",
                        "subject": "Test",
                        "from": {"emailAddress": {"address": "a@b.com"}},
                        "toRecipients": [],
                        "receivedDateTime": "2026-03-26T12:00:00Z",
                        "bodyPreview": "Hello",
                        "hasAttachments": False,
                        "conversationId": "thread_001",
                        "importance": "normal",
                    }
                ],
            },
        )

    async def token_provider():
        return "test-token"

    adapter = EmailAdapter(
        graph_base_url="http://graph.test",
        token_provider=token_provider,
        timeout_s=5.0,
    )
    adapter._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://graph.test",
    )

    result = await adapter.search_emails(
        "advisor@test.com", "meeting", _scope(),
    )
    assert len(result.messages) == 1
    assert result.messages[0].subject == "Test"
    await adapter.close()


@pytest.mark.asyncio
async def test_crm_adapter_scope_headers() -> None:
    """CRMAdapter propagates scope headers."""
    captured_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        return httpx.Response(200, json=[])

    adapter = CRMAdapter(
        base_url="http://platform.test",
        service_token="test-token",
    )
    adapter._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://platform.test",
    )

    await adapter.search_notes("cl_001", _scope())
    assert captured_headers["x-tenant-id"] == "t_001"
    assert captured_headers["x-actor-id"] == "a_001"
    assert "x-access-scope" in captured_headers
    await adapter.close()


@pytest.mark.asyncio
async def test_calendar_adapter_parses_events() -> None:
    """CalendarAdapter.get_upcoming_events returns parsed events."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "evt_001",
                        "subject": "Client Review",
                        "start": {"dateTime": "2026-03-28T10:00:00"},
                        "end": {"dateTime": "2026-03-28T11:00:00"},
                        "location": {"displayName": "Office"},
                        "attendees": [
                            {"emailAddress": {"address": "client@test.com"}}
                        ],
                        "organizer": {"emailAddress": {"address": "advisor@test.com"}},
                        "isRecurring": False,
                        "bodyPreview": "Review portfolio",
                    }
                ],
            },
        )

    async def token_provider():
        return "test-token"

    adapter = CalendarAdapter(
        graph_base_url="http://graph.test",
        token_provider=token_provider,
    )
    adapter._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://graph.test",
    )

    events = await adapter.get_upcoming_events(
        "advisor@test.com", _scope(),
    )
    assert len(events) == 1
    assert events[0].subject == "Client Review"
    await adapter.close()
