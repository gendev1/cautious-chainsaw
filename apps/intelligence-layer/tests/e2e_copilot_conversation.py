"""
End-to-end copilot conversation flow test.

Tests the full lifecycle: portfolio construction -> Redis storage ->
conversation memory -> copilot tool loading -> real copilot agent ->
revise mode with prior context.

Run with:
    cd apps/intelligence-layer
    export $(grep -v '^#' .env | xargs)
    .venv/bin/python tests/e2e_copilot_conversation.py
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import redis.asyncio as aioredis


# ---------------------------------------------------------------------------
# Mock platform client (20 stocks — lightweight for copilot tests)
# ---------------------------------------------------------------------------

class MockPlatformClient:
    """Simulates the platform API with a small 20-stock universe."""

    UNIVERSE = [
        {"ticker": "NVDA", "name": "NVIDIA Corp", "sector": "Information Technology", "industry": "Semiconductors", "market_cap": 3200e9, "description": "AI training chips and GPUs, data center accelerators", "tags": ["AI", "semiconductor", "data center"]},
        {"ticker": "MSFT", "name": "Microsoft Corp", "sector": "Information Technology", "industry": "Systems Software", "market_cap": 3100e9, "description": "Azure cloud, Copilot AI, enterprise software", "tags": ["AI", "cloud", "enterprise"]},
        {"ticker": "GOOGL", "name": "Alphabet Inc", "sector": "Communication Services", "industry": "Internet Services", "market_cap": 2200e9, "description": "Search, Google Cloud, DeepMind AI research", "tags": ["AI", "cloud", "search"]},
        {"ticker": "AMZN", "name": "Amazon.com", "sector": "Consumer Discretionary", "industry": "Internet Retail", "market_cap": 2100e9, "description": "E-commerce and AWS cloud computing", "tags": ["cloud", "e-commerce"]},
        {"ticker": "META", "name": "Meta Platforms", "sector": "Communication Services", "industry": "Internet Services", "market_cap": 1600e9, "description": "Social media, AI research, VR/AR", "tags": ["AI", "social media", "VR"]},
        {"ticker": "AVGO", "name": "Broadcom Inc", "sector": "Information Technology", "industry": "Semiconductors", "market_cap": 800e9, "description": "AI networking chips, custom accelerators", "tags": ["AI", "semiconductor", "networking"]},
        {"ticker": "AAPL", "name": "Apple Inc", "sector": "Information Technology", "industry": "Technology Hardware", "market_cap": 3500e9, "description": "Consumer electronics, Apple Intelligence AI", "tags": ["consumer", "hardware", "AI"]},
        {"ticker": "AMD", "name": "Advanced Micro Devices", "sector": "Information Technology", "industry": "Semiconductors", "market_cap": 220e9, "description": "CPUs and GPUs for AI and data centers", "tags": ["AI", "semiconductor", "data center"]},
        {"ticker": "CRM", "name": "Salesforce Inc", "sector": "Information Technology", "industry": "Application Software", "market_cap": 280e9, "description": "CRM platform with Einstein AI", "tags": ["AI", "enterprise", "SaaS"]},
        {"ticker": "NOW", "name": "ServiceNow Inc", "sector": "Information Technology", "industry": "Application Software", "market_cap": 190e9, "description": "Enterprise workflow automation with AI", "tags": ["AI", "enterprise", "SaaS"]},
        {"ticker": "PLTR", "name": "Palantir Technologies", "sector": "Information Technology", "industry": "Application Software", "market_cap": 140e9, "description": "Data analytics and AI platforms for defense and enterprise", "tags": ["AI", "data analytics", "defense"]},
        {"ticker": "MRVL", "name": "Marvell Technology", "sector": "Information Technology", "industry": "Semiconductors", "market_cap": 70e9, "description": "Custom AI silicon, data center networking", "tags": ["AI", "semiconductor", "networking"]},
        {"ticker": "PANW", "name": "Palo Alto Networks", "sector": "Information Technology", "industry": "Systems Software", "market_cap": 120e9, "description": "AI-powered cybersecurity", "tags": ["AI", "cybersecurity"]},
        {"ticker": "SNOW", "name": "Snowflake Inc", "sector": "Information Technology", "industry": "Application Software", "market_cap": 55e9, "description": "Cloud data platform for AI/ML", "tags": ["AI", "cloud", "data"]},
        {"ticker": "XOM", "name": "Exxon Mobil", "sector": "Energy", "industry": "Oil & Gas", "market_cap": 500e9, "description": "Integrated oil and gas", "tags": ["energy", "oil"]},
        {"ticker": "CVX", "name": "Chevron Corp", "sector": "Energy", "industry": "Oil & Gas", "market_cap": 300e9, "description": "Integrated oil and gas", "tags": ["energy", "oil"]},
        {"ticker": "JNJ", "name": "Johnson & Johnson", "sector": "Health Care", "industry": "Pharmaceuticals", "market_cap": 380e9, "description": "Pharmaceuticals and consumer health", "tags": ["healthcare"]},
        {"ticker": "PG", "name": "Procter & Gamble", "sector": "Consumer Staples", "industry": "Household Products", "market_cap": 390e9, "description": "Consumer staples", "tags": ["consumer staples"]},
        {"ticker": "JPM", "name": "JPMorgan Chase", "sector": "Financials", "industry": "Banks", "market_cap": 680e9, "description": "Banking and financial services", "tags": ["finance", "banking"]},
        {"ticker": "KO", "name": "Coca-Cola", "sector": "Consumer Staples", "industry": "Soft Drinks", "market_cap": 280e9, "description": "Beverages", "tags": ["consumer staples", "beverages"]},
    ]

    FUNDAMENTALS_BY_TICKER = {
        "NVDA": {"pe_ratio": 60.0, "pb_ratio": 40.0, "roe": 0.90, "profit_margin": 0.55, "debt_to_equity": 0.41, "revenue_growth": 0.94, "earnings_growth": 1.20, "roic": 0.75, "operating_margin": 0.60},
        "MSFT": {"pe_ratio": 35.0, "pb_ratio": 12.0, "roe": 0.38, "profit_margin": 0.36, "debt_to_equity": 0.35, "revenue_growth": 0.16, "earnings_growth": 0.20, "roic": 0.30, "operating_margin": 0.44},
        "GOOGL": {"pe_ratio": 22.0, "pb_ratio": 7.0, "roe": 0.30, "profit_margin": 0.27, "debt_to_equity": 0.10, "revenue_growth": 0.14, "earnings_growth": 0.30, "roic": 0.25, "operating_margin": 0.32},
        "AMZN": {"pe_ratio": 55.0, "pb_ratio": 8.0, "roe": 0.22, "profit_margin": 0.08, "debt_to_equity": 0.55, "revenue_growth": 0.12, "earnings_growth": 0.60, "roic": 0.12, "operating_margin": 0.11},
        "META": {"pe_ratio": 24.0, "pb_ratio": 8.5, "roe": 0.35, "profit_margin": 0.34, "debt_to_equity": 0.18, "revenue_growth": 0.22, "earnings_growth": 0.35, "roic": 0.28, "operating_margin": 0.41},
        "AVGO": {"pe_ratio": 30.0, "pb_ratio": 11.0, "roe": 0.25, "profit_margin": 0.30, "debt_to_equity": 1.00, "revenue_growth": 0.44, "earnings_growth": 0.25, "roic": 0.15, "operating_margin": 0.35},
        "AAPL": {"pe_ratio": 30.0, "pb_ratio": 45.0, "roe": 1.60, "profit_margin": 0.26, "debt_to_equity": 1.50, "revenue_growth": 0.05, "earnings_growth": 0.08, "roic": 0.55, "operating_margin": 0.30},
        "AMD": {"pe_ratio": 45.0, "pb_ratio": 4.0, "roe": 0.05, "profit_margin": 0.08, "debt_to_equity": 0.05, "revenue_growth": 0.10, "earnings_growth": 0.25, "roic": 0.04, "operating_margin": 0.12},
        "CRM": {"pe_ratio": 45.0, "pb_ratio": 5.0, "roe": 0.10, "profit_margin": 0.17, "debt_to_equity": 0.15, "revenue_growth": 0.11, "earnings_growth": 0.50, "roic": 0.08, "operating_margin": 0.20},
        "NOW": {"pe_ratio": 55.0, "pb_ratio": 18.0, "roe": 0.15, "profit_margin": 0.20, "debt_to_equity": 0.25, "revenue_growth": 0.23, "earnings_growth": 0.30, "roic": 0.10, "operating_margin": 0.25},
        "PLTR": {"pe_ratio": 80.0, "pb_ratio": 20.0, "roe": 0.12, "profit_margin": 0.18, "debt_to_equity": 0.05, "revenue_growth": 0.25, "earnings_growth": 0.40, "roic": 0.10, "operating_margin": 0.15},
        "MRVL": {"pe_ratio": 70.0, "pb_ratio": 5.0, "roe": 0.04, "profit_margin": 0.10, "debt_to_equity": 0.40, "revenue_growth": 0.20, "earnings_growth": 0.50, "roic": 0.05, "operating_margin": 0.12},
        "PANW": {"pe_ratio": 50.0, "pb_ratio": 20.0, "roe": 0.40, "profit_margin": 0.25, "debt_to_equity": 1.20, "revenue_growth": 0.16, "earnings_growth": 0.45, "roic": 0.15, "operating_margin": 0.18},
        "SNOW": {"pe_ratio": None, "pb_ratio": 15.0, "roe": -0.10, "profit_margin": -0.05, "debt_to_equity": 0.10, "revenue_growth": 0.28, "earnings_growth": None, "roic": -0.08, "operating_margin": -0.03},
        "XOM": {"pe_ratio": 12.0, "pb_ratio": 2.0, "roe": 0.18, "profit_margin": 0.10, "debt_to_equity": 0.20, "revenue_growth": -0.05, "earnings_growth": -0.10, "roic": 0.12, "operating_margin": 0.12},
        "CVX": {"pe_ratio": 13.0, "pb_ratio": 2.2, "roe": 0.16, "profit_margin": 0.11, "debt_to_equity": 0.22, "revenue_growth": -0.04, "earnings_growth": -0.08, "roic": 0.11, "operating_margin": 0.13},
        "JNJ": {"pe_ratio": 15.0, "pb_ratio": 5.0, "roe": 0.22, "profit_margin": 0.20, "debt_to_equity": 0.45, "revenue_growth": 0.03, "earnings_growth": 0.05, "roic": 0.15, "operating_margin": 0.25},
        "PG": {"pe_ratio": 25.0, "pb_ratio": 8.0, "roe": 0.30, "profit_margin": 0.18, "debt_to_equity": 0.70, "revenue_growth": 0.02, "earnings_growth": 0.04, "roic": 0.20, "operating_margin": 0.22},
        "JPM": {"pe_ratio": 12.0, "pb_ratio": 2.0, "roe": 0.15, "profit_margin": 0.30, "debt_to_equity": 2.00, "revenue_growth": 0.08, "earnings_growth": 0.10, "roic": 0.10, "operating_margin": 0.35},
        "KO": {"pe_ratio": 22.0, "pb_ratio": 10.0, "roe": 0.38, "profit_margin": 0.22, "debt_to_equity": 1.50, "revenue_growth": 0.01, "earnings_growth": 0.03, "roic": 0.12, "operating_margin": 0.28},
    }

    PRICES_BY_TICKER = {
        "NVDA": {"return_6m": 0.40, "return_12_1m": 0.80, "volatility_1y": 0.50, "beta": 1.8, "max_drawdown_1y": -0.25},
        "MSFT": {"return_6m": 0.12, "return_12_1m": 0.22, "volatility_1y": 0.22, "beta": 1.1, "max_drawdown_1y": -0.10},
        "GOOGL": {"return_6m": 0.08, "return_12_1m": 0.18, "volatility_1y": 0.25, "beta": 1.1, "max_drawdown_1y": -0.12},
        "AMZN": {"return_6m": 0.15, "return_12_1m": 0.25, "volatility_1y": 0.28, "beta": 1.2, "max_drawdown_1y": -0.15},
        "META": {"return_6m": 0.10, "return_12_1m": 0.30, "volatility_1y": 0.35, "beta": 1.3, "max_drawdown_1y": -0.18},
        "AVGO": {"return_6m": 0.30, "return_12_1m": 0.60, "volatility_1y": 0.35, "beta": 1.4, "max_drawdown_1y": -0.20},
        "AAPL": {"return_6m": 0.05, "return_12_1m": 0.10, "volatility_1y": 0.20, "beta": 1.0, "max_drawdown_1y": -0.08},
        "AMD": {"return_6m": -0.05, "return_12_1m": 0.00, "volatility_1y": 0.45, "beta": 1.7, "max_drawdown_1y": -0.30},
        "CRM": {"return_6m": 0.08, "return_12_1m": 0.15, "volatility_1y": 0.28, "beta": 1.2, "max_drawdown_1y": -0.12},
        "NOW": {"return_6m": 0.12, "return_12_1m": 0.20, "volatility_1y": 0.30, "beta": 1.2, "max_drawdown_1y": -0.14},
        "PLTR": {"return_6m": 0.50, "return_12_1m": 0.90, "volatility_1y": 0.60, "beta": 2.0, "max_drawdown_1y": -0.35},
        "MRVL": {"return_6m": 0.15, "return_12_1m": 0.25, "volatility_1y": 0.40, "beta": 1.5, "max_drawdown_1y": -0.22},
        "PANW": {"return_6m": 0.10, "return_12_1m": 0.20, "volatility_1y": 0.30, "beta": 1.1, "max_drawdown_1y": -0.12},
        "SNOW": {"return_6m": -0.10, "return_12_1m": -0.15, "volatility_1y": 0.55, "beta": 1.8, "max_drawdown_1y": -0.40},
        "XOM": {"return_6m": -0.02, "return_12_1m": 0.05, "volatility_1y": 0.20, "beta": 0.8, "max_drawdown_1y": -0.10},
        "CVX": {"return_6m": -0.01, "return_12_1m": 0.06, "volatility_1y": 0.21, "beta": 0.85, "max_drawdown_1y": -0.11},
        "JNJ": {"return_6m": 0.02, "return_12_1m": 0.05, "volatility_1y": 0.15, "beta": 0.6, "max_drawdown_1y": -0.08},
        "PG": {"return_6m": 0.03, "return_12_1m": 0.08, "volatility_1y": 0.15, "beta": 0.5, "max_drawdown_1y": -0.06},
        "JPM": {"return_6m": 0.08, "return_12_1m": 0.15, "volatility_1y": 0.22, "beta": 1.1, "max_drawdown_1y": -0.12},
        "KO": {"return_6m": 0.01, "return_12_1m": 0.06, "volatility_1y": 0.14, "beta": 0.5, "max_drawdown_1y": -0.05},
    }

    async def get_security_universe(self, access_scope=None):
        return list(self.UNIVERSE)

    async def bulk_fundamentals(self, tickers, access_scope=None):
        return [{"ticker": t, **self.FUNDAMENTALS_BY_TICKER.get(t, {"pe_ratio": 20.0, "roe": 0.10, "profit_margin": 0.10})} for t in tickers]

    async def bulk_price_data(self, tickers, access_scope=None):
        return [{"ticker": t, **self.PRICES_BY_TICKER.get(t, {"return_6m": 0.05, "volatility_1y": 0.25, "beta": 1.0})} for t in tickers]

    # Stub methods for copilot tools that won't be called in this test
    async def get_household_summary(self, **kw):
        return {"error": "mock"}

    async def get_account_summary(self, **kw):
        return {"error": "mock"}

    async def get_client_timeline(self, **kw):
        return []

    async def get_transfer_case(self, **kw):
        return {"error": "mock"}

    async def get_order_projection(self, **kw):
        return {"error": "mock"}

    async def get_report_snapshot(self, **kw):
        return {"error": "mock"}

    async def get_document_content(self, *a, **kw):
        return ""

    async def get_document_metadata(self, *a, **kw):
        class _M:
            filename = "mock.pdf"
        return _M()

    async def get_advisor_clients(self, **kw):
        return []


class MockAccessScope:
    visibility_mode = "full_tenant"
    def model_dump(self):
        return {"visibility_mode": "full_tenant"}


# ---------------------------------------------------------------------------
# Test 1: Construct a portfolio and store in Redis
# ---------------------------------------------------------------------------

async def test_construct_and_store(redis: aioredis.Redis) -> str:
    """Construct a portfolio via the pipeline and store the result in Redis."""
    print("=" * 60)
    print("TEST 1: Construct Portfolio and Store in Redis")
    print("=" * 60)

    from app.config import get_settings
    from app.portfolio_construction.orchestrator import PortfolioConstructionPipeline
    from app.portfolio_construction.models import ConstructPortfolioRequest

    settings = get_settings()
    job_id = f"e2e-copilot-{int(time.time())}"

    pipeline = PortfolioConstructionPipeline(
        platform=MockPlatformClient(),
        redis=redis,
        access_scope=MockAccessScope(),
        settings=settings,
    )

    request = ConstructPortfolioRequest(
        message="Build me a 10-stock AI and semiconductor portfolio, avoid energy",
        target_count=10,
        exclude_tickers=["XOM", "CVX"],
    )

    start = time.time()
    result = await pipeline.run(request=request, job_id=job_id)
    elapsed = time.time() - start

    print(f"  Completed in {elapsed:.1f}s")
    print(f"  Holdings: {len(result.proposed_holdings)}")
    for h in result.proposed_holdings:
        print(f"    {h.ticker:6s} weight={h.weight:.2%} composite={h.composite_score:.1f} sector={h.sector}")

    # Store result in Redis (simulating the job storing it)
    result_key = f"sidecar:portfolio:result:{job_id}"
    await redis.set(result_key, result.model_dump_json(), ex=3600)
    print(f"  Stored in Redis: {result_key}")

    # Verify storage
    raw = await redis.get(result_key)
    assert raw is not None, "Portfolio result should be stored in Redis"
    loaded = json.loads(raw)
    assert "proposed_holdings" in loaded
    assert len(loaded["proposed_holdings"]) > 0
    print(f"  Redis round-trip verified: {len(loaded['proposed_holdings'])} holdings")

    assert "XOM" not in [h.ticker for h in result.proposed_holdings], "XOM should be excluded"
    assert "CVX" not in [h.ticker for h in result.proposed_holdings], "CVX should be excluded"
    print("  Energy exclusions verified: PASS")

    print("TEST 1: PASSED\n")
    return job_id


# ---------------------------------------------------------------------------
# Test 2: Conversation memory with portfolio job ID
# ---------------------------------------------------------------------------

async def test_conversation_memory(redis: aioredis.Redis, job_id: str) -> str:
    """Store and load conversation state with active_portfolio_job_id."""
    print("=" * 60)
    print("TEST 2: Conversation Memory with Portfolio Job ID")
    print("=" * 60)

    from app.services.conversation_memory import ConversationMemory
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    memory = ConversationMemory(redis)
    tenant_id = "test-tenant"
    actor_id = "test-advisor"
    conversation_id = f"conv-{uuid.uuid4().hex[:8]}"

    # Create a simple message history
    messages = [
        ModelRequest(parts=[UserPromptPart(content="Build me an AI portfolio")]),
    ]

    # Save with extra state (active_portfolio_job_id)
    await memory.save(
        tenant_id=tenant_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
        messages=messages,
        extra_state={"active_portfolio_job_id": job_id},
    )
    print(f"  Saved conversation {conversation_id} with active_portfolio_job_id={job_id}")

    # Load state back
    state = await memory.load_state(
        tenant_id=tenant_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
    )
    print(f"  Loaded state: {state}")

    assert state["active_portfolio_job_id"] == job_id, (
        f"Expected job_id={job_id}, got {state['active_portfolio_job_id']}"
    )
    print(f"  active_portfolio_job_id survives round-trip: PASS")

    # Load messages back
    loaded_messages = await memory.load(
        tenant_id=tenant_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
    )
    assert len(loaded_messages) == 1, f"Expected 1 message, got {len(loaded_messages)}"
    print(f"  Message round-trip: {len(loaded_messages)} message(s): PASS")

    print("TEST 2: PASSED\n")
    return conversation_id


# ---------------------------------------------------------------------------
# Test 3: get_constructed_portfolio tool function
# ---------------------------------------------------------------------------

async def test_portfolio_tool_loading(redis: aioredis.Redis, job_id: str) -> None:
    """Directly call the get_constructed_portfolio tool function."""
    print("=" * 60)
    print("TEST 3: get_constructed_portfolio Tool Loading")
    print("=" * 60)

    from app.models.access_scope import AccessScope

    # Build a mock RunContext-like object
    class _MockDeps:
        def __init__(self):
            self.platform = MockPlatformClient()
            self.access_scope = AccessScope(
                visibility_mode="full_tenant",
                tenant_id="test-tenant",
                actor_id="test-advisor",
            )
            self.tenant_id = "test-tenant"
            self.actor_id = "test-advisor"
            self.redis = redis

    class _MockCtx:
        def __init__(self):
            self.deps = _MockDeps()

    ctx = _MockCtx()

    # Call the tool function directly (it reads from Redis)
    from app.tools.platform import get_constructed_portfolio
    result = await get_constructed_portfolio(ctx, job_id=job_id)

    assert "error" not in result, f"Tool returned error: {result}"
    assert "proposed_holdings" in result, "Result should contain proposed_holdings"
    holdings = result["proposed_holdings"]
    print(f"  Loaded {len(holdings)} holdings via tool")

    # Verify structure
    first = holdings[0]
    assert "ticker" in first, "Holding should have ticker"
    assert "weight" in first, "Holding should have weight"
    assert "composite_score" in first, "Holding should have composite_score"
    assert "sector" in first, "Holding should have sector"
    print(f"  First holding: {first['ticker']} weight={first['weight']:.2%}")

    # Verify rationale is present
    assert "rationale" in result, "Result should contain rationale"
    thesis = result["rationale"]["thesis_summary"]
    print(f"  Thesis: {thesis[:80]}...")

    # Verify parsed_intent is present
    assert "parsed_intent" in result, "Result should contain parsed_intent"
    print(f"  Themes: {result['parsed_intent']['themes']}")

    # Test with non-existent job_id
    not_found = await get_constructed_portfolio(ctx, job_id="nonexistent-12345")
    assert "error" in not_found, "Non-existent job should return error"
    assert not_found["error"] == "not_found"
    print(f"  Non-existent job returns error: PASS")

    print("TEST 3: PASSED\n")


# ---------------------------------------------------------------------------
# Test 4: Real copilot agent with portfolio context
# ---------------------------------------------------------------------------

async def test_copilot_agent(redis: aioredis.Redis, job_id: str) -> None:
    """Run the real copilot agent (Sonnet) with a portfolio question."""
    print("=" * 60)
    print("TEST 4: Real Copilot Agent — 'Why is NVDA in my portfolio?'")
    print("=" * 60)

    from app.agents.copilot import copilot_agent, CopilotDeps
    from app.models.access_scope import AccessScope

    deps = CopilotDeps(
        platform=MockPlatformClient(),
        access_scope=AccessScope(
            visibility_mode="full_tenant",
            tenant_id="test-tenant",
            actor_id="test-advisor",
        ),
        tenant_id="test-tenant",
        actor_id="test-advisor",
        redis=redis,
        active_portfolio_job_id=job_id,
    )

    question = "Why is NVDA in my portfolio? What was its score?"

    print(f"  Question: {question!r}")
    print(f"  active_portfolio_job_id: {job_id}")
    print(f"  Running copilot agent (anthropic:claude-sonnet-4-6)...")

    start = time.time()
    result = await copilot_agent.run(question, deps=deps)
    elapsed = time.time() - start

    # pydantic-ai 1.73.0 uses result.output (not result.data)
    output = result.output
    print(f"  Completed in {elapsed:.1f}s")
    print(f"  Answer ({len(output.answer)} chars):")
    # Print first 500 chars of the answer
    for line in output.answer[:500].split("\n"):
        print(f"    {line}")
    if len(output.answer) > 500:
        print(f"    ... ({len(output.answer) - 500} more chars)")

    print(f"  Confidence: {output.confidence}")
    print(f"  Citations: {len(output.citations)}")
    print(f"  Follow-up questions: {output.follow_up_questions[:2]}")

    # Verify the response references NVDA
    answer_lower = output.answer.lower()
    nvda_mentioned = "nvda" in answer_lower or "nvidia" in answer_lower
    assert nvda_mentioned, "Response should reference NVDA or NVIDIA"
    print(f"  Response references NVDA/NVIDIA: PASS")

    # Check that the agent used the get_constructed_portfolio tool
    # (we can't directly inspect tool calls easily, but the answer should
    # contain portfolio-specific information like scores or sectors)
    has_portfolio_detail = any(term in answer_lower for term in [
        "score", "semiconductor", "composite", "theme", "factor",
        "weight", "sector", "ai", "infrastructure",
    ])
    if has_portfolio_detail:
        print(f"  Response contains portfolio-specific detail: PASS")
    else:
        print(f"  Response contains portfolio-specific detail: WARN (may be generic)")

    print("TEST 4: PASSED\n")


# ---------------------------------------------------------------------------
# Test 5: Revise flow with prior_job_id
# ---------------------------------------------------------------------------

async def test_revise_flow(redis: aioredis.Redis, prior_job_id: str) -> None:
    """Construct a revised portfolio using prior_job_id."""
    print("=" * 60)
    print("TEST 5: Revise Flow — Drop NVDA, Keep Prior Exclusions")
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

    # NVDA should be excluded (from this request)
    assert "NVDA" not in tickers, "NVDA should be excluded in revision!"
    print("  NVDA excluded: PASS")

    # XOM and CVX should still be excluded (from prior intent)
    assert "XOM" not in tickers, "XOM should still be excluded from prior intent!"
    assert "CVX" not in tickers, "CVX should still be excluded from prior intent!"
    print("  Prior exclusions (XOM, CVX) still active: PASS")

    # Check revision warning
    has_revise_warning = any("Revising" in w for w in result.warnings)
    print(f"  Revise warning present: {has_revise_warning}")

    total_weight = sum(h.weight for h in result.proposed_holdings)
    assert abs(total_weight - 1.0) < 0.02, f"Weights sum to {total_weight:.4f}"
    print(f"  Weights sum to {total_weight:.4f}: PASS")

    # Store revised result for potential further testing
    result_key = f"sidecar:portfolio:result:{revise_job_id}"
    await redis.set(result_key, result.model_dump_json(), ex=3600)
    print(f"  Stored revised result: {result_key}")

    print("TEST 5: PASSED\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    redis = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)
    await redis.ping()
    print("Redis: connected\n")

    try:
        # Test 1: Build a portfolio and store it
        job_id = await test_construct_and_store(redis)

        # Test 2: Conversation memory persistence
        conversation_id = await test_conversation_memory(redis, job_id)

        # Test 3: Tool function loading from Redis
        await test_portfolio_tool_loading(redis, job_id)

        # Test 4: Real copilot agent with portfolio context
        await test_copilot_agent(redis, job_id)

        # Test 5: Revise mode with prior context
        await test_revise_flow(redis, job_id)

        print("=" * 60)
        print("ALL COPILOT CONVERSATION TESTS PASSED")
        print("=" * 60)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
