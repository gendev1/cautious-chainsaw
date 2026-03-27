# 06 — Feature Implementations: FastAPI Routers and Agent Wiring

This document specifies the complete FastAPI router implementation for every sidecar feature endpoint. Each section covers Pydantic request/response models, dependency injection, agent invocation, error handling, and Langfuse tracing.

All routers share these conventions:

- Every request carries tenant/actor context via `Depends(get_request_context)`
- Every agent call is wrapped in Langfuse tracing via `Depends(get_langfuse)`
- Errors are classified into `platform_read`, `model_provider`, `validation`, and `internal` categories
- Async jobs return HTTP 202 with a job reference
- All financial outputs include `as_of` freshness metadata

---

## Shared Infrastructure

### Request Context and Dependencies

Every router depends on a shared request context extracted from platform-provided headers. This matches the core infrastructure and platform-client docs: access scope is propagated in `X-Access-Scope`, not parsed from the request body.

```python
# app/dependencies.py

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from langfuse import Langfuse
from redis.asyncio import Redis

from app.config import Settings
from app.context import RequestContext
from app.models.access_scope import AccessScope
from app.services.platform_client import PlatformClient


@lru_cache
async def get_settings() -> Settings:
    return Settings()


async def get_redis(request: Request) -> Redis:
    return request.app.state.redis


async def get_platform_client(
    request: Request,
) -> PlatformClient:
    return request.app.state.platform_client


async def get_langfuse(
    request: Request,
) -> Langfuse:
    return request.app.state.langfuse


async def get_request_context(
    request: Request,
    x_tenant_id: Annotated[str, Header()],
    x_actor_id: Annotated[str, Header()],
    x_actor_type: Annotated[str, Header()] = "advisor",
    x_request_id: Annotated[str | None, Header()] = None,
    x_conversation_id: Annotated[str | None, Header()] = None,
    x_access_scope: Annotated[str | None, Header()] = None,
) -> RequestContext:
    """Extract and validate request context from platform-provided headers."""
    import json
    from fastapi import HTTPException

    try:
        access_scope_data = json.loads(x_access_scope) if x_access_scope else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid X-Access-Scope header") from exc

    return RequestContext(
        tenant_id=x_tenant_id,
        actor_id=x_actor_id,
        actor_type=x_actor_type,
        request_id=x_request_id or getattr(request.state, "request_id", "unknown"),
        conversation_id=x_conversation_id,
        access_scope=AccessScope(
            tenant_id=x_tenant_id,
            actor_id=x_actor_id,
            request_id=x_request_id or getattr(request.state, "request_id", "unknown"),
            visibility_mode=access_scope_data.get("visibility_mode", "scoped"),
            household_ids=access_scope_data.get("household_ids", []),
            client_ids=access_scope_data.get("client_ids", []),
            account_ids=access_scope_data.get("account_ids", []),
            document_ids=access_scope_data.get("document_ids", []),
            advisor_ids=access_scope_data.get("advisor_ids", []),
        ),
    )
```

### Error Classification

```python
# app/utils/errors.py

from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel


class ErrorCategory(str, Enum):
    PLATFORM_READ = "platform_read"
    MODEL_PROVIDER = "model_provider"
    VALIDATION = "validation"
    INTERNAL = "internal"


class SidecarError(BaseModel):
    category: ErrorCategory
    code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


class PlatformReadError(HTTPException):
    def __init__(self, detail: str, request_id: str | None = None):
        super().__init__(
            status_code=502,
            detail=SidecarError(
                category=ErrorCategory.PLATFORM_READ,
                code="PLATFORM_READ_FAILED",
                message=detail,
                request_id=request_id,
            ).model_dump(),
        )


class ModelProviderError(HTTPException):
    def __init__(self, detail: str, request_id: str | None = None):
        super().__init__(
            status_code=502,
            detail=SidecarError(
                category=ErrorCategory.MODEL_PROVIDER,
                code="MODEL_PROVIDER_FAILED",
                message=detail,
                request_id=request_id,
            ).model_dump(),
        )


class ValidationError(HTTPException):
    def __init__(self, detail: str, request_id: str | None = None):
        super().__init__(
            status_code=422,
            detail=SidecarError(
                category=ErrorCategory.VALIDATION,
                code="VALIDATION_FAILED",
                message=detail,
                request_id=request_id,
            ).model_dump(),
        )


class InternalError(HTTPException):
    def __init__(self, detail: str, request_id: str | None = None):
        super().__init__(
            status_code=500,
            detail=SidecarError(
                category=ErrorCategory.INTERNAL,
                code="INTERNAL_ERROR",
                message=detail,
                request_id=request_id,
            ).model_dump(),
        )
```

### Langfuse Tracing Decorator

```python
# app/utils/tracing.py

from __future__ import annotations

import functools
import time
from typing import Any, Callable

from langfuse import Langfuse

from app.context import RequestContext


def traced_agent_call(
    feature: str,
    agent_name: str,
):
    """Decorator that wraps an agent invocation with Langfuse tracing."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(
            *args: Any,
            ctx: RequestContext,
            langfuse: Langfuse,
            **kwargs: Any,
        ) -> Any:
            trace = langfuse.trace(
                name=f"{feature}/{agent_name}",
                user_id=ctx.actor_id,
                metadata={
                    "tenant_id": ctx.tenant_id,
                    "request_id": ctx.request_id,
                    "actor_type": ctx.actor_type,
                    "feature": feature,
                },
            )
            generation = trace.generation(
                name=agent_name,
                metadata={"feature": feature},
            )
            start = time.monotonic()
            try:
                result = await func(*args, ctx=ctx, langfuse=langfuse, trace=trace, **kwargs)
                generation.end(
                    metadata={"duration_ms": (time.monotonic() - start) * 1000},
                )
                trace.update(output={"status": "success"})
                return result
            except Exception as exc:
                generation.end(
                    metadata={
                        "duration_ms": (time.monotonic() - start) * 1000,
                        "error": str(exc),
                    },
                    level="ERROR",
                )
                trace.update(output={"status": "error", "error": str(exc)})
                raise
        return wrapper
    return decorator
```

---

## Feature 1: Copilot Chat

**Router:** `app/routers/chat.py`
**Endpoints:** `POST /ai/chat`, `POST /ai/chat/stream`
**Agent tier:** Copilot (Claude Sonnet 4.6, fallback GPT-4o)

### Request/Response Models

```python
# app/routers/chat.py

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langfuse import Langfuse
from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage
from redis.asyncio import Redis

from app.agents.base_deps import AgentDeps
from app.context import RequestContext
from app.dependencies import (
    get_langfuse,
    get_platform_client,
    get_redis,
    get_request_context,
)
from app.services.platform_client import PlatformClient
from app.services.message_codec import (
    deserialize_message,
    extract_active_client_id,
    extract_active_household_id,
    serialize_message,
    trim_message_history,
)
from app.utils.errors import ModelProviderError, PlatformReadError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/chat", tags=["copilot"])

# ── Request Models ──────────────────────────────────────────────

class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(..., min_length=1, max_length=10_000)
    client_id: str | None = None          # Optional: scope to a specific client
    household_id: str | None = None       # Optional: scope to a household


# ── Response Models ─────────────────────────────────────────────

class Citation(BaseModel):
    source_type: str                      # "document", "email", "crm_note", "meeting_transcript", "account_data"
    source_id: str
    title: str
    excerpt: str
    relevance_score: float

class Action(BaseModel):
    type: str                             # "CREATE_REBALANCE_PROPOSAL", "SCHEDULE_MEETING", "DRAFT_EMAIL"
    target_id: str | None = None
    reason: str

class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    citations: list[Citation]
    confidence: float
    as_of: str
    recommended_actions: list[Action]
    follow_up_questions: list[str]

class StreamChunk(BaseModel):
    """Shape of each SSE data frame."""
    type: str                             # "token", "citation", "action", "done", "error"
    content: str | None = None
    citation: Citation | None = None
    action: Action | None = None
    metadata: dict | None = None
```

### Conversation Memory and Turn Stitching

```python
# ── Conversation Memory ─────────────────────────────────────────

CONVERSATION_TTL = 7200        # 2 hours
MAX_CONVERSATION_MESSAGES = 50

class ConversationMemory:
    """Stores structured model messages so tool traces survive across turns."""

    def __init__(
        self,
        redis: Redis,
        tenant_id: str,
        actor_id: str,
        conversation_id: str,
    ) -> None:
        self.redis = redis
        self.conversation_id = conversation_id
        self.key = f"chat:{tenant_id}:{actor_id}:{conversation_id}"

    async def load(self) -> list[ModelMessage]:
        raw = await self.redis.get(self.key)
        if raw is None:
            return []
        payload = json.loads(raw)
        return [deserialize_message(item) for item in payload["messages"]]

    async def load_state(self) -> dict[str, str | None]:
        raw = await self.redis.get(self.key)
        if raw is None:
            return {"active_client_id": None, "active_household_id": None}
        payload = json.loads(raw)
        return {
            "active_client_id": payload.get("active_client_id"),
            "active_household_id": payload.get("active_household_id"),
        }

    async def save(self, messages: list[ModelMessage]) -> None:
        trimmed = trim_message_history(messages, max_messages=MAX_CONVERSATION_MESSAGES)
        await self.redis.set(
            self.key,
            json.dumps(
                {
                    "messages": [serialize_message(m) for m in trimmed],
                    "active_client_id": extract_active_client_id(trimmed),
                    "active_household_id": extract_active_household_id(trimmed),
                }
            ),
            ex=CONVERSATION_TTL,
        )


def resolve_active_scope(
    body: ChatRequest,
    persisted_state: dict[str, str | None],
) -> tuple[str | None, str | None]:
    """
    Carry forward the last active client/household when the next user turn
    refers to them implicitly with phrases like "there" or "that position".
    """
    active_client_id = body.client_id or persisted_state.get("active_client_id")
    active_household_id = body.household_id or persisted_state.get("active_household_id")
    return active_client_id, active_household_id
```

The important part is not the Redis key itself. It is the payload shape:

- Store serialized `ModelMessage` entries, not plain `{role, content}` text.
- Persist tool calls and tool results along with user and assistant turns.
- Rebuild the system prompt on every request so live platform data is fresh.
- Trim by recent message history after the agent run, never by flattening the transcript into one string prompt.

The concrete helper behavior for `serialize_message`, `deserialize_message`, `trim_message_history`, and active-scope extraction is defined in [02-agents-and-tools.md](/Users/eswar/Desktop/wealth-advisor/specs/sidecar/02-agents-and-tools.md).

### Synchronous Chat Endpoint

