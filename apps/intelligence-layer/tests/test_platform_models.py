"""Tests for platform response models."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.models.platform_models import (
    AccountStatus,
    AccountSummary,
    AccountType,
    ClientProfile,
    ContactInfo,
    FreshnessMeta,
    HouseholdSummary,
)


def test_freshness_meta_roundtrip() -> None:
    """FreshnessMeta serializes and deserializes."""
    fm = FreshnessMeta(
        as_of=datetime(2026, 3, 26, 12, 0),
        source="test",
        staleness_seconds=5,
    )
    data = fm.model_dump()
    restored = FreshnessMeta.model_validate(data)
    assert restored.source == "test"
    assert restored.staleness_seconds == 5


def test_account_summary_uses_decimal() -> None:
    """Monetary values are Decimal, not float."""
    acct = AccountSummary(
        account_id="acc_001",
        account_name="Test",
        account_type=AccountType.INDIVIDUAL,
        status=AccountStatus.ACTIVE,
        custodian="schwab",
        total_value=Decimal("500000.00"),
        cash_balance=Decimal("25000.00"),
        holdings=[],
        household_id="hh_001",
        client_id="cl_001",
        freshness=FreshnessMeta(
            as_of=datetime(2026, 3, 26),
            source="test",
        ),
    )
    assert isinstance(acct.total_value, Decimal)
    assert isinstance(acct.cash_balance, Decimal)


def test_household_summary_has_accounts() -> None:
    """HouseholdSummary nests AccountSummary list."""
    hh = HouseholdSummary(
        household_id="hh_001",
        household_name="Test Household",
        primary_advisor_id="adv_001",
        accounts=[],
        total_aum=Decimal("500000.00"),
        client_ids=["cl_001"],
        freshness=FreshnessMeta(
            as_of=datetime(2026, 3, 26),
            source="test",
        ),
    )
    assert hh.household_id == "hh_001"
    assert hh.total_aum == Decimal("500000.00")


def test_client_profile_fields() -> None:
    """ClientProfile includes contact and optional fields."""
    profile = ClientProfile(
        client_id="cl_001",
        first_name="Jane",
        last_name="Smith",
        household_id="hh_001",
        contact=ContactInfo(email="jane@example.com"),
        account_ids=["acc_001"],
        freshness=FreshnessMeta(
            as_of=datetime(2026, 3, 26),
            source="test",
        ),
    )
    assert profile.risk_tolerance is None
    assert profile.contact.email == "jane@example.com"
