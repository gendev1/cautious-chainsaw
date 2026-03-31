"""Integration test for PortfolioConstructionPipeline with mocked agents and platform."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.portfolio_construction.orchestrator import PortfolioConstructionPipeline
from app.portfolio_construction.models import (
    CompositeScoreResult,
    ConstructPortfolioRequest,
    ConstructPortfolioResponse,
    CriticFeedback,
    FactorPreferences,
    FactorScoreResult,
    IntentConstraints,
    ParsedIntent,
    PortfolioRationale,
    ProposedHolding,
    ThemeScoreResult,
)


# ---------------------------------------------------------------------------
# Canned data for mocked agents
# ---------------------------------------------------------------------------

CANNED_INTENT = ParsedIntent(
    themes=["artificial intelligence", "cloud computing"],
    anti_goals=["social media"],
    factor_preferences=FactorPreferences(),
    intent_constraints=IntentConstraints(
        excluded_tickers=["META"],
        max_sector_concentration=0.30,
    ),
    ambiguity_flags=[],
    theme_weight=0.60,
    speculative=False,
)

CANNED_THEME_SCORES = [
    ThemeScoreResult(ticker="NVDA", score=92, confidence=0.95, anti_goal_hit=False, reasoning="Leading AI chip maker."),
    ThemeScoreResult(ticker="MSFT", score=85, confidence=0.90, anti_goal_hit=False, reasoning="Cloud and AI leader."),
    ThemeScoreResult(ticker="GOOGL", score=80, confidence=0.88, anti_goal_hit=False, reasoning="AI research leader."),
    ThemeScoreResult(ticker="AMZN", score=75, confidence=0.85, anti_goal_hit=False, reasoning="AWS AI services."),
    ThemeScoreResult(ticker="CRM", score=70, confidence=0.80, anti_goal_hit=False, reasoning="Enterprise AI."),
    ThemeScoreResult(ticker="AAPL", score=60, confidence=0.75, anti_goal_hit=False, reasoning="AI features."),
    ThemeScoreResult(ticker="META", score=0, confidence=0.95, anti_goal_hit=True, reasoning="Social media anti-goal."),
]

CANNED_RATIONALE = PortfolioRationale(
    thesis_summary="AI-focused portfolio targeting infrastructure and cloud leaders.",
    holdings_rationale={
        "NVDA": "Dominant GPU maker for AI workloads.",
        "MSFT": "Azure cloud and AI integration.",
        "GOOGL": "AI research and cloud growth.",
        "AMZN": "AWS market leader.",
        "CRM": "Enterprise AI adoption.",
    },
    core_holdings=["NVDA", "MSFT", "GOOGL"],
    supporting_holdings=["AMZN", "CRM"],
)

CANNED_CRITIC_APPROVED = CriticFeedback(
    status="APPROVED",
    reasoning="Portfolio is well-diversified and aligned with AI themes.",
)

CANNED_CRITIC_REVISION = CriticFeedback(
    status="NEEDS_REVISION",
    reasoning="Missing AMD as an obvious AI chip play. Consider adding for diversification.",
    add_tickers=["AMD"],
    remove_tickers=[],
    adjust_weights={},
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_mock_platform(n_securities: int = 30) -> MagicMock:
    """Build a mock PlatformClient with canned security data."""
    platform = MagicMock()
    tickers = [f"T{i:03d}" for i in range(n_securities)]
    real_tickers = ["NVDA", "MSFT", "GOOGL", "AMZN", "CRM", "AAPL", "META", "AMD"]
    all_tickers = real_tickers + tickers[:n_securities - len(real_tickers)]

    platform.get_security_universe = AsyncMock(return_value=[
        {
            "ticker": t,
            "name": f"{t} Corp",
            "sector": "Technology",
            "industry": "Software",
            "market_cap": 50_000_000_000.0,
            "description": f"{t} company.",
            "tags": [],
            "freshness": {"as_of": "2026-03-28T12:00:00", "source": "platform", "staleness_seconds": 100},
        }
        for t in all_tickers
    ])

    platform.bulk_fundamentals = AsyncMock(return_value=[
        {
            "ticker": t,
            "pe_ratio": 25.0 + i,
            "pb_ratio": 5.0,
            "roe": 0.20,
            "revenue_growth": 0.15,
            "freshness": {"as_of": "2026-03-28T12:00:00", "source": "platform", "staleness_seconds": 100},
        }
        for i, t in enumerate(all_tickers)
    ])

    platform.bulk_price_data = AsyncMock(return_value=[
        {
            "ticker": t,
            "realized_vol_1y": 0.25 + i * 0.01,
            "beta": 1.0 + i * 0.02,
            "momentum_3m": 0.05,
            "momentum_12m": 0.10,
            "prices": [{"date": "2026-03-27", "close": 150.0, "volume": 1_000_000}],
            "freshness": {"as_of": "2026-03-28T12:00:00", "source": "platform", "staleness_seconds": 100},
        }
        for i, t in enumerate(all_tickers)
    ])

    return platform


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Mock Redis for events and cache."""
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value=b"1234567890-0")
    redis.get = AsyncMock(return_value=None)  # Cache miss by default
    redis.set = AsyncMock()
    return redis


