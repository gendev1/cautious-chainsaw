"""
app/routers/indexing.py — Webhook receiver for document
indexing events.
"""
from __future__ import annotations

import logging
from typing import Literal

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("sidecar.routers.indexing")

router = APIRouter(
    prefix="/internal/indexing",
    tags=["indexing"],
)


# ── Pydantic event models ──────────────────────────


class IndexEvent(BaseModel):
    """Payload for an index-document webhook."""

    event_type: Literal["index"] = "index"
    tenant_id: str
    source_id: str
    source_type: str
    text: str
    household_id: str | None = None
    client_id: str | None = None
    advisor_id: str | None = None
    account_id: str | None = None
    visibility_tags: list[str] = Field(
        default_factory=list,
    )
    title: str | None = None
    author: str | None = None


class DeleteEvent(BaseModel):
    """Payload for a delete-source webhook."""

    event_type: Literal["delete"] = "delete"
    tenant_id: str
    source_id: str


# ── Dependency stub ─────────────────────────────────


async def _get_arq_redis(
    request: Request,
) -> ArqRedis:
    """
    Resolve ArqRedis from application state.
    The pool is created at startup in main.py lifespan.
    """
    return request.app.state.arq_redis  # type: ignore[return-value]


# ── Endpoint ────────────────────────────────────────


@router.post("/event")
async def receive_event(
    event: IndexEvent | DeleteEvent,
    arq: ArqRedis = Depends(_get_arq_redis),  # noqa: B008
) -> dict:
    """
    Accept an indexing or deletion event and enqueue
    the corresponding ARQ job.
    """
    if isinstance(event, IndexEvent):
        job = await arq.enqueue_job(
            "index_document",
            tenant_id=event.tenant_id,
            source_id=event.source_id,
            source_type=event.source_type,
            text=event.text,
            household_id=event.household_id,
            client_id=event.client_id,
            advisor_id=event.advisor_id,
            account_id=event.account_id,
            visibility_tags=event.visibility_tags,
            title=event.title,
            author=event.author,
        )
        logger.info(
            "enqueued index_document job=%s source=%s",
            job.job_id,
            event.source_id,
        )
        return {
            "status": "enqueued",
            "job_id": job.job_id,
            "action": "index",
        }

    # DeleteEvent
    job = await arq.enqueue_job(
        "delete_source",
        tenant_id=event.tenant_id,
        source_id=event.source_id,
    )
    logger.info(
        "enqueued delete_source job=%s source=%s",
        job.job_id,
        event.source_id,
    )
    return {
        "status": "enqueued",
        "job_id": job.job_id,
        "action": "delete",
    }
