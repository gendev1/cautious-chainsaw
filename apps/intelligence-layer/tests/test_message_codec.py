"""Tests for message codec — serialize/deserialize Pydantic AI messages."""
from __future__ import annotations

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from app.services.message_codec import (
    deserialize_message,
    extract_active_client_id,
    extract_active_household_id,
    serialize_message,
    trim_message_history,
)


def test_serialize_deserialize_user_prompt() -> None:
    """T9a: Round-trip for UserPromptPart."""
    msg = ModelRequest(parts=[UserPromptPart(content="Hello")])
    payload = serialize_message(msg)
    restored = deserialize_message(payload)
    assert isinstance(restored, ModelRequest)
    assert restored.parts[0].content == "Hello"


def test_serialize_deserialize_text_response() -> None:
    """T9b: Round-trip for TextPart in ModelResponse."""
    msg = ModelResponse(parts=[TextPart(content="Hi there")])
    payload = serialize_message(msg)
    restored = deserialize_message(payload)
    assert isinstance(restored, ModelResponse)
    assert restored.parts[0].content == "Hi there"


def test_serialize_deserialize_tool_call() -> None:
    """T9c: Round-trip for ToolCallPart."""
    msg = ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="get_household_summary",
                args={"household_id": "hh_001"},
            )
        ]
    )
    payload = serialize_message(msg)
    restored = deserialize_message(payload)
    assert isinstance(restored, ModelResponse)
    part = restored.parts[0]
    assert isinstance(part, ToolCallPart)
    assert part.tool_name == "get_household_summary"
    assert part.args == {"household_id": "hh_001"}


def test_serialize_deserialize_tool_return() -> None:
    """T9d: Round-trip for ToolReturnPart."""
    msg = ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="get_household_summary",
                content={"household_id": "hh_001", "total_aum": 2400000},
            )
        ]
    )
    payload = serialize_message(msg)
    restored = deserialize_message(payload)
    part = restored.parts[0]
    assert isinstance(part, ToolReturnPart)
    assert part.content["total_aum"] == 2400000


def test_trim_under_limit() -> None:
    """T10a: Messages under limit are returned as-is."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content=f"msg {i}")])
        for i in range(5)
    ]
    result = trim_message_history(messages, max_messages=50)
    assert len(result) == 5


def test_trim_caps_at_max() -> None:
    """T10b: Messages over limit are trimmed to max."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content=f"msg {i}")])
        for i in range(60)
    ]
    result = trim_message_history(messages, max_messages=50)
    assert len(result) == 50


def test_trim_preserves_system_prompt() -> None:
    """T11: Leading system prompt is preserved during trimming."""
    system = ModelRequest(
        parts=[SystemPromptPart(content="You are Hazel")]
    )
    messages = [system] + [
        ModelRequest(parts=[UserPromptPart(content=f"msg {i}")])
        for i in range(60)
    ]
    result = trim_message_history(messages, max_messages=10)
    assert len(result) == 10
    assert isinstance(result[0].parts[0], SystemPromptPart)


def test_extract_client_id_from_tool_call() -> None:
    """extract_active_client_id finds client_id in tool call args."""
    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="get_client_profile",
                    args={"client_id": "cl_123"},
                )
            ]
        )
    ]
    assert extract_active_client_id(messages) == "cl_123"


def test_extract_household_id_from_tool_return() -> None:
    """extract_active_household_id finds household_id in tool return."""
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="get_household_summary",
                    content={"household_id": "hh_456"},
                )
            ]
        )
    ]
    assert extract_active_household_id(messages) == "hh_456"


def test_extract_returns_none_when_missing() -> None:
    """extract returns None when no matching ID found."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content="Hello")])
    ]
    assert extract_active_client_id(messages) is None
    assert extract_active_household_id(messages) is None
