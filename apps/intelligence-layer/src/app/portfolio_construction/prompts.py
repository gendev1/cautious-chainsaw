"""Prompt templates for portfolio construction agents."""
from __future__ import annotations

import json
from typing import Any


def build_theme_scorer_prompt(
    themes: list[str],
    anti_goals: list[str],
    tickers: list[str],
    security_metadata: dict[str, dict[str, Any]],
) -> str:
    """Build the theme scorer prompt for a batch of tickers."""
    theme_list = ", ".join(themes) if themes else "no specific themes"
    anti_goal_list = ", ".join(anti_goals) if anti_goals else "none"

    ticker_details = []
    for ticker in tickers:
        meta = security_metadata.get(ticker, {})
        detail = f"- {ticker}: {meta.get('name', 'Unknown')} (sector: {meta.get('sector', 'Unknown')})"
        ticker_details.append(detail)
    ticker_section = "\n".join(ticker_details) if ticker_details else "No tickers provided."

    return f"""You are an investment theme alignment scorer. Score each security's alignment with the given themes.

## Themes
{theme_list}

## Anti-Goals (hard negatives — score must be 0 if matched)
{anti_goal_list}

## Securities to Score
{ticker_section}

## Instructions
- Score each ticker from 0 to 100 based on actual business exposure to the themes.
- Consider revenue mix, product reality, and real business operations — not just name association.
- For broad themes, give partial credit for tangential exposure.
- For specific themes, require clear direct exposure.
- When multiple themes apply, scores should be higher.
- Anti-goals are hard negatives: if a security matches an anti-goal, set anti_goal_hit=true and score=0.
- Set confidence between 0.0 and 1.0 based on your certainty.
- When uncertain, score conservatively toward 40-50.

## Output Format
Return a JSON array where each element has:
- ticker: string
- score: integer (0-100)
- confidence: float (0.0-1.0)
- anti_goal_hit: boolean
- reasoning: string (1-2 sentences)
"""
