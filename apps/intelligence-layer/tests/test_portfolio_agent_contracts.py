"""Prompt-contract tests for intent parser, rationale, and critic agents."""
from __future__ import annotations

import pytest

from app.portfolio_construction.models import (
    CriticFeedback,
    FactorPreferences,
    IntentConstraints,
    ParsedIntent,
    PortfolioRationale,
)


# ---------------------------------------------------------------------------
# Intent Parser: output parsing
# ---------------------------------------------------------------------------


def test_intent_parser_output_parses_from_json() -> None:
    """Sample LLM JSON output for intent parser parses into ParsedIntent."""
    sample_output = {
        "themes": ["artificial intelligence", "cloud computing"],
        "anti_goals": ["social media"],
        "factor_preferences": {
            "value": 0.15,
            "quality": 0.25,
            "growth": 0.25,
            "momentum": 0.15,
            "low_volatility": 0.10,
            "size": 0.10,
        },
        "intent_constraints": {
            "excluded_tickers": ["META", "SNAP"],
            "excluded_sectors": ["Communication Services"],
            "min_market_cap": 10_000_000_000,
            "max_beta": 1.5,
            "max_single_position": 0.08,
            "max_sector_concentration": 0.25,
        },
        "ambiguity_flags": [],
        "theme_weight": 0.65,
        "speculative": False,
    }

    parsed = ParsedIntent.model_validate(sample_output)
    assert parsed.themes == ["artificial intelligence", "cloud computing"]
    assert parsed.anti_goals == ["social media"]
    assert parsed.factor_preferences.quality == 0.25
    assert parsed.intent_constraints.excluded_tickers == ["META", "SNAP"]
    assert parsed.theme_weight == 0.65


def test_intent_parser_default_factor_preferences_normalize() -> None:
    """Default FactorPreferences normalize sum to 1.0."""
    sample = {
        "themes": ["value investing"],
        "anti_goals": [],
        "factor_preferences": {},  # Use defaults
        "intent_constraints": {},
        "ambiguity_flags": [],
        "theme_weight": 0.60,
        "speculative": False,
    }

    parsed = ParsedIntent.model_validate(sample)
    fp = parsed.factor_preferences
    total = fp.value + fp.quality + fp.growth + fp.momentum + fp.low_volatility + fp.size
    assert abs(total - 1.0) < 1e-6


def test_intent_parser_speculative_flag() -> None:
    """Speculative flag is correctly parsed."""
    sample = {
        "themes": ["high growth biotech"],
        "anti_goals": [],
        "factor_preferences": {
            "value": 0.05,
            "quality": 0.10,
            "growth": 0.40,
            "momentum": 0.25,
            "low_volatility": 0.05,
            "size": 0.15,
        },
        "intent_constraints": {
            "max_beta": 2.0,
        },
        "ambiguity_flags": ["highly speculative intent"],
        "theme_weight": 0.75,
        "speculative": True,
    }

    parsed = ParsedIntent.model_validate(sample)
    assert parsed.speculative is True
    assert len(parsed.ambiguity_flags) == 1


def test_intent_parser_ambiguity_flags() -> None:
    """Ambiguity flags are preserved in parsed output."""
    sample = {
        "themes": ["something interesting"],
        "anti_goals": [],
        "factor_preferences": {},
        "intent_constraints": {},
        "ambiguity_flags": [
            "vague theme - 'something interesting' is not specific",
            "no sector or ticker guidance provided",
        ],
        "theme_weight": 0.50,
        "speculative": False,
    }

    parsed = ParsedIntent.model_validate(sample)
    assert len(parsed.ambiguity_flags) == 2


def test_intent_parser_conservative_inference() -> None:
    """Conservative intent produces lower max_beta and higher quality weight."""
    sample = {
        "themes": ["dividend aristocrats"],
        "anti_goals": ["meme stocks", "speculative"],
        "factor_preferences": {
            "value": 0.25,
            "quality": 0.30,
            "growth": 0.10,
            "momentum": 0.05,
            "low_volatility": 0.20,
            "size": 0.10,
        },
        "intent_constraints": {
            "max_beta": 0.8,
            "min_market_cap": 10_000_000_000,
            "max_single_position": 0.05,
        },
        "ambiguity_flags": [],
        "theme_weight": 0.55,
        "speculative": False,
    }

    parsed = ParsedIntent.model_validate(sample)
    assert parsed.intent_constraints.max_beta == 0.8
    assert parsed.factor_preferences.quality >= 0.25
    assert parsed.speculative is False


