"""
app/models/base.py — Financial data freshness metadata.
"""
from __future__ import annotations

import datetime

from pydantic import BaseModel, Field

STALE_THRESHOLD_SECONDS = 3600


class FinancialDataMixin(BaseModel):
    """Every response with financial numbers must
    include freshness metadata.
    """

    as_of: datetime.datetime = Field(
        ...,
        description="Timestamp of the data snapshot",
    )
    source: str = Field(
        ...,
        description="Data source identifier",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )


class StaleDataWarning(BaseModel):
    is_stale: bool
    data_age_seconds: int
    warning: str | None = None


def check_staleness(
    as_of: datetime.datetime,
) -> StaleDataWarning:
    """Check if data is stale."""
    now = datetime.datetime.now(datetime.UTC)
    age = int((now - as_of).total_seconds())
    if age > STALE_THRESHOLD_SECONDS:
        return StaleDataWarning(
            is_stale=True,
            data_age_seconds=age,
            warning=(
                f"Data is {age // 60} minutes old. "
                "Figures may not reflect recent activity."
            ),
        )
    return StaleDataWarning(
        is_stale=False, data_age_seconds=age
    )
