"""Tests for TaxLossHarvestingScorer."""
from __future__ import annotations

from app.analytics.tax_loss_harvesting import TaxLossHarvestingScorer


def _make_lot(ticker="SPY", shares=100, cost_basis=300.0, current_price=250.0, lot_id="lot1"):
    return {
        "lot_id": lot_id,
        "ticker": ticker,
        "shares": shares,
        "cost_basis_per_share": cost_basis,
        "current_price": current_price,
        "acquisition_date": "2025-01-15",
        "account_id": "acc_001",
    }


def test_finds_loss_candidate() -> None:
    scorer = TaxLossHarvestingScorer()
    result = scorer.score({
        "lots": [_make_lot()],
        "as_of": "2026-03-28",
        "federal_bracket": 0.37,
        "lt_rate": 0.20,
        "realized_gains_ytd": 0.0,
    })
    assert result["candidates_found"] >= 1
    assert result["total_potential_saving"] > 0


def test_ignores_gains() -> None:
    scorer = TaxLossHarvestingScorer()
    result = scorer.score({
        "lots": [_make_lot(current_price=400.0)],
        "as_of": "2026-03-28",
    })
    assert result["candidates_found"] == 0


def test_wash_sale_blocks() -> None:
    scorer = TaxLossHarvestingScorer()
    result = scorer.score({
        "lots": [_make_lot()],
        "recent_trades": [{
            "ticker": "SPY",
            "trade_date": "2026-03-20",
            "direction": "buy",
            "account_id": "acc_001",
        }],
        "as_of": "2026-03-28",
    })
    candidates = result["candidates"]
    assert any(c["wash_sale_blocked"] for c in candidates)


def test_replacement_candidates_found() -> None:
    scorer = TaxLossHarvestingScorer()
    result = scorer.score({
        "lots": [_make_lot(ticker="SPY")],
        "as_of": "2026-03-28",
    })
    cand = result["candidates"][0]
    assert "IVV" in cand["replacement_candidates"] or "VOO" in cand["replacement_candidates"]