```python
# ── POST /ai/chat ───────────────────────────────────────────────

@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> ChatResponse:
    """
    Synchronous copilot chat. Accepts a user message, loads conversation
    history, retrieves scoped context via RAG, invokes the copilot agent,
    and returns a structured response with citations.
    """
    import uuid
    from app.agents.copilot import copilot_agent, CopilotDeps

    conversation_id = body.conversation_id or str(uuid.uuid4())

    # ── Langfuse trace ──────────────────────────────────────────
    trace = langfuse.trace(
        name="copilot/chat",
        user_id=ctx.actor_id,
        session_id=conversation_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
            "client_id": body.client_id,
        },
    )

    memory = ConversationMemory(
        redis=redis,
        tenant_id=ctx.tenant_id,
        actor_id=ctx.actor_id,
        conversation_id=conversation_id,
    )
    history = await memory.load()
    persisted_state = await memory.load_state()
    active_client_id, active_household_id = resolve_active_scope(
        body, persisted_state,
    )

    # ── Invoke copilot agent ────────────────────────────────────
    try:
        result = await copilot_agent.run(
            body.message,
            message_history=history,
            deps=CopilotDeps(
                platform=platform,
                access_scope=ctx.access_scope,
                tenant_id=ctx.tenant_id,
                actor_id=ctx.actor_id,
                active_client_id=active_client_id,
                active_household_id=active_household_id,
                langfuse_trace=trace,
            ),
        )
    except Exception as exc:
        logger.exception("Copilot agent failed", extra={"request_id": ctx.request_id})
        if "platform" in str(exc).lower() or "httpx" in str(exc).lower():
            raise PlatformReadError(
                detail=f"Failed to read platform data: {exc}",
                request_id=ctx.request_id,
            )
        raise ModelProviderError(
            detail=f"Model provider error: {exc}",
            request_id=ctx.request_id,
        )

    # Persist the full model transcript, including tool calls and tool results.
    await memory.save(result.all_messages())

    # ── Assemble response ───────────────────────────────────────
    return ChatResponse(
        conversation_id=conversation_id,
        answer=result.data.answer,
        citations=[
            Citation(**c.model_dump()) for c in result.data.citations
        ],
        confidence=result.data.confidence,
        as_of=result.data.as_of or datetime.now(timezone.utc).isoformat(),
        recommended_actions=[
            Action(**a.model_dump()) for a in result.data.recommended_actions
        ],
        follow_up_questions=result.data.follow_up_questions,
    )
```

### SSE Streaming Endpoint

```python
# ── POST /ai/chat/stream ───────────────────────────────────────

@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> StreamingResponse:
    """
    Streaming copilot chat via Server-Sent Events. Tokens are yielded
    incrementally, followed by structured citation and action frames,
    and a final 'done' frame with metadata.
    """
    import uuid
    from app.agents.copilot import copilot_agent, CopilotDeps

    conversation_id = body.conversation_id or str(uuid.uuid4())

    trace = langfuse.trace(
        name="copilot/chat_stream",
        user_id=ctx.actor_id,
        session_id=conversation_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
        },
    )

    memory = ConversationMemory(
        redis=redis,
        tenant_id=ctx.tenant_id,
        actor_id=ctx.actor_id,
        conversation_id=conversation_id,
    )
    history = await memory.load()
    persisted_state = await memory.load_state()
    active_client_id, active_household_id = resolve_active_scope(
        body, persisted_state,
    )

    async def _event_generator() -> AsyncIterator[str]:
        """
        Yield SSE frames. Each frame is formatted as:
            data: {"type": "...", ...}\n\n
        """
        accumulated_answer = ""

        try:
            async with copilot_agent.run_stream(
                body.message,
                message_history=history,
                deps=CopilotDeps(
                    platform=platform,
                    access_scope=ctx.access_scope,
                    tenant_id=ctx.tenant_id,
                    actor_id=ctx.actor_id,
                    active_client_id=active_client_id,
                    active_household_id=active_household_id,
                    langfuse_trace=trace,
                ),
            ) as stream:
                async for token in stream.stream_text():
                    accumulated_answer += token
                    chunk = StreamChunk(type="token", content=token)
                    yield f"data: {chunk.model_dump_json()}\n\n"

                # After streaming completes, get the final structured result
                result = await stream.get_data()

                # Emit citations
                for citation in result.citations:
                    chunk = StreamChunk(
                        type="citation",
                        citation=Citation(**citation.model_dump()),
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"

                # Emit recommended actions
                for action in result.recommended_actions:
                    chunk = StreamChunk(
                        type="action",
                        action=Action(**action.model_dump()),
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"

                # Emit completion frame
                done = StreamChunk(
                    type="done",
                    metadata={
                        "conversation_id": conversation_id,
                        "confidence": result.confidence,
                        "as_of": result.as_of
                            or datetime.now(timezone.utc).isoformat(),
                        "follow_up_questions": result.follow_up_questions,
                    },
                )
                yield f"data: {done.model_dump_json()}\n\n"

        except Exception as exc:
            logger.exception("Stream error", extra={"request_id": ctx.request_id})
            error_chunk = StreamChunk(
                type="error",
                content=str(exc),
                metadata={"request_id": ctx.request_id},
            )
            yield f"data: {error_chunk.model_dump_json()}\n\n"
            return

        # Persist the full structured transcript after successful stream.
        await memory.save(stream.all_messages())

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Conversation-ID": conversation_id,
            "X-Request-ID": ctx.request_id,
        },
    )
```

### Copilot Agent Definition

```python
# app/agents/copilot.py  (referenced by the router)

from __future__ import annotations

from dataclasses import dataclass

from langfuse import Langfuse
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from app.agents.base_deps import AgentDeps

class CopilotCitation(BaseModel):
    source_type: str
    source_id: str
    title: str
    excerpt: str
    relevance_score: float

class CopilotAction(BaseModel):
    type: str
    target_id: str | None = None
    reason: str

class HazelCopilotResult(BaseModel):
    answer: str
    citations: list[CopilotCitation]
    confidence: float
    as_of: str | None = None
    recommended_actions: list[CopilotAction]
    follow_up_questions: list[str]

@dataclass
class CopilotDeps(AgentDeps):
    active_client_id: str | None = None
    active_household_id: str | None = None
    langfuse_trace: object | None = None


copilot_agent = Agent(
    model="anthropic:claude-sonnet-4-6",
    result_type=HazelCopilotResult,
    retries=1,
)


@copilot_agent.system_prompt
async def build_system_prompt(ctx: RunContext[CopilotDeps]) -> str:
    """
    Rebuilt on every turn. The sidecar refreshes live client and household
    context from platform reads instead of trusting stale turn-1 state.
    """
    parts = [
        "You are Hazel, an AI assistant for wealth advisors.",
        f"Tenant: {ctx.deps.tenant_id}",
        f"Advisor: {ctx.deps.actor_id}",
        "Always cite sources. Never fabricate financial numbers.",
    ]

    if ctx.deps.active_client_id:
        client = await ctx.deps.platform.get_client_profile(
            ctx.deps.active_client_id,
            ctx.deps.access_scope,
        )
        timeline = await ctx.deps.platform.get_client_timeline(
            ctx.deps.active_client_id,
            ctx.deps.access_scope,
            days=30,
        )
        parts.extend(
            [
                "",
                "Active client context:",
                f"- Client: {client.name} ({client.client_id})",
                f"- Recent activity items: {len(timeline)}",
            ]
        )

    if ctx.deps.active_household_id:
        household = await ctx.deps.platform.get_household_summary(
            ctx.deps.active_household_id,
            ctx.deps.access_scope,
        )
        parts.extend(
            [
                "",
                "Active household context:",
                f"- Household: {household.household_name} ({household.household_id})",
                f"- AUM: {household.total_aum}",
            ]
        )

    return "\n".join(parts)


@copilot_agent.tool
async def search_documents(
    ctx: RunContext[CopilotDeps], query: str
) -> list[dict]:
    """Search uploaded documents, tax returns, and estate plans."""
    return await ctx.deps.platform.search_documents_text(
        query=query,
        filters={"client_id": ctx.deps.active_client_id},
        access_scope=ctx.deps.access_scope,
    )


@copilot_agent.tool
async def get_household_summary(
    ctx: RunContext[CopilotDeps], household_id: str
) -> dict:
    """Get household overview with accounts, AUM, performance."""
    return await ctx.deps.platform.get_household_summary(
        household_id=household_id,
        access_scope=ctx.deps.access_scope,
    )


@copilot_agent.tool
async def get_account_summary(
    ctx: RunContext[CopilotDeps], account_id: str
) -> dict:
    """Get account detail with holdings, activity, status."""
    return await ctx.deps.platform.get_account_summary(
        account_id=account_id,
        access_scope=ctx.deps.access_scope,
    )


@copilot_agent.tool
async def get_client_timeline(
    ctx: RunContext[CopilotDeps], client_id: str, days: int = 90
) -> list[dict]:
    """Get aggregated activity feed for a client."""
    return await ctx.deps.platform.get_client_timeline(
        client_id=client_id,
        access_scope=ctx.deps.access_scope,
        days=days,
    )
```

---

## Feature 2: Daily Digest

**Router:** `app/routers/digest.py`
**Endpoints:** `POST /ai/digest/generate`, `GET /ai/digest/latest`
**Agent tier:** Batch (Claude Haiku 4.5, fallback Llama 3.3 70B)

```python
# app/routers/digest.py

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from langfuse import Langfuse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.dependencies import (
    RequestContext,
    get_langfuse,
    get_redis,
    get_request_context,
)
from app.utils.errors import InternalError, PlatformReadError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/digest", tags=["digest"])


# ── Response Models ─────────────────────────────────────────────

class DigestItem(BaseModel):
    type: str                             # "meeting", "task", "email", "alert", "crm_update"
    title: str
    summary: str
    client_id: str | None = None
    urgency: str                          # "high", "medium", "low"
    action_url: str | None = None

class DigestSection(BaseModel):
    title: str                            # "Today's Meetings", "Pending Tasks", "Account Alerts"
    items: list[DigestItem]

class Action(BaseModel):
    type: str
    target_id: str | None = None
    reason: str

class PriorityItem(BaseModel):
    type: str
    title: str
    summary: str
    client_id: str | None = None
    urgency: str

class DailyDigestResponse(BaseModel):
    advisor_id: str
    generated_at: str
    greeting: str
    sections: list[DigestSection]
    priority_items: list[PriorityItem]
    suggested_actions: list[Action]

class DigestJobAccepted(BaseModel):
    job_id: str
    status: str = "accepted"
    message: str = "Digest generation has been queued."


# ── Request Models ──────────────────────────────────────────────

class DigestGenerateRequest(BaseModel):
    advisor_id: str | None = None         # Defaults to actor_id if not provided
    force_regenerate: bool = False


# ── POST /ai/digest/generate ───────────────────────────────────

@router.post("/generate", response_model=DigestJobAccepted, status_code=202)
async def generate_digest(
    body: DigestGenerateRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> DigestJobAccepted:
    """
    Trigger async daily digest generation. Enqueues an ARQ job that pulls
    calendar, tasks, emails, CRM activity, and account alerts, then runs the
    digest agent. The result is cached in Redis for retrieval via GET /latest.

    Returns 202 immediately with a job reference.
    """
    from app.jobs.enqueue import JobContext, get_job_pool

    advisor_id = body.advisor_id or ctx.actor_id

    trace = langfuse.trace(
        name="digest/generate",
        user_id=advisor_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
            "force_regenerate": body.force_regenerate,
        },
    )

    # Check for existing cached digest (skip if force_regenerate)
    if not body.force_regenerate:
        from datetime import date
        cache_key = f"digest:{ctx.tenant_id}:{advisor_id}:{date.today().isoformat()}"
        cached = await redis.get(cache_key)
        if cached is not None:
            trace.update(output={"status": "cache_hit"})
            return DigestJobAccepted(
                job_id="cached",
                status="already_available",
                message="Today's digest is already available. Use GET /ai/digest/latest.",
            )

    # Enqueue ARQ job
    try:
        pool = await get_job_pool()
        job = await pool.enqueue_job(
            "generate_daily_digest",
            JobContext(
                tenant_id=ctx.tenant_id,
                actor_id=advisor_id,
                actor_type=ctx.actor_type,
                request_id=ctx.request_id,
                access_scope=ctx.access_scope.model_dump(),
            ).model_dump(),
            advisor_id,
        )
        job_id = job.job_id
    except Exception as exc:
        logger.exception("Failed to enqueue digest job")
        raise InternalError(
            detail=f"Failed to enqueue digest generation: {exc}",
            request_id=ctx.request_id,
        )

    trace.update(output={"status": "enqueued", "job_id": job_id})
    return DigestJobAccepted(job_id=job_id)


# ── GET /ai/digest/latest ──────────────────────────────────────

@router.get("/latest", response_model=DailyDigestResponse)
async def get_latest_digest(
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> DailyDigestResponse:
    """
    Retrieve the most recent cached daily digest for the current advisor.
    Checks today first, then falls back to yesterday. Returns 404 if
    no digest has been generated.
    """
    from datetime import date, timedelta

    advisor_id = ctx.actor_id
    today = date.today()

    # Try today, then yesterday
    for d in [today, today - timedelta(days=1)]:
        cache_key = f"digest:{ctx.tenant_id}:{advisor_id}:{d.isoformat()}"
        cached = await redis.get(cache_key)
        if cached is not None:
            return DailyDigestResponse(**json.loads(cached))

    raise HTTPException(
        status_code=404,
        detail={
            "code": "DIGEST_NOT_FOUND",
            "message": "No digest available. Trigger generation via POST /ai/digest/generate.",
        },
    )
```

