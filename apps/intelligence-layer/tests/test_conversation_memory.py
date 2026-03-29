"""Tests for conversation memory — Redis-backed message persistence."""
from __future__ import annotations

import fakeredis.aioredis
import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from app.services.conversation_memory import ConversationMemory


@pytest.fixture
def memory():
    redis_client = fakeredis.aioredis.FakeRedis()
    return ConversationMemory(redis_client)


@pytest.mark.anyio
async def test_save_and_load(memory: ConversationMemory) -> None:
    """T12: Save and load round-trip preserves messages."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content="Hello")]),
        ModelResponse(parts=[TextPart(content="Hi there")]),
    ]
    await memory.save("t1", "a1", "conv1", messages)

    history = await memory.load("t1", "a1", "conv1")
    assert len(history) == 2


@pytest.mark.anyio
async def test_load_empty(memory: ConversationMemory) -> None:
    """Loading a nonexistent conversation returns empty list."""
    history = await memory.load("t1", "a1", "nonexistent")
    assert history == []


@pytest.mark.anyio
async def test_tenant_isolation(memory: ConversationMemory) -> None:
    """T13: Different tenants cannot see each other's conversations."""
    await memory.save(
        "tenant_a", "a1", "conv1",
        [ModelRequest(parts=[UserPromptPart(content="secret A")])],
    )
    await memory.save(
        "tenant_b", "a1", "conv1",
        [ModelRequest(parts=[UserPromptPart(content="secret B")])],
    )

    history_a = await memory.load("tenant_a", "a1", "conv1")
    history_b = await memory.load("tenant_b", "a1", "conv1")

    assert len(history_a) == 1
    assert len(history_b) == 1


@pytest.mark.anyio
async def test_clear(memory: ConversationMemory) -> None:
    """Clear removes a conversation."""
    await memory.save(
        "t1", "a1", "conv1",
        [ModelRequest(parts=[UserPromptPart(content="test")])],
    )
    await memory.clear("t1", "a1", "conv1")
    history = await memory.load("t1", "a1", "conv1")
    assert history == []


@pytest.mark.anyio
async def test_load_state(memory: ConversationMemory) -> None:
    """load_state returns active client/household from saved data."""
    state = await memory.load_state("t1", "a1", "nonexistent")
    assert state["active_client_id"] is None
    assert state["active_household_id"] is None
