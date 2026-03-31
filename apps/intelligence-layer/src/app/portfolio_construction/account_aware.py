"""Account-aware analysis: overlap, turnover, drift, tax-sensitive warnings."""
from __future__ import annotations

from typing import Any

from app.portfolio_construction.models import ProposedHolding


async def compute_account_context(
    current_holdings: list[dict],
    proposed_holdings: list[ProposedHolding],
    platform: Any,
    access_scope: Any,
    account_id: str,
) -> dict[str, Any]:
    """
    Compute account-aware context.

    Returns dict with overlap_pct, estimated_turnover, drift_summary, tax_warnings.
    """
    current_tickers = {h.get("ticker", "") for h in current_holdings}
    proposed_tickers = {h.ticker for h in proposed_holdings}

    # Overlap
    overlap = current_tickers & proposed_tickers
    overlap_pct = len(overlap) / len(proposed_tickers) if proposed_tickers else 0.0

    # Estimated turnover
    current_weights = {h.get("ticker", ""): float(h.get("weight", 0)) for h in current_holdings}
    proposed_weights = {h.ticker: h.weight for h in proposed_holdings}

    all_tickers = current_tickers | proposed_tickers
    turnover = sum(
        abs(proposed_weights.get(t, 0.0) - current_weights.get(t, 0.0))
        for t in all_tickers
    ) / 2.0

    # Tax warnings
    tax_warnings: list[str] = []
    for h in current_holdings:
        unrealized = h.get("unrealized_gain", 0)
        if unrealized and float(unrealized) > 10000:
            tax_warnings.append(
                f"{h.get('ticker', 'Unknown')}: large unrealized gain ${float(unrealized):,.0f}"
            )

    return {
        "overlap_pct": round(overlap_pct, 4),
        "estimated_turnover": round(turnover, 4),
        "drift_summary": f"{len(overlap)} overlapping positions out of {len(proposed_tickers)} proposed",
        "tax_warnings": tax_warnings,
    }