### Digest Background Job

```python
# app/jobs/daily_digest.py  (executed by ARQ worker, referenced by the router)

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from langfuse import Langfuse

from app.agents.digest import digest_agent, DigestDeps
from app.models.access_scope import AccessScope

logger = logging.getLogger(__name__)

DIGEST_CACHE_TTL = 86400  # 24 hours


async def generate_daily_digest(
    ctx: dict,
    job_ctx: dict,
    advisor_id: str,
) -> None:
    """
    ARQ job: pull multi-source data, invoke digest agent, cache result.
    """
    redis = ctx["redis"]
    platform = ctx["platform_client"]
    langfuse = ctx["langfuse"]
    tenant_id = job_ctx["tenant_id"]
    request_id = job_ctx["request_id"]
    access_scope = AccessScope.model_validate(job_ctx["access_scope"])

    trace = langfuse.trace(
        name="digest/generate_job",
        user_id=advisor_id,
        metadata={"tenant_id": tenant_id, "request_id": request_id},
    )

    try:
        # Pull data sources in parallel
        import asyncio
        meetings, tasks, emails, alerts, crm_activity = await asyncio.gather(
            platform.get_advisor_calendar(advisor_id, access_scope),
            platform.get_advisor_tasks(advisor_id, access_scope),
            platform.get_advisor_priority_emails(advisor_id, access_scope),
            platform.get_account_alerts(advisor_id, access_scope),
            platform.get_crm_activity_feed(advisor_id, access_scope),
        )

        # Invoke digest agent
        result = await digest_agent.run(
            f"Generate daily digest for advisor {advisor_id}",
            deps=DigestDeps(
                meetings=meetings,
                tasks=tasks,
                emails=emails,
                alerts=alerts,
                crm_activity=crm_activity,
                tenant_id=tenant_id,
                advisor_id=advisor_id,
            ),
        )

        # Cache result
        cache_key = f"digest:{tenant_id}:{advisor_id}:{date.today().isoformat()}"
        digest_data = result.data.model_dump()
        digest_data["advisor_id"] = advisor_id
        digest_data["generated_at"] = datetime.now(timezone.utc).isoformat()
        await redis.set(cache_key, json.dumps(digest_data), ex=DIGEST_CACHE_TTL)

        trace.update(output={"status": "success"})

    except Exception as exc:
        logger.exception("Digest generation failed", extra={
            "tenant_id": tenant_id, "advisor_id": advisor_id,
        })
        trace.update(output={"status": "error", "error": str(exc)})
        raise
```

---

## Feature 3: Email Draft

**Router:** `app/routers/email.py` (email draft section)
**Endpoint:** `POST /ai/email/draft`
**Agent tier:** Copilot (Claude Sonnet 4.6)

```python
# app/routers/email.py

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from langfuse import Langfuse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.dependencies import (
    RequestContext,
    get_langfuse,
    get_platform_client,
    get_redis,
    get_request_context,
)
from app.services.platform_client import PlatformClient
from app.utils.errors import ModelProviderError, PlatformReadError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/email", tags=["email"])


# ── Shared Email Models ─────────────────────────────────────────

class EmailDraft(BaseModel):
    subject: str
    body: str                             # HTML or plain text
    tone_confidence: float                # How well it matches advisor's style
    suggestions: list[str]                # Alternative phrasings
    warnings: list[str]                   # "Contains specific return numbers - verify"


# ── Email Draft Request ─────────────────────────────────────────

class EmailDraftRequest(BaseModel):
    advisor_id: str | None = None         # Defaults to actor_id
    client_id: str
    intent: str                           # "quarterly_review_followup", "account_update", "meeting_request", "custom"
    context: str                          # Advisor's notes on what to communicate
    reply_to_email_id: str | None = None  # If replying to an existing thread


# ── Style Profile Loader ────────────────────────────────────────

STYLE_PROFILE_TTL = 604800  # 7 days


async def _load_style_profile(
    redis: Redis,
    tenant_id: str,
    advisor_id: str,
    platform: PlatformClient,
) -> dict:
    """
    Load advisor's email style profile from Redis cache.
    Falls back to a default profile if none has been computed yet.
    """
    cache_key = f"style_profile:{tenant_id}:{advisor_id}"
    cached = await redis.get(cache_key)
    if cached is not None:
        return json.loads(cached)

    # Return a neutral default profile — the weekly style_profile job
    # will compute a real one from the advisor's sent email history.
    return {
        "formality": "professional",
        "greeting_pattern": "Hi {first_name},",
        "sign_off_pattern": "Best regards,",
        "avg_length": "medium",
        "vocabulary_notes": [],
        "computed": False,
    }


# ── POST /ai/email/draft ───────────────────────────────────────

@router.post("/draft", response_model=EmailDraft)
async def draft_email(
    body: EmailDraftRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> EmailDraft:
    """
    Draft a client-facing email matching the advisor's writing style.

    Flow:
    1. Load advisor's style profile from Redis (computed by weekly ARQ job)
    2. Load client profile and recent correspondence for context
    3. If reply_to_email_id is provided, load the original thread
    4. Invoke email drafter agent with style profile injected
    5. Scan the draft for safety warnings (specific numbers, forward-looking statements)
    6. Return draft for advisor review — never auto-send
    """
    from app.agents.email_drafter import email_drafter_agent, EmailDrafterDeps

    advisor_id = body.advisor_id or ctx.actor_id

    trace = langfuse.trace(
        name="email/draft",
        user_id=advisor_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
            "client_id": body.client_id,
            "intent": body.intent,
        },
    )

    # Load style profile
    style_profile = await _load_style_profile(
        redis, ctx.tenant_id, advisor_id, platform,
    )

    # Load client context
    try:
        client_profile = await platform.get_client_profile(
            client_id=body.client_id,
            access_scope=ctx.access_scope,
        )
    except Exception as exc:
        raise PlatformReadError(
            detail=f"Failed to load client profile: {exc}",
            request_id=ctx.request_id,
        )

    # Load reply thread if applicable
    reply_thread = None
    if body.reply_to_email_id:
        try:
            reply_thread = await platform.get_email_thread(
                email_id=body.reply_to_email_id,
                access_scope=ctx.access_scope,
            )
        except Exception:
            logger.warning("Could not load reply thread, proceeding without it")

    # Invoke agent
    try:
        result = await email_drafter_agent.run(
            body.context,
            deps=EmailDrafterDeps(
                platform=platform,
                access_scope=ctx.access_scope,
                tenant_id=ctx.tenant_id,
                advisor_id=advisor_id,
                client_id=body.client_id,
                client_profile=client_profile,
                style_profile=style_profile,
                intent=body.intent,
                reply_thread=reply_thread,
                langfuse_trace=trace,
            ),
        )
    except Exception as exc:
        logger.exception("Email drafter agent failed")
        raise ModelProviderError(
            detail=f"Email drafting failed: {exc}",
            request_id=ctx.request_id,
        )

    # Safety warning scan
    warnings = list(result.data.warnings)
    draft_body = result.data.body

    import re
    # Warn on specific return/performance numbers
    if re.search(r'\d+\.?\d*%', draft_body):
        warnings.append("Contains percentage figures — verify accuracy before sending.")
    # Warn on dollar amounts
    if re.search(r'\$[\d,]+', draft_body):
        warnings.append("Contains dollar amounts — verify against current data.")
    # Warn on forward-looking language
    forward_looking = ["will increase", "guaranteed", "expected return", "projected"]
    for phrase in forward_looking:
        if phrase.lower() in draft_body.lower():
            warnings.append(
                f"Contains potentially forward-looking statement: '{phrase}'. "
                "Review for compliance."
            )

    trace.update(output={"status": "success", "warning_count": len(warnings)})

    return EmailDraft(
        subject=result.data.subject,
        body=draft_body,
        tone_confidence=result.data.tone_confidence,
        suggestions=result.data.suggestions,
        warnings=warnings,
    )
```

---

## Feature 4: Email Triage

**Endpoint:** `POST /ai/email/triage`
**Agent tier:** Batch (Claude Haiku 4.5)