@pytest.fixture
def mock_access_scope() -> MagicMock:
    scope = MagicMock()
    scope.fingerprint.return_value = "test_scope"
    return scope


@pytest.fixture
def mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.portfolio_freshness_warn_s = 86400
    settings.portfolio_theme_cache_ttl_s = 21600
    settings.copilot_model = "anthropic:claude-sonnet-4-6"
    settings.batch_model = "anthropic:claude-haiku-4-5"
    return settings


# ---------------------------------------------------------------------------
# Tests: Full pipeline — happy path (APPROVED on first iteration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_happy_path_approved_first_iteration(
    mock_redis: AsyncMock,
    mock_access_scope: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Pipeline completes with APPROVED on first critic iteration."""
    platform = _build_mock_platform()

    pipeline = PortfolioConstructionPipeline(
        platform=platform,
        redis=mock_redis,
        access_scope=mock_access_scope,
        settings=mock_settings,
    )

    request = ConstructPortfolioRequest(message="Build me an AI portfolio")

    with (
        patch.object(pipeline, "_parse_intent", return_value=CANNED_INTENT),
        patch.object(pipeline, "_score_themes", return_value={t.ticker: t for t in CANNED_THEME_SCORES}),
        patch.object(pipeline, "_generate_rationale", return_value=CANNED_RATIONALE),
        patch.object(pipeline, "_run_critic", return_value=CANNED_CRITIC_APPROVED),
    ):
        result = await pipeline.run(request=request, job_id="job_happy")

    assert isinstance(result, ConstructPortfolioResponse)
    assert result.parsed_intent is not None
    assert len(result.proposed_holdings) > 0
    assert result.rationale is not None


@pytest.mark.asyncio
async def test_pipeline_events_emitted_in_order(
    mock_redis: AsyncMock,
    mock_access_scope: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Progress events are emitted in the correct order."""
    platform = _build_mock_platform()

    pipeline = PortfolioConstructionPipeline(
        platform=platform,
        redis=mock_redis,
        access_scope=mock_access_scope,
        settings=mock_settings,
    )

    request = ConstructPortfolioRequest(message="Build me an AI portfolio")

    with (
        patch.object(pipeline, "_parse_intent", return_value=CANNED_INTENT),
        patch.object(pipeline, "_score_themes", return_value={t.ticker: t for t in CANNED_THEME_SCORES}),
        patch.object(pipeline, "_generate_rationale", return_value=CANNED_RATIONALE),
        patch.object(pipeline, "_run_critic", return_value=CANNED_CRITIC_APPROVED),
    ):
        await pipeline.run(request=request, job_id="job_events")

    # Verify XADD was called for progress events
    assert mock_redis.xadd.await_count >= 1

    # Collect emitted event types in order
    event_types = []
    for call in mock_redis.xadd.call_args_list:
        fields = call[0][1] if len(call[0]) > 1 else {}
        et = fields.get("event_type", "")
        if et:
            event_types.append(et)

    # Expected sequence includes at minimum these events
    expected_events = [
        "intent_parsed",
        "data_loaded",
        "recall_pool_built",
        "theme_scoring_started",
        "theme_scoring_completed",
    ]
    for expected in expected_events:
        assert expected in event_types, f"Missing expected event: {expected}"


