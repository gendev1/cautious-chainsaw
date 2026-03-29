"""Tests for MockPlatformClient — test double for agent tests."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from app.errors import PlatformReadError
from app.models.access_scope import AccessScope
from app.models.platform_models import (
    FreshnessMeta,
    HouseholdSummary,
)
from tests.mocks.mock_platform_client import MockPlatformClient


def _scope() -> AccessScope:
    return AccessScope(
        tenant_id="t_001",
        actor_id="a_001",
        actor_type="advisor",
        request_id="r_001",
        visibility_mode="scoped",
        household_ids=["hh_001"],
    )


@pytest.mark.asyncio
async def test_default_canned_response() -> None:
    """Methods return plausible canned data by default."""
    mock = MockPlatformClient()
    result = await mock.get_household_summary("hh_001", _scope())
    assert result.household_id == "hh_001"
    assert result.total_aum == Decimal("500000.00")


@pytest.mark.asyncio
async def test_custom_override() -> None:
    """set_household overrides default response."""
    mock = MockPlatformClient()
    custom = HouseholdSummary(
        household_id="hh_999",
        household_name="VIP",
        primary_advisor_id="adv_002",
        accounts=[],
        total_aum=Decimal("10000000.00"),
        client_ids=["cl_999"],
        freshness=FreshnessMeta(
            as_of=datetime(2026, 3, 26, 12, 0),
            source="test",
        ),
    )
    mock.set_household("hh_999", custom)
    result = await mock.get_household_summary("hh_999", _scope())
    assert result.total_aum == Decimal("10000000.00")


@pytest.mark.asyncio
async def test_error_simulation() -> None:
    """set_error causes method to raise configured error."""
    mock = MockPlatformClient()
    mock.set_error(
        "household:hh_404",
        PlatformReadError(
            status_code=404,
            error_code="NOT_FOUND",
            message="Household not found",
        ),
    )
    with pytest.raises(PlatformReadError) as exc_info:
        await mock.get_household_summary("hh_404", _scope())
    assert exc_info.value.error_code == "NOT_FOUND"
