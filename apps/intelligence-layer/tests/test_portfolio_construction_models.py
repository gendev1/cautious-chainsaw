"""Tests for portfolio construction domain models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.portfolio_construction.models import (
    CompositeScoreResult,
    ConstructPortfolioRequest,
    ConstructPortfolioResponse,
    CriticFeedback,
    FactorPreferences,
    FactorScoreResult,
    IntentConstraints,
    JobEvent,
    ParsedIntent,
    PortfolioConstructionAccepted,
    PortfolioRationale,
    ProposedHolding,
    ThemeScoreResult,
)


# ---------------------------------------------------------------------------
# ConstructPortfolioRequest
# ---------------------------------------------------------------------------


def test_construct_request_minimal() -> None:
    """Request requires only a message."""
    req = ConstructPortfolioRequest(message="Build me an AI portfolio")
    assert req.message == "Build me an AI portfolio"
    assert req.account_id is None
    assert req.target_count is None
    assert req.weighting_strategy is None
    assert req.include_tickers == []
    assert req.exclude_tickers == []


def test_construct_request_with_all_fields() -> None:
    """Request accepts all optional fields."""
    req = ConstructPortfolioRequest(
        message="AI portfolio",
        account_id="acc_001",
        target_count=20,
        weighting_strategy="equal",
        include_tickers=["AAPL", "MSFT"],
        exclude_tickers=["META"],
    )
    assert req.account_id == "acc_001"
    assert req.target_count == 20
    assert req.weighting_strategy == "equal"
    assert req.include_tickers == ["AAPL", "MSFT"]
    assert req.exclude_tickers == ["META"]


def test_construct_request_serialization_roundtrip() -> None:
    """Request round-trips through model_dump / model_validate."""
    req = ConstructPortfolioRequest(
        message="Clean energy stocks",
        account_id="acc_002",
        target_count=15,
        include_tickers=["ENPH"],
    )
    data = req.model_dump()
    restored = ConstructPortfolioRequest.model_validate(data)
    assert restored.message == req.message
    assert restored.account_id == req.account_id
    assert restored.include_tickers == req.include_tickers


# ---------------------------------------------------------------------------
# ParsedIntent
# ---------------------------------------------------------------------------


def test_parsed_intent_construction() -> None:
    """ParsedIntent constructs with required fields."""
    intent = ParsedIntent(
        themes=["artificial intelligence", "cloud computing"],
        anti_goals=["social media"],
        factor_preferences=FactorPreferences(),
        intent_constraints=IntentConstraints(),
        ambiguity_flags=[],
        theme_weight=0.60,
        speculative=False,
    )
    assert len(intent.themes) == 2
    assert intent.anti_goals == ["social media"]
    assert intent.speculative is False


def test_parsed_intent_serialization_roundtrip() -> None:
    """ParsedIntent round-trips through dict."""
    intent = ParsedIntent(
        themes=["AI"],
        anti_goals=[],
        factor_preferences=FactorPreferences(),
        intent_constraints=IntentConstraints(),
        ambiguity_flags=["vague theme"],
        theme_weight=0.70,
        speculative=True,
    )
    data = intent.model_dump()
    restored = ParsedIntent.model_validate(data)
    assert restored.themes == ["AI"]
    assert restored.speculative is True
    assert restored.ambiguity_flags == ["vague theme"]


# ---------------------------------------------------------------------------
# FactorPreferences
# ---------------------------------------------------------------------------


def test_factor_preferences_defaults() -> None:
    """Default weights are defined and sum to 1.0."""
    prefs = FactorPreferences()
    total = (
        prefs.value
        + prefs.quality
        + prefs.growth
        + prefs.momentum
        + prefs.low_volatility
        + prefs.size
    )
    assert abs(total - 1.0) < 1e-6


def test_factor_preferences_custom_weights() -> None:
    """Custom weights are accepted."""
    prefs = FactorPreferences(
        value=0.40,
        quality=0.30,
        growth=0.15,
        momentum=0.05,
        low_volatility=0.05,
        size=0.05,
    )
    assert prefs.value == 0.40
    total = (
        prefs.value
        + prefs.quality
        + prefs.growth
        + prefs.momentum
        + prefs.low_volatility
        + prefs.size
    )
    assert abs(total - 1.0) < 1e-6


def test_factor_preferences_normalization() -> None:
    """If weights don't sum to 1.0, they should be normalized."""
    prefs = FactorPreferences(
        value=2.0,
        quality=2.0,
        growth=2.0,
        momentum=1.5,
        low_volatility=1.0,
        size=1.5,
    )
    total = (
        prefs.value
        + prefs.quality
        + prefs.growth
        + prefs.momentum
        + prefs.low_volatility
        + prefs.size
    )
    assert abs(total - 1.0) < 1e-6, f"Weights should be normalized, got sum={total}"


