"""app/routers/documents.py -- Document classification and extraction endpoints."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from langfuse import Langfuse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.context import RequestContext
from app.dependencies import get_langfuse, get_platform_client, get_redis, get_request_context
from app.models.schemas import DocClassification, DocExtraction
from app.services.platform_client import PlatformClient
from app.utils.errors import ModelProviderHTTPError

logger = logging.getLogger("sidecar.routers.documents")

router = APIRouter(prefix="/documents", tags=["documents"])

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class DocumentClassifyRequest(BaseModel):
    document_id: str = Field(description="Document identifier")
    filename: str = Field(description="Original filename of the document")
    content_preview: str = Field(description="Text preview or first page content of the document")


class DocumentExtractRequest(BaseModel):
    document_id: str = Field(description="Document identifier")
    document_type: str = Field(
        description="Document type: tax_return, estate_plan, trust_document, etc."
    )
    content: str = Field(description="Full text content of the document")
    custom_fields: list[str] = Field(
        default_factory=list,
        description="Additional fields to attempt to extract",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/classify")
async def classify_document(
    body: DocumentClassifyRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> DocClassification:
    """Run the document classifier agent to classify a document."""
    try:
        from app.agents.base_deps import AgentDeps
        from app.agents.doc_classifier import doc_classifier_agent

        deps = AgentDeps(
            platform=platform,
            access_scope=ctx.access_scope,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
        )

        prompt = (
            f"Classify the following document.\n"
            f"Document ID: {body.document_id}\n"
            f"Filename: {body.filename}\n\n"
            f"Content preview:\n{body.content_preview}"
        )

        result = await doc_classifier_agent.run(prompt, deps=deps)
        return result.output
    except Exception as exc:
        logger.exception("Document classification failed")
        raise ModelProviderHTTPError(str(exc), ctx.request_id) from exc


@router.post("/extract")
async def extract_document_data(
    body: DocumentExtractRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> DocExtraction:
    """Run the document extractor agent to extract structured data from a document."""
    try:
        from app.agents.base_deps import AgentDeps
        from app.agents.doc_extractor import doc_extractor_agent

        deps = AgentDeps(
            platform=platform,
            access_scope=ctx.access_scope,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
        )

        prompt_parts = [
            f"Extract structured data from the following {body.document_type} document.",
            f"Document ID: {body.document_id}",
        ]
        if body.custom_fields:
            prompt_parts.append(
                f"In addition to standard fields, also extract: {', '.join(body.custom_fields)}"
            )
        prompt_parts.append(f"\nContent:\n{body.content}")

        result = await doc_extractor_agent.run("\n".join(prompt_parts), deps=deps)
        return result.output
    except Exception as exc:
        logger.exception("Document extraction failed")
        raise ModelProviderHTTPError(str(exc), ctx.request_id) from exc