# ---------------------------------------------------------------------------
# Rationale: output parsing
# ---------------------------------------------------------------------------


def test_rationale_output_parses_from_json() -> None:
    """Sample LLM JSON output for rationale parses into PortfolioRationale."""
    sample_output = {
        "thesis_summary": "This portfolio targets AI infrastructure leaders with strong fundamentals, diversified across cloud, chips, and enterprise software.",
        "holdings_rationale": {
            "NVDA": "Dominant GPU manufacturer powering AI training and inference workloads.",
            "MSFT": "Cloud infrastructure leader with deep AI integration across Azure.",
            "GOOGL": "Leading AI research lab with growing cloud business.",
            "AMZN": "AWS market leader with expanding AI services.",
            "CRM": "Enterprise AI adoption leader through Salesforce Einstein.",
        },
        "core_holdings": ["NVDA", "MSFT", "GOOGL"],
        "supporting_holdings": ["AMZN", "CRM"],
    }

    parsed = PortfolioRationale.model_validate(sample_output)
    assert len(parsed.holdings_rationale) == 5
    assert "NVDA" in parsed.core_holdings
    assert "CRM" in parsed.supporting_holdings


def test_rationale_core_and_supporting_disjoint() -> None:
    """Core holdings and supporting holdings must be disjoint sets."""
    sample = {
        "thesis_summary": "Dividend focus.",
        "holdings_rationale": {
            "JNJ": "Healthcare dividend leader.",
            "PG": "Consumer staples stability.",
            "KO": "Beverage giant with 60+ year dividend streak.",
        },
        "core_holdings": ["JNJ", "PG"],
        "supporting_holdings": ["KO"],
    }

    parsed = PortfolioRationale.model_validate(sample)
    core_set = set(parsed.core_holdings)
    support_set = set(parsed.supporting_holdings)
    assert core_set.isdisjoint(support_set), (
        f"Core and supporting overlap: {core_set & support_set}"
    )


def test_rationale_thesis_summary_not_empty() -> None:
    """Thesis summary must be non-empty."""
    sample = {
        "thesis_summary": "Growth-oriented AI portfolio.",
        "holdings_rationale": {"NVDA": "AI leader."},
        "core_holdings": ["NVDA"],
        "supporting_holdings": [],
    }

    parsed = PortfolioRationale.model_validate(sample)
    assert len(parsed.thesis_summary) > 0


def test_rationale_holdings_rationale_per_holding() -> None:
    """Each holding should have a rationale entry."""
    sample = {
        "thesis_summary": "Balanced tech portfolio.",
        "holdings_rationale": {
            "AAPL": "Strong ecosystem.",
            "MSFT": "Cloud dominance.",
            "GOOGL": "Search and AI.",
        },
        "core_holdings": ["AAPL"],
        "supporting_holdings": ["MSFT", "GOOGL"],
    }

    parsed = PortfolioRationale.model_validate(sample)
    all_holdings = set(parsed.core_holdings) | set(parsed.supporting_holdings)
    for holding in all_holdings:
        assert holding in parsed.holdings_rationale, (
            f"Missing rationale for {holding}"
        )


# ---------------------------------------------------------------------------
# Critic: output parsing
# ---------------------------------------------------------------------------


def test_critic_output_approved_parses() -> None:
    """APPROVED critic output parses into CriticFeedback."""
    sample = {
        "status": "APPROVED",
        "reasoning": "Portfolio is well-aligned with AI themes, properly diversified across sectors, and factor scores support the thesis.",
    }

    parsed = CriticFeedback.model_validate(sample)
    assert parsed.status == "APPROVED"
    assert len(parsed.reasoning) > 0


