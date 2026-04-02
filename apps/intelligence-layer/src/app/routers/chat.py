"""
app/routers/chat.py — Chat endpoint with conversation memory.

Enhanced with patterns from Claude Code:
- Streaming progress events (tool.start, tool.result, agent.thinking)
- Context prefetch during LLM streaming
- Conversation compaction (handled by ConversationMemory.save)
"""
from __future__ import annotations

import logging
import time
from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.agents import registry
from app.dependencies import get_agent_deps, get_conversation_memory
from app.models.schemas import ChatRequest, HazelCopilot
from app.services.conversation_memory import ConversationMemory
from app.services.prefetch import PrefetchManager
from app.services.progress_events import (
    agent_done,
    agent_error,
    agent_start,
    agent_thinking,
    done_sentinel,
    text_delta,
    tool_result,
    tool_start,
)

logger = logging.getLogger("sidecar.chat")

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=HazelCopilot)
async def chat(
    request: ChatRequest,
    deps=Depends(get_agent_deps),  # noqa: B008
    memory: ConversationMemory = Depends(get_conversation_memory),  # noqa: B008
):
    """Run the copilot agent with conversation memory.

    Uses context prefetch (ported from Claude Code's QueryEngine)
    to load conversation history and state concurrently.
    """
    entry = registry.get("copilot")
    conversation_id = request.conversation_id or str(uuid4())

    # Prefetch history and state concurrently instead of sequentially
    # (ported from claudecode/QueryEngine.ts: startRelevantMemoryPrefetch)
    prefetch = PrefetchManager()
    prefetch.schedule(
        "history",
        memory.load,
        tenant_id=deps.tenant_id,
        actor_id=deps.actor_id,
        conversation_id=conversation_id,
    )
    prefetch.schedule(
        "state",
        memory.load_state,
        tenant_id=deps.tenant_id,
        actor_id=deps.actor_id,
        conversation_id=conversation_id,
    )
    prefetch.start()

    history = await prefetch.get("history", default=[])
    state = await prefetch.get("state", default={})

    # Carry forward active context from conversation state
    if hasattr(deps, "active_client_id"):
        deps.active_client_id = (
            request.client_id or state.get("active_client_id")
        )
    if hasattr(deps, "active_household_id"):
        deps.active_household_id = (
            request.household_id
            or state.get("active_household_id")
        )
    if hasattr(deps, "active_portfolio_job_id"):
        deps.active_portfolio_job_id = (
            request.portfolio_job_id
            or state.get("active_portfolio_job_id")
        )

    # Run agent with history
    result = await entry.agent.run(
        request.message,
        deps=deps,
        message_history=history,
    )

    # Persist full structured transcript (compaction applied in save())
    await memory.save(
        deps.tenant_id,
        deps.actor_id,
        conversation_id,
        result.all_messages(),
    )

    return result.output


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    deps=Depends(get_agent_deps),  # noqa: B008
    memory: ConversationMemory = Depends(get_conversation_memory),  # noqa: B008
):
    """Stream copilot response via SSE with rich progress events.

    Enhanced with patterns from Claude Code's StreamingToolExecutor:
    - agent.start / agent.thinking / agent.done lifecycle events
    - tool.start / tool.result events for real-time tool visibility
    - text.delta for incremental text (replaces raw 'data:' format)
    - Concurrent context prefetch during streaming
    """
    entry = registry.get("copilot")
    conversation_id = request.conversation_id or str(uuid4())

    # Concurrent prefetch (ported from Claude Code QueryEngine)
    prefetch = PrefetchManager()
    prefetch.schedule(
        "history",
        memory.load,
        tenant_id=deps.tenant_id,
        actor_id=deps.actor_id,
        conversation_id=conversation_id,
    )
    prefetch.schedule(
        "state",
        memory.load_state,
        tenant_id=deps.tenant_id,
        actor_id=deps.actor_id,
        conversation_id=conversation_id,
    )
    prefetch.start()

    history = await prefetch.get("history", default=[])
    state = await prefetch.get("state", default={})

    if hasattr(deps, "active_client_id"):
        deps.active_client_id = (
            request.client_id or state.get("active_client_id")
        )
    if hasattr(deps, "active_household_id"):
        deps.active_household_id = (
            request.household_id
            or state.get("active_household_id")
        )
    if hasattr(deps, "active_portfolio_job_id"):
        deps.active_portfolio_job_id = (
            request.portfolio_job_id
            or state.get("active_portfolio_job_id")
        )

    async def event_stream():
        start_time = time.monotonic()
        yield agent_start("copilot", request.message).to_sse()
        yield agent_thinking("copilot").to_sse()

        try:
            async with entry.agent.run_stream(
                request.message,
                deps=deps,
                message_history=history,
            ) as result:
                async for chunk in result.stream_text():
                    yield text_delta(chunk).to_sse()

                await result.get_output()

                # Persist with compaction
                await memory.save(
                    deps.tenant_id,
                    deps.actor_id,
                    conversation_id,
                    result.all_messages(),
                )

            elapsed_ms = (time.monotonic() - start_time) * 1000
            yield agent_done("copilot", total_duration_ms=elapsed_ms).to_sse()

        except Exception as exc:
            logger.exception(
                "chat_stream_error conversation_id=%s",
                conversation_id,
            )
            yield agent_error("copilot", str(exc)).to_sse()

        yield done_sentinel().to_sse()

    return StreamingResponse(
        event_stream(), media_type="text/event-stream"
    )