```python
# ── Email Triage (same file: app/routers/email.py) ─────────────

class IncomingEmail(BaseModel):
    email_id: str
    from_address: str
    subject: str
    body_preview: str = Field(..., max_length=2000)
    received_at: str
    thread_id: str | None = None
    has_attachments: bool = False

class EmailTriageRequest(BaseModel):
    advisor_id: str | None = None
    emails: list[IncomingEmail] = Field(..., max_length=100)

class TriagedEmail(BaseModel):
    email_id: str
    priority: str                         # "urgent", "high", "normal", "low", "informational"
    category: str                         # "client_request", "compliance", "prospect", "vendor", "internal", "newsletter"
    client_id: str | None = None          # Matched to a platform client
    summary: str
    suggested_action: str                 # "reply_now", "reply_today", "delegate", "archive", "review_later"
    draft_reply: EmailDraft | None = None
    reasoning: str

class EmailTriageResponse(BaseModel):
    advisor_id: str
    triaged_count: int
    emails: list[TriagedEmail]
    as_of: str


@router.post("/triage", response_model=EmailTriageResponse)
async def triage_emails(
    body: EmailTriageRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> EmailTriageResponse:
    """
    Batch-triage a set of emails: classify priority and category, match
    senders to platform clients, and auto-draft replies for urgent items.

    Flow:
    1. Load advisor's client list for sender-to-client matching
    2. Invoke triage agent on the full batch (batch agent for cost)
    3. For emails classified as urgent/high, invoke drafting sub-agent
    4. Return structured triage results
    """
    from datetime import datetime, timezone
    from app.agents.email_triager import email_triager_agent, TriagerDeps

    advisor_id = body.advisor_id or ctx.actor_id

    trace = langfuse.trace(
        name="email/triage",
        user_id=advisor_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
            "email_count": len(body.emails),
        },
    )

    # Load advisor's client list for sender matching
    try:
        clients = await platform.get_advisor_clients(
            advisor_id=advisor_id,
            access_scope=ctx.access_scope,
        )
    except Exception as exc:
        logger.warning(f"Could not load client list for matching: {exc}")
        clients = []

    # Build sender-to-client lookup
    client_email_map: dict[str, dict] = {}
    for client in clients:
        for email_addr in getattr(client, "email_addresses", []):
            client_email_map[email_addr.lower()] = {
                "client_id": client.client_id,
                "client_name": getattr(client, "name", ""),
            }

    # Invoke triage agent
    try:
        result = await email_triager_agent.run(
            f"Triage {len(body.emails)} emails for advisor {advisor_id}",
            deps=TriagerDeps(
                platform=platform,
                access_scope=ctx.access_scope,
                tenant_id=ctx.tenant_id,
                advisor_id=advisor_id,
                emails=[e.model_dump() for e in body.emails],
                client_email_map=client_email_map,
                langfuse_trace=trace,
            ),
        )
    except Exception as exc:
        logger.exception("Triage agent failed")
        raise ModelProviderError(
            detail=f"Email triage failed: {exc}",
            request_id=ctx.request_id,
        )

    # Enrich with client matches and draft replies for urgent emails
    triaged: list[TriagedEmail] = []
    for item in result.data.triaged_emails:
        # Client matching from sender address
        matched = client_email_map.get(item.from_address.lower())
        client_id = matched["client_id"] if matched else item.client_id

        # Draft reply for urgent/high priority
        draft_reply = None
        if item.priority in ("urgent", "high") and item.draft_reply:
            draft_reply = EmailDraft(**item.draft_reply.model_dump())

        triaged.append(TriagedEmail(
            email_id=item.email_id,
            priority=item.priority,
            category=item.category,
            client_id=client_id,
            summary=item.summary,
            suggested_action=item.suggested_action,
            draft_reply=draft_reply,
            reasoning=item.reasoning,
        ))

    trace.update(output={
        "status": "success",
        "triaged_count": len(triaged),
        "urgent_count": sum(1 for t in triaged if t.priority == "urgent"),
    })

    return EmailTriageResponse(
        advisor_id=advisor_id,
        triaged_count=len(triaged),
        emails=triaged,
        as_of=datetime.now(timezone.utc).isoformat(),
    )
```

---

## Feature 5: Task Extraction

**Router:** `app/routers/tasks.py`
**Endpoint:** `POST /ai/tasks/extract`
**Agent tier:** Batch (Claude Haiku 4.5)

```python
# app/routers/tasks.py

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from langfuse import Langfuse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.dependencies import (
    RequestContext,
    get_langfuse,
    get_platform_client,
    get_request_context,
)
from app.services.platform_client import PlatformClient
from app.utils.errors import ModelProviderError, ValidationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/tasks", tags=["tasks"])


# ── Models ──────────────────────────────────────────────────────

class TaskExtractionRequest(BaseModel):
    source_type: str = Field(
        ..., pattern="^(meeting_transcript|email|note)$",
    )
    source_id: str
    content: str = Field(..., min_length=10, max_length=50_000)
    advisor_id: str | None = None
    client_id: str | None = None

class ExtractedTask(BaseModel):
    title: str
    description: str
    assigned_to: str | None = None
    due_date: str | None = None
    priority: str                         # "high", "medium", "low"
    client_id: str | None = None
    source_type: str
    source_id: str
    confidence: float

class TaskExtractionResponse(BaseModel):
    source_type: str
    source_id: str
    tasks: list[ExtractedTask]
    task_count: int
    as_of: str


# ── POST /ai/tasks/extract ─────────────────────────────────────

@router.post("/extract", response_model=TaskExtractionResponse)
async def extract_tasks(
    body: TaskExtractionRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> TaskExtractionResponse:
    """
    Extract structured task candidates from a meeting transcript, email,
    or advisor note. Returns tasks with titles, assignments, due dates,
    and confidence scores. Tasks are recommendations — the platform
    decides whether to create them after advisor confirmation.

    Flow:
    1. Validate source type and content length
    2. Load client context if client_id provided (for name resolution)
    3. Invoke task extraction agent with source content
    4. Return structured tasks with source attribution
    """
    from app.agents.task_extractor import task_extractor_agent, TaskExtractorDeps

    advisor_id = body.advisor_id or ctx.actor_id

    trace = langfuse.trace(
        name="tasks/extract",
        user_id=advisor_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
            "source_type": body.source_type,
            "source_id": body.source_id,
            "content_length": len(body.content),
        },
    )

    # Load team members for assignment resolution
    try:
        team_members = await platform.get_advisor_team(
            advisor_id=advisor_id,
            access_scope=ctx.access_scope,
        )
    except Exception:
        logger.warning("Could not load team members; assignment resolution will be limited")
        team_members = []

    # Invoke extraction agent
    try:
        result = await task_extractor_agent.run(
            body.content,
            deps=TaskExtractorDeps(
                source_type=body.source_type,
                source_id=body.source_id,
                advisor_id=advisor_id,
                client_id=body.client_id,
                team_members=team_members,
                langfuse_trace=trace,
            ),
        )
    except Exception as exc:
        logger.exception("Task extraction agent failed")
        raise ModelProviderError(
            detail=f"Task extraction failed: {exc}",
            request_id=ctx.request_id,
        )

    tasks = [
        ExtractedTask(
            title=t.title,
            description=t.description,
            assigned_to=t.assigned_to,
            due_date=t.due_date,
            priority=t.priority,
            client_id=t.client_id or body.client_id,
            source_type=body.source_type,
            source_id=body.source_id,
            confidence=t.confidence,
        )
        for t in result.data.tasks
    ]

    trace.update(output={"status": "success", "task_count": len(tasks)})

    return TaskExtractionResponse(
        source_type=body.source_type,
        source_id=body.source_id,
        tasks=tasks,
        task_count=len(tasks),
        as_of=datetime.now(timezone.utc).isoformat(),
    )
```

---

## Feature 6: CRM Sync Payload

**Router:** `app/routers/crm.py`
**Endpoint:** `POST /ai/crm/sync-payload`
**Agent tier:** Batch (Claude Haiku 4.5)

The CRM sync endpoint generates structured payloads that the platform executes against CRM systems. The sidecar never calls CRM APIs directly.

```python
# app/routers/crm.py

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from langfuse import Langfuse
from pydantic import BaseModel, Field

from app.dependencies import (
    RequestContext,
    get_langfuse,
    get_platform_client,
    get_request_context,
)
from app.services.platform_client import PlatformClient
from app.utils.errors import ModelProviderError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/crm", tags=["crm"])


# ── Models ──────────────────────────────────────────────────────

class CRMSyncRequest(BaseModel):
    source_type: str = Field(
        ..., pattern="^(meeting_summary|task_extraction|email_triage|manual)$",
    )
    source_id: str
    provider: str = Field(
        ..., pattern="^(salesforce|wealthbox|redtail)$",
    )
    items: list[CRMSyncItem]

class CRMSyncItem(BaseModel):
    operation: str                        # "create_task", "create_note", "update_contact", "log_activity"
    data: dict                            # Source data to transform into CRM payload
    client_id: str | None = None

class CRMSyncPayload(BaseModel):
    provider: str
    operation: str
    data: dict                            # Provider-specific shaped payload
    idempotency_key: str
    source_type: str
    source_id: str

class CRMSyncResponse(BaseModel):
    payloads: list[CRMSyncPayload]
    payload_count: int
    provider: str
    as_of: str
    warning: str = (
        "These are generated payloads for platform execution. "
        "The sidecar does not execute CRM writes."
    )


# ── POST /ai/crm/sync-payload ──────────────────────────────────

@router.post("/sync-payload", response_model=CRMSyncResponse)
async def generate_crm_sync_payload(
    body: CRMSyncRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> CRMSyncResponse:
    """
    Generate CRM sync payloads from extracted data (tasks, notes, activities).
    Returns provider-specific shaped payloads with idempotency keys.
    The platform API server executes these against the actual CRM.

    This endpoint NEVER calls CRM APIs directly.

    Flow:
    1. Validate provider and operations
    2. Load client CRM mappings (platform client IDs -> CRM contact IDs)
    3. Invoke CRM payload shaping agent to transform items into provider format
    4. Assign idempotency keys for safe retry
    5. Return payloads for platform execution
    """
    from app.agents.crm_sync import crm_sync_agent, CRMSyncDeps

    trace = langfuse.trace(
        name="crm/sync-payload",
        user_id=ctx.actor_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
            "provider": body.provider,
            "item_count": len(body.items),
        },
    )

    # Optional enrichment: in a fuller implementation this can be loaded
    # from a dedicated platform read or CRM adapter projection.
    crm_mappings = {}

    # Invoke agent
    try:
        result = await crm_sync_agent.run(
            f"Generate {body.provider} payloads for {len(body.items)} items",
            deps=CRMSyncDeps(
                provider=body.provider,
                items=[i.model_dump() for i in body.items],
                crm_mappings=crm_mappings,
                source_type=body.source_type,
                source_id=body.source_id,
                langfuse_trace=trace,
            ),
        )
    except Exception as exc:
        logger.exception("CRM sync agent failed")
        raise ModelProviderError(
            detail=f"CRM payload generation failed: {exc}",
            request_id=ctx.request_id,
        )

    # Assign idempotency keys
    payloads = [
        CRMSyncPayload(
            provider=body.provider,
            operation=p.operation,
            data=p.data,
            idempotency_key=f"{body.source_type}:{body.source_id}:{p.operation}:{uuid.uuid4().hex[:12]}",
            source_type=body.source_type,
            source_id=body.source_id,
        )
        for p in result.data.payloads
    ]

    trace.update(output={"status": "success", "payload_count": len(payloads)})

    return CRMSyncResponse(
        payloads=payloads,
        payload_count=len(payloads),
        provider=body.provider,
        as_of=datetime.now(timezone.utc).isoformat(),
    )
```

---

## Feature 7: Meeting Prep

**Router:** `app/routers/meetings.py` (prep section)
**Endpoint:** `POST /ai/meetings/prep`
**Agent tier:** Copilot (Claude Sonnet 4.6)

