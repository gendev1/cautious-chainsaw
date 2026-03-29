"""
app/main.py — FastAPI application entry point.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.dependencies import (
    close_platform_client,
    close_redis,
    close_vector_store,
    init_platform_client,
    init_redis,
    init_vector_store,
)
from app.errors import SidecarError
from app.middleware.logging import StructuredLoggingMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.tenant import TenantContextMiddleware
from app.routers import (
    admin,
    chat,
    crm,
    digest,
    documents,
    email,
    health,
    indexing,
    meetings,
    portfolio,
    reports,
    tasks,
    tax,
)

logger = logging.getLogger("sidecar")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Startup: initialize shared resources (Redis, vector store, platform client).
    Shutdown: close connections gracefully.

    Resources are stored on app.state so that dependency-injection functions
    can retrieve them without module-level singletons.
    """
    settings = get_settings()

    # --- Startup -----------------------------------------------------------
    logger.info("sidecar starting", extra={"env": settings.environment})

    app.state.settings = settings
    app.state.redis = await init_redis(settings)
    app.state.vector_store = await init_vector_store(settings)
    app.state.platform_client = await init_platform_client(settings)

    yield

    # --- Shutdown ----------------------------------------------------------
    logger.info("sidecar shutting down")

    await close_platform_client(app.state.platform_client)
    await close_vector_store(app.state.vector_store)
    await close_redis(app.state.redis)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Wealth Advisor — AI Sidecar",
        version="0.1.0",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------
    # Middleware — order matters.
    #
    # FastAPI/Starlette applies middleware in REVERSE registration order.
    # The first middleware registered is the OUTERMOST layer (runs first
    # on the way in, last on the way out).
    #
    # Desired execution order on each request:
    #   1. CORS (outermost — must run before anything reads the body)
    #   2. RequestId (generate or forward X-Request-ID)
    #   3. TenantContext (extract X-Tenant-ID, X-Actor-ID, access scope)
    #   4. StructuredLogging (log with tenant + request id in scope)
    #
    # Therefore we register in this order (outermost first):
    # ------------------------------------------------------------------

    # 1. CORS — outermost
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. Request-ID propagation
    app.add_middleware(RequestIdMiddleware)

    # 3. Tenant context extraction
    app.add_middleware(TenantContextMiddleware)

    # 4. Structured logging — innermost, can read tenant + request id
    app.add_middleware(StructuredLoggingMiddleware)

    # ------------------------------------------------------------------
    # Global exception handler
    # ------------------------------------------------------------------
    @app.exception_handler(SidecarError)
    async def sidecar_error_handler(request: Request, exc: SidecarError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "ok": False,
                "error": {
                    "code": exc.error_code,
                    "category": exc.category,
                    "message": exc.message,
                    "detail": exc.detail,
                    "request_id": getattr(request.state, "request_id", None),
                },
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "category": "internal",
                    "message": "An unexpected error occurred.",
                    "detail": None,
                    "request_id": getattr(request.state, "request_id", None),
                },
            },
        )

    # ------------------------------------------------------------------
    # Routers
    # ------------------------------------------------------------------
    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(indexing.router)
    app.include_router(chat.router, prefix="/ai")
    app.include_router(digest.router, prefix="/ai")
    app.include_router(email.router, prefix="/ai")
    app.include_router(tasks.router, prefix="/ai")
    app.include_router(crm.router, prefix="/ai")
    app.include_router(meetings.router, prefix="/ai")
    app.include_router(tax.router, prefix="/ai")
    app.include_router(portfolio.router, prefix="/ai")
    app.include_router(reports.router, prefix="/ai")
    app.include_router(documents.router, prefix="/ai")

    return app


app = create_app()


def main() -> None:
    """CLI entry point for running the sidecar API."""
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.environment == "development",
        log_level=settings.log_level.lower(),
    )
