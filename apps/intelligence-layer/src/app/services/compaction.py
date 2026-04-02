"""
app/services/compaction.py — Conversation compaction service.

Ported from Claude Code's multi-tier compaction architecture
(claudecode/services/compact/). Replaces naive 50-message truncation
with intelligent context-preserving compaction.

Tiers:
1. Microcompact — trim oversized tool results per-message (cheap, always on)
2. Auto-compact — summarize old turns when token count exceeds threshold
   a. Deterministic (fast, free)
   b. LLM-based (better quality, costs tokens — escalated at 2x threshold)
3. Reactive compact — emergency compaction on prompt_too_long errors

Strategy pattern allows swapping between deterministic and LLM-based
summarization. Financial data detection ensures critical financial
identifiers are preserved during compaction.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

logger = logging.getLogger("sidecar.compaction")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MICROCOMPACT_MAX_TOOL_RESULT_CHARS = 4_000
"""Max characters for a single tool result before truncation."""

AUTO_COMPACT_TOKEN_THRESHOLD = 30_000
"""Estimated token count that triggers auto-compaction."""

CHARS_PER_TOKEN_ESTIMATE = 4
"""Rough chars-per-token estimate for threshold checks."""

RECENT_TURNS_TO_KEEP = 10
"""Number of most recent messages to preserve during compaction."""

COMPACT_SUMMARY_PREFIX = "[Conversation summary] "
"""Prefix for compacted summary messages."""

LLM_ESCALATION_MULTIPLIER = 2.0
"""Token threshold multiplier for escalating from deterministic to LLM strategy."""

# ---------------------------------------------------------------------------
# Token estimation (tiktoken with fallback)
# ---------------------------------------------------------------------------

_tiktoken_encoder: Any = None
_tiktoken_loaded = False


def _get_tiktoken_encoder() -> Any:
    """Lazy-load tiktoken cl100k_base encoder. Returns None if unavailable."""
    global _tiktoken_encoder, _tiktoken_loaded
    if _tiktoken_loaded:
        return _tiktoken_encoder
    _tiktoken_loaded = True
    try:
        import tiktoken
        _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _tiktoken_encoder = None
    return _tiktoken_encoder


def _extract_all_text(messages: list[ModelMessage]) -> str:
    """Extract all text content from messages for token counting."""
    parts: list[str] = []
    for msg in messages:
        for part in msg.parts:
            if isinstance(part, (TextPart, UserPromptPart, SystemPromptPart)):
                parts.append(str(part.content))
            elif isinstance(part, ToolReturnPart):
                if isinstance(part.content, str):
                    parts.append(part.content)
                else:
                    parts.append(json.dumps(part.content))
            elif isinstance(part, ToolCallPart):
                if isinstance(part.args, str):
                    parts.append(part.args)
                elif isinstance(part.args, dict):
                    parts.append(json.dumps(part.args))
    return " ".join(parts)


def estimate_token_count(messages: list[ModelMessage]) -> int:
    """Token estimate. Uses tiktoken cl100k_base if available, else chars/4."""
    text = _extract_all_text(messages)
    enc = _get_tiktoken_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return len(text) // CHARS_PER_TOKEN_ESTIMATE


# ---------------------------------------------------------------------------
# Financial data detection
# ---------------------------------------------------------------------------

# Patterns for financial identifiers
_ACCOUNT_NUMBER_RE = re.compile(r"(?:account|acct)[^\d]*#?\s*(\d{4,12})", re.IGNORECASE)
_DOLLAR_AMOUNT_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?")
_TICKER_RE = re.compile(r"\b[A-Z]{1,5}\b")
_CUSIP_RE = re.compile(r"\b[A-Z0-9]{9}\b")
_PERCENTAGE_RE = re.compile(r"\d+\.?\d*\s*%")

# Common English words that look like tickers but aren't
_TICKER_STOPWORDS = frozenset({
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HAD",
    "HER", "WAS", "ONE", "OUR", "OUT", "HAS", "HIS", "HOW", "ITS", "MAY",
    "NEW", "NOW", "OLD", "SEE", "WAY", "WHO", "DID", "GET", "HIM", "LET",
    "SAY", "SHE", "TOO", "USE", "DAD", "MOM", "RUN", "SET", "TRY", "ASK",
    "BIG", "KEY", "END", "PUT", "TOP", "YES", "TAX", "NET", "FEE", "ETF",
    "IRA", "SEP", "USD", "AUM", "YTD", "MTD", "QTD", "RMD", "AGI",
})

# Known tickers to always match
_KNOWN_TICKERS = frozenset({
    "AAPL", "MSFT", "GOOG", "GOOGL", "AMZN", "META", "TSLA", "NVDA",
    "BRK", "JPM", "V", "JNJ", "WMT", "PG", "MA", "UNH", "HD", "DIS",
    "PYPL", "NFLX", "INTC", "VZ", "T", "PFE", "MRK", "KO", "PEP",
    "SPY", "QQQ", "VTI", "VOO", "IWM", "AGG", "BND", "VEA", "VWO",
})


def detect_financial_data(messages: list[ModelMessage]) -> list[str]:
    """Scan messages for financial identifiers that must be preserved.

    Returns a list of detected items like ["Account #12345", "$50,000"].
    """
    text = _extract_all_text(messages)
    found: list[str] = []

    # Account numbers
    for m in _ACCOUNT_NUMBER_RE.finditer(text):
        found.append(f"Account #{m.group(1)}")

    # Dollar amounts
    for m in _DOLLAR_AMOUNT_RE.finditer(text):
        found.append(m.group(0))

    # Percentages (only if near financial keywords)
    for m in _PERCENTAGE_RE.finditer(text):
        start = max(0, m.start() - 50)
        context = text[start:m.end() + 20].lower()
        if any(kw in context for kw in ("return", "yield", "rate", "alloc", "drift", "weight")):
            found.append(m.group(0))

    # Ticker symbols (only known tickers or uppercase words not in stoplist)
    for m in _TICKER_RE.finditer(text):
        word = m.group(0)
        if word in _KNOWN_TICKERS:
            found.append(word)
        elif len(word) >= 2 and word not in _TICKER_STOPWORDS:
            # Check context — near financial keywords?
            start = max(0, m.start() - 30)
            context = text[start:m.end() + 30].lower()
            if any(kw in context for kw in ("share", "stock", "buy", "sell", "hold", "ticker", "position")):
                found.append(word)

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


# ---------------------------------------------------------------------------
# Tier 1: Microcompact — trim oversized tool results
# ---------------------------------------------------------------------------

def microcompact_messages(
    messages: list[ModelMessage],
    *,
    max_tool_result_chars: int = MICROCOMPACT_MAX_TOOL_RESULT_CHARS,
) -> list[ModelMessage]:
    """Trim oversized tool results to prevent context bloat.

    Inspired by claudecode/services/compact/microCompact.ts.
    """
    compacted: list[ModelMessage] = []

    for message in messages:
        if not isinstance(message, ModelRequest):
            compacted.append(message)
            continue

        new_parts = []
        changed = False
        for part in message.parts:
            if (
                isinstance(part, ToolReturnPart)
                and isinstance(part.content, str)
                and len(part.content) > max_tool_result_chars
            ):
                truncated_content = (
                    part.content[:max_tool_result_chars]
                    + "\n\n... [truncated — full result was "
                    f"{len(part.content):,} chars]"
                )
                new_parts.append(
                    ToolReturnPart(
                        tool_name=part.tool_name,
                        content=truncated_content,
                        tool_call_id=getattr(part, "tool_call_id", None) or "",
                    )
                )
                changed = True
            elif (
                isinstance(part, ToolReturnPart)
                and isinstance(part.content, (dict, list))
            ):
                serialized = json.dumps(part.content)
                if len(serialized) > max_tool_result_chars:
                    truncated_content = (
                        serialized[:max_tool_result_chars]
                        + "\n\n... [truncated — full result was "
                        f"{len(serialized):,} chars]"
                    )
                    new_parts.append(
                        ToolReturnPart(
                            tool_name=part.tool_name,
                            content=truncated_content,
                            tool_call_id=getattr(part, "tool_call_id", None) or "",
                        )
                    )
                    changed = True
                else:
                    new_parts.append(part)
            else:
                new_parts.append(part)

        if changed:
            compacted.append(ModelRequest(parts=new_parts))
        else:
            compacted.append(message)

    return compacted


# ---------------------------------------------------------------------------
# Compaction Strategy Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class CompactionStrategy(Protocol):
    """Strategy for summarizing older conversation turns."""

    async def summarize(
        self,
        messages: list[ModelMessage],
        *,
        financial_context: list[str] | None = None,
    ) -> str: ...


@dataclass
class DeterministicCompactor:
    """Fast deterministic summarization (no LLM call)."""

    max_chars_per_turn: int = 150
    max_turns_in_summary: int = 8

    async def summarize(
        self,
        messages: list[ModelMessage],
        *,
        financial_context: list[str] | None = None,
    ) -> str:
        summaries: list[str] = []
        turn_num = 0

        for msg in messages:
            content = _extract_text_content(msg)
            if content.strip():
                turn_num += 1
                truncated = (
                    content[:self.max_chars_per_turn]
                    + ("..." if len(content) > self.max_chars_per_turn else "")
                )
                summaries.append(f"Turn {turn_num}: {truncated}")

        if not summaries:
            return "No prior conversation context."

        result = (
            COMPACT_SUMMARY_PREFIX
            + f"This conversation has {len(messages)} earlier turns. "
            + "Key points:\n"
            + "\n".join(summaries[-self.max_turns_in_summary:])
        )

        if financial_context:
            result += (
                "\n\nFinancial data referenced: "
                + ", ".join(financial_context[:20])
            )

        return result


@dataclass
class LLMCompactor:
    """LLM-based summarization for complex conversations.

    Falls back to DeterministicCompactor on model errors.
    """

    model: str = "anthropic:claude-haiku-4-5"
    max_summary_tokens: int = 2000

    async def summarize(
        self,
        messages: list[ModelMessage],
        *,
        financial_context: list[str] | None = None,
    ) -> str:
        """Summarize using an LLM. Falls back to deterministic on error."""
        try:
            return await self._llm_summarize(messages, financial_context)
        except Exception as exc:
            logger.warning(
                "llm_compactor_fallback error=%s", exc
            )
            fallback = DeterministicCompactor()
            return await fallback.summarize(
                messages, financial_context=financial_context
            )

    async def _llm_summarize(
        self,
        messages: list[ModelMessage],
        financial_context: list[str] | None,
    ) -> str:
        """Call the LLM to produce a summary."""
        from pydantic_ai import Agent

        prompt_parts = [
            "Summarize this conversation history concisely. "
            "Preserve all key decisions, action items, and context needed "
            "to continue the conversation naturally."
        ]

        if financial_context:
            prompt_parts.append(
                "\n\nIMPORTANT: Preserve these financial identifiers exactly: "
                + ", ".join(financial_context[:20])
            )

        # Build the conversation text to summarize
        conversation_text = "\n".join(
            _extract_text_content(msg) for msg in messages
            if _extract_text_content(msg).strip()
        )
        prompt_parts.append(f"\n\nConversation:\n{conversation_text[:8000]}")

        agent: Agent[None, str] = Agent(
            self.model,
            output_type=str,
            system_prompt="You are a concise conversation summarizer for a financial advisor AI.",
        )

        result = await agent.run("\n".join(prompt_parts))
        return COMPACT_SUMMARY_PREFIX + result.output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def needs_compaction(
    messages: list[ModelMessage],
    *,
    threshold: int = AUTO_COMPACT_TOKEN_THRESHOLD,
) -> bool:
    """Check if conversation has exceeded the compaction threshold."""
    return estimate_token_count(messages) > threshold


def _extract_text_content(message: ModelMessage) -> str:
    """Extract readable text from a message for summarization."""
    parts_text = []
    for part in message.parts:
        if isinstance(part, UserPromptPart):
            parts_text.append(f"User: {part.content}")
        elif isinstance(part, TextPart):
            parts_text.append(f"Assistant: {part.content}")
        elif isinstance(part, ToolReturnPart):
            preview = (
                part.content[:200]
                if isinstance(part.content, str)
                else json.dumps(part.content)[:200]
            )
            parts_text.append(f"Tool({part.tool_name}): {preview}")
    return " | ".join(parts_text)


def build_compaction_summary(
    messages_to_summarize: list[ModelMessage],
) -> str:
    """Build a deterministic compact summary (legacy entry point)."""
    compactor = DeterministicCompactor()
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(compactor.summarize(messages_to_summarize))
    # If already in an async context, use a sync fallback
    summaries: list[str] = []
    turn_num = 0
    for msg in messages_to_summarize:
        content = _extract_text_content(msg)
        if content.strip():
            turn_num += 1
            truncated = content[:150] + ("..." if len(content) > 150 else "")
            summaries.append(f"Turn {turn_num}: {truncated}")
    if not summaries:
        return "No prior conversation context."
    return (
        COMPACT_SUMMARY_PREFIX
        + f"This conversation has {len(messages_to_summarize)} earlier turns. "
        + "Key points:\n"
        + "\n".join(summaries[-8:])
    )


# ---------------------------------------------------------------------------
# Auto-compact (deterministic, synchronous-compatible)
# ---------------------------------------------------------------------------

def auto_compact(
    messages: list[ModelMessage],
    *,
    recent_to_keep: int = RECENT_TURNS_TO_KEEP,
    threshold: int = AUTO_COMPACT_TOKEN_THRESHOLD,
) -> list[ModelMessage]:
    """Apply auto-compaction with deterministic summary."""
    if not needs_compaction(messages, threshold=threshold):
        return messages

    system_prompt_msg: ModelMessage | None = None
    conversation = messages

    if messages and isinstance(messages[0], ModelRequest):
        has_system = any(
            isinstance(p, SystemPromptPart) for p in messages[0].parts
        )
        if has_system:
            system_prompt_msg = messages[0]
            conversation = messages[1:]

    if len(conversation) <= recent_to_keep:
        return messages

    old_messages = conversation[:-recent_to_keep]
    recent_messages = conversation[-recent_to_keep:]

    summary_text = build_compaction_summary(old_messages)

    summary_msg = ModelRequest(
        parts=[SystemPromptPart(content=summary_text)]
    )

    result: list[ModelMessage] = []
    if system_prompt_msg:
        result.append(system_prompt_msg)
    result.append(summary_msg)
    result.extend(recent_messages)

    old_tokens = estimate_token_count(messages)
    new_tokens = estimate_token_count(result)
    logger.info(
        "auto_compact applied",
        extra={
            "old_messages": len(messages),
            "new_messages": len(result),
            "old_token_estimate": old_tokens,
            "new_token_estimate": new_tokens,
            "savings_pct": round(
                (1 - new_tokens / max(old_tokens, 1)) * 100, 1
            ),
        },
    )

    return result


# ---------------------------------------------------------------------------
# CompactionResult
# ---------------------------------------------------------------------------

@dataclass
class CompactionResult:
    """Result of the compaction pipeline."""

    messages: list[ModelMessage]
    was_compacted: bool
    original_count: int
    final_count: int
    estimated_tokens_saved: int
    strategy_used: str = "none"  # "none" | "deterministic" | "llm"


# ---------------------------------------------------------------------------
# Full compaction pipeline (async, strategy-aware)
# ---------------------------------------------------------------------------

async def compact_conversation(
    messages: list[ModelMessage],
    *,
    max_tool_result_chars: int = MICROCOMPACT_MAX_TOOL_RESULT_CHARS,
    auto_compact_threshold: int = AUTO_COMPACT_TOKEN_THRESHOLD,
    recent_to_keep: int = RECENT_TURNS_TO_KEEP,
    strategy: CompactionStrategy | None = None,
    circuit_breaker: Any | None = None,
) -> CompactionResult:
    """Run the full compaction pipeline on a conversation.

    Pipeline order (matching Claude Code's approach):
    1. Microcompact — trim oversized tool results
    2. Auto-compact — summarize older turns if over threshold
       - Below 2x threshold: deterministic
       - Above 2x threshold: LLM (if strategy provided and circuit not open)
    """
    original_count = len(messages)
    original_tokens = estimate_token_count(messages)

    # Tier 1: Microcompact
    messages = microcompact_messages(
        messages, max_tool_result_chars=max_tool_result_chars
    )

    # Check if compaction is needed
    current_tokens = estimate_token_count(messages)
    if current_tokens <= auto_compact_threshold:
        return CompactionResult(
            messages=messages,
            was_compacted=False,
            original_count=original_count,
            final_count=len(messages),
            estimated_tokens_saved=original_tokens - current_tokens,
            strategy_used="none",
        )

    # Determine strategy
    strategy_used = "deterministic"
    llm_threshold = int(auto_compact_threshold * LLM_ESCALATION_MULTIPLIER)
    use_llm = (
        strategy is not None
        and current_tokens > llm_threshold
    )

    # Check circuit breaker before using LLM
    if use_llm and circuit_breaker is not None:
        try:
            circuit_breaker.check()
        except Exception:
            use_llm = False
            logger.info("compaction_circuit_open, falling back to deterministic")

    # Separate system prompt
    system_prompt_msg: ModelMessage | None = None
    conversation = messages

    if messages and isinstance(messages[0], ModelRequest):
        has_system = any(
            isinstance(p, SystemPromptPart) for p in messages[0].parts
        )
        if has_system:
            system_prompt_msg = messages[0]
            conversation = messages[1:]

    if len(conversation) <= recent_to_keep:
        return CompactionResult(
            messages=messages,
            was_compacted=False,
            original_count=original_count,
            final_count=len(messages),
            estimated_tokens_saved=original_tokens - estimate_token_count(messages),
            strategy_used="none",
        )

    old_messages = conversation[:-recent_to_keep]
    recent_messages = conversation[-recent_to_keep:]

    # Detect financial data for context preservation
    financial_context = detect_financial_data(old_messages)

    # Summarize using chosen strategy
    if use_llm and strategy is not None:
        try:
            summary_text = await strategy.summarize(
                old_messages, financial_context=financial_context
            )
            strategy_used = "llm"
            if circuit_breaker is not None:
                circuit_breaker.record_success()
        except Exception as exc:
            logger.warning("llm_compaction_failed error=%s", exc)
            if circuit_breaker is not None:
                circuit_breaker.record_failure()
            # Fall back to deterministic
            fallback = DeterministicCompactor()
            summary_text = await fallback.summarize(
                old_messages, financial_context=financial_context
            )
            strategy_used = "deterministic"
    else:
        compactor = DeterministicCompactor()
        summary_text = await compactor.summarize(
            old_messages, financial_context=financial_context
        )
        strategy_used = "deterministic"

    # Build compacted message list
    summary_msg = ModelRequest(
        parts=[SystemPromptPart(content=summary_text)]
    )

    result_messages: list[ModelMessage] = []
    if system_prompt_msg:
        result_messages.append(system_prompt_msg)
    result_messages.append(summary_msg)
    result_messages.extend(recent_messages)

    final_tokens = estimate_token_count(result_messages)

    logger.info(
        "compact_conversation_complete",
        extra={
            "strategy": strategy_used,
            "original_messages": original_count,
            "final_messages": len(result_messages),
            "tokens_saved": original_tokens - final_tokens,
            "financial_items_preserved": len(financial_context),
        },
    )

    return CompactionResult(
        messages=result_messages,
        was_compacted=True,
        original_count=original_count,
        final_count=len(result_messages),
        estimated_tokens_saved=original_tokens - final_tokens,
        strategy_used=strategy_used,
    )


async def reactive_compact(
    messages: list[ModelMessage],
    *,
    strategy: CompactionStrategy | None = None,
) -> CompactionResult:
    """Emergency compaction when prompt_too_long is raised.

    Uses half the normal recent_to_keep for aggressive compaction.
    """
    return await compact_conversation(
        messages,
        recent_to_keep=RECENT_TURNS_TO_KEEP // 2,
        auto_compact_threshold=0,  # Force compaction regardless of size
        strategy=strategy,
    )