```python
# app/routers/meetings.py

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from langfuse import Langfuse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.dependencies import (
    RequestContext,
    get_langfuse,
    get_platform_client,
    get_redis,
    get_request_context,
)
from app.services.platform_client import PlatformClient
from app.utils.errors import ModelProviderError, PlatformReadError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/meetings", tags=["meetings"])


# ── Shared Models ───────────────────────────────────────────────

class Citation(BaseModel):
    source_type: str
    source_id: str
    title: str
    excerpt: str
    relevance_score: float

class ActivityItem(BaseModel):
    type: str
    date: str
    description: str
    client_id: str | None = None

class TalkingPoint(BaseModel):
    topic: str
    context: str
    suggested_framing: str
    priority: str                         # "must_discuss", "should_discuss", "nice_to_have"


# ── Meeting Prep Models ─────────────────────────────────────────

class MeetingPrepRequest(BaseModel):
    advisor_id: str | None = None
    client_id: str
    household_id: str | None = None
    meeting_date: str
    meeting_type: str = Field(
        ..., pattern="^(quarterly_review|annual_review|ad_hoc|prospect)$",
    )

class MeetingPrepResponse(BaseModel):
    client_id: str
    meeting_type: str
    client_summary: str
    relationship_history: str
    recent_activity: list[ActivityItem]
    account_snapshot: dict
    open_items: list[str]
    past_meeting_highlights: list[str]
    talking_points: list[TalkingPoint]
    suggested_topics: list[str]
    relevant_documents: list[Citation]
    warnings: list[str]
    as_of: str


# ── POST /ai/meetings/prep ─────────────────────────────────────

@router.post("/prep", response_model=MeetingPrepResponse)
async def prepare_meeting(
    body: MeetingPrepRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> MeetingPrepResponse:
    """
    Generate a meeting prep packet by pulling data from multiple sources
    in parallel, then invoking the meeting prep agent to synthesize
    talking points and recommendations.

    Multi-source data pull (parallel):
    - Client profile
    - Household summary when a household ID is available
    - Recent activity (last 90 days)
    - Relevant documents

    Agent synthesis:
    - Client summary and relationship context
    - Talking points prioritized by meeting type
    - Warnings (RMD deadlines, large unrealized losses, etc.)
    - Suggested discussion topics
    """
    from app.agents.meeting_prep import meeting_prep_agent, MeetingPrepDeps

    advisor_id = body.advisor_id or ctx.actor_id

    trace = langfuse.trace(
        name="meetings/prep",
        user_id=advisor_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
            "client_id": body.client_id,
            "meeting_type": body.meeting_type,
        },
    )

    # ── Multi-source parallel data pull ─────────────────────────
    try:
        (
            client_profile,
            household_summary,
            timeline,
            relevant_docs,
        ) = await asyncio.gather(
            platform.get_client_profile(body.client_id, ctx.access_scope),
            platform.get_household_summary(body.household_id, ctx.access_scope)
            if body.household_id
            else asyncio.sleep(0, result=None),
            platform.get_client_timeline(
                body.client_id, ctx.access_scope, days=90,
            ),
            platform.search_documents_text(
                query=f"client:{body.client_id}",
                filters={"client_id": body.client_id},
                access_scope=ctx.access_scope,
            ),
        )
    except Exception as exc:
        logger.exception("Failed multi-source data pull for meeting prep")
        raise PlatformReadError(
            detail=f"Failed to load meeting prep data: {exc}",
            request_id=ctx.request_id,
        )

    # ── Invoke meeting prep agent ───────────────────────────────
    try:
        result = await meeting_prep_agent.run(
            (
                f"Prepare a {body.meeting_type} meeting prep for client "
                f"{body.client_id} on {body.meeting_date}"
            ),
            deps=MeetingPrepDeps(
                platform=platform,
                access_scope=ctx.access_scope,
                tenant_id=ctx.tenant_id,
                advisor_id=advisor_id,
                client_id=body.client_id,
                meeting_type=body.meeting_type,
                meeting_date=body.meeting_date,
                client_profile=client_profile,
                household_summary=household_summary,
                timeline=timeline,
                relevant_docs=relevant_docs,
                langfuse_trace=trace,
            ),
        )
    except Exception as exc:
        logger.exception("Meeting prep agent failed")
        raise ModelProviderError(
            detail=f"Meeting prep generation failed: {exc}",
            request_id=ctx.request_id,
        )

    prep = result.data

    trace.update(output={
        "status": "success",
        "talking_point_count": len(prep.talking_points),
        "warning_count": len(prep.warnings),
    })

    return MeetingPrepResponse(
        client_id=body.client_id,
        meeting_type=body.meeting_type,
        client_summary=prep.client_summary,
        relationship_history=prep.relationship_history,
        recent_activity=[
            ActivityItem(**a.model_dump()) for a in prep.recent_activity
        ],
        account_snapshot=prep.account_snapshot,
        open_items=prep.open_items,
        past_meeting_highlights=prep.past_meeting_highlights,
        talking_points=[
            TalkingPoint(**t.model_dump()) for t in prep.talking_points
        ],
        suggested_topics=prep.suggested_topics,
        relevant_documents=[
            Citation(**d.model_dump()) for d in prep.relevant_documents
        ],
        warnings=prep.warnings,
        as_of=datetime.now(timezone.utc).isoformat(),
    )
```

---

## Feature 8: Meeting Transcription

**Endpoint:** `POST /ai/meetings/transcribe`
**Agent tier:** Transcription (Whisper large-v3 / Deepgram Nova-3)

This endpoint accepts an audio reference and dispatches an ARQ background job. It returns HTTP 202 immediately.

```python
# ── Meeting Transcription (same file: app/routers/meetings.py) ──

class TranscriptionRequest(BaseModel):
    meeting_id: str
    audio_storage_ref: str                # Platform-managed object storage reference
    audio_format: str = "wav"             # "wav", "mp3", "webm", "m4a"
    duration_seconds: int | None = None   # Estimated duration for job sizing
    speaker_count: int | None = None      # Hint for diarization

class TranscriptionJobAccepted(BaseModel):
    job_id: str
    meeting_id: str
    status: str = "accepted"
    message: str = "Transcription job has been queued."
    estimated_duration_minutes: int | None = None


@router.post("/transcribe", response_model=TranscriptionJobAccepted, status_code=202)
async def transcribe_meeting(
    body: TranscriptionRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> TranscriptionJobAccepted:
    """
    Accept an audio storage reference and dispatch an ARQ transcription job.
    Returns 202 immediately. The worker pipeline:

    1. Download audio from platform-managed object storage
    2. Transcribe via Whisper API or Deepgram
    3. Perform speaker diarization if supported
    4. Store raw transcript via platform API
    5. Auto-trigger summarization job on completion

    The platform is notified of completion via callback or polling.
    """
    from app.jobs.enqueue import JobContext, enqueue_transcription
    from app.utils.errors import InternalError

    trace = langfuse.trace(
        name="meetings/transcribe",
        user_id=ctx.actor_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
            "meeting_id": body.meeting_id,
            "audio_format": body.audio_format,
        },
    )

    # Estimate processing time (rough: 1 min of audio ~ 10 sec processing)
    estimated_minutes = None
    if body.duration_seconds:
        estimated_minutes = max(1, body.duration_seconds // 60 // 6)

    try:
        job_id = await enqueue_transcription(
            JobContext(
                tenant_id=ctx.tenant_id,
                actor_id=ctx.actor_id,
                actor_type=ctx.actor_type,
                request_id=ctx.request_id,
                access_scope=ctx.access_scope.model_dump(),
            ),
            meeting_id=body.meeting_id,
            audio_object_key=body.audio_storage_ref,
            audio_duration_seconds=body.duration_seconds or 0,
        )
    except Exception as exc:
        logger.exception("Failed to enqueue transcription job")
        raise InternalError(
            detail=f"Failed to enqueue transcription: {exc}",
            request_id=ctx.request_id,
        )

    # Store job metadata for status polling
    job_meta_key = f"transcription_job:{ctx.tenant_id}:{body.meeting_id}"
    import json
    await redis.set(
        job_meta_key,
        json.dumps({
            "job_id": job_id,
            "meeting_id": body.meeting_id,
            "status": "queued",
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }),
        ex=86400,  # 24 hour TTL
    )

    trace.update(output={"status": "enqueued", "job_id": job_id})

    return TranscriptionJobAccepted(
        job_id=job_id,
        meeting_id=body.meeting_id,
        estimated_duration_minutes=estimated_minutes,
    )
```

---

## Feature 9: Meeting Summary

**Endpoints:** `POST /ai/meetings/summarize`, `GET /ai/meetings/{meeting_id}/summary`
**Agent tier:** Copilot (Claude Sonnet 4.6)

```python
# ── Meeting Summary (same file: app/routers/meetings.py) ───────

class TopicSection(BaseModel):
    topic: str
    summary: str
    speaker_attribution: dict[str, str]   # Who said what
    decisions_made: list[str]

class ExtractedTask(BaseModel):
    title: str
    description: str
    assigned_to: str | None = None
    due_date: str | None = None
    priority: str
    confidence: float

class MeetingSummarizeRequest(BaseModel):
    meeting_id: str
    transcript: str = Field(..., min_length=50, max_length=200_000)
    participants: list[str] = Field(default_factory=list)
    duration_minutes: int | None = None

class MeetingSummaryResponse(BaseModel):
    meeting_id: str
    duration_minutes: int | None
    participants: list[str]
    executive_summary: str
    key_topics: list[TopicSection]
    action_items: list[ExtractedTask]
    follow_up_drafts: list[dict]          # Suggested follow-up emails
    client_sentiment: str | None = None   # "positive", "neutral", "concerned"
    next_steps: list[str]
    crm_sync_payloads: list[dict]
    as_of: str


@router.post("/summarize", response_model=MeetingSummaryResponse)
async def summarize_meeting(
    body: MeetingSummarizeRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> MeetingSummaryResponse:
    """
    Generate a structured meeting summary from a transcript.

    Flow:
    1. Validate transcript length and participants
    2. Invoke meeting summarizer agent (Copilot tier)
    3. Extract action items as structured tasks
    4. Generate follow-up email draft suggestions
    5. Generate CRM sync payloads for notes/activities
    6. Assess client sentiment if detectable
    """
    from app.agents.meeting_summarizer import (
        meeting_summarizer_agent,
        SummarizerDeps,
    )

    trace = langfuse.trace(
        name="meetings/summarize",
        user_id=ctx.actor_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
            "meeting_id": body.meeting_id,
            "transcript_length": len(body.transcript),
        },
    )

    try:
        result = await meeting_summarizer_agent.run(
            body.transcript,
            deps=SummarizerDeps(
                platform=platform,
                access_scope=ctx.access_scope,
                tenant_id=ctx.tenant_id,
                actor_id=ctx.actor_id,
                meeting_id=body.meeting_id,
                participants=body.participants,
                duration_minutes=body.duration_minutes,
                langfuse_trace=trace,
            ),
        )
    except Exception as exc:
        logger.exception("Meeting summarizer agent failed")
        raise ModelProviderError(
            detail=f"Meeting summarization failed: {exc}",
            request_id=ctx.request_id,
        )

    summary = result.data

    # Cache the summary for GET retrieval
    import json
    summary_key = f"meeting_summary:{ctx.tenant_id}:{body.meeting_id}"
    summary_data = {
        "meeting_id": body.meeting_id,
        "duration_minutes": body.duration_minutes,
        "participants": body.participants,
        "executive_summary": summary.executive_summary,
        "key_topics": [t.model_dump() for t in summary.key_topics],
        "action_items": [a.model_dump() for a in summary.action_items],
        "follow_up_drafts": [d.model_dump() for d in summary.follow_up_drafts],
        "client_sentiment": summary.client_sentiment,
        "next_steps": summary.next_steps,
        "crm_sync_payloads": [p.model_dump() for p in summary.crm_sync_payloads],
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
    await redis.set(summary_key, json.dumps(summary_data), ex=604800)  # 7 days

    trace.update(output={
        "status": "success",
        "topic_count": len(summary.key_topics),
        "action_item_count": len(summary.action_items),
    })

    return MeetingSummaryResponse(**summary_data)


@router.get("/{meeting_id}/summary", response_model=MeetingSummaryResponse)
async def get_meeting_summary(
    meeting_id: str,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> MeetingSummaryResponse:
    """
    Retrieve a previously generated meeting summary by meeting ID.
    Returns 404 if no summary exists for this meeting.
    """
    import json

    summary_key = f"meeting_summary:{ctx.tenant_id}:{meeting_id}"
    cached = await redis.get(summary_key)

    if cached is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "MEETING_SUMMARY_NOT_FOUND",
                "message": f"No summary found for meeting {meeting_id}. "
                           "Generate one via POST /ai/meetings/summarize.",
            },
        )

    return MeetingSummaryResponse(**json.loads(cached))
```

