"""
app/jobs/rag_index.py — RAG index update job.

Handles created/updated/deleted events for documents, emails, and CRM notes.
Fetches content, chunks with paragraph-aware splitting, generates embeddings
via OpenAI, and upserts/deletes from the vector store.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from pydantic import BaseModel, Field

from app.jobs.enqueue import JobContext
from app.jobs.observability import JobTracer
from app.jobs.retry import with_retry_policy
from app.models.access_scope import AccessScope

logger = logging.getLogger("sidecar.jobs.rag_index")

# ---------------------------------------------------------------------------
# Local models
# ---------------------------------------------------------------------------


class ChunkMetadata(BaseModel):
    """Metadata stored alongside each indexed chunk."""
    tenant_id: str
    source_id: str
    source_type: str
    chunk_index: int
    chunk_id: str
    household_id: str | None = None
    client_id: str | None = None
    advisor_id: str | None = None
    account_id: str | None = None
    title: str | None = None


class IndexedChunk(BaseModel):
    """A single indexed chunk with embedding."""
    chunk_id: str
    text: str
    embedding: list[float] = Field(default_factory=list)
    metadata: ChunkMetadata


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_SIZE = 1000
DEFAULT_OVERLAP = 200


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    """
    Split text into overlapping chunks with paragraph-aware boundaries.

    Tries to break on paragraph boundaries (double newlines) when possible.
    Falls back to sentence boundaries, then hard character splits.
    """
    if not text or not text.strip():
        return []

    if len(text) <= chunk_size:
        return [text.strip()]

    chunks: list[str] = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        if end < len(text):
            # Try to find a paragraph break
            search_region = text[start:end]

            # Look for double newline (paragraph break) in last 30% of chunk
            split_search_start = int(len(search_region) * 0.7)
            para_pos = search_region.rfind("\n\n", split_search_start)

            if para_pos != -1:
                end = start + para_pos + 2  # include the double newline
            else:
                # Try single newline
                newline_pos = search_region.rfind("\n", split_search_start)
                if newline_pos != -1:
                    end = start + newline_pos + 1
                else:
                    # Try sentence boundary (. ! ?)
                    for sep in (". ", "! ", "? "):
                        sent_pos = search_region.rfind(sep, split_search_start)
                        if sent_pos != -1:
                            end = start + sent_pos + len(sep)
                            break
                    else:
                        # Try space
                        space_pos = search_region.rfind(" ", split_search_start)
                        if space_pos != -1:
                            end = start + space_pos + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        # Advance with overlap
        next_start = end - overlap
        if next_start <= start:
            next_start = end  # prevent infinite loop
        start = next_start

    return chunks


def make_chunk_id(tenant_id: str, source_id: str, chunk_index: int) -> str:
    """Generate a deterministic chunk ID using SHA256[:24]."""
    raw = f"{tenant_id}:{source_id}:{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


async def generate_embeddings(
    texts: list[str],
    http_client: Any,
    settings: Any,
) -> list[list[float]]:
    """Generate embeddings via OpenAI API."""
    import httpx

    api_key = settings.openai_api_key if hasattr(settings, "openai_api_key") else ""
    model = (
        settings.embedding_model
        if hasattr(settings, "embedding_model")
        else "text-embedding-3-small"
    )

    # Process in batches of 64
    batch_size = 64
    all_embeddings: list[list[float]] = []

    for batch_start in range(0, len(texts), batch_size):
        batch = texts[batch_start : batch_start + batch_size]

        resp = await http_client.post(
            "https://api.openai.com/v1/embeddings",
            json={
                "model": model,
                "input": batch,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0),
        )
        resp.raise_for_status()
        data = resp.json()

        # Sort by index to maintain order
        items = sorted(data["data"], key=lambda x: x["index"])
        for item in items:
            all_embeddings.append(item["embedding"])

    return all_embeddings


# ---------------------------------------------------------------------------
# Vector store operations
# ---------------------------------------------------------------------------


async def upsert_chunks(
    chunks: list[IndexedChunk],
    vector_store: Any,
    tenant_id: str,
) -> int:
    """Upsert chunks into the vector store. Returns count upserted."""
    if not chunks:
        return 0

    upserted = 0
    for chunk in chunks:
        try:
            await vector_store.upsert(
                id=chunk.chunk_id,
                embedding=chunk.embedding,
                text=chunk.text,
                metadata=chunk.metadata.model_dump(),
            )
            upserted += 1
        except AttributeError:
            # Fallback: if vector store uses a different API
            try:
                await vector_store.add_documents(
                    ids=[chunk.chunk_id],
                    embeddings=[chunk.embedding],
                    documents=[chunk.text],
                    metadatas=[chunk.metadata.model_dump()],
                )
                upserted += 1
            except Exception as exc:
                logger.warning("Failed to upsert chunk %s: %s", chunk.chunk_id, exc)

    return upserted


async def delete_source_chunks(
    vector_store: Any,
    tenant_id: str,
    source_id: str,
) -> int:
    """Delete all chunks for a source from the vector store."""
    try:
        result = await vector_store.delete(
            filter={"tenant_id": tenant_id, "source_id": source_id},
        )
        deleted = result if isinstance(result, int) else 0
        return deleted
    except AttributeError:
        try:
            result = await vector_store.delete_by_metadata(
                {"tenant_id": tenant_id, "source_id": source_id},
            )
            return result if isinstance(result, int) else 0
        except Exception as exc:
            logger.warning(
                "Failed to delete chunks for source %s: %s",
                source_id,
                exc,
            )
            return 0


# ---------------------------------------------------------------------------
# Content fetchers
# ---------------------------------------------------------------------------


async def _fetch_document_content(
    platform: Any,
    source_id: str,
    access_scope: AccessScope,
) -> dict[str, Any]:
    """Fetch document content and metadata from the platform."""
    metadata = await platform.get_document_metadata(source_id, access_scope)
    content = await platform.get_document_content(source_id, access_scope)
    meta_obj = metadata if isinstance(metadata, dict) else metadata.model_dump()
    return {
        "text": content,
        "title": meta_obj.get("filename", ""),
        "client_id": meta_obj.get("client_id"),
        "household_id": meta_obj.get("household_id"),
        "account_id": meta_obj.get("account_id"),
        "advisor_id": None,
    }


async def _fetch_email_content(
    platform: Any,
    source_id: str,
    access_scope: AccessScope,
) -> dict[str, Any]:
    """Fetch email thread content from the platform."""
    thread = await platform.get_email_thread(source_id, access_scope)
    thread_obj = thread if isinstance(thread, dict) else thread.model_dump()

    # Assemble text from all messages in thread
    parts: list[str] = [f"Subject: {thread_obj.get('subject', '')}"]
    for msg in thread_obj.get("messages", []):
        sender = msg.get("from", msg.get("sender", "unknown"))
        body = msg.get("body", msg.get("body_preview", ""))
        parts.append(f"\nFrom: {sender}\n{body}")

    return {
        "text": "\n".join(parts),
        "title": thread_obj.get("subject", ""),
        "client_id": None,
        "household_id": None,
        "account_id": None,
        "advisor_id": None,
    }


async def _fetch_crm_note_content(
    platform: Any,
    source_id: str,
    access_scope: AccessScope,
) -> dict[str, Any]:
    """Fetch CRM note/activity content from the platform."""
    # CRM activities are fetched via the activity feed; we use the source_id
    # as the activity_id. The platform may expose a dedicated endpoint.
    try:
        activity = await platform.get_crm_activity(source_id, access_scope)
        obj = activity if isinstance(activity, dict) else activity.model_dump()
    except (AttributeError, Exception):
        # Fallback: return minimal content
        return {
            "text": f"CRM note (ID: {source_id})",
            "title": "",
            "client_id": None,
            "household_id": None,
            "account_id": None,
            "advisor_id": None,
        }

    text_parts = [obj.get("subject", "")]
    if obj.get("description"):
        text_parts.append(obj["description"])

    return {
        "text": "\n".join(text_parts),
        "title": obj.get("subject", ""),
        "client_id": obj.get("client_id"),
        "household_id": None,
        "account_id": None,
        "advisor_id": obj.get("advisor_id"),
    }


CONTENT_FETCHERS = {
    "document": _fetch_document_content,
    "email": _fetch_email_content,
    "crm_note": _fetch_crm_note_content,
}


# ---------------------------------------------------------------------------
# Job entry point
# ---------------------------------------------------------------------------


@with_retry_policy
async def run_rag_index_update(
    ctx: dict[str, Any],
    job_ctx_raw: dict | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    event_type: str | None = None,
) -> dict:
    """
    Update the RAG index for a single source.

    Handles created/updated/deleted events for documents, emails,
    and CRM notes.
    """
    if job_ctx_raw is None:
        raise ValueError("run_rag_index_update requires job_ctx_raw")
    if not source_type or not source_id or not event_type:
        raise ValueError("source_type, source_id, and event_type are required")

    job_ctx = JobContext(**job_ctx_raw)
    access_scope = AccessScope(**job_ctx.access_scope)

    platform = ctx["platform_client"]
    vector_store = ctx.get("vector_store")
    http_client = ctx.get("http_client")
    settings = ctx.get("settings")
    langfuse = ctx.get("langfuse")

    tracer: JobTracer | None = None
    if langfuse:
        tracer = JobTracer(
            langfuse=langfuse,
            job_name="rag_index_update",
            tenant_id=job_ctx.tenant_id,
            actor_id=job_ctx.actor_id,
            extra_metadata={
                "source_type": source_type,
                "source_id": source_id,
                "event_type": event_type,
            },
        )

    try:
        # Handle deletion
        if event_type == "deleted":
            deleted = 0
            if vector_store:
                deleted = await delete_source_chunks(
                    vector_store, job_ctx.tenant_id, source_id,
                )

            if tracer:
                tracer.complete(output={"deleted": deleted})

            logger.info(
                "rag_index: deleted %d chunks for %s/%s",
                deleted,
                source_type,
                source_id,
            )
            return {
                "status": "deleted",
                "source_type": source_type,
                "source_id": source_id,
                "chunks_deleted": deleted,
            }

        # Handle created/updated — fetch content
        fetcher = CONTENT_FETCHERS.get(source_type)
        if not fetcher:
            msg = f"Unknown source_type: {source_type}"
            logger.error("rag_index: %s", msg)
            if tracer:
                tracer.fail(ValueError(msg), category="invalid_source_type")
            return {"status": "error", "error": msg}

        content_data = await fetcher(platform, source_id, access_scope)
        if tracer:
            tracer.record_platform_read()

        text = content_data.get("text", "")
        if not text or not text.strip():
            logger.warning("rag_index: empty content for %s/%s", source_type, source_id)
            if tracer:
                tracer.complete(output={"status": "empty_content"})
            return {
                "status": "empty_content",
                "source_type": source_type,
                "source_id": source_id,
            }

        # Chunk text
        text_chunks = chunk_text(text)
        logger.info(
            "rag_index: %d chunks from %s/%s (%d chars)",
            len(text_chunks),
            source_type,
            source_id,
            len(text),
        )

        if not text_chunks:
            if tracer:
                tracer.complete(output={"status": "no_chunks"})
            return {"status": "no_chunks", "source_type": source_type, "source_id": source_id}

        # Generate embeddings
        if http_client is None:
            import httpx
            http_client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))

        embeddings = await generate_embeddings(text_chunks, http_client, settings)

        # Build IndexedChunk objects
        indexed_chunks: list[IndexedChunk] = []
        for i, (chunk_text_str, embedding) in enumerate(zip(text_chunks, embeddings, strict=False)):
            chunk_id = make_chunk_id(job_ctx.tenant_id, source_id, i)
            metadata = ChunkMetadata(
                tenant_id=job_ctx.tenant_id,
                source_id=source_id,
                source_type=source_type,
                chunk_index=i,
                chunk_id=chunk_id,
                household_id=content_data.get("household_id"),
                client_id=content_data.get("client_id"),
                advisor_id=content_data.get("advisor_id"),
                account_id=content_data.get("account_id"),
                title=content_data.get("title"),
            )
            indexed_chunks.append(IndexedChunk(
                chunk_id=chunk_id,
                text=chunk_text_str,
                embedding=embedding,
                metadata=metadata,
            ))

        # For updates, delete old chunks first
        deleted = 0
        if event_type == "updated" and vector_store:
            deleted = await delete_source_chunks(
                vector_store, job_ctx.tenant_id, source_id,
            )

        # Upsert new chunks
        upserted = 0
        if vector_store:
            upserted = await upsert_chunks(indexed_chunks, vector_store, job_ctx.tenant_id)

        if tracer:
            tracer.complete(output={
                "source_type": source_type,
                "source_id": source_id,
                "event_type": event_type,
                "chunks_indexed": upserted,
                "chunks_deleted": deleted,
            })

        logger.info(
            "rag_index: indexed %s/%s — %d chunks upserted, %d deleted (event: %s)",
            source_type,
            source_id,
            upserted,
            deleted,
            event_type,
        )

        return {
            "status": "indexed",
            "source_type": source_type,
            "source_id": source_id,
            "event_type": event_type,
            "chunks_indexed": upserted,
            "chunks_deleted": deleted,
            "total_chars": len(text),
        }

    except Exception as exc:
        if tracer:
            tracer.fail(exc, category="rag_index_error")
        raise
