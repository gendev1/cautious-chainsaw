"""
app/routers/chat.py — Chat endpoint with conversation memory.
"""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.agents import registry
from app.dependencies import get_agent_deps, get_conversation_memory
from app.models.schemas import ChatRequest, HazelCopilot
from app.services.conversation_memory import ConversationMemory

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=HazelCopilot)
async def chat(
    request: ChatRequest,
    deps=Depends(get_agent_deps),  # noqa: B008
    memory: ConversationMemory = Depends(get_conversation_memory),  # noqa: B008
):
    """Run the copilot agent with conversation memory."""
    entry = registry.get("copilot")
    conversation_id = request.conversation_id or str(uuid4())

    # Load structured conversation history from Redis
    history = await memory.load(
        tenant_id=deps.tenant_id,
        actor_id=deps.actor_id,
        conversation_id=conversation_id,
    )
    state = await memory.load_state(
        deps.tenant_id, deps.actor_id, conversation_id,
    )

    # Carry forward active context from conversation state
    if hasattr(deps, "active_client_id"):
        deps.active_client_id = (
            request.client_id or state["active_client_id"]
        )
    if hasattr(deps, "active_household_id"):
        deps.active_household_id = (
            request.household_id
            or state["active_household_id"]
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

    # Persist full structured transcript
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
    """Stream copilot response via SSE."""
    entry = registry.get("copilot")
    conversation_id = request.conversation_id or str(uuid4())
    history = await memory.load(
        deps.tenant_id, deps.actor_id, conversation_id,
    )
    state = await memory.load_state(
        deps.tenant_id, deps.actor_id, conversation_id,
    )

    if hasattr(deps, "active_client_id"):
        deps.active_client_id = (
            request.client_id or state["active_client_id"]
        )
    if hasattr(deps, "active_household_id"):
        deps.active_household_id = (
            request.household_id
            or state["active_household_id"]
        )
    if hasattr(deps, "active_portfolio_job_id"):
        deps.active_portfolio_job_id = (
            request.portfolio_job_id
            or state.get("active_portfolio_job_id")
        )

    async def event_stream():
        async with entry.agent.run_stream(
            request.message,
            deps=deps,
            message_history=history,
        ) as result:
            async for chunk in result.stream_text():
                yield f"data: {chunk}\n\n"

            await result.get_output()
            await memory.save(
                deps.tenant_id,
                deps.actor_id,
                conversation_id,
                result.all_messages(),
            )
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(), media_type="text/event-stream"
    )
