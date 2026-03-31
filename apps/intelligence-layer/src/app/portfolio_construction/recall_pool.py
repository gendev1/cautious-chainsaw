"""Recall pool builder: two-stage candidate selection for theme scoring."""
from __future__ import annotations

import re
from typing import Any

from app.portfolio_construction.config import RECALL_POOL_PARAMS
from app.portfolio_construction.models import ParsedIntent

# ---------------------------------------------------------------------------
# Theme → keyword expansion map
#
# Investment themes are abstract concepts ("AI infrastructure"). Stock metadata
# uses concrete industry labels ("Semiconductors", "Systems Software"). This
# map bridges the gap so the recall pool doesn't miss obvious candidates.
# ---------------------------------------------------------------------------

THEME_KEYWORD_EXPANSIONS: dict[str, list[str]] = {
    # AI / machine learning
    "ai": ["artificial intelligence", "machine learning", "deep learning", "neural", "gpu", "accelerator", "semiconductor", "software", "cloud", "data"],
    "artificial intelligence": ["ai", "machine learning", "gpu", "semiconductor", "software", "cloud", "data", "neural"],
    "machine learning": ["ai", "artificial intelligence", "gpu", "data", "software", "cloud"],
    "ai infrastructure": ["semiconductor", "gpu", "data center", "cloud", "networking", "chip", "accelerator", "server", "hardware", "software", "systems"],
    # Semiconductor
    "semiconductor": ["chip", "fabrication", "foundry", "integrated circuit", "gpu", "cpu", "processor", "memory", "silicon", "equipment", "lithography"],
    "chip": ["semiconductor", "processor", "gpu", "cpu", "fabrication", "silicon"],
    # Cloud / data center
    "cloud": ["cloud computing", "saas", "iaas", "paas", "data center", "hyperscaler", "infrastructure", "software"],
    "cloud computing": ["cloud", "saas", "iaas", "data center", "software", "infrastructure"],
    "data center": ["cloud", "colocation", "reit", "server", "networking", "storage", "infrastructure"],
    # Software
    "software": ["application", "systems", "saas", "platform", "enterprise"],
    "enterprise": ["software", "saas", "crm", "erp", "platform", "systems"],
    # Networking / communications
    "networking": ["communications", "equipment", "infrastructure", "5g", "wireless", "broadband"],
    # Energy (for anti-goal matching)
    "energy": ["oil", "gas", "petroleum", "fossil", "drilling", "exploration", "refining", "pipeline"],
    "clean energy": ["solar", "wind", "renewable", "battery", "ev", "electric vehicle", "hydrogen"],
    # Finance
    "fintech": ["financial", "payment", "banking", "insurance", "trading"],
    # Healthcare
    "biotech": ["biotechnology", "pharmaceutical", "drug", "therapy", "genomic"],
    "healthcare": ["medical", "health", "hospital", "pharmaceutical", "device", "diagnostic"],
    # General
    "dividend": ["yield", "income", "payout", "distribution"],
    "value": ["undervalued", "cheap", "low pe", "low price"],
    "growth": ["high growth", "revenue growth", "expanding", "scaling"],
}

# Industry → related concept keywords (GICS industry labels → searchable terms)
INDUSTRY_KEYWORDS: dict[str, list[str]] = {
    "semiconductors": ["ai", "chip", "semiconductor", "gpu", "processor", "silicon", "fabrication"],
    "semiconductor equipment": ["ai", "chip", "semiconductor", "lithography", "fabrication", "equipment"],
    "systems software": ["ai", "cloud", "software", "platform", "operating system", "infrastructure"],
    "application software": ["ai", "saas", "cloud", "software", "enterprise", "platform"],
    "technology hardware": ["hardware", "server", "device", "computing", "infrastructure"],
    "interactive media": ["ai", "internet", "digital", "media", "advertising", "search"],
    "internet services": ["ai", "cloud", "internet", "digital", "platform", "search"],
    "broadline retail": ["e-commerce", "cloud", "retail", "logistics"],
    "it consulting": ["ai", "consulting", "digital", "enterprise", "services"],
    "data processing": ["ai", "data", "analytics", "cloud", "processing"],
    "communications equipment": ["networking", "5g", "infrastructure", "wireless"],
    "electric utilities": ["energy", "utility", "power", "grid"],
    "oil & gas": ["energy", "fossil", "petroleum", "drilling"],
    "integrated oil & gas": ["energy", "fossil", "petroleum", "oil", "gas"],
    "tobacco": ["tobacco", "cigarette", "nicotine"],
}


def _tokenize(text: str) -> set[str]:
    """Split text into lowercase tokens, removing short noise words."""
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) >= 2}


def _expand_themes(themes: list[str]) -> set[str]:
    """Expand theme list into a broad keyword set for recall matching."""
    keywords: set[str] = set()
    for theme in themes:
        theme_lower = theme.lower().strip()
        # Add the theme itself and its individual words
        keywords.add(theme_lower)
        keywords.update(w for w in theme_lower.split() if len(w) >= 2)
        # Add expanded keywords
        for key, expansions in THEME_KEYWORD_EXPANSIONS.items():
            if key in theme_lower or theme_lower in key:
                keywords.update(expansions)
            # Also check if any word in the theme matches a key
            for word in theme_lower.split():
                if word in key or key in word:
                    keywords.update(expansions)
    return keywords


