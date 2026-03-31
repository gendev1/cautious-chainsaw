"""Portfolio construction domain models."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------


class ConstructPortfolioRequest(BaseModel):
    """Inbound request to construct a portfolio."""

    message: str
    account_id: str | None = None
    target_count: int | None = None
    weighting_strategy: str | None = None
    include_tickers: list[str] = Field(default_factory=list)
    exclude_tickers: list[str] = Field(default_factory=list)
    prior_job_id: str | None = Field(
        default=None,
        description="Job ID of a previous construction to revise. "
        "Loads the prior portfolio and intent as context for the "
        "new request, enabling conversational portfolio refinement.",
    )


class PortfolioConstructionAccepted(BaseModel):
    """Returned from POST /portfolio/construct with 202."""

    job_id: str


# ---------------------------------------------------------------------------
# Factor Preferences
# ---------------------------------------------------------------------------


class FactorPreferences(BaseModel):
    """Weight allocation across the six canonical factors. Normalized to sum=1."""

    value: float = 0.20
    quality: float = 0.20
    growth: float = 0.20
    momentum: float = 0.15
    low_volatility: float = 0.10
    size: float = 0.15

    @model_validator(mode="after")
    def _normalize(self) -> FactorPreferences:
        total = self.value + self.quality + self.growth + self.momentum + self.low_volatility + self.size
        if total > 0 and abs(total - 1.0) > 1e-9:
            self.value /= total
            self.quality /= total
            self.growth /= total
            self.momentum /= total
            self.low_volatility /= total
            self.size /= total
        return self

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# Intent Constraints
# ---------------------------------------------------------------------------


class IntentConstraints(BaseModel):
    """Hard and soft constraints parsed from user intent."""

    excluded_tickers: list[str] = Field(default_factory=list)
    excluded_sectors: list[str] = Field(default_factory=list)
    include_tickers: list[str] = Field(default_factory=list)
    min_market_cap: float | None = None
    max_market_cap: float | None = None
    max_beta: float | None = None
    max_single_position: float = 0.10
    max_sector_concentration: float = 0.30
    turnover_budget: float | None = None
    target_count: int | None = None


# ---------------------------------------------------------------------------
# Parsed Intent
# ---------------------------------------------------------------------------


class ParsedIntent(BaseModel):
    """Structured output from intent parser agent."""

    themes: list[str]
    anti_goals: list[str]
    factor_preferences: FactorPreferences
    intent_constraints: IntentConstraints
    ambiguity_flags: list[str]
    theme_weight: float
    speculative: bool
    target_count: int | None = None


# ---------------------------------------------------------------------------
# Score Results
# ---------------------------------------------------------------------------


class ThemeScoreResult(BaseModel):
    """Per-ticker theme alignment score from the LLM scorer."""

    ticker: str
    score: int  # 0-100
    confidence: float  # 0.0-1.0
    anti_goal_hit: bool
    reasoning: str


class FactorScoreResult(BaseModel):
    """Per-ticker deterministic factor score."""

    ticker: str
    overall_score: float  # 0-100
    per_factor_scores: dict[str, float]
    reliability: float
    sub_factor_coverage: float


class CompositeScoreResult(BaseModel):
    """Combined factor + theme score with gating metadata."""

    ticker: str
    composite_score: float
    factor_score: float
    theme_score: float
    gated: bool
    gate_reason: str | None = None
    coherence_bonus: float = 0.0
    weak_link_penalty: float = 0.0


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------


class ProposedHolding(BaseModel):
    """A single position in the proposed portfolio."""

    ticker: str
    weight: float
    composite_score: float
    factor_score: float
    theme_score: float
    sector: str
    rationale_snippet: str


# ---------------------------------------------------------------------------
# Critic Feedback
# ---------------------------------------------------------------------------


class CriticFeedback(BaseModel):
    """Output from the portfolio critic agent."""

    status: Literal["APPROVED", "NEEDS_REVISION"]
    reasoning: str
    add_tickers: list[str] = Field(default_factory=list)
    remove_tickers: list[str] = Field(default_factory=list)
    adjust_weights: dict[str, float] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rationale
# ---------------------------------------------------------------------------


class PortfolioRationale(BaseModel):
    """Narrative explanation of the portfolio."""

    thesis_summary: str
    holdings_rationale: dict[str, str]
    core_holdings: list[str]
    supporting_holdings: list[str]


# ---------------------------------------------------------------------------
# Job Events
# ---------------------------------------------------------------------------


class JobEvent(BaseModel):
    """A progress event emitted by the pipeline."""

    event_type: str
    job_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Full Response
# ---------------------------------------------------------------------------


class ConstructPortfolioResponse(BaseModel):
    """Complete response from the portfolio construction pipeline."""

    parsed_intent: ParsedIntent
    proposed_holdings: list[ProposedHolding]
    score_breakdowns: list[CompositeScoreResult]
    rationale: PortfolioRationale
    warnings: list[str]
    relaxations: list[str]
    metadata: dict[str, Any]