---

## Feature 10: Tax Planning

**Router:** `app/routers/tax.py`
**Endpoint:** `POST /ai/tax/plan`
**Agent tier:** Copilot (Claude Sonnet 4.6) for standard plans, Analysis (Claude Opus 4.6) for complex scenarios

```python
# app/routers/tax.py

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from langfuse import Langfuse
from pydantic import BaseModel, Field

from app.dependencies import (
    RequestContext,
    get_langfuse,
    get_platform_client,
    get_request_context,
)
from app.services.platform_client import PlatformClient
from app.utils.errors import ModelProviderError, PlatformReadError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/tax", tags=["tax"])

MANDATORY_DISCLAIMER = (
    "This analysis is generated as decision-support for the advisor. "
    "It does not constitute tax advice. All figures are estimates based on "
    "available data and stated assumptions. Verify with a qualified tax "
    "professional before taking action."
)

VALID_SCENARIOS = {
    "roth_conversion", "tax_loss_harvest", "charitable_giving",
    "rmd_strategy", "gain_deferral", "income_timing", "qcd",
}


# ── Models ──────────────────────────────────────────────────────

class Action(BaseModel):
    type: str
    target_id: str | None = None
    reason: str

class TaxSituation(BaseModel):
    filing_status: str
    estimated_income: float
    estimated_tax_bracket: float
    capital_gains_summary: dict
    rmd_status: dict | None = None
    loss_harvesting_potential: float

class TaxOpportunity(BaseModel):
    type: str                             # "tax_loss_harvest", "roth_conversion", etc.
    description: str
    estimated_impact: float               # Dollars saved/deferred
    confidence: str                       # "high", "medium", "low"
    action: Action
    assumptions: list[str]

class TaxScenario(BaseModel):
    name: str                             # "Harvest all losses", "Convert $50K to Roth"
    inputs: dict
    projected_tax_liability: float
    compared_to_baseline: float           # Delta
    trade_offs: list[str]

class TaxPlanRequest(BaseModel):
    client_id: str
    tax_year: int = Field(..., ge=2020, le=2030)
    documents: list[str] = Field(default_factory=list)  # Document IDs
    include_scenarios: list[str] = Field(default_factory=list)

class TaxPlanResponse(BaseModel):
    client_id: str
    tax_year: int
    current_situation: TaxSituation
    opportunities: list[TaxOpportunity]
    scenarios: list[TaxScenario]
    warnings: list[str]
    disclaimer: str
    as_of: str


# ── POST /ai/tax/plan ──────────────────────────────────────────

@router.post("/plan", response_model=TaxPlanResponse)
async def generate_tax_plan(
    body: TaxPlanRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> TaxPlanResponse:
    """
    Generate a tax plan with opportunity analysis and what-if scenarios.

    Pipeline:
    1. Validate requested scenarios
    2. Extract data from uploaded tax documents (doc extraction agent)
    3. Pull custodial data: holdings, cost basis, realized gains (platform API)
    4. Pull client financial profile: income, filing status, tax bracket
    5. Invoke tax planning agent for opportunity analysis
    6. For each requested scenario, run what-if modeling
    7. Attach mandatory disclaimer and source freshness metadata

    Uses Copilot tier for standard plans. Escalates to Analysis tier (Opus)
    when more than 3 scenarios are requested or documents exceed 5.
    """
    from app.agents.tax_planner import (
        tax_planner_agent,
        tax_planner_analysis_agent,
        TaxPlannerDeps,
    )
    from app.agents.doc_extractor import doc_extractor_agent, DocExtractorDeps

    trace = langfuse.trace(
        name="tax/plan",
        user_id=ctx.actor_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
            "client_id": body.client_id,
            "tax_year": body.tax_year,
            "scenario_count": len(body.include_scenarios),
            "document_count": len(body.documents),
        },
    )

    # Validate scenarios
    invalid = set(body.include_scenarios) - VALID_SCENARIOS
    if invalid:
        from app.utils.errors import ValidationError
        raise ValidationError(
            detail=f"Invalid scenarios: {invalid}. Valid: {VALID_SCENARIOS}",
            request_id=ctx.request_id,
        )

    # ── Stage 1: Document extraction + platform data pull (parallel) ──
    try:
        # Extract data from tax documents
        doc_extraction_tasks = []
        for doc_id in body.documents:
            doc_extraction_tasks.append(
                _extract_tax_document(
                    doc_id=doc_id,
                    platform=platform,
                    access_scope=ctx.access_scope,
                    trace=trace,
                )
            )

        # Platform data reads
        results = await asyncio.gather(
            platform.get_client_profile(body.client_id, ctx.access_scope),
            platform.get_client_holdings(
                body.client_id, ctx.access_scope, include_cost_basis=True,
            ),
            platform.get_client_realized_gains(
                body.client_id, ctx.access_scope, tax_year=body.tax_year,
            ),
            *doc_extraction_tasks,
            return_exceptions=True,
        )
    except Exception as exc:
        raise PlatformReadError(
            detail=f"Failed to load tax planning data: {exc}",
            request_id=ctx.request_id,
        )

    # Unpack results, handling partial failures
    client_profile = results[0] if not isinstance(results[0], Exception) else {}
    holdings = results[1] if not isinstance(results[1], Exception) else []
    realized_gains = results[2] if not isinstance(results[2], Exception) else {}

    doc_extractions = []
    warnings = []
    for i, r in enumerate(results[3:]):
        if isinstance(r, Exception):
            warnings.append(
                f"Failed to extract document {body.documents[i]}: {r}"
            )
        else:
            doc_extractions.append(r)

    # ── Stage 2: Choose agent tier ──────────────────────────────
    use_analysis_tier = (
        len(body.include_scenarios) > 3 or len(body.documents) > 5
    )
    agent = tax_planner_analysis_agent if use_analysis_tier else tax_planner_agent

    # ── Stage 3: Invoke tax planning agent ──────────────────────
    try:
        result = await agent.run(
            (
                f"Generate tax plan for client {body.client_id}, "
                f"tax year {body.tax_year}, "
                f"scenarios: {body.include_scenarios}"
            ),
            deps=TaxPlannerDeps(
                platform=platform,
                access_scope=ctx.access_scope,
                tenant_id=ctx.tenant_id,
                client_id=body.client_id,
                tax_year=body.tax_year,
                client_profile=client_profile,
                holdings=holdings,
                realized_gains=realized_gains,
                doc_extractions=doc_extractions,
                requested_scenarios=body.include_scenarios,
                langfuse_trace=trace,
            ),
        )
    except Exception as exc:
        logger.exception("Tax planning agent failed")
        raise ModelProviderError(
            detail=f"Tax plan generation failed: {exc}",
            request_id=ctx.request_id,
        )

    plan = result.data
    warnings.extend(plan.warnings)

    trace.update(output={
        "status": "success",
        "opportunity_count": len(plan.opportunities),
        "scenario_count": len(plan.scenarios),
        "agent_tier": "analysis" if use_analysis_tier else "copilot",
    })

    return TaxPlanResponse(
        client_id=body.client_id,
        tax_year=body.tax_year,
        current_situation=TaxSituation(**plan.current_situation.model_dump()),
        opportunities=[
            TaxOpportunity(**o.model_dump()) for o in plan.opportunities
        ],
        scenarios=[TaxScenario(**s.model_dump()) for s in plan.scenarios],
        warnings=warnings,
        disclaimer=MANDATORY_DISCLAIMER,
        as_of=datetime.now(timezone.utc).isoformat(),
    )


async def _extract_tax_document(
    doc_id: str,
    platform: PlatformClient,
    access_scope: AccessScope,
    trace,
) -> dict:
    """Helper: extract structured data from a tax document."""
    from app.agents.doc_extractor import doc_extractor_agent, DocExtractorDeps

    doc_meta = await platform.get_document_metadata(doc_id, access_scope)
    doc_content = await platform.get_document_content(doc_id, access_scope)

    result = await doc_extractor_agent.run(
        f"Extract tax-relevant fields from document: {doc_meta.title or doc_id}",
        deps=DocExtractorDeps(
            document_id=doc_id,
            document_type=doc_meta.document_type or "unknown",
            content=doc_content,
            extraction_schema="tax_document",
        ),
    )
    return result.data.model_dump()
```

---

## Feature 11: Portfolio Analytics

**Router:** `app/routers/portfolio.py`
**Endpoint:** `POST /ai/portfolio/analyze`
**Agent tier:** Copilot (Claude Sonnet 4.6)

