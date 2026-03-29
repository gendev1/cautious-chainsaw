"""
app/utils/errors.py — Shared HTTP error utilities for routers.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel


class ErrorCategory(str, Enum):
    PLATFORM_READ = "platform_read"
    MODEL_PROVIDER = "model_provider"
    VALIDATION = "validation"
    INTERNAL = "internal"


class SidecarErrorResponse(BaseModel):
    category: ErrorCategory
    code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


class PlatformReadHTTPError(HTTPException):
    def __init__(
        self,
        detail: str,
        request_id: str | None = None,
    ) -> None:
        super().__init__(
            status_code=502,
            detail=SidecarErrorResponse(
                category=ErrorCategory.PLATFORM_READ,
                code="PLATFORM_READ_FAILED",
                message=detail,
                request_id=request_id,
            ).model_dump(),
        )


class ModelProviderHTTPError(HTTPException):
    def __init__(
        self,
        detail: str,
        request_id: str | None = None,
    ) -> None:
        super().__init__(
            status_code=502,
            detail=SidecarErrorResponse(
                category=ErrorCategory.MODEL_PROVIDER,
                code="MODEL_PROVIDER_FAILED",
                message=detail,
                request_id=request_id,
            ).model_dump(),
        )


class ValidationHTTPError(HTTPException):
    def __init__(
        self,
        detail: str,
        request_id: str | None = None,
    ) -> None:
        super().__init__(
            status_code=422,
            detail=SidecarErrorResponse(
                category=ErrorCategory.VALIDATION,
                code="VALIDATION_FAILED",
                message=detail,
                request_id=request_id,
            ).model_dump(),
        )


class InternalHTTPError(HTTPException):
    def __init__(
        self,
        detail: str,
        request_id: str | None = None,
    ) -> None:
        super().__init__(
            status_code=500,
            detail=SidecarErrorResponse(
                category=ErrorCategory.INTERNAL,
                code="INTERNAL_ERROR",
                message=detail,
                request_id=request_id,
            ).model_dump(),
        )
