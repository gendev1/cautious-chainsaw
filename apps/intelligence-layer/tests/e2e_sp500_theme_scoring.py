"""
End-to-end scale test: Theme scoring with full S&P 500 universe (503 stocks).

Uses the real portfolio construction pipeline with real LLM calls (claude-haiku-4-5
for theme scoring) against the full S&P 500 universe loaded from mandate-py.

Run with:
    cd apps/intelligence-layer
    export $(grep -v '^#' .env | xargs)
    .venv/bin/python tests/e2e_sp500_theme_scoring.py
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import math
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis


# ---------------------------------------------------------------------------
# Parse the S&P 500 list from mandate-py (no import — just parse the AST)
# ---------------------------------------------------------------------------

def load_sp500() -> list[dict[str, str]]:
    """Load the SP500 list from mandate-py/_sp500.py by executing the module."""
    sp500_path = Path(__file__).resolve().parents[3] / "mandate-py" / "src" / "mandate" / "adapters" / "_sp500.py"
    if not sp500_path.exists():
        raise FileNotFoundError(f"SP500 file not found at {sp500_path}")

    namespace: dict[str, Any] = {}
    exec(sp500_path.read_text(), namespace)  # noqa: S102
    if "SP500" not in namespace:
        raise ValueError("SP500 variable not found in _sp500.py")
    return namespace["SP500"]


# ---------------------------------------------------------------------------
# Sector-based synthetic data generators
# ---------------------------------------------------------------------------

SECTOR_FUNDAMENTALS: dict[str, dict[str, float]] = {
    "Information Technology": {"pe_ratio": 35.0, "pb_ratio": 10.0, "roe": 0.25, "profit_margin": 0.22, "debt_to_equity": 0.40, "revenue_growth": 0.15, "earnings_growth": 0.20, "roic": 0.18, "operating_margin": 0.28},
    "Communication Services": {"pe_ratio": 22.0, "pb_ratio": 6.0, "roe": 0.20, "profit_margin": 0.18, "debt_to_equity": 0.50, "revenue_growth": 0.10, "earnings_growth": 0.15, "roic": 0.14, "operating_margin": 0.22},
    "Consumer Discretionary": {"pe_ratio": 25.0, "pb_ratio": 7.0, "roe": 0.18, "profit_margin": 0.12, "debt_to_equity": 0.60, "revenue_growth": 0.08, "earnings_growth": 0.12, "roic": 0.12, "operating_margin": 0.15},
    "Consumer Staples": {"pe_ratio": 22.0, "pb_ratio": 8.0, "roe": 0.28, "profit_margin": 0.15, "debt_to_equity": 0.80, "revenue_growth": 0.03, "earnings_growth": 0.05, "roic": 0.15, "operating_margin": 0.18},
    "Energy": {"pe_ratio": 12.0, "pb_ratio": 2.0, "roe": 0.15, "profit_margin": 0.10, "debt_to_equity": 0.35, "revenue_growth": -0.02, "earnings_growth": -0.05, "roic": 0.10, "operating_margin": 0.12},
    "Financials": {"pe_ratio": 14.0, "pb_ratio": 1.8, "roe": 0.12, "profit_margin": 0.25, "debt_to_equity": 1.50, "revenue_growth": 0.06, "earnings_growth": 0.08, "roic": 0.08, "operating_margin": 0.30},
    "Health Care": {"pe_ratio": 20.0, "pb_ratio": 5.0, "roe": 0.18, "profit_margin": 0.16, "debt_to_equity": 0.55, "revenue_growth": 0.08, "earnings_growth": 0.10, "roic": 0.14, "operating_margin": 0.20},
    "Industrials": {"pe_ratio": 22.0, "pb_ratio": 5.0, "roe": 0.20, "profit_margin": 0.12, "debt_to_equity": 0.65, "revenue_growth": 0.06, "earnings_growth": 0.09, "roic": 0.12, "operating_margin": 0.14},
    "Materials": {"pe_ratio": 18.0, "pb_ratio": 3.5, "roe": 0.16, "profit_margin": 0.12, "debt_to_equity": 0.50, "revenue_growth": 0.04, "earnings_growth": 0.06, "roic": 0.10, "operating_margin": 0.16},
    "Real Estate": {"pe_ratio": 30.0, "pb_ratio": 2.5, "roe": 0.08, "profit_margin": 0.25, "debt_to_equity": 1.20, "revenue_growth": 0.04, "earnings_growth": 0.05, "roic": 0.05, "operating_margin": 0.35},
    "Utilities": {"pe_ratio": 18.0, "pb_ratio": 2.0, "roe": 0.10, "profit_margin": 0.14, "debt_to_equity": 1.30, "revenue_growth": 0.02, "earnings_growth": 0.03, "roic": 0.05, "operating_margin": 0.20},
}

SECTOR_PRICES: dict[str, dict[str, float]] = {
    "Information Technology": {"return_6m": 0.15, "return_12_1m": 0.28, "volatility_1y": 0.30, "beta": 1.25, "max_drawdown_1y": -0.15},
    "Communication Services": {"return_6m": 0.10, "return_12_1m": 0.20, "volatility_1y": 0.28, "beta": 1.15, "max_drawdown_1y": -0.14},
    "Consumer Discretionary": {"return_6m": 0.08, "return_12_1m": 0.15, "volatility_1y": 0.26, "beta": 1.10, "max_drawdown_1y": -0.13},
    "Consumer Staples": {"return_6m": 0.03, "return_12_1m": 0.07, "volatility_1y": 0.14, "beta": 0.60, "max_drawdown_1y": -0.06},
    "Energy": {"return_6m": -0.02, "return_12_1m": 0.04, "volatility_1y": 0.25, "beta": 0.90, "max_drawdown_1y": -0.12},
    "Financials": {"return_6m": 0.07, "return_12_1m": 0.14, "volatility_1y": 0.22, "beta": 1.05, "max_drawdown_1y": -0.11},
    "Health Care": {"return_6m": 0.05, "return_12_1m": 0.10, "volatility_1y": 0.20, "beta": 0.80, "max_drawdown_1y": -0.09},
    "Industrials": {"return_6m": 0.06, "return_12_1m": 0.12, "volatility_1y": 0.22, "beta": 1.00, "max_drawdown_1y": -0.10},
    "Materials": {"return_6m": 0.04, "return_12_1m": 0.09, "volatility_1y": 0.22, "beta": 0.95, "max_drawdown_1y": -0.10},
    "Real Estate": {"return_6m": 0.02, "return_12_1m": 0.06, "volatility_1y": 0.20, "beta": 0.75, "max_drawdown_1y": -0.08},
    "Utilities": {"return_6m": 0.02, "return_12_1m": 0.05, "volatility_1y": 0.15, "beta": 0.50, "max_drawdown_1y": -0.05},
}

# Specific AI/semiconductor overrides for key tickers
AI_TICKER_OVERRIDES: dict[str, dict[str, float]] = {
    "NVDA": {"pe_ratio": 60.0, "pb_ratio": 40.0, "roe": 0.90, "profit_margin": 0.55, "revenue_growth": 0.94, "earnings_growth": 1.20, "roic": 0.75, "operating_margin": 0.60},
    "AMD": {"pe_ratio": 45.0, "pb_ratio": 4.0, "roe": 0.05, "revenue_growth": 0.10, "earnings_growth": 0.25},
    "AVGO": {"pe_ratio": 30.0, "pb_ratio": 11.0, "roe": 0.25, "revenue_growth": 0.44, "earnings_growth": 0.25},
    "MSFT": {"pe_ratio": 35.0, "pb_ratio": 12.0, "roe": 0.38, "revenue_growth": 0.16, "earnings_growth": 0.20, "roic": 0.30},
    "GOOGL": {"pe_ratio": 22.0, "pb_ratio": 7.0, "roe": 0.30, "revenue_growth": 0.14, "earnings_growth": 0.30},
    "META": {"pe_ratio": 24.0, "pb_ratio": 8.5, "roe": 0.35, "revenue_growth": 0.22, "earnings_growth": 0.35},
}

AI_PRICE_OVERRIDES: dict[str, dict[str, float]] = {
    "NVDA": {"return_6m": 0.40, "return_12_1m": 0.80, "volatility_1y": 0.50, "beta": 1.8},
    "AMD": {"return_6m": -0.05, "return_12_1m": 0.00, "volatility_1y": 0.45, "beta": 1.7},
    "AVGO": {"return_6m": 0.30, "return_12_1m": 0.60, "volatility_1y": 0.35, "beta": 1.4},
    "MSFT": {"return_6m": 0.12, "return_12_1m": 0.22, "volatility_1y": 0.22, "beta": 1.1},
    "GOOGL": {"return_6m": 0.08, "return_12_1m": 0.18, "volatility_1y": 0.25, "beta": 1.1},
    "META": {"return_6m": 0.10, "return_12_1m": 0.30, "volatility_1y": 0.35, "beta": 1.3},
}


def _jitter(value: float, pct: float = 0.15) -> float:
    """Add deterministic-ish random jitter to a value."""
    return value * (1.0 + random.uniform(-pct, pct))


def generate_fundamentals(ticker: str, sector: str) -> dict[str, Any]:
    """Generate synthetic fundamentals using sector defaults + optional overrides."""
    # Seed random per-ticker for reproducibility
    random.seed(hashlib.md5(ticker.encode()).hexdigest())

    base = dict(SECTOR_FUNDAMENTALS.get(sector, SECTOR_FUNDAMENTALS["Industrials"]))

    # Apply per-ticker overrides for key AI stocks
    if ticker in AI_TICKER_OVERRIDES:
        base.update(AI_TICKER_OVERRIDES[ticker])

    # Add jitter to all values
    result = {"ticker": ticker}
    for key, value in base.items():
        if isinstance(value, (int, float)):
            result[key] = round(_jitter(value), 4)
        else:
            result[key] = value

    return result


def generate_prices(ticker: str, sector: str) -> dict[str, Any]:
    """Generate synthetic price data using sector defaults + optional overrides."""
    random.seed(hashlib.md5((ticker + "_prices").encode()).hexdigest())

    base = dict(SECTOR_PRICES.get(sector, SECTOR_PRICES["Industrials"]))

    if ticker in AI_PRICE_OVERRIDES:
        base.update(AI_PRICE_OVERRIDES[ticker])

    result = {"ticker": ticker}
    for key, value in base.items():
        if isinstance(value, (int, float)):
            result[key] = round(_jitter(value), 4)
        else:
            result[key] = value

    return result


# ---------------------------------------------------------------------------
# Mock platform client serving full S&P 500
# ---------------------------------------------------------------------------

class SP500MockPlatformClient:
    """Platform client serving all 503 S&P 500 stocks with synthetic data."""

    def __init__(self, sp500: list[dict[str, str]]) -> None:
        self._sp500 = sp500
        # Enrich with descriptions and tags for metadata matching
        self._universe = self._build_universe()

    def _build_universe(self) -> list[dict[str, Any]]:
        """Build the full universe with descriptions and tags for recall pool."""
        universe = []
        for entry in self._sp500:
            sec = dict(entry)
            # Add synthetic description and tags based on industry
            industry = sec.get("industry", "")
            sector = sec.get("sector", "")
            ticker = sec["ticker"]

            desc_parts = [sec["name"], industry]
            tags = [sector.lower()]

            # AI/semiconductor tagging
            industry_lower = industry.lower()
            if "semiconductor" in industry_lower:
                desc_parts.append("chip design and fabrication")
                tags.extend(["semiconductor", "AI", "chips"])
            if ticker in ("NVDA", "AMD"):
                desc_parts.append("AI training chips and GPUs, data center accelerators")
                tags.extend(["AI", "data center", "GPU"])
            if ticker == "AVGO":
                desc_parts.append("AI networking chips, custom accelerators")
                tags.extend(["AI", "networking"])
            if ticker == "MSFT":
                desc_parts.append("Azure cloud, Copilot AI, enterprise software")
                tags.extend(["AI", "cloud", "enterprise"])
            if ticker in ("GOOGL", "GOOG"):
                desc_parts.append("Search, Google Cloud, DeepMind AI research")
                tags.extend(["AI", "cloud", "search"])
            if ticker == "META":
                desc_parts.append("AI research, social media, VR/AR")
                tags.extend(["AI", "social media"])
            if ticker == "AMZN":
                desc_parts.append("AWS cloud computing, AI services")
                tags.extend(["cloud", "AI", "e-commerce"])
            if "software" in industry_lower:
                tags.extend(["software"])
            if "cloud" in industry_lower or "internet" in industry_lower:
                tags.extend(["cloud", "internet"])

            # Energy tagging
            if sector == "Energy":
                desc_parts.append("fossil fuel energy")
                tags.extend(["energy", "oil", "fossil fuel"])

            # Tobacco tagging
            if "tobacco" in industry_lower:
                desc_parts.append("tobacco products")
                tags.extend(["tobacco", "sin stock"])

            sec["description"] = ", ".join(desc_parts)
            sec["tags"] = list(set(tags))
            sec["market_cap"] = random.uniform(10e9, 3500e9)  # Synthetic market cap
            universe.append(sec)

        return universe

    async def get_security_universe(self, access_scope: Any = None) -> list[dict[str, Any]]:
        return list(self._universe)

    async def bulk_fundamentals(self, tickers: list[str], access_scope: Any = None) -> list[dict[str, Any]]:
        sector_map = {s["ticker"]: s.get("sector", "Industrials") for s in self._sp500}
        return [generate_fundamentals(t, sector_map.get(t, "Industrials")) for t in tickers]

    async def bulk_price_data(self, tickers: list[str], access_scope: Any = None) -> list[dict[str, Any]]:
        sector_map = {s["ticker"]: s.get("sector", "Industrials") for s in self._sp500}
        return [generate_prices(t, sector_map.get(t, "Industrials")) for t in tickers]


class MockAccessScope:
    visibility_mode = "full_tenant"
    def model_dump(self):
        return {"visibility_mode": "full_tenant"}


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

async def test_sp500_theme_scoring(redis: aioredis.Redis) -> None:
    """Full scale test: 503 S&P 500 stocks through the portfolio construction pipeline."""
    print("=" * 70)
    print("S&P 500 SCALE TEST: Theme Scoring with 503 Stocks")
    print("=" * 70)

    # ---- Load SP500 ----
    t0 = time.time()
    sp500 = load_sp500()
    t_load = time.time() - t0
    print(f"\n[1] Loaded S&P 500 list: {len(sp500)} stocks ({t_load:.2f}s)")
    assert len(sp500) == 503, f"Expected 503 stocks, got {len(sp500)}"
    print(f"    Sectors: {sorted(set(s['sector'] for s in sp500))}")

    energy_tickers = [s["ticker"] for s in sp500 if s["sector"] == "Energy"]
    tobacco_tickers = [s["ticker"] for s in sp500 if "Tobacco" in s.get("industry", "")]
    print(f"    Energy tickers ({len(energy_tickers)}): {energy_tickers[:5]}...")
    print(f"    Tobacco tickers ({len(tobacco_tickers)}): {tobacco_tickers}")

    # ---- Build mock platform ----
    platform = SP500MockPlatformClient(sp500)
    universe = await platform.get_security_universe()
    print(f"    Mock universe built: {len(universe)} securities")

    # ---- Run pipeline ----
    from app.config import get_settings
    from app.portfolio_construction.orchestrator import PortfolioConstructionPipeline
    from app.portfolio_construction.models import ConstructPortfolioRequest

    settings = get_settings()
    job_id = f"e2e-sp500-{int(time.time())}"

    pipeline = PortfolioConstructionPipeline(
        platform=platform,
        redis=redis,
        access_scope=MockAccessScope(),
        settings=settings,
    )

    request = ConstructPortfolioRequest(
        message="Build me a 15-stock AI infrastructure portfolio, avoid energy and tobacco",
        target_count=15,
    )

    print(f"\n[2] Running pipeline (job_id={job_id})...")
    print(f"    Prompt: {request.message!r}")

    t_start = time.time()

    # Stage-by-stage timing via intermediate pipeline access
    result = await pipeline.run(request=request, job_id=job_id)

    t_total = time.time() - t_start
    print(f"\n[3] Pipeline completed in {t_total:.1f}s")

    # ---- Read events for timing breakdown ----
    from app.portfolio_construction.events import ProgressEventEmitter
    emitter = ProgressEventEmitter(redis)
    events = await emitter.read_events(job_id)
    print(f"    Progress events: {len(events)}")
    for evt in events:
        etype = evt.get("event_type", "?")
        payload_raw = evt.get("payload", "{}")
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        ts = evt.get("timestamp", "")
        detail = ", ".join(f"{k}={v}" for k, v in payload.items()) if payload else ""
        print(f"      {etype}: {detail} @ {ts}")

    # ---- Verify recall pool ----
    print(f"\n[4] Verifying recall pool and theme scoring...")

    # The recall pool is built from factor_scores. We can check via score breakdowns.
    scored_tickers = {cs.ticker for cs in result.score_breakdowns}
    print(f"    Total tickers scored (composite): {len(scored_tickers)}")

    # Theme-scored tickers are those with non-zero theme scores in the breakdowns
    theme_scored = [cs for cs in result.score_breakdowns if cs.theme_score > 0]
    print(f"    Tickers with LLM theme scores: {len(theme_scored)}")
    # The recall pool should contain a substantial subset (150 factor top + metadata matches)
    assert len(theme_scored) >= 50, (
        f"Expected at least 50 theme-scored tickers (recall pool), got {len(theme_scored)}"
    )
    print(f"    Recall pool size check: PASS (>= 50 theme-scored)")

    # ---- Verify AI/semiconductor scores ----
    print(f"\n[5] Checking AI/semiconductor stock scores...")
    ai_tickers = ["NVDA", "AMD", "AVGO", "MSFT", "GOOGL", "META"]
    ai_scores_ok = True
    for t in ai_tickers:
        cs = next((c for c in result.score_breakdowns if c.ticker == t), None)
        if cs is None:
            print(f"    {t}: NOT IN SCORES (may not be in recall pool)")
            continue
        status = "PASS" if cs.theme_score >= 65 else f"WARN (theme={cs.theme_score:.0f} < 65)"
        if cs.theme_score < 65:
            ai_scores_ok = False
        print(f"    {t}: theme={cs.theme_score:.0f} composite={cs.composite_score:.1f} gated={cs.gated} {status}")

    if ai_scores_ok:
        print("    AI stock theme scores >= 65: PASS")
    else:
        print("    AI stock theme scores >= 65: WARN (some below threshold, LLM variance)")

    # ---- Verify energy stocks ----
    print(f"\n[6] Checking energy stock exclusions...")
    energy_check_tickers = ["XOM", "CVX", "COP", "EOG", "SLB"]
    energy_ok = True
    for t in energy_check_tickers:
        cs = next((c for c in result.score_breakdowns if c.ticker == t), None)
        if cs is None:
            print(f"    {t}: not in scored set (excluded or not in recall pool) - OK")
            continue
        is_blocked = cs.gated or cs.theme_score <= 10
        is_anti_goal = cs.gate_reason and "anti" in cs.gate_reason.lower() if cs.gate_reason else False
        status = "PASS" if is_blocked else f"WARN (theme={cs.theme_score:.0f}, gated={cs.gated})"
        if not is_blocked:
            energy_ok = False
        print(f"    {t}: theme={cs.theme_score:.0f} gated={cs.gated} reason={cs.gate_reason!r} {status}")

    if energy_ok:
        print("    Energy stocks blocked: PASS")
    else:
        print("    Energy stocks blocked: WARN (some not blocked, LLM variance)")

    # ---- Verify tobacco stocks ----
    print(f"\n[7] Checking tobacco stock exclusions...")
    tobacco_check_tickers = ["MO", "PM"]
    tobacco_ok = True
    for t in tobacco_check_tickers:
        cs = next((c for c in result.score_breakdowns if c.ticker == t), None)
        if cs is None:
            print(f"    {t}: not in scored set (excluded or not in recall pool) - OK")
            continue
        is_blocked = cs.gated or cs.theme_score <= 10
        status = "PASS" if is_blocked else f"WARN (theme={cs.theme_score:.0f}, gated={cs.gated})"
        if not is_blocked:
            tobacco_ok = False
        print(f"    {t}: theme={cs.theme_score:.0f} gated={cs.gated} reason={cs.gate_reason!r} {status}")

    if tobacco_ok:
        print("    Tobacco stocks blocked: PASS")
    else:
        print("    Tobacco stocks blocked: WARN (some not blocked, LLM variance)")

    # ---- Verify final portfolio ----
    print(f"\n[8] Verifying final portfolio...")
    holdings = result.proposed_holdings
    holding_tickers = [h.ticker for h in holdings]
    total_weight = sum(h.weight for h in holdings)

    print(f"    Holdings count: {len(holdings)}")
    for h in holdings:
        print(f"      {h.ticker:6s} weight={h.weight:.2%} composite={h.composite_score:.1f} "
              f"factor={h.factor_score:.1f} theme={h.theme_score:.1f} sector={h.sector}")

    # Count check
    assert len(holdings) == 15, f"Expected 15 holdings, got {len(holdings)}"
    print(f"    Holdings count == 15: PASS")

    # No energy
    energy_in_portfolio = [h.ticker for h in holdings if h.sector == "Energy"]
    assert len(energy_in_portfolio) == 0, f"Energy stocks in portfolio: {energy_in_portfolio}"
    print(f"    No energy in portfolio: PASS")

    # No tobacco
    tobacco_in_portfolio = [h.ticker for h in holdings
                           if h.ticker in tobacco_tickers]
    assert len(tobacco_in_portfolio) == 0, f"Tobacco stocks in portfolio: {tobacco_in_portfolio}"
    print(f"    No tobacco in portfolio: PASS")

    # Weights sum to 1.0
    assert abs(total_weight - 1.0) < 0.02, f"Weights sum to {total_weight:.4f}, expected ~1.0"
    print(f"    Weights sum to {total_weight:.4f}: PASS")

    # ---- Timing summary ----
    print(f"\n[9] Timing Summary")
    print(f"    SP500 load:      {t_load:.2f}s")
    print(f"    Full pipeline:   {t_total:.1f}s")
    print(f"    Avg per stock:   {t_total / len(sp500) * 1000:.1f}ms")

    # ---- Store result ----
    result_key = f"sidecar:portfolio:result:{job_id}"
    await redis.set(result_key, result.model_dump_json(), ex=3600)
    print(f"\n    Result stored in Redis: {result_key}")

    # ---- Summary ----
    print("\n" + "=" * 70)
    all_pass = energy_ok and tobacco_ok and len(holdings) == 15 and abs(total_weight - 1.0) < 0.02
    if all_pass and ai_scores_ok:
        print("S&P 500 SCALE TEST: ALL CHECKS PASSED")
    elif all_pass:
        print("S&P 500 SCALE TEST: PASSED (with LLM variance warnings on AI scores)")
    else:
        print("S&P 500 SCALE TEST: COMPLETED WITH WARNINGS (review above)")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    redis = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)
    await redis.ping()
    print("Redis: connected\n")

    try:
        await test_sp500_theme_scoring(redis)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
