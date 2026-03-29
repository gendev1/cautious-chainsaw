"""
app/routers/health.py — Liveness and readiness probes.
"""
from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

logger = logging.getLogger("sidecar.health")

router = APIRouter(tags=["health"])


@router.get(
    "/metrics", response_class=PlainTextResponse
)
async def prometheus_metrics() -> PlainTextResponse:
    """Prometheus metrics endpoint."""
    return PlainTextResponse(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@router.get("/health")
async def health() -> dict:
    """
    Liveness probe.
    Returns 200 if the process is alive. No dependency checks.
    """
    return {"status": "ok"}


@router.get("/health/worker")
async def worker_health(request: Request) -> dict:
    """Check if the ARQ worker is alive based on its
    health check key in Redis.
    """
    try:
        last_heartbeat = await request.app.state.redis.get(
            "sidecar:worker:health"
        )
    except Exception as exc:
        return {
            "status": "unhealthy",
            "reason": f"redis_error: {exc}",
        }

    if last_heartbeat is None:
        return {
            "status": "unhealthy",
            "reason": "no_heartbeat",
        }

    age = time.time() - float(last_heartbeat)
    if age > 60:
        return {
            "status": "unhealthy",
            "reason": "stale_heartbeat",
            "age_seconds": age,
        }

    return {
        "status": "healthy",
        "last_heartbeat_age_seconds": age,
    }


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """
    Readiness probe.
    Checks Redis, vector store, and platform API reachability.
    Returns 200 only if ALL dependencies are healthy.
    Returns 503 with a breakdown if any dependency is unhealthy.
    """
    checks: dict[str, dict] = {}

    checks["redis"] = await _check_redis(request)
    checks["vector_store"] = await _check_vector_store(request)
    checks["platform_api"] = await _check_platform_api(request)
    checks["llm_provider"] = await _check_llm_provider(request)

    all_healthy = all(c["status"] == "ok" for c in checks.values())
    status_code = 200 if all_healthy else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if all_healthy else "degraded",
            "checks": checks,
        },
    )


async def _check_redis(request: Request) -> dict:
    try:
        start = time.perf_counter()
        await request.app.state.redis.ping()
        latency_ms = (time.perf_counter() - start) * 1000
        return {"status": "ok", "latency_ms": round(latency_ms, 2)}
    except Exception as exc:
        logger.warning("redis health check failed", exc_info=exc)
        return {"status": "error", "error": str(exc)}


async def _check_vector_store(request: Request) -> dict:
    try:
        start = time.perf_counter()
        await request.app.state.vector_store.health_check()
        latency_ms = (time.perf_counter() - start) * 1000
        return {"status": "ok", "latency_ms": round(latency_ms, 2)}
    except Exception as exc:
        logger.warning("vector store health check failed", exc_info=exc)
        return {"status": "error", "error": str(exc)}


async def _check_platform_api(request: Request) -> dict:
    try:
        start = time.perf_counter()
        resp = await request.app.state.platform_client._http.get("/health")
        latency_ms = (time.perf_counter() - start) * 1000
        if resp.status_code == 200:
            return {"status": "ok", "latency_ms": round(latency_ms, 2)}
        return {"status": "error", "error": f"HTTP {resp.status_code}"}
    except Exception as exc:
        logger.warning("platform API health check failed", exc_info=exc)
        return {"status": "error", "error": str(exc)}


async def _check_llm_provider(request: Request) -> dict:
    """
    Lightweight check: verify the primary LLM provider API key is set.
    """
    settings = request.app.state.settings
    if not settings.anthropic_api_key and not settings.openai_api_key:
        return {"status": "error", "error": "No LLM provider API key configured."}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            if settings.anthropic_api_key:
                resp = await client.get(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": settings.anthropic_api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
                if resp.status_code < 500:
                    return {"status": "ok"}
                return {"status": "error", "error": f"HTTP {resp.status_code}"}
    except Exception as exc:
        logger.warning("LLM provider health check failed", exc_info=exc)
        return {"status": "error", "error": str(exc)}

    return {"status": "ok"}