def test_factor_preferences_serialization_roundtrip() -> None:
    """FactorPreferences round-trips through dict."""
    prefs = FactorPreferences(value=0.30, quality=0.25, growth=0.20, momentum=0.10, low_volatility=0.10, size=0.05)
    data = prefs.model_dump()
    restored = FactorPreferences.model_validate(data)
    assert restored.value == prefs.value


# ---------------------------------------------------------------------------
# IntentConstraints
# ---------------------------------------------------------------------------


def test_intent_constraints_defaults() -> None:
    """IntentConstraints has sensible defaults."""
    ic = IntentConstraints()
    assert ic.excluded_tickers == []
    assert ic.excluded_sectors == []
    assert ic.max_single_position == 0.10
    assert ic.max_sector_concentration == 0.30


def test_intent_constraints_optional_fields() -> None:
    """All constraint fields are optional with defaults."""
    ic = IntentConstraints(
        excluded_tickers=["META", "SNAP"],
        excluded_sectors=["Communication Services"],
        min_market_cap=10_000_000_000,
        max_market_cap=None,
        max_beta=1.2,
        max_single_position=0.08,
        max_sector_concentration=0.25,
        turnover_budget=0.50,
    )
    assert ic.excluded_tickers == ["META", "SNAP"]
    assert ic.max_beta == 1.2
    assert ic.turnover_budget == 0.50


def test_intent_constraints_serialization_roundtrip() -> None:
    """IntentConstraints round-trips through dict."""
    ic = IntentConstraints(
        excluded_tickers=["TSLA"],
        min_market_cap=5_000_000_000,
    )
    data = ic.model_dump()
    restored = IntentConstraints.model_validate(data)
    assert restored.excluded_tickers == ["TSLA"]
    assert restored.min_market_cap == 5_000_000_000


# ---------------------------------------------------------------------------
# ThemeScoreResult
# ---------------------------------------------------------------------------


def test_theme_score_result_construction() -> None:
    """ThemeScoreResult constructs with all fields."""
    ts = ThemeScoreResult(
        ticker="NVDA",
        score=85,
        confidence=0.92,
        anti_goal_hit=False,
        reasoning="NVIDIA is a leading AI chip manufacturer.",
    )
    assert ts.ticker == "NVDA"
    assert ts.score == 85
    assert ts.confidence == 0.92
    assert ts.anti_goal_hit is False


def test_theme_score_result_anti_goal() -> None:
    """Anti-goal hit produces score of 0."""
    ts = ThemeScoreResult(
        ticker="META",
        score=0,
        confidence=0.95,
        anti_goal_hit=True,
        reasoning="Social media company matches anti-goal.",
    )
    assert ts.anti_goal_hit is True
    assert ts.score == 0


def test_theme_score_result_serialization_roundtrip() -> None:
    """ThemeScoreResult round-trips through dict."""
    ts = ThemeScoreResult(
        ticker="AAPL",
        score=70,
        confidence=0.80,
        anti_goal_hit=False,
        reasoning="Apple has significant AI investments.",
    )
    data = ts.model_dump()
    restored = ThemeScoreResult.model_validate(data)
    assert restored.ticker == "AAPL"
    assert restored.score == 70


# ---------------------------------------------------------------------------
# FactorScoreResult
# ---------------------------------------------------------------------------


def test_factor_score_result_construction() -> None:
    """FactorScoreResult constructs with all fields."""
    fs = FactorScoreResult(
        ticker="AAPL",
        overall_score=72.5,
        per_factor_scores={
            "value": 55.0,
            "quality": 88.0,
            "growth": 70.0,
            "momentum": 80.0,
            "low_volatility": 65.0,
            "size": 60.0,
        },
        reliability=0.85,
        sub_factor_coverage=0.90,
    )
    assert fs.overall_score == 72.5
    assert fs.per_factor_scores["quality"] == 88.0
    assert fs.reliability == 0.85


