"""Redis Streams event emission and reading for portfolio construction progress."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


class ProgressEventEmitter:
    """Emit and read progress events via Redis Streams."""

    STREAM_PREFIX = "sidecar:portfolio:events:"

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    def _stream_key(self, job_id: str) -> str:
        return f"{self.STREAM_PREFIX}{job_id}"

    async def emit(
        self,
        job_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Write a progress event to the Redis Stream."""
        fields = {
            "v": "1",
            "job_id": job_id,
            "event_type": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if payload is not None:
            fields["payload"] = json.dumps(payload)

        await self._redis.xadd(self._stream_key(job_id), fields)

    async def read_events(
        self,
        job_id: str,
        last_id: str = "0-0",
    ) -> list[dict[str, Any]]:
        """Read events from the stream after last_id."""
        stream_key = self._stream_key(job_id)
        raw = await self._redis.xread({stream_key: last_id})

        if not raw:
            return []

        events: list[dict[str, Any]] = []
        for _stream_name, messages in raw:
            for _msg_id, fields in messages:
                # Decode bytes if needed
                decoded = {}
                for k, v in fields.items():
                    key = k.decode() if isinstance(k, bytes) else k
                    val = v.decode() if isinstance(v, bytes) else v
                    decoded[key] = val
                events.append(decoded)

        return events

    async def get_job_status(self, job_id: str) -> str | None:
        """Get the latest event type for a job."""
        stream_key = self._stream_key(job_id)
        raw = await self._redis.xrevrange(stream_key, count=1)

        if not raw:
            return None

        _msg_id, fields = raw[0]
        event_type = fields.get(b"event_type") or fields.get("event_type")
        if isinstance(event_type, bytes):
            return event_type.decode()
        return str(event_type) if event_type else None
