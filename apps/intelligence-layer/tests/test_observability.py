"""Tests for JobTracer and JobMetrics using real Langfuse."""
from __future__ import annotations

import os
import time

import pytest
from langfuse import Langfuse

from app.jobs.observability import JobMetrics, JobTracer


@pytest.fixture
def langfuse_client() -> Langfuse:
    """Create a real Langfuse client from env."""
    return Langfuse(
        public_key=os.environ.get(
            "LANGFUSE_PUBLIC_KEY", "pk-lf-74908167-e01a-4d35-9d99-7df19f8bbedb",
        ),
        secret_key=os.environ.get(
            "LANGFUSE_SECRET_KEY", "sk-lf-420c6273-4071-4311-96bd-053ec275d927",
        ),
        host=os.environ.get("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com"),
    )


def test_job_metrics_duration() -> None:
    """JobMetrics tracks duration from start to end."""
    m = JobMetrics(job_name="test", tenant_id="t1", actor_id="a1")
    time.sleep(0.01)
    m.ended_at = time.monotonic()
    assert m.duration_seconds >= 0.01


def test_job_metrics_defaults() -> None:
    """JobMetrics has zero counters by default."""
    m = JobMetrics(job_name="test", tenant_id="t1", actor_id="a1")
    assert m.total_tokens == 0
    assert m.model_calls == 0
    assert m.status == "running"


def test_job_tracer_accumulates_tokens(langfuse_client: Langfuse) -> None:
    """JobTracer accumulates token counts across generations."""
    tracer = JobTracer(
        langfuse=langfuse_client,
        job_name="test_job",
        tenant_id="t_test",
        actor_id="a_test",
    )
    gen = tracer.start_generation("gen1", model="test")
    tracer.end_generation(gen, token_usage={
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
    })
    assert tracer.metrics.total_tokens == 150
    assert tracer.metrics.model_calls == 1
    tracer.complete(output={"test": True})
    assert tracer.metrics.status == "success"