```python
# app/routers/portfolio.py

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from langfuse import Langfuse
from pydantic import BaseModel, Field

from app.dependencies import (
    RequestContext,
    get_langfuse,
    get_platform_client,
    get_request_context,
)
from app.services.platform_client import PlatformClient
from app.utils.errors import ModelProviderError, PlatformReadError, ValidationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/portfolio", tags=["portfolio"])

VALID_ANALYSIS_TYPES = {
    "concentration", "exposure", "rmd_status", "loss_harvest",
    "drift", "beneficiary_audit", "cash_drag",
}


# ── Models ──────────────────────────────────────────────────────

class Action(BaseModel):
    type: str
    target_id: str | None = None
    reason: str

class AnalysisResult(BaseModel):
    type: str
    title: str
    summary: str
    data: dict                            # Type-specific structured data
    severity: str                         # "info", "warning", "action_needed"

class Alert(BaseModel):
    type: str
    title: str
    description: str
    client_id: str
    account_id: str | None = None
    urgency: str                          # "high", "medium", "low"

class PortfolioAnalysisRequest(BaseModel):
    client_id: str
    analysis_types: list[str] = Field(
        default_factory=lambda: list(VALID_ANALYSIS_TYPES),
    )
    account_ids: list[str] | None = None  # Limit to specific accounts

class PortfolioAnalysisResponse(BaseModel):
    client_id: str
    as_of: str
    analyses: list[AnalysisResult]
    alerts: list[Alert]
    recommended_actions: list[Action]


# ── POST /ai/portfolio/analyze ──────────────────────────────────

@router.post("/analyze", response_model=PortfolioAnalysisResponse)
async def analyze_portfolio(
    body: PortfolioAnalysisRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> PortfolioAnalysisResponse:
    """
    Run multi-type portfolio analytics on a client's accounts.

    Supported analysis types:
    - concentration: single stock >10%, sector >30%, geographic overweight
    - exposure: sector/asset class vs benchmark and model targets
    - rmd_status: RMD age, calculated amounts, deadline tracking
    - loss_harvest: unrealized losses, replacement candidates, wash sale windows
    - drift: portfolio drift from target allocation
    - beneficiary_audit: missing or outdated beneficiary designations
    - cash_drag: excessive uninvested cash

    These analyses mix deterministic threshold models (concentration, drift,
    beneficiary completeness) with LLM-powered explanation and recommendation
    generation.

    Flow:
    1. Validate requested analysis types
    2. Pull holdings, account registrations, benchmarks (platform API)
    3. Run deterministic analytical models for threshold checks
    4. Invoke portfolio analyst agent for explanation and recommendations
    5. Merge alerts from all analysis types
    """
    from app.agents.portfolio_analyst import (
        portfolio_analyst_agent,
        PortfolioAnalystDeps,
    )
    from app.models.analytics import (
        run_concentration_model,
        run_drift_model,
        run_rmd_model,
        run_loss_harvest_model,
        run_beneficiary_audit,
        run_cash_drag_model,
    )

    trace = langfuse.trace(
        name="portfolio/analyze",
        user_id=ctx.actor_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
            "client_id": body.client_id,
            "analysis_types": body.analysis_types,
        },
    )

    # Validate
    invalid = set(body.analysis_types) - VALID_ANALYSIS_TYPES
    if invalid:
        raise ValidationError(
            detail=f"Invalid analysis types: {invalid}. Valid: {VALID_ANALYSIS_TYPES}",
            request_id=ctx.request_id,
        )

    # ── Platform data pull ──────────────────────────────────────
    try:
        holdings, accounts, client_profile, benchmarks = await asyncio.gather(
            platform.get_client_holdings(
                body.client_id, ctx.access_scope, include_cost_basis=True,
            ),
            platform.get_client_accounts(
                body.client_id, ctx.access_scope,
                account_ids=body.account_ids,
            ),
            platform.get_client_profile(body.client_id, ctx.access_scope),
            platform.get_benchmark_data(ctx.access_scope),
        )
    except Exception as exc:
        raise PlatformReadError(
            detail=f"Failed to load portfolio data: {exc}",
            request_id=ctx.request_id,
        )

    # ── Deterministic analytical models ─────────────────────────
    model_results: dict[str, dict] = {}
    model_alerts: list[dict] = []

    analysis_runners = {
        "concentration": lambda: run_concentration_model(holdings, accounts),
        "drift": lambda: run_drift_model(holdings, accounts, benchmarks),
        "rmd_status": lambda: run_rmd_model(accounts, client_profile),
        "loss_harvest": lambda: run_loss_harvest_model(holdings),
        "beneficiary_audit": lambda: run_beneficiary_audit(accounts),
        "cash_drag": lambda: run_cash_drag_model(holdings, accounts),
    }

    for analysis_type in body.analysis_types:
        runner = analysis_runners.get(analysis_type)
        if runner:
            try:
                result = runner()
                model_results[analysis_type] = result["data"]
                model_alerts.extend(result.get("alerts", []))
            except Exception as exc:
                logger.warning(f"Analytical model failed for {analysis_type}: {exc}")
                model_results[analysis_type] = {"error": str(exc)}

    # ── LLM agent for explanation + recommendations ─────────────
    try:
        result = await portfolio_analyst_agent.run(
            (
                f"Analyze portfolio for client {body.client_id}. "
                f"Requested analyses: {body.analysis_types}"
            ),
            deps=PortfolioAnalystDeps(
                platform=platform,
                access_scope=ctx.access_scope,
                tenant_id=ctx.tenant_id,
                client_id=body.client_id,
                holdings=holdings,
                accounts=accounts,
                client_profile=client_profile,
                benchmarks=benchmarks,
                model_results=model_results,
                model_alerts=model_alerts,
                langfuse_trace=trace,
            ),
        )
    except Exception as exc:
        logger.exception("Portfolio analyst agent failed")
        raise ModelProviderError(
            detail=f"Portfolio analysis failed: {exc}",
            request_id=ctx.request_id,
        )

    agent_output = result.data

    trace.update(output={
        "status": "success",
        "analysis_count": len(agent_output.analyses),
        "alert_count": len(agent_output.alerts),
    })

    return PortfolioAnalysisResponse(
        client_id=body.client_id,
        as_of=datetime.now(timezone.utc).isoformat(),
        analyses=[
            AnalysisResult(**a.model_dump()) for a in agent_output.analyses
        ],
        alerts=[Alert(**a.model_dump()) for a in agent_output.alerts],
        recommended_actions=[
            Action(**a.model_dump()) for a in agent_output.recommended_actions
        ],
    )
```

---

## Feature 12: Firm-Wide Reports

**Router:** `app/routers/reports.py`
**Endpoint:** `POST /ai/reports/firm-wide`
**Agent tier:** Analysis (Claude Opus 4.6)

This is an async job endpoint that returns HTTP 202.

```python
# app/routers/reports.py

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from langfuse import Langfuse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.dependencies import (
    RequestContext,
    get_langfuse,
    get_platform_client,
    get_redis,
    get_request_context,
)
from app.services.platform_client import PlatformClient
from app.utils.errors import InternalError, ModelProviderError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/reports", tags=["reports"])

VALID_REPORT_TYPES = {
    "rmd_audit", "loss_harvest_sweep", "concentration_scan",
    "compliance_review", "beneficiary_audit", "cash_drag_sweep",
    "stale_document_review",
}


# ── Firm-Wide Report Models ─────────────────────────────────────

class FirmWideReportRequest(BaseModel):
    report_type: str
    filters: dict = Field(default_factory=dict)  # Optional scoping filters

class FirmWideJobAccepted(BaseModel):
    job_id: str
    report_type: str
    status: str = "accepted"
    message: str = "Firm-wide report generation has been queued."

class FlaggedItem(BaseModel):
    client_id: str
    client_name: str
    account_id: str | None = None
    issue: str
    severity: str
    recommended_action: dict
    estimated_impact: float | None = None

class ReportSection(BaseModel):
    title: str
    summary: str
    items: list[dict]

class FirmWideReportResponse(BaseModel):
    firm_id: str
    report_type: str
    generated_at: str
    summary: str
    sections: list[ReportSection]
    flagged_items: list[FlaggedItem]
    total_opportunity: float | None = None


# ── POST /ai/reports/firm-wide ──────────────────────────────────

@router.post("/firm-wide", response_model=FirmWideJobAccepted, status_code=202)
async def generate_firm_wide_report(
    body: FirmWideReportRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> FirmWideJobAccepted:
    """
    Dispatch an async firm-wide analytical report job. Returns 202 immediately.

    The ARQ worker will:
    1. Load all accounts/clients in the tenant (scoped by access_scope)
    2. Run per-account analysis agents (batched for throughput)
    3. Aggregate results into firm-level findings
    4. Store the report artifact for platform retrieval

    Report types: rmd_audit, loss_harvest_sweep, concentration_scan,
    compliance_review, beneficiary_audit, cash_drag_sweep, stale_document_review.
    """
    from app.utils.errors import ValidationError

    if body.report_type not in VALID_REPORT_TYPES:
        raise ValidationError(
            detail=f"Invalid report_type: {body.report_type}. Valid: {VALID_REPORT_TYPES}",
            request_id=ctx.request_id,
        )

    trace = langfuse.trace(
        name="reports/firm-wide",
        user_id=ctx.actor_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
            "report_type": body.report_type,
        },
    )

    job_id = str(uuid.uuid4())

    try:
        from app.jobs.enqueue import JobContext, enqueue_firm_report

        queued_job_id = await enqueue_firm_report(
            JobContext(
                tenant_id=ctx.tenant_id,
                actor_id=ctx.actor_id,
                actor_type=ctx.actor_type,
                request_id=ctx.request_id,
                access_scope=ctx.access_scope.model_dump(),
            ),
            report_type=body.report_type,
            filters=body.filters,
        )
        job_id = queued_job_id
    except Exception as exc:
        logger.exception("Failed to enqueue firm-wide report job")
        raise InternalError(
            detail=f"Failed to enqueue report generation: {exc}",
            request_id=ctx.request_id,
        )

    # Store job metadata
    job_key = f"firm_report_job:{ctx.tenant_id}:{job_id}"
    await redis.set(
        job_key,
        json.dumps({
            "job_id": job_id,
            "report_type": body.report_type,
            "status": "queued",
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }),
        ex=86400,
    )

    trace.update(output={"status": "enqueued", "job_id": job_id})

    return FirmWideJobAccepted(
        job_id=job_id,
        report_type=body.report_type,
    )
```

---

## Feature 13: Report Narrative

**Endpoint:** `POST /ai/reports/narrative`
**Agent tier:** Copilot (Claude Sonnet 4.6)

The report narrative endpoint accepts a frozen data snapshot and generates prose narrative. It does not query live data -- the platform provides the snapshot.

```python
# ── Report Narrative (same file: app/routers/reports.py) ────────

class ReportNarrativeRequest(BaseModel):
    report_id: str
    report_type: str                      # "performance", "quarterly_review", "annual", "custom"
    snapshot: dict                        # Frozen data snapshot from platform
    tone: str = "professional"            # "professional", "conversational", "formal"
    max_length_words: int = Field(default=1000, ge=100, le=5000)
    include_sections: list[str] = Field(
        default_factory=lambda: [
            "executive_summary", "performance_review",
            "market_context", "outlook", "recommendations",
        ],
    )
class ReportNarrativeResponse(BaseModel):
    report_id: str
    narrative: str                        # Full narrative text (Markdown)
    sections: list[NarrativeSection]
    word_count: int
    warnings: list[str]
    as_of: str

class NarrativeSection(BaseModel):
    title: str
    content: str
    data_references: list[str]            # Keys from snapshot that were used


@router.post("/narrative", response_model=ReportNarrativeResponse)
async def generate_report_narrative(
    body: ReportNarrativeRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> ReportNarrativeResponse:
    """
    Generate a prose narrative for a report from a frozen data snapshot.

    The platform provides the snapshot (performance numbers, allocation data,
    market indices, etc.) and the sidecar generates human-readable narrative.
    This ensures the narrative references the exact same data the report displays.

    Flow:
    1. Validate snapshot structure has minimum required fields
    2. Invoke narrative agent with snapshot, tone, and section instructions
    3. Scan narrative for data consistency warnings
    4. Return narrative with section breakdown and data references
    """
    from app.agents.report_narrator import (
        report_narrative_agent,
        NarrativeDeps,
    )

    trace = langfuse.trace(
        name="reports/narrative",
        user_id=ctx.actor_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
            "report_id": body.report_id,
            "report_type": body.report_type,
        },
    )

    # Validate snapshot has content
    if not body.snapshot:
        from app.utils.errors import ValidationError
        raise ValidationError(
            detail="Snapshot cannot be empty. Provide a frozen data snapshot.",
            request_id=ctx.request_id,
        )

    try:
        result = await report_narrative_agent.run(
            (
                f"Generate a {body.tone} narrative for a {body.report_type} report. "
                f"Target length: {body.max_length_words} words. "
                f"Sections: {body.include_sections}"
            ),
            deps=NarrativeDeps(
                report_id=body.report_id,
                report_type=body.report_type,
                snapshot=body.snapshot,
                tone=body.tone,
                max_length_words=body.max_length_words,
                include_sections=body.include_sections,
                langfuse_trace=trace,
            ),
        )
    except Exception as exc:
        logger.exception("Report narrative agent failed")
        raise ModelProviderError(
            detail=f"Narrative generation failed: {exc}",
            request_id=ctx.request_id,
        )

    narrative_data = result.data
    word_count = len(narrative_data.narrative.split())

    warnings = list(narrative_data.warnings)
    if word_count > body.max_length_words * 1.2:
        warnings.append(
            f"Narrative exceeded target length ({word_count} words vs "
            f"{body.max_length_words} target)."
        )

    trace.update(output={
        "status": "success",
        "word_count": word_count,
        "section_count": len(narrative_data.sections),
    })

    return ReportNarrativeResponse(
        report_id=body.report_id,
        narrative=narrative_data.narrative,
        sections=[
            NarrativeSection(**s.model_dump())
            for s in narrative_data.sections
        ],
        word_count=word_count,
        warnings=warnings,
        as_of=datetime.now(timezone.utc).isoformat(),
    )
```

