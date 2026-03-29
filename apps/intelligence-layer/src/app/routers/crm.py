"""app/routers/crm.py -- CRM sync payload generation endpoints."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from langfuse import Langfuse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.context import RequestContext
from app.dependencies import get_langfuse, get_platform_client, get_redis, get_request_context
from app.services.platform_client import PlatformClient
from app.utils.errors import ModelProviderHTTPError

logger = logging.getLogger("sidecar.routers.crm")

router = APIRouter(prefix="/crm", tags=["crm"])

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class CRMSyncRequest(BaseModel):
    source_type: str = Field(description="Source type: meeting, email, note")
    source_id: str = Field(description="Identifier for the source record")
    content: str = Field(description="Content to generate CRM sync payloads from")
    provider: str = Field(default="generic", description="CRM provider name")


class CRMSyncResponse(BaseModel):
    payloads: list = Field(default_factory=list, description="List of CRM sync payloads")
    payload_count: int = Field(description="Number of payloads generated")
    provider: str = Field(description="CRM provider used")
    as_of: str = Field(description="ISO 8601 timestamp of generation")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/sync-payload")
async def generate_crm_sync_payload(
    body: CRMSyncRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> CRMSyncResponse:
    """Run the meeting summarizer agent to extract CRM sync payloads from content."""
    try:
        from app.agents.base_deps import AgentDeps
        from app.agents.meeting_summarizer import meeting_summarizer_agent

        deps = AgentDeps(
            platform=platform,
            access_scope=ctx.access_scope,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
        )

        prompt = (
            f"Extract CRM sync payloads from the following {body.source_type} content.\n"
            f"Source ID: {body.source_id}\n"
            f"Provider: {body.provider}\n\n"
            f"Content:\n{body.content}"
        )

        result = await meeting_summarizer_agent.run(prompt, deps=deps)
        now = datetime.now(UTC).isoformat()

        # Extract CRM payloads from the meeting summary result
        payloads = [p.model_dump() for p in result.data.crm_sync_payloads]

        return CRMSyncResponse(
            payloads=payloads,
            payload_count=len(payloads),
            provider=body.provider,
            as_of=now,
        )
    except Exception as exc:
        logger.exception("CRM sync payload generation failed")
        raise ModelProviderHTTPError(str(exc), ctx.request_id) from exc
