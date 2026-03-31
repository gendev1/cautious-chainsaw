"""Portfolio construction pipeline orchestrator."""
from __future__ import annotations

import logging
from typing import Any

from app.portfolio_construction.composite_scorer import score_composite
from app.portfolio_construction.config import DEFAULT_COMPOSITE_PARAMS, FACTOR_DEFINITIONS
from app.portfolio_construction.data_loader import DataLoader
from app.portfolio_construction.events import ProgressEventEmitter
from app.portfolio_construction.models import (
    CompositeScoreResult,
    ConstructPortfolioRequest,
    ConstructPortfolioResponse,
    CriticFeedback,
    FactorPreferences,
    IntentConstraints,
    ParsedIntent,
    PortfolioRationale,
    ProposedHolding,
    ThemeScoreResult,
)
from app.portfolio_construction.optimizer import (
    clamp_positions,
    select_candidates,
    weight_conviction,
    weight_equal,
    weight_min_variance,
    weight_risk_parity,
)
from app.portfolio_construction.recall_pool import build_recall_pool

logger = logging.getLogger(__name__)


class PortfolioConstructionPipeline:
    """Class-based pipeline orchestrator with private stage methods."""

    MAX_REVIEW_ITERATIONS = 3

    def __init__(
        self,
        platform: Any,
        redis: Any,
        access_scope: Any,
        tracer: Any | None = None,
        settings: Any | None = None,
    ) -> None:
        self._platform = platform
        self._redis = redis
        self._scope = access_scope
        self._tracer = tracer
        self._settings = settings
        self._emitter = ProgressEventEmitter(redis)

        # DataLoader
        if settings:
            self._loader = DataLoader(platform, access_scope, settings)
        else:
            # Create minimal settings mock
            class _MinSettings:
                portfolio_freshness_warn_s = 86400
            self._loader = DataLoader(platform, access_scope, _MinSettings())

    async def run(
        self,
        request: ConstructPortfolioRequest,
        job_id: str,
    ) -> ConstructPortfolioResponse:
        """Execute the full portfolio construction pipeline."""
        warnings: list[str] = []
        relaxations: list[str] = []

        # Stage 0: Load prior portfolio context for revise mode
        prior_context = None
        if request.prior_job_id:
            prior_context = await self._load_prior_context(request.prior_job_id)
            if prior_context:
                warnings.append(
                    f"Revising portfolio from job {request.prior_job_id}."
                )
            else:
                warnings.append(
                    f"Prior job {request.prior_job_id} not found. "
                    "Building from scratch."
                )

        # Stage 1: Parse intent (with prior context if revising)
        intent = await self._parse_intent(request, prior_context=prior_context)
        await self._emitter.emit(job_id, "intent_parsed", {
            "themes": intent.themes,
            "revise_mode": request.prior_job_id is not None,
        })

        # Stage 2: Load data
        universe = await self._loader.load_universe()
        tickers = [s["ticker"] if isinstance(s, dict) else s.ticker for s in universe]

        fundamentals_list = await self._loader.load_fundamentals(tickers)
        prices_list = await self._loader.load_prices(tickers)

        warnings.extend(self._loader.warnings)

        # Convert to dicts keyed by ticker
        fundamentals = {}
        for f in fundamentals_list:
            t = f.get("ticker") if isinstance(f, dict) else getattr(f, "ticker", None)
            if t:
                fundamentals[t] = f

        prices = {}
        for p in prices_list:
            t = p.get("ticker") if isinstance(p, dict) else getattr(p, "ticker", None)
            if t:
                prices[t] = p

        await self._emitter.emit(job_id, "data_loaded", {"universe_size": len(universe)})

        # Stage 3: Factor scoring
        from app.analytics.portfolio_factor_model_v2 import PortfolioFactorModelV2
        factor_model = PortfolioFactorModelV2()
        factor_result = factor_model.score({
            "securities": universe,
            "fundamentals": fundamentals,
            "prices": prices,
            "preferences": intent.factor_preferences.model_dump(),
        })
        factor_scores = factor_result["scores"]

        # Stage 4: Build recall pool
        pool = build_recall_pool(
            intent=intent,
            factor_scores=factor_scores,
            securities=universe,
            fundamentals=fundamentals,
        )
        await self._emitter.emit(job_id, "recall_pool_built", {"pool_size": len(pool)})

        # Stage 5: Theme scoring
        await self._emitter.emit(job_id, "theme_scoring_started", {})
        securities_by_ticker = {
            (s["ticker"] if isinstance(s, dict) else s.ticker): s
            for s in universe
        }
        theme_scores = await self._score_themes(pool, intent, securities_metadata=securities_by_ticker)
        await self._emitter.emit(job_id, "theme_scoring_completed", {"scored_count": len(theme_scores)})

        # Stage 6: Review loop
        best_holdings: list[ProposedHolding] = []
        best_rationale = None
        best_composites: list[CompositeScoreResult] = []

        for iteration in range(self.MAX_REVIEW_ITERATIONS):
            await self._emitter.emit(job_id, "review_iteration_started", {"iteration": iteration + 1})

            # Compute composite scores
            theme_scores_dict = {t: ts.model_dump() if isinstance(ts, ThemeScoreResult) else ts for t, ts in theme_scores.items()}
            composites = score_composite(
                factor_scores=factor_scores,
                theme_scores=theme_scores_dict,
                intent=intent,
            )
            best_composites = composites

            # Select candidates
            securities_metadata = {
                (s["ticker"] if isinstance(s, dict) else s.ticker): s
                for s in universe
            }
            selected, relax_notes = select_candidates(composites, intent, securities_metadata)
            relaxations.extend(relax_notes)

            if not selected:
                # Try auto-relax (not importing to avoid circular)
                from app.portfolio_construction.optimizer import auto_relax
                selected, relax_notes = auto_relax(composites, intent, securities_metadata)
                relaxations.extend(relax_notes)

            # Weight the portfolio
            strategy = request.weighting_strategy or "equal"
            if strategy == "conviction":
                cs_dict = {c.ticker: c for c in composites}
                raw_weights = weight_conviction(selected, cs_dict)
            elif strategy == "risk_parity":
                raw_weights = weight_risk_parity(selected, prices)
            elif strategy == "min_variance":
                cs_dict = {c.ticker: c for c in composites}
                raw_weights = weight_min_variance(selected, prices, cs_dict)
            else:
                raw_weights = weight_equal(selected)

            # Clamp — ensure max_weight is feasible for the number of positions
            n_pos = len(raw_weights)
            max_wt = intent.intent_constraints.max_single_position
            if n_pos > 0 and n_pos * max_wt < 1.0:
                max_wt = 1.0 / n_pos  # relax to equal-weight ceiling
            clamped = clamp_positions(raw_weights, max_weight=max_wt)

            # Build proposed holdings
            holdings: list[ProposedHolding] = []
            cs_by_ticker = {c.ticker: c for c in composites}
            for ticker, weight in clamped.items():
                cs = cs_by_ticker.get(ticker)
                sec = securities_metadata.get(ticker, {})
                sector = sec.get("sector", "Unknown") if isinstance(sec, dict) else getattr(sec, "sector", "Unknown")
                holdings.append(ProposedHolding(
                    ticker=ticker,
                    weight=round(weight, 4),
                    composite_score=cs.composite_score if cs else 0.0,
                    factor_score=cs.factor_score if cs else 0.0,
                    theme_score=cs.theme_score if cs else 0.0,
                    sector=sector,
                    rationale_snippet=f"Selected for portfolio based on composite score.",
                ))
            best_holdings = holdings

            await self._emitter.emit(job_id, "draft_built", {"holdings_count": len(holdings)})

            # Generate rationale
            rationale = await self._generate_rationale(holdings, intent)
            best_rationale = rationale

            # Run critic
            critic_feedback = await self._run_critic(holdings, intent, rationale)
            await self._emitter.emit(job_id, "critic_verdict", {"status": critic_feedback.status})

            if critic_feedback.status == "APPROVED":
                break

            # NEEDS_REVISION: apply feedback for next iteration
            if critic_feedback.add_tickers:
                for t in critic_feedback.add_tickers:
                    if t not in intent.intent_constraints.excluded_tickers:
                        if t not in [h.ticker for h in holdings]:
                            selected.append(t)
        else:
            # Max iterations reached
            warnings.append(
                "Portfolio review loop exhausted after 3 iterations. "
                "Using best-effort result. Manual review recommended."
            )

        # Account-aware (if account_id present)
        account_context = {}
        if request.account_id:
            try:
                from app.portfolio_construction.account_aware import compute_account_context
                account_context = await compute_account_context(
                    current_holdings=[],
                    proposed_holdings=best_holdings,
                    platform=self._platform,
                    access_scope=self._scope,
                    account_id=request.account_id,
                )
            except Exception:
                logger.warning("Account-aware computation failed", exc_info=True)

        await self._emitter.emit(job_id, "job_completed", {"holdings_count": len(best_holdings)})

        metadata: dict[str, Any] = {
            "iterations": min(self.MAX_REVIEW_ITERATIONS, len(best_holdings)),
        }
        if account_context:
            metadata["account_context"] = account_context

        return ConstructPortfolioResponse(
            parsed_intent=intent,
            proposed_holdings=best_holdings,
            score_breakdowns=best_composites,
            rationale=best_rationale or PortfolioRationale(
                thesis_summary="Portfolio constructed.",
                holdings_rationale={},
                core_holdings=[],
                supporting_holdings=[],
            ),
            warnings=warnings,
            relaxations=relaxations,
            metadata=metadata,
        )

    async def _load_prior_context(self, prior_job_id: str) -> dict | None:
        """Load a previously constructed portfolio result from Redis."""
        import json as _json
        raw = await self._redis.get(f"sidecar:portfolio:result:{prior_job_id}")
        if raw is None:
            return None
        return _json.loads(raw)

    async def _parse_intent(
        self,
        request: ConstructPortfolioRequest,
        *,
        prior_context: dict | None = None,
    ) -> ParsedIntent:
        """Parse user intent via LLM agent, with revise-mode merging."""
        # Revise mode: use prior intent as base
        if prior_context and "parsed_intent" in prior_context:
            prior_intent = prior_context["parsed_intent"]
            base_themes = prior_intent.get("themes", ["general"])
            base_anti_goals = prior_intent.get("anti_goals", [])
            base_factor_prefs = prior_intent.get("factor_preferences", {})
            base_constraints = prior_intent.get("intent_constraints", {})
            base_theme_weight = prior_intent.get("theme_weight", 0.60)

            excluded = list(set(
                base_constraints.get("excluded_tickers", [])
                + request.exclude_tickers
            ))
            included = list(set(
                base_constraints.get("include_tickers", [])
                + request.include_tickers
            ))

            return ParsedIntent(
                themes=base_themes,
                anti_goals=base_anti_goals,
                factor_preferences=FactorPreferences(**base_factor_prefs) if base_factor_prefs else FactorPreferences(),
                intent_constraints=IntentConstraints(
                    excluded_tickers=excluded,
                    include_tickers=included,
                ),
                ambiguity_flags=[f"Revising prior job: {request.prior_job_id}"],
                theme_weight=base_theme_weight,
                speculative=prior_intent.get("speculative", False),
            )

        # Fresh construction: use LLM agent if available
        try:
            from app.portfolio_construction.agents.intent_parser import portfolio_intent_parser
            result = await portfolio_intent_parser.run(request.message)
            intent = result.output
            # Merge explicit request overrides
            if request.exclude_tickers:
                intent.intent_constraints.excluded_tickers = list(set(
                    intent.intent_constraints.excluded_tickers + request.exclude_tickers
                ))
            if request.include_tickers:
                intent.intent_constraints.include_tickers = list(set(
                    intent.intent_constraints.include_tickers + request.include_tickers
                ))
            if request.target_count is not None:
                intent.target_count = request.target_count
            return intent
        except Exception as exc:
            logger.warning("Intent parser agent failed, using defaults: %s", exc)
            return ParsedIntent(
                themes=["general"],
                anti_goals=[],
                factor_preferences=FactorPreferences(),
                intent_constraints=IntentConstraints(
                    excluded_tickers=request.exclude_tickers,
                    include_tickers=request.include_tickers,
                ),
                ambiguity_flags=["Intent parser unavailable — using defaults"],
                theme_weight=0.60,
                speculative=False,
            )

    async def _score_themes(
        self, pool: list[str], intent: ParsedIntent, securities_metadata: dict | None = None,
    ) -> dict[str, ThemeScoreResult]:
        """Score themes via LLM agent in batches of 20 stocks."""
        import asyncio as _asyncio
        from app.portfolio_construction.agents.theme_scorer import portfolio_theme_scorer

        BATCH_SIZE = 10
        MAX_CONCURRENCY = 5
        sem = _asyncio.Semaphore(MAX_CONCURRENCY)

        def _build_batch_prompt(batch_tickers: list[str]) -> str:
            stock_lines = []
            for ticker in batch_tickers:
                meta = (securities_metadata or {}).get(ticker, {})
                name = meta.get("name", ticker) if isinstance(meta, dict) else getattr(meta, "name", ticker)
                sector = meta.get("sector", "Unknown") if isinstance(meta, dict) else getattr(meta, "sector", "Unknown")
                industry = meta.get("industry", "") if isinstance(meta, dict) else getattr(meta, "industry", "")
                desc = meta.get("description", "") if isinstance(meta, dict) else getattr(meta, "description", "")
                stock_lines.append(f"- {ticker}: {name} | sector={sector} | industry={industry} | {desc}")
            return (
                f"Themes: {intent.themes}\n"
                f"Anti-goals: {intent.anti_goals}\n\n"
                f"Score these stocks:\n" + "\n".join(stock_lines)
            )

        async def _score_batch(batch_tickers: list[str], batch_num: int) -> list[ThemeScoreResult]:
            async with sem:
                MAX_RETRIES = 2
                for attempt in range(MAX_RETRIES + 1):
                    try:
                        result = await portfolio_theme_scorer.run(_build_batch_prompt(batch_tickers))
                        scored = result.output
                        logger.info(
                            "Theme batch %d: requested=%d returned=%d (attempt %d)",
                            batch_num, len(batch_tickers), len(scored), attempt + 1,
                        )
                        return scored
                    except Exception as exc:
                        if attempt < MAX_RETRIES:
                            logger.warning(
                                "Theme batch %d attempt %d failed (%s), retrying...",
                                batch_num, attempt + 1, exc,
                            )
                            continue
                        logger.warning(
                            "Theme batch %d failed after %d attempts: %s",
                            batch_num, MAX_RETRIES + 1, exc,
                        )
                        return [
                            ThemeScoreResult(
                                ticker=t, score=50, confidence=0.3,
                                anti_goal_hit=False,
                                reasoning=f"Batch scoring failed after retries: {exc}",
                            )
                            for t in batch_tickers
                        ]
                return []  # unreachable but satisfies type checker

        # Split into batches
        batches = [pool[i:i + BATCH_SIZE] for i in range(0, len(pool), BATCH_SIZE)]
        logger.info("Scoring %d stocks in %d batches (batch_size=%d, concurrency=%d)",
                     len(pool), len(batches), BATCH_SIZE, MAX_CONCURRENCY)

        # Run all batches concurrently (bounded by semaphore)
        batch_results = await _asyncio.gather(*[_score_batch(b, i) for i, b in enumerate(batches)])

        # Merge results
        all_results: dict[str, ThemeScoreResult] = {}
        scored_count = 0
        fallback_count = 0
        for batch_idx, batch in enumerate(batch_results):
            for ts in batch:
                all_results[ts.ticker] = ts
                if ts.confidence >= 0.5:
                    scored_count += 1
                else:
                    fallback_count += 1

        # Fill in any pool tickers that the LLM missed
        filled = 0
        for ticker in pool:
            if ticker not in all_results:
                all_results[ticker] = ThemeScoreResult(
                    ticker=ticker, score=50, confidence=0.3,
                    anti_goal_hit=False, reasoning="Not scored by LLM.",
                )
                filled += 1

        logger.info(
            "Theme scoring complete: pool=%d scored=%d fallback=%d filled=%d total=%d",
            len(pool), scored_count, fallback_count, filled, len(all_results),
        )

        return all_results

    async def _generate_rationale(
        self, holdings: list[ProposedHolding], intent: ParsedIntent
    ) -> PortfolioRationale:
        """Generate portfolio rationale via LLM agent."""
        try:
            from app.portfolio_construction.agents.rationale import portfolio_rationale

            holdings_desc = "\n".join(
                f"- {h.ticker}: weight={h.weight:.2%}, composite={h.composite_score:.1f}, sector={h.sector}"
                for h in holdings
            )
            prompt = (
                f"Themes: {intent.themes}\n"
                f"Anti-goals: {intent.anti_goals}\n\n"
                f"Holdings:\n{holdings_desc}\n\n"
                f"Generate a thesis summary and per-holding rationale."
            )

            result = await portfolio_rationale.run(prompt)
            return result.output

        except Exception as exc:
            logger.warning("Rationale agent failed, using defaults: %s", exc)
            return PortfolioRationale(
                thesis_summary="Portfolio constructed based on factor and theme analysis.",
                holdings_rationale={h.ticker: h.rationale_snippet for h in holdings},
                core_holdings=[h.ticker for h in holdings[:3]],
                supporting_holdings=[h.ticker for h in holdings[3:]],
            )

    async def _run_critic(
        self,
        holdings: list[ProposedHolding],
        intent: ParsedIntent,
        rationale: PortfolioRationale,
    ) -> CriticFeedback:
        """Run portfolio critic via LLM agent."""
        try:
            from app.portfolio_construction.agents.critic import portfolio_critic

            holdings_desc = "\n".join(
                f"- {h.ticker}: weight={h.weight:.2%}, composite={h.composite_score:.1f}, sector={h.sector}"
                for h in holdings
            )
            prompt = (
                f"Original request themes: {intent.themes}\n"
                f"Anti-goals: {intent.anti_goals}\n"
                f"Theme weight: {intent.theme_weight}\n\n"
                f"Thesis: {rationale.thesis_summary}\n\n"
                f"Holdings:\n{holdings_desc}\n\n"
                f"Review this portfolio. Approve if it meets the intent, "
                f"or suggest revisions."
            )

            result = await portfolio_critic.run(prompt)
            return result.output

        except Exception as exc:
            logger.warning("Critic agent failed, auto-approving: %s", exc)
            return CriticFeedback(
                status="APPROVED",
                reasoning=f"Critic unavailable ({exc}), auto-approved.",
            )