---

## Feature 14: Document Classify and Extract

**Router:** `app/routers/documents.py`
**Endpoints:** `POST /ai/documents/classify`, `POST /ai/documents/extract`
**Agent tier:** Extraction (Claude Haiku 4.5)

```python
# app/routers/documents.py

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from langfuse import Langfuse
from pydantic import BaseModel, Field

from app.dependencies import (
    RequestContext,
    get_langfuse,
    get_platform_client,
    get_request_context,
)
from app.services.platform_client import PlatformClient
from app.utils.errors import ModelProviderError, PlatformReadError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/documents", tags=["documents"])

KNOWN_DOCUMENT_TYPES = [
    "tax_return_1040", "tax_return_1099", "w2", "k1",
    "estate_plan", "trust_document", "power_of_attorney",
    "account_statement", "trade_confirmation",
    "insurance_policy", "annuity_contract",
    "letter", "correspondence", "other",
]


# ── Classification Models ───────────────────────────────────────

class DocumentClassifyRequest(BaseModel):
    document_id: str
    filename: str | None = None
    content_preview: str | None = None    # First 2000 chars if available

class DocumentClassification(BaseModel):
    document_id: str
    document_type: str
    confidence: float
    sub_type: str | None = None           # e.g., "1040_schedule_c" for tax returns
    tax_year: int | None = None
    client_association: str | None = None  # Detected client name/ID
    key_entities: list[str]               # Names, account numbers, dates detected
    as_of: str


@router.post("/classify", response_model=DocumentClassification)
async def classify_document(
    body: DocumentClassifyRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> DocumentClassification:
    """
    Classify an uploaded document by type, detect key entities, and
    suggest client association.

    Flow:
    1. Load document content from platform storage (first N pages)
    2. Invoke classification agent with content + filename hints
    3. Cross-reference detected entities against platform clients
    4. Return classification with confidence score
    """
    from app.agents.doc_classifier import doc_classifier_agent, ClassifierDeps

    trace = langfuse.trace(
        name="documents/classify",
        user_id=ctx.actor_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
            "document_id": body.document_id,
        },
    )

    # Load document content
    try:
        doc_meta = await platform.get_document_metadata(
            body.document_id, ctx.access_scope,
        )
        doc_content = await platform.get_document_content(
            body.document_id, ctx.access_scope,
        )
    except Exception as exc:
        raise PlatformReadError(
            detail=f"Failed to load document: {exc}",
            request_id=ctx.request_id,
        )

    # Invoke classifier
    try:
        result = await doc_classifier_agent.run(
            (
                f"Classify this document. Filename: {body.filename or doc_meta.get('filename', 'unknown')}. "
                f"Known types: {KNOWN_DOCUMENT_TYPES}"
            ),
            deps=ClassifierDeps(
                document_id=body.document_id,
                content=doc_content,
                filename=body.filename or doc_meta.get("filename"),
                content_preview=body.content_preview,
                known_types=KNOWN_DOCUMENT_TYPES,
                langfuse_trace=trace,
            ),
        )
    except Exception as exc:
        logger.exception("Document classifier agent failed")
        raise ModelProviderError(
            detail=f"Document classification failed: {exc}",
            request_id=ctx.request_id,
        )

    classification = result.data

    # Cross-reference detected entities with platform clients
    client_association = None
    if classification.detected_names:
        try:
            clients = await platform.get_advisor_clients(
                advisor_id=ctx.actor_id,
                access_scope=ctx.access_scope,
            )
            for name in classification.detected_names:
                for client in clients:
                    if name.lower() in getattr(client, "name", "").lower():
                        client_association = client.client_id
                        break
                if client_association:
                    break
        except Exception:
            logger.warning("Could not cross-reference entities with clients")

    trace.update(output={
        "status": "success",
        "document_type": classification.document_type,
        "confidence": classification.confidence,
    })

    return DocumentClassification(
        document_id=body.document_id,
        document_type=classification.document_type,
        confidence=classification.confidence,
        sub_type=classification.sub_type,
        tax_year=classification.tax_year,
        client_association=client_association,
        key_entities=classification.key_entities,
        as_of=datetime.now(timezone.utc).isoformat(),
    )


# ── Extraction Models ───────────────────────────────────────────

class DocumentExtractRequest(BaseModel):
    document_id: str
    document_type: str                    # From classification or user-specified
    extraction_schema: str = "auto"       # "auto", "tax_return", "estate_plan", "statement", "custom"
    custom_fields: list[str] | None = None  # For custom extraction

class ExtractedField(BaseModel):
    field_name: str
    value: str | float | int | bool | None
    confidence: float
    source_page: int | None = None
    source_excerpt: str | None = None

class DocumentExtractResponse(BaseModel):
    document_id: str
    document_type: str
    extraction_schema: str
    fields: list[ExtractedField]
    field_count: int
    warnings: list[str]
    as_of: str


@router.post("/extract", response_model=DocumentExtractResponse)
async def extract_document(
    body: DocumentExtractRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> DocumentExtractResponse:
    """
    Extract structured fields from a classified document.

    Flow:
    1. Load document content from platform storage
    2. Select extraction schema based on document_type (or use custom_fields)
    3. Invoke document extraction agent
    4. Validate extracted values against known constraints
    5. Return structured fields with confidence and source references
    """
    from app.agents.doc_extractor import doc_extractor_agent, DocExtractorDeps

    trace = langfuse.trace(
        name="documents/extract",
        user_id=ctx.actor_id,
        metadata={
            "tenant_id": ctx.tenant_id,
            "request_id": ctx.request_id,
            "document_id": body.document_id,
            "document_type": body.document_type,
            "extraction_schema": body.extraction_schema,
        },
    )

    # Load document
    try:
        doc_content = await platform.get_document_content(
            body.document_id, ctx.access_scope,
        )
    except Exception as exc:
        raise PlatformReadError(
            detail=f"Failed to load document: {exc}",
            request_id=ctx.request_id,
        )

    # Resolve extraction schema
    extraction_schema = body.extraction_schema
    if extraction_schema == "auto":
        schema_map = {
            "tax_return_1040": "tax_return",
            "tax_return_1099": "tax_return",
            "w2": "tax_return",
            "k1": "tax_return",
            "estate_plan": "estate_plan",
            "trust_document": "estate_plan",
            "account_statement": "statement",
        }
        extraction_schema = schema_map.get(body.document_type, "general")

    # Invoke extractor
    try:
        result = await doc_extractor_agent.run(
            (
                f"Extract structured fields from this {body.document_type} document "
                f"using the {extraction_schema} schema."
            ),
            deps=DocExtractorDeps(
                document_id=body.document_id,
                document_type=body.document_type,
                content=doc_content,
                extraction_schema=extraction_schema,
                custom_fields=body.custom_fields,
                langfuse_trace=trace,
            ),
        )
    except Exception as exc:
        logger.exception("Document extractor agent failed")
        raise ModelProviderError(
            detail=f"Document extraction failed: {exc}",
            request_id=ctx.request_id,
        )

    extraction = result.data

    # Validate extracted values
    warnings = list(extraction.warnings)
    low_confidence_fields = [
        f.field_name for f in extraction.fields if f.confidence < 0.7
    ]
    if low_confidence_fields:
        warnings.append(
            f"Low confidence on fields: {', '.join(low_confidence_fields)}. "
            "Manual verification recommended."
        )

    trace.update(output={
        "status": "success",
        "field_count": len(extraction.fields),
        "low_confidence_count": len(low_confidence_fields),
    })

    return DocumentExtractResponse(
        document_id=body.document_id,
        document_type=body.document_type,
        extraction_schema=extraction_schema,
        fields=[
            ExtractedField(**f.model_dump()) for f in extraction.fields
        ],
        field_count=len(extraction.fields),
        warnings=warnings,
        as_of=datetime.now(timezone.utc).isoformat(),
    )
```

---

## Router Registration

All routers are registered in the FastAPI application entrypoint.

```python
# app/main.py

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from redis.asyncio import Redis

from app.config import Settings
from app.routers import (
    chat,
    crm,
    digest,
    documents,
    email,
    meetings,
    portfolio,
    reports,
    tasks,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown lifecycle: initialize Redis pool, warm caches."""
    settings = Settings()
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    yield
    await app.state.redis.aclose()


app = FastAPI(
    title="Hazel AI Sidecar",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Register all feature routers ────────────────────────────────

app.include_router(chat.router)          # POST /ai/chat, POST /ai/chat/stream
app.include_router(digest.router)        # POST /ai/digest/generate, GET /ai/digest/latest
app.include_router(email.router)         # POST /ai/email/draft, POST /ai/email/triage
app.include_router(tasks.router)         # POST /ai/tasks/extract
app.include_router(crm.router)           # POST /ai/crm/sync-payload
app.include_router(meetings.router)      # POST /ai/meetings/prep, /transcribe, /summarize, /{id}/summary
app.include_router(portfolio.router)     # POST /ai/portfolio/analyze
app.include_router(reports.router)       # POST /ai/reports/firm-wide, POST /ai/reports/narrative
app.include_router(documents.router)     # POST /ai/documents/classify, POST /ai/documents/extract
app.include_router(tax.router)           # POST /ai/tax/plan
```

---

## Endpoint Summary

| Endpoint | Router File | HTTP | Status | Agent Tier | Sync/Async |
|----------|-------------|------|--------|------------|------------|
| `POST /ai/chat` | `chat.py` | POST | 200 | Copilot | Sync |
| `POST /ai/chat/stream` | `chat.py` | POST | 200 (SSE) | Copilot | Streaming |
| `POST /ai/digest/generate` | `digest.py` | POST | 202 | Batch | Async (ARQ) |
| `GET /ai/digest/latest` | `digest.py` | GET | 200 | -- | Cache read |
| `POST /ai/email/draft` | `email.py` | POST | 200 | Copilot | Sync |
| `POST /ai/email/triage` | `email.py` | POST | 200 | Batch | Sync |
| `POST /ai/tasks/extract` | `tasks.py` | POST | 200 | Batch | Sync |
| `POST /ai/crm/sync-payload` | `crm.py` | POST | 200 | Batch | Sync |
| `POST /ai/meetings/prep` | `meetings.py` | POST | 200 | Copilot | Sync |
| `POST /ai/meetings/transcribe` | `meetings.py` | POST | 202 | Transcription | Async (ARQ) |
| `POST /ai/meetings/summarize` | `meetings.py` | POST | 200 | Copilot | Sync |
| `GET /ai/meetings/{id}/summary` | `meetings.py` | GET | 200 | -- | Cache read |
| `POST /ai/tax/plan` | `tax.py` | POST | 200 | Copilot/Analysis | Sync |
| `POST /ai/portfolio/analyze` | `portfolio.py` | POST | 200 | Copilot | Sync |
| `POST /ai/reports/firm-wide` | `reports.py` | POST | 202 | Analysis | Async (ARQ) |
| `POST /ai/reports/narrative` | `reports.py` | POST | 200 | Copilot | Sync |
| `POST /ai/documents/classify` | `documents.py` | POST | 200 | Extraction | Sync |
| `POST /ai/documents/extract` | `documents.py` | POST | 200 | Extraction | Sync |