def test_factor_score_result_serialization_roundtrip() -> None:
    """FactorScoreResult round-trips through dict."""
    fs = FactorScoreResult(
        ticker="MSFT",
        overall_score=80.0,
        per_factor_scores={"value": 60.0, "quality": 90.0},
        reliability=0.90,
        sub_factor_coverage=0.95,
    )
    data = fs.model_dump()
    restored = FactorScoreResult.model_validate(data)
    assert restored.ticker == "MSFT"
    assert restored.overall_score == 80.0


# ---------------------------------------------------------------------------
# CompositeScoreResult
# ---------------------------------------------------------------------------


def test_composite_score_result_construction() -> None:
    """CompositeScoreResult constructs with all fields."""
    cs = CompositeScoreResult(
        ticker="NVDA",
        composite_score=82.3,
        factor_score=75.0,
        theme_score=90.0,
        gated=False,
        gate_reason=None,
        coherence_bonus=5.0,
        weak_link_penalty=0.0,
    )
    assert cs.composite_score == 82.3
    assert cs.gated is False
    assert cs.coherence_bonus == 5.0


def test_composite_score_result_gated() -> None:
    """Gated composite score has score 0 and a reason."""
    cs = CompositeScoreResult(
        ticker="META",
        composite_score=0.0,
        factor_score=45.0,
        theme_score=0.0,
        gated=True,
        gate_reason="anti_goal_hit",
        coherence_bonus=0.0,
        weak_link_penalty=0.0,
    )
    assert cs.gated is True
    assert cs.gate_reason == "anti_goal_hit"


# ---------------------------------------------------------------------------
# ProposedHolding
# ---------------------------------------------------------------------------


def test_proposed_holding_construction() -> None:
    """ProposedHolding constructs with all fields."""
    h = ProposedHolding(
        ticker="NVDA",
        weight=0.08,
        composite_score=85.0,
        factor_score=78.0,
        theme_score=92.0,
        sector="Technology",
        rationale_snippet="Leading AI chip company with strong growth.",
    )
    assert h.ticker == "NVDA"
    assert h.weight == 0.08
    assert h.sector == "Technology"


def test_proposed_holding_serialization_roundtrip() -> None:
    """ProposedHolding round-trips through dict."""
    h = ProposedHolding(
        ticker="AAPL",
        weight=0.05,
        composite_score=72.0,
        factor_score=70.0,
        theme_score=75.0,
        sector="Technology",
        rationale_snippet="Strong ecosystem and AI services.",
    )
    data = h.model_dump()
    restored = ProposedHolding.model_validate(data)
    assert restored.ticker == "AAPL"
    assert restored.weight == 0.05


# ---------------------------------------------------------------------------
# CriticFeedback
# ---------------------------------------------------------------------------


def test_critic_feedback_approved() -> None:
    """APPROVED CriticFeedback has no adjustment fields."""
    cf = CriticFeedback(
        status="APPROVED",
        reasoning="Portfolio looks well-constructed and aligned with themes.",
    )
    assert cf.status == "APPROVED"


def test_critic_feedback_needs_revision() -> None:
    """NEEDS_REVISION CriticFeedback includes adjustment reasoning."""
    cf = CriticFeedback(
        status="NEEDS_REVISION",
        reasoning="Missing obvious core name NVDA for AI theme.",
        add_tickers=["NVDA"],
        remove_tickers=[],
        adjust_weights={},
    )
    assert cf.status == "NEEDS_REVISION"
    assert "NVDA" in cf.add_tickers


def test_critic_feedback_invalid_status() -> None:
    """Invalid status string raises validation error."""
    with pytest.raises(PydanticValidationError):
        CriticFeedback(
            status="INVALID_STATUS",
            reasoning="test",
        )


def test_critic_feedback_serialization_roundtrip() -> None:
    """CriticFeedback round-trips through dict."""
    cf = CriticFeedback(
        status="APPROVED",
        reasoning="Looks good.",
    )
    data = cf.model_dump()
    restored = CriticFeedback.model_validate(data)
    assert restored.status == "APPROVED"


# ---------------------------------------------------------------------------
# PortfolioRationale
# ---------------------------------------------------------------------------