def _score_metadata_match(
    security: dict,
    theme_keywords: set[str],
    anti_goal_keywords: set[str],
) -> float:
    """Score how well a security's metadata matches the theme keywords.

    Returns a match score >= 0. Higher = better match.
    Returns -1 if anti-goal match detected.
    """
    name = (security.get("name", "") or "").lower()
    sector = (security.get("sector", "") or "").lower()
    industry = (security.get("industry", "") or "").lower()
    description = (security.get("description", "") or "").lower()
    tags = " ".join(security.get("tags", []) or []).lower()

    # Check anti-goal match first
    searchable_text = f"{name} {sector} {industry} {description} {tags}"
    searchable_tokens = _tokenize(searchable_text)
    if anti_goal_keywords & searchable_tokens:
        return -1.0

    # Build enriched token set including industry-derived keywords
    enriched_tokens = set(searchable_tokens)
    for ind_key, ind_keywords in INDUSTRY_KEYWORDS.items():
        if ind_key in industry:
            enriched_tokens.update(ind_keywords)

    # Score: count matching theme keywords
    matches = theme_keywords & enriched_tokens
    if not matches:
        # Try substring matching for multi-word themes
        for kw in theme_keywords:
            if len(kw) >= 4 and kw in searchable_text:
                matches.add(kw)

    return len(matches)


def build_recall_pool(
    intent: ParsedIntent,
    factor_scores: dict[str, dict],
    securities: list[dict],
    fundamentals: dict[str, Any] | None = None,
) -> list[str]:
    """
    Build the recall pool of tickers for theme scoring.

    Two-stage selection:
    1. Top N_factor tickers by factor score descending
    2. Metadata keyword matches with theme expansion against securities
    Plus explicit include_tickers from intent.
    Minus explicit excluded_tickers and anti-goal matches from intent.
    Capped at 250.
    """
    N_factor = RECALL_POOL_PARAMS["N_factor"]
    N_metadata = RECALL_POOL_PARAMS["N_metadata"]
    cap = RECALL_POOL_PARAMS["cap"]

    excluded = set(intent.intent_constraints.excluded_tickers)
    excluded_sectors_lower = {s.lower() for s in intent.intent_constraints.excluded_sectors}
    includes = set(intent.intent_constraints.include_tickers) - excluded

    # Expand themes and anti-goals into broad keyword sets
    theme_keywords = _expand_themes(intent.themes)
    anti_goal_keywords = _expand_themes(intent.anti_goals)

    # Stage 1: Top N_factor by factor score (excluding anti-goal sectors)
    scored = []
    for ticker, data in factor_scores.items():
        scored.append((ticker, data.get("overall_score", 0.0)))
    scored.sort(key=lambda x: x[1], reverse=True)
    factor_top = [t for t, _ in scored[:N_factor]]

    # Stage 2: Metadata keyword matching with expansion
    metadata_matches: list[tuple[str, float, float]] = []  # (ticker, match_score, factor_score)

    for sec in securities:
        ticker = sec.get("ticker", "")
        if ticker in excluded:
            continue
        sector_lower = (sec.get("sector", "") or "").lower()
        if sector_lower in excluded_sectors_lower:
            continue

        match_score = _score_metadata_match(sec, theme_keywords, anti_goal_keywords)
        if match_score > 0:
            fscore = factor_scores.get(ticker, {}).get("overall_score", 0.0)
            metadata_matches.append((ticker, match_score, fscore))

    # Sort by match score first (relevance), then by factor score (quality)
    metadata_matches.sort(key=lambda x: (-x[1], -x[2]))
    metadata_tickers = [t for t, _, _ in metadata_matches[:N_metadata]]

    # Combine: factor_top + metadata + includes, deduplicate
    pool_set: set[str] = set()
    pool_ordered: list[str] = []

    # Build a ticker → security lookup for fast access
    sec_by_ticker = {s.get("ticker", ""): s for s in securities}

    # Add factor top tickers (excluding anti-goal matches and excluded sectors)
    for t in factor_top:
        if t in excluded:
            continue
        sec_data = sec_by_ticker.get(t)
        if sec_data:
            sec_sector = (sec_data.get("sector", "") or "").lower()
            if sec_sector in excluded_sectors_lower:
                continue
            # Also check anti-goal keyword match on metadata (catches tobacco, etc.)
            if _score_metadata_match(sec_data, theme_keywords, anti_goal_keywords) < 0:
                continue
        if t not in pool_set:
            pool_set.add(t)
            pool_ordered.append(t)

    # Add metadata matches
    for t in metadata_tickers:
        if t not in pool_set:
            pool_set.add(t)
            pool_ordered.append(t)

    # Add explicit includes
    for t in sorted(includes):
        if t not in pool_set:
            pool_set.add(t)
            pool_ordered.append(t)

    # Cap at 250 — trim lowest-factor-score entries from the end
    if len(pool_ordered) > cap:
        safe = includes.copy()
        scored_pool = []
        for t in pool_ordered:
            fscore = factor_scores.get(t, {}).get("overall_score", 0.0)
            scored_pool.append((t, fscore, t in safe))
        scored_pool.sort(key=lambda x: (not x[2], -x[1]))
        pool_ordered = [t for t, _, _ in scored_pool[:cap]]

    return pool_ordered
