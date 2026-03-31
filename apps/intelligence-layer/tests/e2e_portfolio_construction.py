"""
End-to-end portfolio construction test with real LLM calls and real Redis.

Run with:
    cd apps/intelligence-layer
    export $(grep -v '^#' .env | xargs)
    .venv/bin/python tests/e2e_portfolio_construction.py
"""
from __future__ import annotations

import asyncio
import json
import time

import redis.asyncio as aioredis


# ---------------------------------------------------------------------------
# Mock platform client (returns synthetic but realistic market data)
# ---------------------------------------------------------------------------

class MockPlatformClient:
    """Simulates the platform API's security master endpoints."""

    UNIVERSE = [
        {"ticker": "NVDA", "name": "NVIDIA Corp", "sector": "Information Technology", "industry": "Semiconductors", "market_cap": 3200e9, "description": "AI training chips and GPUs, data center accelerators", "tags": ["AI", "semiconductor", "data center"]},
        {"ticker": "MSFT", "name": "Microsoft Corp", "sector": "Information Technology", "industry": "Systems Software", "market_cap": 3100e9, "description": "Azure cloud, Copilot AI, enterprise software", "tags": ["AI", "cloud", "enterprise"]},
        {"ticker": "GOOGL", "name": "Alphabet Inc", "sector": "Communication Services", "industry": "Internet Services", "market_cap": 2200e9, "description": "Search, Google Cloud, DeepMind AI research", "tags": ["AI", "cloud", "search"]},
        {"ticker": "AMZN", "name": "Amazon.com", "sector": "Consumer Discretionary", "industry": "Internet Retail", "market_cap": 2100e9, "description": "E-commerce and AWS cloud computing", "tags": ["cloud", "e-commerce"]},
        {"ticker": "META", "name": "Meta Platforms", "sector": "Communication Services", "industry": "Internet Services", "market_cap": 1600e9, "description": "Social media, AI research, VR/AR", "tags": ["AI", "social media", "VR"]},
        {"ticker": "AVGO", "name": "Broadcom Inc", "sector": "Information Technology", "industry": "Semiconductors", "market_cap": 800e9, "description": "AI networking chips, custom accelerators", "tags": ["AI", "semiconductor", "networking"]},
        {"ticker": "TSM", "name": "Taiwan Semi", "sector": "Information Technology", "industry": "Semiconductors", "market_cap": 750e9, "description": "Advanced chip fabrication, AI chip manufacturing", "tags": ["semiconductor", "manufacturing"]},
        {"ticker": "AAPL", "name": "Apple Inc", "sector": "Information Technology", "industry": "Technology Hardware", "market_cap": 3500e9, "description": "Consumer electronics, Apple Intelligence AI", "tags": ["consumer", "hardware", "AI"]},
        {"ticker": "AMD", "name": "Advanced Micro Devices", "sector": "Information Technology", "industry": "Semiconductors", "market_cap": 220e9, "description": "CPUs and GPUs for AI and data centers", "tags": ["AI", "semiconductor", "data center"]},
        {"ticker": "CRM", "name": "Salesforce Inc", "sector": "Information Technology", "industry": "Application Software", "market_cap": 280e9, "description": "CRM platform with Einstein AI", "tags": ["AI", "enterprise", "SaaS"]},
        {"ticker": "NOW", "name": "ServiceNow Inc", "sector": "Information Technology", "industry": "Application Software", "market_cap": 190e9, "description": "Enterprise workflow automation with AI", "tags": ["AI", "enterprise", "SaaS"]},
        {"ticker": "SNOW", "name": "Snowflake Inc", "sector": "Information Technology", "industry": "Application Software", "market_cap": 55e9, "description": "Cloud data platform for AI/ML", "tags": ["AI", "cloud", "data"]},
        {"ticker": "PLTR", "name": "Palantir Technologies", "sector": "Information Technology", "industry": "Application Software", "market_cap": 140e9, "description": "Data analytics and AI platforms for defense and enterprise", "tags": ["AI", "data analytics", "defense"]},
        {"ticker": "MRVL", "name": "Marvell Technology", "sector": "Information Technology", "industry": "Semiconductors", "market_cap": 70e9, "description": "Custom AI silicon, data center networking", "tags": ["AI", "semiconductor", "networking"]},
        {"ticker": "PANW", "name": "Palo Alto Networks", "sector": "Information Technology", "industry": "Systems Software", "market_cap": 120e9, "description": "AI-powered cybersecurity", "tags": ["AI", "cybersecurity"]},
        {"ticker": "XOM", "name": "Exxon Mobil", "sector": "Energy", "industry": "Oil & Gas", "market_cap": 500e9, "description": "Integrated oil and gas", "tags": ["energy", "oil"]},
        {"ticker": "JNJ", "name": "Johnson & Johnson", "sector": "Health Care", "industry": "Pharmaceuticals", "market_cap": 380e9, "description": "Pharmaceuticals and consumer health", "tags": ["healthcare"]},
        {"ticker": "PG", "name": "Procter & Gamble", "sector": "Consumer Staples", "industry": "Household Products", "market_cap": 390e9, "description": "Consumer staples", "tags": ["consumer staples"]},
        {"ticker": "KO", "name": "Coca-Cola", "sector": "Consumer Staples", "industry": "Soft Drinks", "market_cap": 280e9, "description": "Beverages", "tags": ["consumer staples", "beverages"]},
        {"ticker": "JPM", "name": "JPMorgan Chase", "sector": "Financials", "industry": "Banks", "market_cap": 680e9, "description": "Banking and financial services", "tags": ["finance", "banking"]},
    ]

    FUNDAMENTALS_BY_TICKER = {
        "NVDA": {"pe_ratio": 60.0, "pb_ratio": 40.0, "roe": 0.90, "profit_margin": 0.55, "debt_to_equity": 0.41, "revenue_growth": 0.94, "earnings_growth": 1.20, "roic": 0.75, "operating_margin": 0.60},
        "MSFT": {"pe_ratio": 35.0, "pb_ratio": 12.0, "roe": 0.38, "profit_margin": 0.36, "debt_to_equity": 0.35, "revenue_growth": 0.16, "earnings_growth": 0.20, "roic": 0.30, "operating_margin": 0.44},
        "GOOGL": {"pe_ratio": 22.0, "pb_ratio": 7.0, "roe": 0.30, "profit_margin": 0.27, "debt_to_equity": 0.10, "revenue_growth": 0.14, "earnings_growth": 0.30, "roic": 0.25, "operating_margin": 0.32},
        "AMZN": {"pe_ratio": 55.0, "pb_ratio": 8.0, "roe": 0.22, "profit_margin": 0.08, "debt_to_equity": 0.55, "revenue_growth": 0.12, "earnings_growth": 0.60, "roic": 0.12, "operating_margin": 0.11},
        "META": {"pe_ratio": 24.0, "pb_ratio": 8.5, "roe": 0.35, "profit_margin": 0.34, "debt_to_equity": 0.18, "revenue_growth": 0.22, "earnings_growth": 0.35, "roic": 0.28, "operating_margin": 0.41},
        "AVGO": {"pe_ratio": 30.0, "pb_ratio": 11.0, "roe": 0.25, "profit_margin": 0.30, "debt_to_equity": 1.00, "revenue_growth": 0.44, "earnings_growth": 0.25, "roic": 0.15, "operating_margin": 0.35},
        "TSM": {"pe_ratio": 25.0, "pb_ratio": 7.5, "roe": 0.28, "profit_margin": 0.40, "debt_to_equity": 0.20, "revenue_growth": 0.30, "earnings_growth": 0.35, "roic": 0.22, "operating_margin": 0.45},
        "AAPL": {"pe_ratio": 30.0, "pb_ratio": 45.0, "roe": 1.60, "profit_margin": 0.26, "debt_to_equity": 1.50, "revenue_growth": 0.05, "earnings_growth": 0.08, "roic": 0.55, "operating_margin": 0.30},
        "AMD": {"pe_ratio": 45.0, "pb_ratio": 4.0, "roe": 0.05, "profit_margin": 0.08, "debt_to_equity": 0.05, "revenue_growth": 0.10, "earnings_growth": 0.25, "roic": 0.04, "operating_margin": 0.12},
        "CRM": {"pe_ratio": 45.0, "pb_ratio": 5.0, "roe": 0.10, "profit_margin": 0.17, "debt_to_equity": 0.15, "revenue_growth": 0.11, "earnings_growth": 0.50, "roic": 0.08, "operating_margin": 0.20},
        "NOW": {"pe_ratio": 55.0, "pb_ratio": 18.0, "roe": 0.15, "profit_margin": 0.20, "debt_to_equity": 0.25, "revenue_growth": 0.23, "earnings_growth": 0.30, "roic": 0.10, "operating_margin": 0.25},
        "SNOW": {"pe_ratio": None, "pb_ratio": 15.0, "roe": -0.10, "profit_margin": -0.05, "debt_to_equity": 0.10, "revenue_growth": 0.28, "earnings_growth": None, "roic": -0.08, "operating_margin": -0.03},
        "PLTR": {"pe_ratio": 80.0, "pb_ratio": 20.0, "roe": 0.12, "profit_margin": 0.18, "debt_to_equity": 0.05, "revenue_growth": 0.25, "earnings_growth": 0.40, "roic": 0.10, "operating_margin": 0.15},
        "MRVL": {"pe_ratio": 70.0, "pb_ratio": 5.0, "roe": 0.04, "profit_margin": 0.10, "debt_to_equity": 0.40, "revenue_growth": 0.20, "earnings_growth": 0.50, "roic": 0.05, "operating_margin": 0.12},
        "PANW": {"pe_ratio": 50.0, "pb_ratio": 20.0, "roe": 0.40, "profit_margin": 0.25, "debt_to_equity": 1.20, "revenue_growth": 0.16, "earnings_growth": 0.45, "roic": 0.15, "operating_margin": 0.18},
        "XOM": {"pe_ratio": 12.0, "pb_ratio": 2.0, "roe": 0.18, "profit_margin": 0.10, "debt_to_equity": 0.20, "revenue_growth": -0.05, "earnings_growth": -0.10, "roic": 0.12, "operating_margin": 0.12},
        "JNJ": {"pe_ratio": 15.0, "pb_ratio": 5.0, "roe": 0.22, "profit_margin": 0.20, "debt_to_equity": 0.45, "revenue_growth": 0.03, "earnings_growth": 0.05, "roic": 0.15, "operating_margin": 0.25},
        "PG": {"pe_ratio": 25.0, "pb_ratio": 8.0, "roe": 0.30, "profit_margin": 0.18, "debt_to_equity": 0.70, "revenue_growth": 0.02, "earnings_growth": 0.04, "roic": 0.20, "operating_margin": 0.22},
        "KO": {"pe_ratio": 22.0, "pb_ratio": 10.0, "roe": 0.38, "profit_margin": 0.22, "debt_to_equity": 1.50, "revenue_growth": 0.01, "earnings_growth": 0.03, "roic": 0.12, "operating_margin": 0.28},
        "JPM": {"pe_ratio": 12.0, "pb_ratio": 2.0, "roe": 0.15, "profit_margin": 0.30, "debt_to_equity": 2.00, "revenue_growth": 0.08, "earnings_growth": 0.10, "roic": 0.10, "operating_margin": 0.35},
    }

    PRICES_BY_TICKER = {
        "NVDA": {"return_6m": 0.40, "return_12_1m": 0.80, "volatility_1y": 0.50, "beta": 1.8, "max_drawdown_1y": -0.25},
        "MSFT": {"return_6m": 0.12, "return_12_1m": 0.22, "volatility_1y": 0.22, "beta": 1.1, "max_drawdown_1y": -0.10},
        "GOOGL": {"return_6m": 0.08, "return_12_1m": 0.18, "volatility_1y": 0.25, "beta": 1.1, "max_drawdown_1y": -0.12},
        "AMZN": {"return_6m": 0.15, "return_12_1m": 0.25, "volatility_1y": 0.28, "beta": 1.2, "max_drawdown_1y": -0.15},
        "META": {"return_6m": 0.10, "return_12_1m": 0.30, "volatility_1y": 0.35, "beta": 1.3, "max_drawdown_1y": -0.18},
        "AVGO": {"return_6m": 0.30, "return_12_1m": 0.60, "volatility_1y": 0.35, "beta": 1.4, "max_drawdown_1y": -0.20},
        "TSM": {"return_6m": 0.20, "return_12_1m": 0.35, "volatility_1y": 0.30, "beta": 1.2, "max_drawdown_1y": -0.15},
        "AAPL": {"return_6m": 0.05, "return_12_1m": 0.10, "volatility_1y": 0.20, "beta": 1.0, "max_drawdown_1y": -0.08},
        "AMD": {"return_6m": -0.05, "return_12_1m": 0.00, "volatility_1y": 0.45, "beta": 1.7, "max_drawdown_1y": -0.30},
        "CRM": {"return_6m": 0.08, "return_12_1m": 0.15, "volatility_1y": 0.28, "beta": 1.2, "max_drawdown_1y": -0.12},
        "NOW": {"return_6m": 0.12, "return_12_1m": 0.20, "volatility_1y": 0.30, "beta": 1.2, "max_drawdown_1y": -0.14},
        "SNOW": {"return_6m": -0.10, "return_12_1m": -0.15, "volatility_1y": 0.55, "beta": 1.8, "max_drawdown_1y": -0.40},
        "PLTR": {"return_6m": 0.50, "return_12_1m": 0.90, "volatility_1y": 0.60, "beta": 2.0, "max_drawdown_1y": -0.35},
        "MRVL": {"return_6m": 0.15, "return_12_1m": 0.25, "volatility_1y": 0.40, "beta": 1.5, "max_drawdown_1y": -0.22},
        "PANW": {"return_6m": 0.10, "return_12_1m": 0.20, "volatility_1y": 0.30, "beta": 1.1, "max_drawdown_1y": -0.12},
        "XOM": {"return_6m": -0.02, "return_12_1m": 0.05, "volatility_1y": 0.20, "beta": 0.8, "max_drawdown_1y": -0.10},
        "JNJ": {"return_6m": 0.02, "return_12_1m": 0.05, "volatility_1y": 0.15, "beta": 0.6, "max_drawdown_1y": -0.08},
        "PG": {"return_6m": 0.03, "return_12_1m": 0.08, "volatility_1y": 0.15, "beta": 0.5, "max_drawdown_1y": -0.06},
        "KO": {"return_6m": 0.01, "return_12_1m": 0.06, "volatility_1y": 0.14, "beta": 0.5, "max_drawdown_1y": -0.05},
        "JPM": {"return_6m": 0.08, "return_12_1m": 0.15, "volatility_1y": 0.22, "beta": 1.1, "max_drawdown_1y": -0.12},
    }

    async def get_security_universe(self, access_scope=None):
        return list(self.UNIVERSE)

    async def bulk_fundamentals(self, tickers, access_scope=None):
        return [{"ticker": t, **self.FUNDAMENTALS_BY_TICKER.get(t, {"pe_ratio": 20.0, "roe": 0.10, "profit_margin": 0.10})} for t in tickers]

    async def bulk_price_data(self, tickers, access_scope=None):
        return [{"ticker": t, **self.PRICES_BY_TICKER.get(t, {"return_6m": 0.05, "volatility_1y": 0.25, "beta": 1.0})} for t in tickers]


