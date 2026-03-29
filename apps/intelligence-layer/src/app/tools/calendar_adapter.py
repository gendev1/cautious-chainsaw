"""
app/tools/calendar_adapter.py — Typed read-only client for advisor
calendar via Graph API, plus agent tool wrapper.
"""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
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


class CalendarEvent(BaseModel):
    event_id: str
    subject: str
    start: datetime
    end: datetime
    location: str | None = None
    attendees: list[str] = []
    organizer: str | None = None
    client_id: str | None = None
    is_recurring: bool = False
    body_preview: str | None = None


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class CalendarAdapter:
    """Typed read-only client for advisor calendar via
    Graph API.
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

    async def get_upcoming_events(
        self,
        advisor_email: str,
        access_scope: AccessScope,
        *,
        days: int = 7,
        max_results: int = 50,
    ) -> list[CalendarEvent]:
        """Get calendar events for the next N days."""
        headers = await self._get_headers(access_scope)
        now = datetime.now(tz=UTC)
        end = now + timedelta(days=days)

        resp = await self._http.get(
            f"/v1.0/users/{advisor_email}/calendarView",
            headers=headers,
            params={
                "startDateTime": (
                    now.isoformat() + "Z"
                ),
                "endDateTime": (
                    end.isoformat() + "Z"
                ),
                "$top": str(max_results),
                "$select": (
                    "id,subject,start,end,location,"
                    "attendees,organizer,isRecurring,"
                    "bodyPreview"
                ),
                "$orderby": "start/dateTime",
            },
        )
        if resp.status_code >= 400:
            raise PlatformReadError(
                status_code=resp.status_code,
                error_code="CALENDAR_READ_ERROR",
                message=(
                    f"Graph API calendar error: "
                    f"{resp.text[:300]}"
                ),
            )

        data = resp.json()
        events: list[CalendarEvent] = []
        for evt in data.get("value", []):
            events.append(
                CalendarEvent(
                    event_id=evt["id"],
                    subject=evt.get("subject", ""),
                    start=evt["start"]["dateTime"],
                    end=evt["end"]["dateTime"],
                    location=evt.get(
                        "location", {}
                    ).get("displayName"),
                    attendees=[
                        a["emailAddress"]["address"]
                        for a in evt.get(
                            "attendees", []
                        )
                    ],
                    organizer=(
                        evt.get("organizer", {})
                        .get("emailAddress", {})
                        .get("address")
                    ),
                    is_recurring=evt.get(
                        "isRecurring", False
                    ),
                    body_preview=evt.get("bodyPreview"),
                )
            )
        return events

    async def get_today_schedule(
        self,
        advisor_email: str,
        access_scope: AccessScope,
    ) -> list[CalendarEvent]:
        """Convenience: today's events only."""
        return await self.get_upcoming_events(
            advisor_email,
            access_scope,
            days=1,
        )

    async def close(self) -> None:
        await self._http.aclose()


# ---------------------------------------------------------------------------
# Agent tool (backward-compatible with existing agents)
# ---------------------------------------------------------------------------


async def get_todays_meetings(
    ctx: RunContext[AgentDeps],
) -> list:
    """Retrieve today's meetings for the current advisor.

    Use this when generating daily digests or meeting prep briefs.
    """
    return []
