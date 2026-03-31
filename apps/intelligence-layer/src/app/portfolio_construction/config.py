"""Portfolio construction configuration constants."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Factor definitions — six canonical factors
# ---------------------------------------------------------------------------

FACTOR_DEFINITIONS: dict[str, dict] = {
    "value": {
        "weight": 0.20,
        "sub_factors": ["pe_ratio", "pb_ratio", "free_cash_flow_yield", "dividend_yield"],
        "lower_is_better": ["pe_ratio", "pb_ratio"],
    },
    "quality": {
        "weight": 0.20,
        "sub_factors": ["roe", "roa", "gross_margin", "operating_margin", "net_margin", "current_ratio", "debt_to_equity"],
        "lower_is_better": ["debt_to_equity"],
    },
    "growth": {
        "weight": 0.20,
        "sub_factors": ["revenue_growth", "earnings_growth", "rnd_intensity"],
        "lower_is_better": [],
    },
    "momentum": {
        "weight": 0.15,
        "sub_factors": ["momentum_3m", "momentum_6m", "momentum_12m"],
        "lower_is_better": [],
    },
    "low_volatility": {
        "weight": 0.10,
        "sub_factors": ["realized_vol_1y", "beta"],
        "lower_is_better": ["realized_vol_1y", "beta"],
    },
    "size": {
        "weight": 0.15,
        "sub_factors": ["market_cap"],
        "lower_is_better": [],
    },
}

# ---------------------------------------------------------------------------
# Theme-factor priors — theme keywords to factor weight adjustments
# ---------------------------------------------------------------------------

THEME_FACTOR_PRIORS: dict[str, dict[str, float]] = {
    "value": {"value": 0.40, "quality": 0.25},
    "growth": {"growth": 0.40, "momentum": 0.20},
    "dividend": {"value": 0.30, "quality": 0.30},
    "momentum": {"momentum": 0.40, "growth": 0.20},
    "conservative": {"low_volatility": 0.30, "quality": 0.30},
    "speculative": {"growth": 0.35, "momentum": 0.30},
}

# ---------------------------------------------------------------------------
# Default composite scoring parameters
# ---------------------------------------------------------------------------

DEFAULT_COMPOSITE_PARAMS: dict[str, float] = {
    "theme_weight": 0.60,
    "factor_floor": 25.0,
    "theme_confidence_floor": 0.50,
    "interaction_bonus": 5.0,
    "min_theme_score": 30.0,
    "weak_link_gap": 35.0,
    "weak_link_penalty": 5.0,
}

# ---------------------------------------------------------------------------
# Default optimizer parameters
# ---------------------------------------------------------------------------

DEFAULT_OPTIMIZER_PARAMS: dict[str, float] = {
    "min_weight": 0.02,
    "max_weight": 0.10,
    "default_target_count": 25.0,
}

# ---------------------------------------------------------------------------
# Recall pool parameters
# ---------------------------------------------------------------------------

RECALL_POOL_PARAMS: dict[str, int] = {
    "N_factor": 150,
    "N_metadata": 100,
    "cap": 250,
}

# ---------------------------------------------------------------------------
# Theme scoring batching
# ---------------------------------------------------------------------------

THEME_SCORE_BATCH_SIZE: int = 15
THEME_SCORE_CONCURRENCY_CAP: int = 5
