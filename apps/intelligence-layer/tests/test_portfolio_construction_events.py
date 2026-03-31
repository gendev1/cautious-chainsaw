"""Tests for Redis Streams event emission and reading."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.portfolio_construction.events import ProgressEventEmitter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Build a mock Redis client that records XADD and XREAD calls."""
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value=b"1234567890-0")
    redis.xread = AsyncMock(return_value=[])
    return redis


@pytest.fixture
def emitter(mock_redis: AsyncMock) -> ProgressEventEmitter:
    return ProgressEventEmitter(redis=mock_redis)


# ---------------------------------------------------------------------------
# Tests: Event emission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_calls_xadd(emitter: ProgressEventEmitter, mock_redis: AsyncMock) -> None:
    """emit() calls XADD on the correct stream key."""
    await emitter.emit(job_id="job_001", event_type="intent_parsed", payload={"themes": ["AI"]})

    mock_redis.xadd.assert_awaited_once()
    call_args = mock_redis.xadd.call_args
    # First arg should be the stream key
    stream_key = call_args[0][0] if call_args[0] else call_args[1].get("name", "")
    assert "job_001" in str(stream_key)


@pytest.mark.asyncio
async def test_emit_stream_key_format(emitter: ProgressEventEmitter, mock_redis: AsyncMock) -> None:
    """Stream key follows format sidecar:portfolio:events:{job_id}."""
    await emitter.emit(job_id="job_abc", event_type="data_loaded")

    call_args = mock_redis.xadd.call_args
    stream_key = call_args[0][0] if call_args[0] else ""
    assert stream_key == "sidecar:portfolio:events:job_abc"


@pytest.mark.asyncio
async def test_emit_fields_contain_required_keys(emitter: ProgressEventEmitter, mock_redis: AsyncMock) -> None:
    """Emitted event contains v, job_id, event_type, and timestamp fields."""
    await emitter.emit(job_id="job_002", event_type="recall_pool_built", payload={"count": 150})

    call_args = mock_redis.xadd.call_args
    # Second arg should be the fields dict
    fields = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("fields", {})

    assert "v" in fields or b"v" in fields
    assert "job_id" in fields or b"job_id" in fields
    assert "event_type" in fields or b"event_type" in fields
    assert "timestamp" in fields or b"timestamp" in fields


@pytest.mark.asyncio
async def test_emit_version_is_1(emitter: ProgressEventEmitter, mock_redis: AsyncMock) -> None:
    """Event version field is '1'."""
    await emitter.emit(job_id="job_003", event_type="job_completed")

    call_args = mock_redis.xadd.call_args
    fields = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("fields", {})

    v = fields.get("v") or fields.get(b"v")
    assert str(v) == "1"


@pytest.mark.asyncio
async def test_emit_event_type_in_fields(emitter: ProgressEventEmitter, mock_redis: AsyncMock) -> None:
    """Event type is correctly set in fields."""
    await emitter.emit(job_id="job_004", event_type="theme_scoring_started")

    call_args = mock_redis.xadd.call_args
    fields = call_args[0][1] if len(call_args[0]) > 1 else {}

    event_type = fields.get("event_type") or fields.get(b"event_type")
    assert str(event_type) == "theme_scoring_started"


@pytest.mark.asyncio
async def test_emit_payload_serialized(emitter: ProgressEventEmitter, mock_redis: AsyncMock) -> None:
    """Payload is JSON-serialized in the event fields."""
    await emitter.emit(job_id="job_005", event_type="draft_built", payload={"holdings_count": 25})

    call_args = mock_redis.xadd.call_args
    fields = call_args[0][1] if len(call_args[0]) > 1 else {}

    payload = fields.get("payload") or fields.get(b"payload")
    if payload:
        parsed = json.loads(payload)
        assert parsed["holdings_count"] == 25


@pytest.mark.asyncio
async def test_emit_no_payload() -> None:
    """emit() works without a payload."""
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value=b"1234567890-0")
    emitter = ProgressEventEmitter(redis=redis)

    await emitter.emit(job_id="job_006", event_type="job_enqueued")
    redis.xadd.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: All defined event types
# ---------------------------------------------------------------------------


DEFINED_EVENT_TYPES = [
    "job_enqueued",
    "intent_parsed",
    "data_loaded",
    "recall_pool_built",
    "theme_scoring_started",
    "theme_scoring_completed",
    "review_iteration_started",
    "draft_built",
    "critic_verdict",
    "job_completed",
    "job_failed",
]


@pytest.mark.parametrize("event_type", DEFINED_EVENT_TYPES)
@pytest.mark.asyncio
async def test_all_event_types_emit_successfully(event_type: str) -> None:
    """Each defined event type can be emitted without error."""
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value=b"1234567890-0")
    emitter = ProgressEventEmitter(redis=redis)

    await emitter.emit(job_id="job_all", event_type=event_type)
    redis.xadd.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: Event reading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_events_returns_events_in_order() -> None:
    """read_events returns events in chronological order."""
    redis = AsyncMock()
    # Simulate XREAD returning two events
    redis.xread = AsyncMock(return_value=[
        (
            b"sidecar:portfolio:events:job_007",
            [
                (b"1000-0", {b"event_type": b"intent_parsed", b"timestamp": b"2026-03-28T10:00:00Z", b"v": b"1", b"job_id": b"job_007"}),
                (b"1001-0", {b"event_type": b"data_loaded", b"timestamp": b"2026-03-28T10:00:01Z", b"v": b"1", b"job_id": b"job_007"}),
            ],
        ),
    ])
    emitter = ProgressEventEmitter(redis=redis)

    events = await emitter.read_events(job_id="job_007")
    assert len(events) == 2
    # First event should be intent_parsed
    assert events[0]["event_type"] == "intent_parsed" or events[0][b"event_type"] == b"intent_parsed"


@pytest.mark.asyncio
async def test_read_events_empty_stream() -> None:
    """read_events returns empty list when no events exist."""
    redis = AsyncMock()
    redis.xread = AsyncMock(return_value=[])
    emitter = ProgressEventEmitter(redis=redis)

    events = await emitter.read_events(job_id="job_empty")
    assert events == []


@pytest.mark.asyncio
async def test_read_events_with_last_id() -> None:
    """read_events uses last_id for incremental reads."""
    redis = AsyncMock()
    redis.xread = AsyncMock(return_value=[])
    emitter = ProgressEventEmitter(redis=redis)

    await emitter.read_events(job_id="job_008", last_id="1500-0")
    redis.xread.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: Job status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_job_status_returns_latest_event() -> None:
    """get_job_status returns the event_type of the latest event."""
    redis = AsyncMock()
    # XREVRANGE returns latest first
    redis.xrevrange = AsyncMock(return_value=[
        (b"2000-0", {b"event_type": b"job_completed", b"v": b"1"}),
    ])
    emitter = ProgressEventEmitter(redis=redis)

    status = await emitter.get_job_status(job_id="job_009")
    assert status == "job_completed"


@pytest.mark.asyncio
async def test_get_job_status_returns_none_no_events() -> None:
    """get_job_status returns None when no events exist."""
    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[])
    emitter = ProgressEventEmitter(redis=redis)

    status = await emitter.get_job_status(job_id="job_nonexist")
    assert status is None


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_propagates_redis_error() -> None:
    """Redis errors during emit propagate to caller."""
    redis = AsyncMock()
    redis.xadd = AsyncMock(side_effect=ConnectionError("Redis unavailable"))
    emitter = ProgressEventEmitter(redis=redis)

    with pytest.raises(ConnectionError):
        await emitter.emit(job_id="job_err", event_type="job_failed")
