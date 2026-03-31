"""app/routers/email.py -- Email drafting and triage endpoints."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from langfuse import Langfuse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.context import RequestContext
from app.dependencies import get_langfuse, get_platform_client, get_redis, get_request_context
from app.models.schemas import EmailDraft, TriagedEmail
from app.services.platform_client import PlatformClient
from app.utils.errors import ModelProviderHTTPError

logger = logging.getLogger("sidecar.routers.email")

router = APIRouter(prefix="/email", tags=["email"])

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class EmailDraftRequest(BaseModel):
    client_id: str = Field(description="Client the email is about or for")
    intent: str = Field(description="Purpose of the email: follow_up, introduction, review, etc.")
    context: str = Field(description="Additional context or instructions for the draft")
    reply_to_email_id: str | None = Field(default=None, description="Email ID to reply to, if any")


class EmailTriageRequest(BaseModel):
    emails: list[dict] = Field(description="List of raw email dicts to triage")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/draft")
async def draft_email(
    body: EmailDraftRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> EmailDraft:
    """Run the email drafter agent to generate an email draft."""
    try:
        from app.agents.base_deps import AgentDeps
        from app.agents.email_drafter import email_drafter_agent

        deps = AgentDeps(
            platform=platform,
            access_scope=ctx.access_scope,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
        )

        prompt_parts = [
            f"Draft an email for client {body.client_id}.",
            f"Intent: {body.intent}",
            f"Context: {body.context}",
        ]
        if body.reply_to_email_id:
            prompt_parts.append(f"This is a reply to email ID: {body.reply_to_email_id}")

        result = await email_drafter_agent.run("\n".join(prompt_parts), deps=deps)
        return result.output
    except Exception as exc:
        logger.exception("Email draft generation failed")
        raise ModelProviderHTTPError(str(exc), ctx.request_id) from exc


@router.post("/triage")
async def triage_emails(
    body: EmailTriageRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> list[TriagedEmail]:
    """Run the email triager agent to classify and prioritize emails."""
    try:
        from app.agents.base_deps import AgentDeps
        from app.agents.email_triager import email_triager_agent

        deps = AgentDeps(
            platform=platform,
            access_scope=ctx.access_scope,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
        )

        import json
        prompt = (
            "Triage the following emails. Classify each by urgency and category, "
            "provide a summary, and suggest an action.\n\n"
            f"Emails:\n{json.dumps(body.emails, indent=2)}"
        )

        result = await email_triager_agent.run(prompt, deps=deps)
        return result.output
    except Exception as exc:
        logger.exception("Email triage failed")
        raise ModelProviderHTTPError(str(exc), ctx.request_id) from exc
