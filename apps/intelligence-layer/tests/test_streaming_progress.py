"""Tests for streaming progress integration — cost.update event, tool events in stream."""
from __future__ import annotations

import json

import pytest

from app.services.progress_events import (
    EventType,
    ProgressEvent,
    compact_done,
    compact_start,
    cost_update,
    done_sentinel,
    tool_result,
    tool_start,
)


# ---------------------------------------------------------------------------
# cost.update event
# ---------------------------------------------------------------------------


def test_cost_update_event_format() -> None:
    """cost_update() returns a ProgressEvent with COST_UPDATE type and correct fields."""
    event = cost_update(
        total_cost_usd="0.0105",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=200,
    )

    assert isinstance(event, ProgressEvent)
    assert event.event == EventType.COST_UPDATE
    assert event.data["total_cost_usd"] == "0.0105"
    assert event.data["input_tokens"] == 1000
    assert event.data["output_tokens"] == 500
    assert event.data["cache_read_tokens"] == 200


def test_cost_update_event_to_sse() -> None:
    """cost_update event serializes correctly to SSE format."""
    event = cost_update(
        total_cost_usd="0.005",
        input_tokens=500,
        output_tokens=200,
    )

    sse = event.to_sse()
    assert "event: cost.update" in sse
    assert "data: " in sse

    # Parse the data line
    data_line = [line for line in sse.split("\n") if line.startswith("data: ")][0]
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["type"] == "cost.update"
    assert payload["total_cost_usd"] == "0.005"
    assert payload["input_tokens"] == 500
    assert payload["output_tokens"] == 200


# ---------------------------------------------------------------------------
# Tool events in stream
# ---------------------------------------------------------------------------


def test_stream_includes_tool_start_events() -> None:
    """tool_start events have the correct structure for streaming."""
    event = tool_start(
        tool_name="search_documents",
        tool_call_id="tc_1",
        args_preview={"query": "portfolio performance"},
    )

    assert event.event == EventType.TOOL_START
    assert event.data["tool"] == "search_documents"
    assert event.data["tool_call_id"] == "tc_1"


def test_stream_includes_tool_result_events() -> None:
    """tool_result events include duration and preview."""
    event = tool_result(
        tool_name="search_documents",
        tool_call_id="tc_1",
        duration_ms=150.3,
        result_preview="Found 5 documents matching...",
    )

    assert event.event == EventType.TOOL_RESULT
    assert event.data["tool"] == "search_documents"
    assert event.data["duration_ms"] == 150.3
    assert "Found 5 documents" in event.data["result_preview"]


# ---------------------------------------------------------------------------
# Compact events
# ---------------------------------------------------------------------------


def test_stream_includes_compact_events_when_compaction_fires() -> None:
    """compact_start and compact_done events are well-formed."""
    start_event = compact_start(original_messages=25, estimated_tokens=45000)
    done_event = compact_done(final_messages=12, tokens_saved=20000)

    assert start_event.event == EventType.COMPACT_START
    assert start_event.data["original_messages"] == 25
    assert start_event.data["estimated_tokens"] == 45000

    assert done_event.event == EventType.COMPACT_DONE
    assert done_event.data["final_messages"] == 12
    assert done_event.data["tokens_saved"] == 20000


# ---------------------------------------------------------------------------
# Stream ordering
# ---------------------------------------------------------------------------


def test_stream_includes_cost_update_before_done() -> None:
    """In a complete stream sequence, cost.update appears before the done sentinel."""
    # Simulate a stream event sequence
    events = [
        tool_start("search_documents"),
        tool_result("search_documents", duration_ms=100),
        cost_update(
            total_cost_usd="0.01",
            input_tokens=500,
            output_tokens=200,
        ),
        done_sentinel(),
    ]

    event_types = [e.event for e in events]
    cost_idx = event_types.index(EventType.COST_UPDATE)
    done_idx = event_types.index(EventType.DONE)
    assert cost_idx < done_idx, "cost.update must appear before done sentinel"


def test_stream_backwards_compatible_without_new_events() -> None:
    """A stream without cost.update or tool events still works (backwards compatible)."""
    # Original stream sequence without new event types
    events = [
        ProgressEvent(event=EventType.AGENT_START, data={"agent": "copilot"}),
        ProgressEvent(event=EventType.TEXT_DELTA, data={"text": "Hello"}),
        ProgressEvent(event=EventType.TEXT_DONE, data={}),
        ProgressEvent(event=EventType.AGENT_DONE, data={"agent": "copilot"}),
        done_sentinel(),
    ]

    # All events should serialize without error
    for event in events:
        sse = event.to_sse()
        assert "event: " in sse
        assert "data: " in sse

    # No COST_UPDATE in this sequence
    assert EventType.COST_UPDATE not in [e.event for e in events]