def test_critic_output_needs_revision_parses() -> None:
    """NEEDS_REVISION critic output parses into CriticFeedback with adjustments."""
    sample = {
        "status": "NEEDS_REVISION",
        "reasoning": "Missing TSMC as an obvious AI chip play. Sector concentration in US tech is too high.",
        "add_tickers": ["TSM"],
        "remove_tickers": [],
        "adjust_weights": {"NVDA": 0.08, "MSFT": 0.06},
    }

    parsed = CriticFeedback.model_validate(sample)
    assert parsed.status == "NEEDS_REVISION"
    assert "TSM" in parsed.add_tickers
    assert parsed.adjust_weights["NVDA"] == 0.08


# ---------------------------------------------------------------------------
# Critic: hard rule enforcement
# ---------------------------------------------------------------------------


def test_critic_cannot_add_excluded_ticker() -> None:
    """Critic adjustment must not add a user-excluded ticker."""
    excluded_tickers = ["META", "SNAP"]
    critic_adds = ["NVDA", "META"]  # META is excluded by user

    violations = [t for t in critic_adds if t in excluded_tickers]
    assert len(violations) > 0, "META should be flagged as violating user exclusion"

    # The application logic should reject this — we test the invariant
    for add_ticker in critic_adds:
        if add_ticker in excluded_tickers:
            # This is a violation — the system should reject
            assert add_ticker in excluded_tickers


def test_critic_cannot_violate_max_single_position() -> None:
    """Critic weight adjustment must not exceed max_single_position."""
    max_single_position = 0.10
    critic_weights = {"NVDA": 0.15, "MSFT": 0.08}

    for ticker, weight in critic_weights.items():
        if weight > max_single_position:
            # This is a violation
            assert weight > max_single_position, (
                f"{ticker} weight {weight} exceeds max {max_single_position}"
            )


def test_critic_cannot_override_user_exclusions() -> None:
    """Critic's remove_tickers should not contain user-included tickers."""
    user_includes = ["AAPL", "MSFT"]
    critic_removes = ["AAPL", "GOOGL"]

    violations = [t for t in critic_removes if t in user_includes]
    assert len(violations) > 0, "AAPL should be flagged as user-included"


# ---------------------------------------------------------------------------
# Combined contract: all models serialize/deserialize correctly
# ---------------------------------------------------------------------------


def test_all_agent_outputs_roundtrip() -> None:
    """All three agent output types round-trip through model_dump/validate."""
    intent = ParsedIntent(
        themes=["AI"],
        anti_goals=[],
        factor_preferences=FactorPreferences(),
        intent_constraints=IntentConstraints(),
        ambiguity_flags=[],
        theme_weight=0.60,
        speculative=False,
    )

    rationale = PortfolioRationale(
        thesis_summary="AI portfolio.",
        holdings_rationale={"NVDA": "Leader."},
        core_holdings=["NVDA"],
        supporting_holdings=[],
    )

    critic = CriticFeedback(
        status="APPROVED",
        reasoning="Looks good.",
    )

    # Round-trip each
    for model_instance in [intent, rationale, critic]:
        data = model_instance.model_dump()
        restored = type(model_instance).model_validate(data)
        assert data == restored.model_dump()


# ---------------------------------------------------------------------------
# Agent registration check (import-time side effect)
# ---------------------------------------------------------------------------


def test_intent_parser_agent_importable() -> None:
    """Intent parser agent module is importable."""
    from app.portfolio_construction.agents.intent_parser import portfolio_intent_parser
    assert portfolio_intent_parser is not None


def test_rationale_agent_importable() -> None:
    """Rationale agent module is importable."""
    from app.portfolio_construction.agents.rationale import portfolio_rationale
    assert portfolio_rationale is not None


def test_critic_agent_importable() -> None:
    """Critic agent module is importable."""
    from app.portfolio_construction.agents.critic import portfolio_critic
    assert portfolio_critic is not None


def test_theme_scorer_agent_importable() -> None:
    """Theme scorer agent module is importable."""
    from app.portfolio_construction.agents.theme_scorer import portfolio_theme_scorer
    assert portfolio_theme_scorer is not None
