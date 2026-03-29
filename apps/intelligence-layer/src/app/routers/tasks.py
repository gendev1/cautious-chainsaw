"""app/routers/tasks.py -- Task extraction endpoints."""
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
from app.models.schemas import ExtractedTask
from app.services.platform_client import PlatformClient
from app.utils.errors import ModelProviderHTTPError

logger = logging.getLogger("sidecar.routers.tasks")

router = APIRouter(prefix="/tasks", tags=["tasks"])

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class TaskExtractionRequest(BaseModel):
    source_type: str = Field(description="Source type: meeting, email, crm_note")
    source_id: str = Field(description="Identifier for the source record")
    content: str = Field(description="Content to extract tasks from")
    client_id: str | None = Field(default=None, description="Associated client ID, if known")


class TaskExtractionResponse(BaseModel):
    source_type: str = Field(description="Source type the tasks were extracted from")
    source_id: str = Field(description="Source record identifier")
    tasks: list[ExtractedTask] = Field(default_factory=list, description="Extracted tasks")
    task_count: int = Field(description="Number of tasks extracted")
    as_of: str = Field(description="ISO 8601 timestamp of extraction")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/extract")
async def extract_tasks(
    body: TaskExtractionRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> TaskExtractionResponse:
    """Run the task extractor agent to extract actionable tasks from content."""
    try:
        from app.agents.base_deps import AgentDeps
        from app.agents.task_extractor import task_extractor_agent

        deps = AgentDeps(
            platform=platform,
            access_scope=ctx.access_scope,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
        )

        prompt_parts = [
            f"Extract actionable tasks from the following {body.source_type} content.",
            f"Source ID: {body.source_id}",
        ]
        if body.client_id:
            prompt_parts.append(f"Client ID: {body.client_id}")
        prompt_parts.append(f"\nContent:\n{body.content}")

        result = await task_extractor_agent.run("\n".join(prompt_parts), deps=deps)
        now = datetime.now(UTC).isoformat()

        return TaskExtractionResponse(
            source_type=body.source_type,
            source_id=body.source_id,
            tasks=result.data,
            task_count=len(result.data),
            as_of=now,
        )
    except Exception as exc:
        logger.exception("Task extraction failed")
        raise ModelProviderHTTPError(str(exc), ctx.request_id) from exc
