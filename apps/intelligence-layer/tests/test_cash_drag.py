"""Tests for CashDragDetector."""
from __future__ import annotations

from app.analytics.cash_drag import CashDragDetector


def test_high_cash_flagged() -> None:
    detector = CashDragDetector()
    result = detector.score(
        {
            "accounts": [
                {
                    "account_id": "acc1",
                    "total_value": 100000,
                    "cash_balance": 15000,
                    "account_type": "individual",
                },
            ],
            "as_of": "2026-03-28",
        }
    )
    assert result["accounts_flagged"] > 0


def test_low_cash_not_flagged() -> None:
    detector = CashDragDetector()
    result = detector.score(
        {
            "accounts": [
                {
                    "account_id": "acc1",
                    "total_value": 100000,
                    "cash_balance": 2000,
                    "account_type": "individual",
                },
            ],
            "as_of": "2026-03-28",
        }
    )
    assert result["accounts_flagged"] == 0