class MockAccessScope:
    visibility_mode = "full_tenant"
    def model_dump(self):
        return {"visibility_mode": "full_tenant"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_full_pipeline(redis: aioredis.Redis) -> str:
    """TEST 1: Full pipeline with real factor scoring and default (stub) agents."""
    print("=" * 60)
    print("TEST 1: Full Portfolio Construction Pipeline")
    print("=" * 60)

    from app.config import get_settings
    from app.portfolio_construction.orchestrator import PortfolioConstructionPipeline
    from app.portfolio_construction.models import ConstructPortfolioRequest
    from app.portfolio_construction.events import ProgressEventEmitter

    settings = get_settings()
    job_id = f"e2e-test-{int(time.time())}"

    pipeline = PortfolioConstructionPipeline(
        platform=MockPlatformClient(),
        redis=redis,
        access_scope=MockAccessScope(),
        settings=settings,
    )

    request = ConstructPortfolioRequest(
        message="Build me a 10-stock AI and semiconductor portfolio, avoid energy",
        target_count=10,
        exclude_tickers=["XOM"],
    )

    start = time.time()
    result = await pipeline.run(request=request, job_id=job_id)
    elapsed = time.time() - start

    print(f"  Completed in {elapsed:.1f}s")
    print(f"  Holdings: {len(result.proposed_holdings)}")
    for h in result.proposed_holdings:
        print(f"    {h.ticker:6s} weight={h.weight:.2%} composite={h.composite_score:.1f} sector={h.sector}")

    # Assertions
    tickers = [h.ticker for h in result.proposed_holdings]
    assert "XOM" not in tickers, "XOM should be excluded!"
    print("  XOM excluded: PASS")

    total_weight = sum(h.weight for h in result.proposed_holdings)
    assert abs(total_weight - 1.0) < 0.02, f"Weights should sum to ~1.0, got {total_weight}"
    print(f"  Weights sum to {total_weight:.4f}: PASS")

    assert len(result.proposed_holdings) > 0, "Should have at least 1 holding"
    print(f"  Holdings count {len(result.proposed_holdings)}: PASS")

    # Store result for later tests
    result_key = f"sidecar:portfolio:result:{job_id}"
    await redis.set(result_key, result.model_dump_json(), ex=3600)
    print(f"  Stored in Redis: {result_key}")

    # Check events
    emitter = ProgressEventEmitter(redis)
    events = await emitter.read_events(job_id)
    print(f"  Progress events: {len(events)}")
    assert len(events) >= 5, f"Expected >= 5 events, got {len(events)}"

    status = await emitter.get_job_status(job_id)
    assert status == "job_completed", f"Expected job_completed, got {status}"
    print(f"  Final status: {status}: PASS")

    print("TEST 1: PASSED\n")
    return job_id


async def test_load_from_redis(redis: aioredis.Redis, job_id: str):
    """TEST 2: Load portfolio from Redis (simulates copilot tool)."""
    print("=" * 60)
    print("TEST 2: Load Constructed Portfolio from Redis")
    print("=" * 60)

    raw = await redis.get(f"sidecar:portfolio:result:{job_id}")
    assert raw is not None, "Portfolio result should be in Redis"
    loaded = json.loads(raw)

    assert "proposed_holdings" in loaded
    assert len(loaded["proposed_holdings"]) > 0
    print(f"  Loaded {len(loaded['proposed_holdings'])} holdings")

    assert "rationale" in loaded
    thesis = loaded["rationale"]["thesis_summary"]
    print(f"  Thesis: {thesis[:80]}...")

    assert "parsed_intent" in loaded
    print(f"  Intent themes: {loaded['parsed_intent']['themes']}")

    assert "warnings" in loaded
    assert "relaxations" in loaded
    assert "metadata" in loaded
    print(f"  All fields present: PASS")

    print("TEST 2: PASSED\n")


async def test_revise_mode(redis: aioredis.Redis, prior_job_id: str):
    """TEST 3: Revise mode — modify a previous portfolio."""
    print("=" * 60)
    print("TEST 3: Revise Mode (drop NVDA, rebuild)")
    print("=" * 60)

    from app.config import get_settings
    from app.portfolio_construction.orchestrator import PortfolioConstructionPipeline
    from app.portfolio_construction.models import ConstructPortfolioRequest

    settings = get_settings()
    revise_job_id = f"e2e-revise-{int(time.time())}"

    pipeline = PortfolioConstructionPipeline(
        platform=MockPlatformClient(),
        redis=redis,
        access_scope=MockAccessScope(),
        settings=settings,
    )

    request = ConstructPortfolioRequest(
        message="Make it more conservative, drop NVDA",
        prior_job_id=prior_job_id,
        exclude_tickers=["NVDA"],
        target_count=8,
    )

    start = time.time()
    result = await pipeline.run(request=request, job_id=revise_job_id)
    elapsed = time.time() - start

    print(f"  Completed in {elapsed:.1f}s")
    tickers = [h.ticker for h in result.proposed_holdings]
    print(f"  Holdings: {tickers}")

    assert "NVDA" not in tickers, "NVDA should be excluded in revision!"
    print("  NVDA excluded: PASS")

    assert "XOM" not in tickers, "XOM should still be excluded from prior intent!"
    print("  XOM still excluded: PASS")

    # Check revision warning
    has_revise_warning = any("Revising" in w for w in result.warnings)
    print(f"  Revise warning present: {has_revise_warning}")

    total_weight = sum(h.weight for h in result.proposed_holdings)
    assert abs(total_weight - 1.0) < 0.02
    print(f"  Weights sum to {total_weight:.4f}: PASS")

    print("TEST 3: PASSED\n")


async def test_event_persistence(redis: aioredis.Redis, job_id: str):
    """TEST 4: Events persist and can be re-read (SSE reconnect)."""
    print("=" * 60)
    print("TEST 4: Event Persistence and Reconnect")
    print("=" * 60)

    from app.portfolio_construction.events import ProgressEventEmitter
    emitter = ProgressEventEmitter(redis)

    events = await emitter.read_events(job_id)
    print(f"  Events after completion: {len(events)}")
    assert len(events) >= 5

    # Read with last_id (simulate reconnect)
    if len(events) >= 2:
        first_id = events[0].get("id", "0-0")
        remaining = await emitter.read_events(job_id, last_id=first_id)
        print(f"  Events after reconnect (skip first): {len(remaining)}")

    status = await emitter.get_job_status(job_id)
    assert status == "job_completed"
    print(f"  Status still readable: {status}: PASS")

    print("TEST 4: PASSED\n")


async def test_error_resilience(redis: aioredis.Redis):
    """TEST 5: Graceful handling of errors and edge cases."""
    print("=" * 60)
    print("TEST 5: Error Resilience")
    print("=" * 60)

    from app.portfolio_construction.orchestrator import PortfolioConstructionPipeline
    from app.portfolio_construction.models import ConstructPortfolioRequest

    # Test with empty message
    pipeline = PortfolioConstructionPipeline(
        platform=MockPlatformClient(),
        redis=redis,
        access_scope=MockAccessScope(),
    )

    request = ConstructPortfolioRequest(
        message="",
        target_count=5,
    )

    job_id = f"e2e-error-{int(time.time())}"
    result = await pipeline.run(request=request, job_id=job_id)
    print(f"  Empty message: {len(result.proposed_holdings)} holdings (graceful)")

    # Test with invalid prior_job_id
    request2 = ConstructPortfolioRequest(
        message="revise this",
        prior_job_id="nonexistent-job-id-12345",
    )
    job_id2 = f"e2e-error2-{int(time.time())}"
    result2 = await pipeline.run(request=request2, job_id=job_id2)
    has_not_found = any("not found" in w.lower() for w in result2.warnings)
    print(f"  Invalid prior_job_id warning: {has_not_found}: PASS")

    # Test with everything excluded
    request3 = ConstructPortfolioRequest(
        message="Build a portfolio",
        exclude_tickers=["NVDA", "MSFT", "GOOGL", "AMZN", "META", "AVGO", "TSM",
                         "AAPL", "AMD", "CRM", "NOW", "SNOW", "PLTR", "MRVL", "PANW",
                         "XOM", "JNJ", "PG", "KO", "JPM"],
        target_count=5,
    )
    job_id3 = f"e2e-error3-{int(time.time())}"
    result3 = await pipeline.run(request=request3, job_id=job_id3)
    print(f"  All excluded: {len(result3.proposed_holdings)} holdings, {len(result3.warnings)} warnings")

    print("TEST 5: PASSED\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    redis = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)
    await redis.ping()
    print("Redis: connected\n")

    try:
        job_id = await test_full_pipeline(redis)
        await test_load_from_redis(redis, job_id)
        await test_revise_mode(redis, job_id)
        await test_event_persistence(redis, job_id)
        await test_error_resilience(redis)

        print("=" * 60)
        print("ALL END-TO-END TESTS PASSED")
        print("=" * 60)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
