"""Tests for JobContext model and serialization."""
from __future__ import annotations

from app.jobs.enqueue import JobContext


def test_job_context_serialization() -> None:
    """JobContext round-trips through dict."""
    ctx = JobContext(
        tenant_id="t_001",
        actor_id="a_001",
        actor_type="advisor",
        request_id="r_001",
        access_scope={"visibility_mode": "scoped", "household_ids": ["hh_001"]},
    )
    data = ctx.model_dump()
    restored = JobContext(**data)
    assert restored.tenant_id == "t_001"
    assert restored.access_scope["household_ids"] == ["hh_001"]


def test_job_context_required_fields() -> None:
    """JobContext requires tenant_id, actor_id, actor_type, request_id."""
    import pytest
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        JobContext(tenant_id="t1")  # missing required fields
