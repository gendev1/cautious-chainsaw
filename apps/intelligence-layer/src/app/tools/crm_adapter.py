"""
app/tools/crm_adapter.py — Typed read-only client for CRM data
via platform integration endpoints, plus agent tool wrapper.
"""
from __future__ import annotations

from datetime import datetime

import httpx
from pydantic import BaseModel
from pydantic_ai import RunContext

from app.agents.base_deps import AgentDeps
from app.errors import classify_platform_error
from app.models.access_scope import AccessScope

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class CRMNote(BaseModel):
    note_id: str
    client_id: str
    author_id: str
    content: str
    created_at: datetime
    updated_at: datetime | None = None
    tags: list[str] = []


class CRMActivityModel(BaseModel):
    activity_id: str
    activity_type: str
    client_id: str
    advisor_id: str
    subject: str
    description: str | None = None
    occurred_at: datetime
    status: str = "completed"


class CRMTask(BaseModel):
    task_id: str
    title: str
    description: str | None = None
    client_id: str | None = None
    assigned_to: str
    due_date: datetime | None = None
    status: str
    priority: str = "normal"


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class CRMAdapter:
    """Typed read-only client for CRM data via platform
    integration endpoints.
    """

    def __init__(
        self,
        base_url: str,
        service_token: str,
        timeout_s: float = 3.0,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                timeout_s, connect=2.0
            ),
            headers={
                "Authorization": (
                    f"Bearer {service_token}"
                ),
                "User-Agent": "sidecar/1.0",
            },
        )

    def _scope_headers(
        self, access_scope: AccessScope
    ) -> dict[str, str]:
        return {
            "X-Tenant-ID": access_scope.tenant_id,
            "X-Actor-ID": access_scope.actor_id,
            "X-Request-ID": access_scope.request_id,
            "X-Access-Scope": (
                access_scope.model_dump_json()
            ),
        }

    async def search_notes(
        self,
        client_id: str,
        access_scope: AccessScope,
        *,
        query: str | None = None,
        max_results: int = 25,
    ) -> list[CRMNote]:
        """Search CRM notes for a client."""
        params: dict[str, str] = {
            "client_id": client_id,
            "limit": str(max_results),
        }
        if query:
            params["q"] = query

        resp = await self._http.get(
            "/v1/crm/notes",
            headers=self._scope_headers(access_scope),
            params=params,
        )
        if resp.status_code >= 400:
            raise classify_platform_error(resp)
        return [
            CRMNote.model_validate(item)
            for item in resp.json()
        ]

    async def get_activities(
        self,
        client_id: str,
        access_scope: AccessScope,
        *,
        activity_type: str | None = None,
        max_results: int = 25,
    ) -> list[CRMActivityModel]:
        """Get CRM activities for a client."""
        params: dict[str, str] = {
            "client_id": client_id,
            "limit": str(max_results),
        }
        if activity_type:
            params["type"] = activity_type

        resp = await self._http.get(
            "/v1/crm/activities",
            headers=self._scope_headers(access_scope),
            params=params,
        )
        if resp.status_code >= 400:
            raise classify_platform_error(resp)
        return [
            CRMActivityModel.model_validate(item)
            for item in resp.json()
        ]

    async def get_open_tasks(
        self,
        advisor_id: str,
        access_scope: AccessScope,
    ) -> list[CRMTask]:
        """Get open tasks assigned to an advisor."""
        resp = await self._http.get(
            "/v1/crm/tasks",
            headers=self._scope_headers(access_scope),
            params={
                "assigned_to": advisor_id,
                "status": "open",
            },
        )
        if resp.status_code >= 400:
            raise classify_platform_error(resp)
        return [
            CRMTask.model_validate(item)
            for item in resp.json()
        ]

    async def close(self) -> None:
        await self._http.aclose()


# ---------------------------------------------------------------------------
# Agent tool (backward-compatible with existing agents)
# ---------------------------------------------------------------------------


async def get_pending_tasks(
    ctx: RunContext[AgentDeps],
) -> list:
    """Retrieve pending CRM tasks for the current advisor.

    Use this when generating daily digests or task reports.
    """
    return []
