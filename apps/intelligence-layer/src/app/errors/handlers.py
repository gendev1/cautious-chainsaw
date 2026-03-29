"""
app/errors/handlers.py — Global exception handler.
"""
from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from app.errors.classification import CATEGORY_STATUS_MAP
from app.errors.classifier import classify_exception

logger = logging.getLogger("sidecar.error_handler")


async def classified_error_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Global handler that classifies and returns errors."""
    classified = classify_exception(exc)
    status = CATEGORY_STATUS_MAP[classified.error_code]

    logger.error(
        "classified_error: %s %d %s",
        classified.error_code.value,
        status,
        classified.message,
    )

    headers: dict[str, str] = {}
    if classified.retry_after_seconds:
        headers["Retry-After"] = str(
            classified.retry_after_seconds
        )

    return JSONResponse(
        status_code=status,
        content=classified.model_dump(mode="json"),
        headers=headers,
    )


def register_error_handlers(app) -> None:  # type: ignore[no-untyped-def]
    """Register global exception handler."""
    app.add_exception_handler(
        Exception, classified_error_handler
    )