@pytest.mark.asyncio
async def test_pipeline_response_weights_sum_to_one(
    mock_redis: AsyncMock,
    mock_access_scope: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Proposed holdings weights sum to approximately 1.0."""
    platform = _build_mock_platform()

    pipeline = PortfolioConstructionPipeline(
        platform=platform,
        redis=mock_redis,
        access_scope=mock_access_scope,
        settings=mock_settings,
    )

    request = ConstructPortfolioRequest(message="Build me an AI portfolio")

    with (
        patch.object(pipeline, "_parse_intent", return_value=CANNED_INTENT),
        patch.object(pipeline, "_score_themes", return_value={t.ticker: t for t in CANNED_THEME_SCORES}),
        patch.object(pipeline, "_generate_rationale", return_value=CANNED_RATIONALE),
        patch.object(pipeline, "_run_critic", return_value=CANNED_CRITIC_APPROVED),
    ):
        result = await pipeline.run(request=request, job_id="job_weights")

    if result.proposed_holdings:
        total_weight = sum(h.weight for h in result.proposed_holdings)
        assert abs(total_weight - 1.0) < 0.02, f"Weights sum to {total_weight}, expected ~1.0"


# ---------------------------------------------------------------------------
# Tests: Review loop — NEEDS_REVISION then APPROVED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_review_loop_revision_then_approved(
    mock_redis: AsyncMock,
    mock_access_scope: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Pipeline handles NEEDS_REVISION then APPROVED in the review loop."""
    platform = _build_mock_platform()

    pipeline = PortfolioConstructionPipeline(
        platform=platform,
        redis=mock_redis,
        access_scope=mock_access_scope,
        settings=mock_settings,
    )

    request = ConstructPortfolioRequest(message="Build me an AI portfolio")

    critic_call_count = 0

    async def mock_critic(*args, **kwargs):
        nonlocal critic_call_count
        critic_call_count += 1
        if critic_call_count <= 2:
            return CANNED_CRITIC_REVISION
        return CANNED_CRITIC_APPROVED

    with (
        patch.object(pipeline, "_parse_intent", return_value=CANNED_INTENT),
        patch.object(pipeline, "_score_themes", return_value={t.ticker: t for t in CANNED_THEME_SCORES}),
        patch.object(pipeline, "_generate_rationale", return_value=CANNED_RATIONALE),
        patch.object(pipeline, "_run_critic", side_effect=mock_critic),
    ):
        result = await pipeline.run(request=request, job_id="job_revision")

    assert isinstance(result, ConstructPortfolioResponse)
    assert critic_call_count == 3  # 2 revisions + 1 approval


# ---------------------------------------------------------------------------
# Tests: Review loop — best-effort after 3 failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_best_effort_after_max_iterations(
    mock_redis: AsyncMock,
    mock_access_scope: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Pipeline uses best-effort result after 3 iterations of NEEDS_REVISION."""
    platform = _build_mock_platform()

    pipeline = PortfolioConstructionPipeline(
        platform=platform,
        redis=mock_redis,
        access_scope=mock_access_scope,
        settings=mock_settings,
    )

    request = ConstructPortfolioRequest(message="Build me an AI portfolio")

    with (
        patch.object(pipeline, "_parse_intent", return_value=CANNED_INTENT),
        patch.object(pipeline, "_score_themes", return_value={t.ticker: t for t in CANNED_THEME_SCORES}),
        patch.object(pipeline, "_generate_rationale", return_value=CANNED_RATIONALE),
        patch.object(pipeline, "_run_critic", return_value=CANNED_CRITIC_REVISION),
    ):
        result = await pipeline.run(request=request, job_id="job_best_effort")

    assert isinstance(result, ConstructPortfolioResponse)
    # Should include a manager warning
    has_warning = any("warning" in w.lower() or "best" in w.lower() or "revision" in w.lower() for w in result.warnings)
    assert has_warning or len(result.warnings) > 0


# ---------------------------------------------------------------------------
# Tests: Theme score reuse across iterations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_scores_reused_across_iterations(
    mock_redis: AsyncMock,
    mock_access_scope: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Theme scorer is called once and reused across review iterations."""
    platform = _build_mock_platform()

    pipeline = PortfolioConstructionPipeline(
        platform=platform,
        redis=mock_redis,
        access_scope=mock_access_scope,
        settings=mock_settings,
    )

    request = ConstructPortfolioRequest(message="Build me an AI portfolio")

    theme_scorer_call_count = 0

    async def mock_theme_scorer(*args, **kwargs):
        nonlocal theme_scorer_call_count
        theme_scorer_call_count += 1
        return {t.ticker: t for t in CANNED_THEME_SCORES}

    critic_call_count = 0

    async def mock_critic(*args, **kwargs):
        nonlocal critic_call_count
        critic_call_count += 1
        if critic_call_count <= 2:
            return CANNED_CRITIC_REVISION
        return CANNED_CRITIC_APPROVED

    with (
        patch.object(pipeline, "_parse_intent", return_value=CANNED_INTENT),
        patch.object(pipeline, "_score_themes", side_effect=mock_theme_scorer),
        patch.object(pipeline, "_generate_rationale", return_value=CANNED_RATIONALE),
        patch.object(pipeline, "_run_critic", side_effect=mock_critic),
    ):
        await pipeline.run(request=request, job_id="job_reuse")

    # Theme scorer should be called only once (scores reused)
    assert theme_scorer_call_count == 1


# ---------------------------------------------------------------------------
# Tests: Account refresh mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_account_refresh_mode(
    mock_redis: AsyncMock,
    mock_access_scope: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Account mode populates account-aware fields."""
    platform = _build_mock_platform()

    pipeline = PortfolioConstructionPipeline(
        platform=platform,
        redis=mock_redis,
        access_scope=mock_access_scope,
        settings=mock_settings,
    )

    request = ConstructPortfolioRequest(
        message="Refresh my AI portfolio",
        account_id="acc_001",
    )

    with (
        patch.object(pipeline, "_parse_intent", return_value=CANNED_INTENT),
        patch.object(pipeline, "_score_themes", return_value={t.ticker: t for t in CANNED_THEME_SCORES}),
        patch.object(pipeline, "_generate_rationale", return_value=CANNED_RATIONALE),
        patch.object(pipeline, "_run_critic", return_value=CANNED_CRITIC_APPROVED),
    ):
        result = await pipeline.run(request=request, job_id="job_account")

    assert isinstance(result, ConstructPortfolioResponse)
    # Account-aware metadata should be present
    assert result.metadata is not None


@pytest.mark.asyncio
async def test_pipeline_idea_mode_no_account(
    mock_redis: AsyncMock,
    mock_access_scope: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Idea mode (no account_id) skips account-aware computation."""
    platform = _build_mock_platform()

    pipeline = PortfolioConstructionPipeline(
        platform=platform,
        redis=mock_redis,
        access_scope=mock_access_scope,
        settings=mock_settings,
    )

    request = ConstructPortfolioRequest(
        message="Build me a clean energy portfolio",
    )

    with (
        patch.object(pipeline, "_parse_intent", return_value=CANNED_INTENT),
        patch.object(pipeline, "_score_themes", return_value={t.ticker: t for t in CANNED_THEME_SCORES}),
        patch.object(pipeline, "_generate_rationale", return_value=CANNED_RATIONALE),
        patch.object(pipeline, "_run_critic", return_value=CANNED_CRITIC_APPROVED),
    ):
        result = await pipeline.run(request=request, job_id="job_idea")

    assert isinstance(result, ConstructPortfolioResponse)
    # No account-specific fields should be populated
    account_fields = result.metadata.get("account_context") if result.metadata else None
    assert account_fields is None or account_fields == {}


# ---------------------------------------------------------------------------
# Tests: Response completeness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_response_has_all_fields(
    mock_redis: AsyncMock,
    mock_access_scope: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Response contains all expected fields."""
    platform = _build_mock_platform()

    pipeline = PortfolioConstructionPipeline(
        platform=platform,
        redis=mock_redis,
        access_scope=mock_access_scope,
        settings=mock_settings,
    )

    request = ConstructPortfolioRequest(message="AI stocks")

    with (
        patch.object(pipeline, "_parse_intent", return_value=CANNED_INTENT),
        patch.object(pipeline, "_score_themes", return_value={t.ticker: t for t in CANNED_THEME_SCORES}),
        patch.object(pipeline, "_generate_rationale", return_value=CANNED_RATIONALE),
        patch.object(pipeline, "_run_critic", return_value=CANNED_CRITIC_APPROVED),
    ):
        result = await pipeline.run(request=request, job_id="job_complete")

    assert result.parsed_intent is not None
    assert isinstance(result.proposed_holdings, list)
    assert isinstance(result.score_breakdowns, list)
    assert result.rationale is not None
    assert isinstance(result.warnings, list)
    assert isinstance(result.relaxations, list)
    assert isinstance(result.metadata, dict)


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_platform_error_propagates(
    mock_redis: AsyncMock,
    mock_access_scope: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Platform errors during data loading propagate."""
    platform = MagicMock()
    platform.get_security_universe = AsyncMock(side_effect=Exception("Platform down"))

    pipeline = PortfolioConstructionPipeline(
        platform=platform,
        redis=mock_redis,
        access_scope=mock_access_scope,
        settings=mock_settings,
    )

    request = ConstructPortfolioRequest(message="AI stocks")

    with (
        patch.object(pipeline, "_parse_intent", return_value=CANNED_INTENT),
        pytest.raises(Exception, match="Platform down"),
    ):
        await pipeline.run(request=request, job_id="job_error")
