"""
app/errors/classification.py — Error taxonomy and status mapping.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class ErrorCategory(str, Enum):
    PLATFORM_READ_FAILURE = "platform_read_failure"
    LLM_PROVIDER_FAILURE = "llm_provider_failure"
    TRANSCRIPTION_FAILURE = "transcription_failure"
    VALIDATION_FAILURE = "validation_failure"
    CONTEXT_TOO_LARGE = "context_too_large"
    INTERNAL_ERROR = "internal_error"


CATEGORY_STATUS_MAP: dict[ErrorCategory, int] = {
    ErrorCategory.PLATFORM_READ_FAILURE: 502,
    ErrorCategory.LLM_PROVIDER_FAILURE: 503,
    ErrorCategory.TRANSCRIPTION_FAILURE: 503,
    ErrorCategory.VALIDATION_FAILURE: 422,
    ErrorCategory.CONTEXT_TOO_LARGE: 413,
    ErrorCategory.INTERNAL_ERROR: 500,
}

CATEGORY_RETRYABLE: dict[ErrorCategory, bool] = {
    ErrorCategory.PLATFORM_READ_FAILURE: True,
    ErrorCategory.LLM_PROVIDER_FAILURE: True,
    ErrorCategory.TRANSCRIPTION_FAILURE: True,
    ErrorCategory.VALIDATION_FAILURE: False,
    ErrorCategory.CONTEXT_TOO_LARGE: False,
    ErrorCategory.INTERNAL_ERROR: False,
}


class ClassifiedError(BaseModel):
    error_code: ErrorCategory
    message: str
    detail: str | None = None
    retryable: bool
    retry_after_seconds: int | None = None
