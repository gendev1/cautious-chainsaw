"""
app/rag/context.py — Context window management with token budgeting.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import tiktoken


@dataclass
class ContextBudget:
    """Token budget allocation for the LLM context window."""

    total_limit: int = 120_000
    system_prompt_reserve: int = 2_000
    conversation_history_reserve: int = 8_000
    retrieved_context_limit: int = 12_000
    response_reserve: int = 4_000

    @property
    def available_for_context(self) -> int:
        return (
            self.total_limit
            - self.system_prompt_reserve
            - self.conversation_history_reserve
            - self.response_reserve
        )


class ContextWindowBuilder:
    """Assembles LLM context from retrieved chunks, history,
    and system prompt within token budget constraints."""

    def __init__(
        self,
        budget: ContextBudget | None = None,
        encoding_name: str = "cl100k_base",
    ) -> None:
        self.budget = budget or ContextBudget()
        self.enc = tiktoken.get_encoding(encoding_name)

    def build_context(
        self,
        system_prompt: str,
        conversation_history: list[dict[str, str]],
        chunks: list[Any],
    ) -> tuple[str, list[dict[str, str]], list[Any]]:
        """Build context window. Returns:
        - final system prompt (with retrieved context)
        - truncated conversation history
        - chunks that were actually included
        """
        # Step 1: Fit conversation history
        history_budget = (
            self.budget.conversation_history_reserve
        )
        truncated_history = self._truncate_history(
            conversation_history, history_budget
        )

        # Step 2: Fit retrieved chunks
        context_budget = self.budget.retrieved_context_limit
        included_chunks = self._fit_chunks(
            chunks, context_budget
        )

        # Step 3: Build context block
        context_block = self._format_context_block(
            included_chunks
        )

        # Step 4: Inject into system prompt
        final_prompt = (
            f"{system_prompt}\n\n"
            f"## Retrieved Context\n\n{context_block}"
        )

        return final_prompt, truncated_history, included_chunks

    def _truncate_history(
        self,
        messages: list[dict[str, str]],
        budget: int,
    ) -> list[dict[str, str]]:
        """Keep most recent messages within budget."""
        result: list[dict[str, str]] = []
        used = 0

        for msg in reversed(messages):
            msg_tokens = self._count_tokens(
                msg.get("content", "")
            )
            if used + msg_tokens > budget:
                break
            result.append(msg)
            used += msg_tokens

        result.reverse()
        return result

    def _fit_chunks(
        self,
        chunks: list[Any],
        budget: int,
    ) -> list[Any]:
        """Include top-ranked chunks that fit in budget."""
        included: list[Any] = []
        used = 0

        for chunk in chunks:
            chunk_tokens = self._count_tokens(chunk.text)
            header_tokens = 30
            if used + chunk_tokens + header_tokens > budget:
                break
            included.append(chunk)
            used += chunk_tokens + header_tokens

        return included

    def _format_context_block(
        self, chunks: list[Any]
    ) -> str:
        """Format chunks into structured context block."""
        if not chunks:
            return "No relevant context was retrieved."

        parts: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            source_label = _source_label(chunk)
            parts.append(
                f"[Source {i}: {source_label}]\n"
                f"{chunk.text}\n"
            )
        return "\n".join(parts)

    def _count_tokens(self, text: str) -> int:
        return len(self.enc.encode(text))


def _source_label(chunk: Any) -> str:
    """Human-readable source label for a chunk."""
    metadata = getattr(chunk, "metadata", {})
    if isinstance(metadata, dict):
        title = metadata.get("title", chunk.source_id)
    else:
        title = getattr(metadata, "title", None) or chunk.source_id
    type_labels = {
        "document": "Document",
        "email": "Email",
        "crm_note": "CRM Note",
        "transcript": "Meeting Transcript",
        "activity": "Activity",
    }
    type_label = type_labels.get(
        chunk.source_type, chunk.source_type
    )
    created = getattr(chunk, "created_at", "")
    date_str = created[:10] if created else ""
    return f"{type_label} - {title} ({date_str})"
