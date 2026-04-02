"""
app/services/conversation_memory.py — Redis-backed conversation memory.

Stores full Pydantic AI message lists (including tool calls/results)
with tenant-scoped keys, 2-hour TTL, and intelligent compaction.

Compaction strategy (ported from Claude Code's services/compact/):
- Microcompact: trim oversized tool results per-message
- Auto-compact: summarize old turns when token threshold exceeded
- Fallback: 50-message hard cap as safety net
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

import redis.asyncio as aioredis
from pydantic_ai.messages import ModelMessage

from app.services.compaction import compact_conversation
from app.services.message_codec import (
    deserialize_message,
    extract_active_client_id,
    extract_active_household_id,
    serialize_message,
    trim_message_history,
)

logger = logging.getLogger("sidecar.conversation_memory")

CONVERSATION_TTL = timedelta(hours=2)
MAX_MESSAGES = 50


class ConversationMemory:
    """Redis-backed conversation memory for multi-turn agents."""

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client

    def _key(
        self,
        tenant_id: str,
        actor_id: str,
        conversation_id: str,
    ) -> str:
        return f"chat:{tenant_id}:{actor_id}:{conversation_id}"

    async def load(
        self,
        tenant_id: str,
        actor_id: str,
        conversation_id: str | None,
    ) -> list[ModelMessage]:
        """Load structured message history for agent.run()."""
        if not conversation_id:
            return []
        key = self._key(tenant_id, actor_id, conversation_id)
        raw = await self._redis.get(key)
        if raw is None:
            return []
        payload = json.loads(raw)
        return [
            deserialize_message(item)
            for item in payload["messages"]
        ]

    async def save(
        self,
        tenant_id: str,
        actor_id: str,
        conversation_id: str,
        messages: list[ModelMessage],
        *,
        extra_state: dict[str, str | None] | None = None,
    ) -> None:
        """Persist full structured history with intelligent compaction.

        Compaction pipeline (ported from Claude Code):
        1. Microcompact — trim oversized tool results
        2. Auto-compact — summarize older turns if over token threshold
        3. Hard cap — fallback 50-message trim as safety net

        extra_state merges additional keys into the conversation state
        (e.g., active_portfolio_job_id from portfolio construction).
        """
        key = self._key(tenant_id, actor_id, conversation_id)

        # Apply intelligent compaction before hard-cap trim
        compaction = await compact_conversation(messages)
        if compaction.was_compacted:
            logger.info(
                "conversation_compacted",
                extra={
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                    "original_messages": compaction.original_count,
                    "final_messages": compaction.final_count,
                    "tokens_saved": compaction.estimated_tokens_saved,
                },
            )
        messages = compaction.messages

        # Safety-net hard cap (should rarely trigger after compaction)
        trimmed = trim_message_history(
            messages, max_messages=MAX_MESSAGES
        )
        state = {
            "messages": [
                serialize_message(m) for m in trimmed
            ],
            "active_client_id": (
                extract_active_client_id(trimmed)
            ),
            "active_household_id": (
                extract_active_household_id(trimmed)
            ),
        }
        if extra_state:
            state.update(extra_state)
        await self._redis.set(
            key,
            json.dumps(state),
            ex=int(CONVERSATION_TTL.total_seconds()),
        )

    async def load_state(
        self,
        tenant_id: str,
        actor_id: str,
        conversation_id: str,
    ) -> dict[str, str | None]:
        """Load active client/household/portfolio state."""
        key = self._key(tenant_id, actor_id, conversation_id)
        raw = await self._redis.get(key)
        if raw is None:
            return {
                "active_client_id": None,
                "active_household_id": None,
                "active_portfolio_job_id": None,
            }
        payload = json.loads(raw)
        return {
            "active_client_id": payload.get(
                "active_client_id"
            ),
            "active_household_id": payload.get(
                "active_household_id"
            ),
            "active_portfolio_job_id": payload.get(
                "active_portfolio_job_id"
            ),
        }

    async def clear(
        self,
        tenant_id: str,
        actor_id: str,
        conversation_id: str,
    ) -> None:
        """Remove a conversation."""
        key = self._key(tenant_id, actor_id, conversation_id)
        await self._redis.delete(key)
