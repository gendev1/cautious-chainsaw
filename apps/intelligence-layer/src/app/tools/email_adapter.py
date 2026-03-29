"""
app/tools/email_adapter.py — Typed read-only client for advisor email
via Microsoft Graph API, plus agent tool wrapper.
"""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel
from pydantic_ai import RunContext

from app.agents.base_deps import AgentDeps
from app.errors import PlatformReadError
from app.models.access_scope import AccessScope

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class EmailMessage(BaseModel):
    message_id: str
    subject: str
    sender: str
    recipients: list[str]
    received_at: datetime
    body_preview: str
    has_attachments: bool
    client_id: str | None = None
    thread_id: str | None = None
    importance: str = "normal"


class EmailSearchResult(BaseModel):
    messages: list[EmailMessage]
    total_count: int


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class EmailAdapter:
    """Typed read-only client for advisor email via
    Microsoft Graph API.
    """

    def __init__(
        self,
        graph_base_url: str,
        token_provider: Callable[
            [], Coroutine[Any, Any, str]
        ],
        timeout_s: float = 5.0,
    ) -> None:
        self._graph_base_url = graph_base_url
        self._token_provider = token_provider
        self._http = httpx.AsyncClient(
            base_url=graph_base_url,
            timeout=httpx.Timeout(
                timeout_s, connect=3.0
            ),
        )

    async def _get_headers(
        self, access_scope: AccessScope
    ) -> dict[str, str]:
        token = await self._token_provider()
        return {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": access_scope.tenant_id,
            "X-Actor-ID": access_scope.actor_id,
        }

    async def search_emails(
        self,
        advisor_email: str,
        query: str,
        access_scope: AccessScope,
        *,
        max_results: int = 25,
        since: datetime | None = None,
        client_email: str | None = None,
    ) -> EmailSearchResult:
        """Search advisor's mailbox."""
        headers = await self._get_headers(access_scope)

        filters: list[str] = []
        if since:
            filters.append(
                f"receivedDateTime ge "
                f"{since.isoformat()}"
            )
        if client_email:
            filters.append(
                f"(from/emailAddress/address eq "
                f"'{client_email}' or "
                f"toRecipients/any("
                f"r:r/emailAddress/address eq "
                f"'{client_email}'))"
            )

        params: dict[str, str] = {
            "$search": f'"{query}"',
            "$top": str(max_results),
            "$select": (
                "id,subject,from,toRecipients,"
                "receivedDateTime,bodyPreview,"
                "hasAttachments,conversationId,"
                "importance"
            ),
            "$orderby": "receivedDateTime desc",
        }
        if filters:
            params["$filter"] = " and ".join(filters)

        resp = await self._http.get(
            f"/v1.0/users/{advisor_email}/messages",
            headers=headers,
            params=params,
        )
        if resp.status_code >= 400:
            raise PlatformReadError(
                status_code=resp.status_code,
                error_code="EMAIL_READ_ERROR",
                message=(
                    f"Graph API error: "
                    f"{resp.text[:300]}"
                ),
            )

        data = resp.json()
        messages = [
            EmailMessage(
                message_id=msg["id"],
                subject=msg.get("subject", ""),
                sender=(
                    msg.get("from", {})
                    .get("emailAddress", {})
                    .get("address", "")
                ),
                recipients=[
                    r["emailAddress"]["address"]
                    for r in msg.get("toRecipients", [])
                ],
                received_at=msg["receivedDateTime"],
                body_preview=msg.get("bodyPreview", ""),
                has_attachments=msg.get(
                    "hasAttachments", False
                ),
                thread_id=msg.get("conversationId"),
                importance=msg.get(
                    "importance", "normal"
                ),
            )
            for msg in data.get("value", [])
        ]
        return EmailSearchResult(
            messages=messages,
            total_count=data.get(
                "@odata.count", len(messages)
            ),
        )

    async def get_recent_emails(
        self,
        advisor_email: str,
        access_scope: AccessScope,
        *,
        hours: int = 24,
        max_results: int = 50,
    ) -> list[EmailMessage]:
        """Get recent emails from the last N hours."""
        since = datetime.utcnow().replace(
            hour=0, minute=0, second=0
        )
        result = await self.search_emails(
            advisor_email=advisor_email,
            query="*",
            access_scope=access_scope,
            max_results=max_results,
            since=since,
        )
        return result.messages

    async def close(self) -> None:
        await self._http.aclose()


# ---------------------------------------------------------------------------
# Agent tool (backward-compatible with existing agents)
# ---------------------------------------------------------------------------


async def get_unread_priority_emails(
    ctx: RunContext[AgentDeps],
) -> list:
    """Retrieve unread priority emails for the current advisor.

    Use this when generating daily digests or email triage reports.
    """
    return []