def test_portfolio_rationale_construction() -> None:
    """PortfolioRationale constructs with required fields."""
    pr = PortfolioRationale(
        thesis_summary="This portfolio targets AI and cloud computing growth.",
        holdings_rationale={
            "NVDA": "Leading AI chip maker with dominant market share.",
            "MSFT": "Cloud infrastructure and AI integration leader.",
        },
        core_holdings=["NVDA", "MSFT"],
        supporting_holdings=["AMZN", "GOOGL"],
    )
    assert pr.thesis_summary.startswith("This portfolio")
    assert len(pr.holdings_rationale) == 2
    assert "NVDA" in pr.core_holdings


def test_portfolio_rationale_serialization_roundtrip() -> None:
    """PortfolioRationale round-trips through dict."""
    pr = PortfolioRationale(
        thesis_summary="Dividend-focused portfolio.",
        holdings_rationale={"JNJ": "Consistent dividend grower."},
        core_holdings=["JNJ"],
        supporting_holdings=["PG"],
    )
    data = pr.model_dump()
    restored = PortfolioRationale.model_validate(data)
    assert restored.core_holdings == ["JNJ"]


# ---------------------------------------------------------------------------
# PortfolioConstructionAccepted
# ---------------------------------------------------------------------------


def test_portfolio_construction_accepted() -> None:
    """Accepted response contains job_id."""
    accepted = PortfolioConstructionAccepted(job_id="job_abc123")
    assert accepted.job_id == "job_abc123"


# ---------------------------------------------------------------------------
# ConstructPortfolioResponse
# ---------------------------------------------------------------------------


def test_construct_response_construction() -> None:
    """Full response assembles all sub-components."""
    resp = ConstructPortfolioResponse(
        parsed_intent=ParsedIntent(
            themes=["AI"],
            anti_goals=[],
            factor_preferences=FactorPreferences(),
            intent_constraints=IntentConstraints(),
            ambiguity_flags=[],
            theme_weight=0.60,
            speculative=False,
        ),
        proposed_holdings=[
            ProposedHolding(
                ticker="NVDA",
                weight=0.10,
                composite_score=85.0,
                factor_score=80.0,
                theme_score=90.0,
                sector="Technology",
                rationale_snippet="AI chip leader.",
            ),
        ],
        score_breakdowns=[],
        rationale=PortfolioRationale(
            thesis_summary="AI focused.",
            holdings_rationale={"NVDA": "Leader."},
            core_holdings=["NVDA"],
            supporting_holdings=[],
        ),
        warnings=[],
        relaxations=[],
        metadata={},
    )
    assert len(resp.proposed_holdings) == 1
    assert resp.proposed_holdings[0].ticker == "NVDA"


def test_construct_response_serialization_roundtrip() -> None:
    """Full response round-trips through dict."""
    resp = ConstructPortfolioResponse(
        parsed_intent=ParsedIntent(
            themes=["clean energy"],
            anti_goals=[],
            factor_preferences=FactorPreferences(),
            intent_constraints=IntentConstraints(),
            ambiguity_flags=[],
            theme_weight=0.60,
            speculative=False,
        ),
        proposed_holdings=[],
        score_breakdowns=[],
        rationale=PortfolioRationale(
            thesis_summary="Clean energy.",
            holdings_rationale={},
            core_holdings=[],
            supporting_holdings=[],
        ),
        warnings=["No high-conviction candidates found."],
        relaxations=["Lowered min_theme_score from 30 to 25."],
        metadata={"iterations": 3},
    )
    data = resp.model_dump()
    restored = ConstructPortfolioResponse.model_validate(data)
    assert restored.warnings == ["No high-conviction candidates found."]
    assert restored.metadata["iterations"] == 3


# ---------------------------------------------------------------------------
# JobEvent
# ---------------------------------------------------------------------------


def test_job_event_construction() -> None:
    """JobEvent constructs with event_type and payload."""
    ev = JobEvent(
        event_type="intent_parsed",
        job_id="job_001",
        payload={"themes": ["AI"]},
    )
    assert ev.event_type == "intent_parsed"
    assert ev.job_id == "job_001"


def test_job_event_serialization_roundtrip() -> None:
    """JobEvent round-trips through dict."""
    ev = JobEvent(
        event_type="job_completed",
        job_id="job_002",
        payload={"holdings_count": 25},
    )
    data = ev.model_dump()
    restored = JobEvent.model_validate(data)
    assert restored.event_type == "job_completed"
    assert restored.payload["holdings_count"] == 25
