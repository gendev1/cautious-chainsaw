"""
app/services/message_codec.py — Pydantic AI message serialization.

Serializes/deserializes ModelMessage objects for Redis storage.
Preserves tool call/result traces across conversation turns.
"""
from __future__ import annotations

from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


def serialize_message(message: ModelMessage) -> dict[str, Any]:
    """Convert a Pydantic AI message into a Redis-safe dict."""
    if isinstance(message, ModelRequest):
        role = "request"
    elif isinstance(message, ModelResponse):
        role = "response"
    else:
        raise TypeError(
            f"Unsupported message type: {type(message)!r}"
        )

    return {
        "role": role,
        "parts": [serialize_part(part) for part in message.parts],
    }


def deserialize_message(
    payload: dict[str, Any],
) -> ModelMessage:
    """Rehydrate a Redis payload back into a Pydantic AI message."""
    parts = [deserialize_part(part) for part in payload["parts"]]
    if payload["role"] == "request":
        return ModelRequest(parts=parts)
    if payload["role"] == "response":
        return ModelResponse(parts=parts)
    raise ValueError(f"Unknown message role: {payload['role']}")


def serialize_part(part: Any) -> dict[str, Any]:
    """Serialize a single message part."""
    if isinstance(part, UserPromptPart):
        return {"type": "user_prompt", "content": part.content}
    if isinstance(part, SystemPromptPart):
        return {"type": "system_prompt", "content": part.content}
    if isinstance(part, TextPart):
        return {"type": "text", "content": part.content}
    if isinstance(part, ToolCallPart):
        return {
            "type": "tool_call",
            "tool_name": part.tool_name,
            "args": part.args,
            "tool_call_id": getattr(
                part, "tool_call_id", None
            ),
        }
    if isinstance(part, ToolReturnPart):
        return {
            "type": "tool_return",
            "tool_name": part.tool_name,
            "content": part.content,
            "tool_call_id": getattr(
                part, "tool_call_id", None
            ),
        }
    raise TypeError(f"Unsupported message part: {type(part)!r}")


def deserialize_part(payload: dict[str, Any]) -> Any:
    """Deserialize a single message part."""
    part_type = payload["type"]
    if part_type == "user_prompt":
        return UserPromptPart(content=payload["content"])
    if part_type == "system_prompt":
        return SystemPromptPart(content=payload["content"])
    if part_type == "text":
        return TextPart(content=payload["content"])
    if part_type == "tool_call":
        return ToolCallPart(
            tool_name=payload["tool_name"],
            args=payload["args"],
            tool_call_id=payload.get("tool_call_id"),
        )
    if part_type == "tool_return":
        return ToolReturnPart(
            tool_name=payload["tool_name"],
            content=payload["content"],
            tool_call_id=payload.get("tool_call_id"),
        )
    raise ValueError(f"Unknown message part type: {part_type}")


def trim_message_history(
    messages: list[ModelMessage],
    *,
    max_messages: int,
) -> list[ModelMessage]:
    """Keep newest complete turns, preserving any leading system prompt.

    Strategy:
    - keep the first system prompt message if present
    - keep the newest max_messages-1 additional messages
    - do not split tool call from its result
    """
    if len(messages) <= max_messages:
        return messages

    first = messages[0]
    if _is_system_prompt_message(first):
        tail = messages[-(max_messages - 1):]
        return [first, *tail]

    return messages[-max_messages:]


def extract_active_client_id(
    messages: list[ModelMessage],
) -> str | None:
    """Best-effort extraction of last active client ID."""
    for message in reversed(messages):
        for part in getattr(message, "parts", []):
            client_id = _extract_id_from_part(
                part, keys=("client_id",)
            )
            if client_id:
                return client_id
    return None


def extract_active_household_id(
    messages: list[ModelMessage],
) -> str | None:
    """Best-effort extraction of last active household ID."""
    for message in reversed(messages):
        for part in getattr(message, "parts", []):
            hh_id = _extract_id_from_part(
                part, keys=("household_id",)
            )
            if hh_id:
                return hh_id
    return None


def _is_system_prompt_message(message: ModelMessage) -> bool:
    return any(
        isinstance(part, SystemPromptPart)
        for part in message.parts
    )


def _extract_id_from_part(
    part: Any, *, keys: tuple[str, ...]
) -> str | None:
    if isinstance(part, ToolCallPart) and isinstance(
        part.args, dict
    ):
        for key in keys:
            value = part.args.get(key)
            if isinstance(value, str) and value:
                return value
    if isinstance(part, ToolReturnPart) and isinstance(
        part.content, dict
    ):
        for key in keys:
            value = part.content.get(key)
            if isinstance(value, str) and value:
                return value
    return None
