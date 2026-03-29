"""
app/jobs/firm_report.py — Firm-wide report generation job.

Uses a two-stage agent pipeline:
1. account_analyst (Haiku) — analyses individual accounts in parallel batches.
2. report_aggregator (Opus) — synthesises account analyses into a FirmWideReport.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from app.jobs.enqueue import JobContext
from app.jobs.observability import JobTracer
from app.jobs.retry import with_retry_policy
from app.models.access_scope import AccessScope
from app.models.schemas import FirmWideReport

logger = logging.getLogger("sidecar.jobs.firm_report")

# ---------------------------------------------------------------------------
# Local models (not in schemas.py)
# ---------------------------------------------------------------------------


class AccountAnalysis(BaseModel):
    """Per-account analysis produced by the account analyst agent."""
    account_id: str
    account_name: str
    total_value: float
    performance_ytd_pct: float | None = None
    drift_pct: float | None = None
    risk_flags: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    summary: str = ""


class FlaggedItem(BaseModel):
    """An item requiring attention in the firm report."""
    account_id: str
    account_name: str
    flag_type: str = Field(description="One of: drift, underperformance, risk, compliance")
    severity: str = Field(description="One of: high, medium, low")
    description: str


class ReportSection(BaseModel):
    """A section within the firm-wide report narrative."""
    title: str
    content: str
    flagged_items: list[FlaggedItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

ACCOUNT_BATCH_SIZE = 10

account_analyst: Agent[None, AccountAnalysis] = Agent(
    model="anthropic:claude-haiku-4-5",
    output_type=AccountAnalysis,
    defer_model_check=True,
    system_prompt=(
        "You are a quantitative account analyst for a wealth management firm. "
        "Given an account's holdings, performance, and metadata, produce a concise "
        "AccountAnalysis with risk flags, opportunities, and a one-paragraph summary. "
        "Flag drift > 5%, underperformance vs benchmark, concentrated positions > 20%, "
        "and any compliance concerns."
    ),
    retries=2,
)

report_aggregator: Agent[None, FirmWideReport] = Agent(
    model="anthropic:claude-opus-4-6",
    output_type=FirmWideReport,
    defer_model_check=True,
    system_prompt=(
        "You are a senior analyst producing firm-wide reports for wealth management. "
        "Given individual account analyses, aggregate them into a FirmWideReport with:\n"
        "- Total AUM, account counts, household counts\n"
        "- Key highlights (positive trends, wins)\n"
        "- Concerns (flagged items, risk patterns)\n"
        "- Metrics dict with aggregated performance, drift, risk data\n\n"
        "Be analytical, data-driven, and concise."
    ),
    retries=2,
)

REPORT_TTL_S = 604_800  # 7 days


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _analyse_account(
    account: Any,
    tracer: JobTracer | None = None,
) -> AccountAnalysis | None:
    """Run the account analyst agent on a single account."""
    try:
        obj = account if isinstance(account, dict) else account.model_dump()

        prompt_lines = [
            f"Analyse account {obj.get('account_id', 'unknown')}:",
            f"- Name: {obj.get('account_name', 'N/A')}",
            f"- Type: {obj.get('account_type', 'N/A')}",
            f"- Status: {obj.get('status', 'N/A')}",
            f"- Total value: ${float(obj.get('total_value', 0)):,.2f}",
            f"- Cash balance: ${float(obj.get('cash_balance', 0)):,.2f}",
            f"- YTD performance: {obj.get('performance_ytd_pct', 'N/A')}%",
            f"- Model drift: {obj.get('drift_pct', 'N/A')}%",
        ]

        holdings = obj.get("holdings", [])
        if holdings:
            prompt_lines.append(f"- Holdings ({len(holdings)}):")
            for h in holdings[:20]:
                prompt_lines.append(
                    f"  - {h.get('symbol', '?')}: {h.get('name', '?')} — "
                    f"${float(h.get('market_value', 0)):,.2f} "
                    f"({float(h.get('weight_pct', 0)):.1f}%)"
                )

        prompt = "\n".join(prompt_lines)

        gen = None
        if tracer:
            gen = tracer.start_generation(
                name=f"account_analysis_{obj.get('account_id', 'unknown')}",
                model="anthropic:claude-haiku-4-5",
            )

        result = await account_analyst.run(prompt)
        analysis = result.output

        if tracer and gen:
            tracer.end_generation(gen, output=analysis.model_dump())

        return analysis

    except Exception as exc:
        logger.warning("Failed to analyse account: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Job entry point
# ---------------------------------------------------------------------------


@with_retry_policy
async def run_firm_report(
    ctx: dict[str, Any],
    job_ctx_raw: dict | None = None,
    report_type: str = "quarterly_review",
    filters: dict | None = None,
) -> dict:
    """
    Generate a firm-wide analytical report.

    Fetches all firm accounts, analyses each in batches via the
    account_analyst agent, then aggregates via report_aggregator.
    """
    if job_ctx_raw is None:
        raise ValueError("run_firm_report requires job_ctx_raw")

    job_ctx = JobContext(**job_ctx_raw)
    access_scope = AccessScope(**job_ctx.access_scope)

    platform = ctx["platform_client"]
    redis = ctx["redis"]
    langfuse = ctx.get("langfuse")

    tracer: JobTracer | None = None
    if langfuse:
        tracer = JobTracer(
            langfuse=langfuse,
            job_name="firm_report",
            tenant_id=job_ctx.tenant_id,
            actor_id=job_ctx.actor_id,
            extra_metadata={"report_type": report_type},
        )

    try:
        # Fetch all firm accounts
        accounts = await platform.get_firm_accounts(
            filters or {}, access_scope,
        )
        if tracer:
            tracer.record_platform_read()

        if not accounts:
            logger.warning("firm_report: no accounts found for tenant %s", job_ctx.tenant_id)
            if tracer:
                tracer.complete(output={"accounts": 0})
            return {"status": "no_accounts", "tenant_id": job_ctx.tenant_id}

        logger.info(
            "firm_report: analysing %d accounts for tenant %s",
            len(accounts),
            job_ctx.tenant_id,
        )

        # Batch per-account analysis
        all_analyses: list[AccountAnalysis] = []
        for batch_start in range(0, len(accounts), ACCOUNT_BATCH_SIZE):
            batch = accounts[batch_start : batch_start + ACCOUNT_BATCH_SIZE]
            tasks = [_analyse_account(acct, tracer) for acct in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, AccountAnalysis):
                    all_analyses.append(r)
                elif isinstance(r, Exception):
                    logger.warning("firm_report: account analysis failed: %s", r)

        # Aggregate into firm report
        total_aum = sum(a.total_value for a in all_analyses)
        household_ids = set()
        for acct in accounts:
            obj = acct if isinstance(acct, dict) else acct.model_dump()
            hid = obj.get("household_id")
            if hid:
                household_ids.add(hid)

        # Build aggregation prompt
        analysis_summaries = []
        for a in all_analyses:
            analysis_summaries.append(
                f"- {a.account_name} ({a.account_id}): "
                f"${a.total_value:,.2f}, YTD {a.performance_ytd_pct or 'N/A'}%, "
                f"drift {a.drift_pct or 'N/A'}%, "
                f"flags: {', '.join(a.risk_flags) or 'none'}, "
                f"opportunities: {', '.join(a.opportunities) or 'none'}"
            )

        now = datetime.now(UTC)
        agg_prompt = "\n".join([
            f"Generate a {report_type} firm-wide report for tenant {job_ctx.tenant_id}.",
            f"Date: {now.strftime('%Y-%m-%d')}",
            f"Total accounts analysed: {len(all_analyses)}",
            f"Total AUM: ${total_aum:,.2f}",
            f"Total households: {len(household_ids)}",
            "",
            "## Account Analyses",
            *analysis_summaries,
        ])

        gen = None
        if tracer:
            gen = tracer.start_generation(
                name="report_aggregation",
                model="anthropic:claude-opus-4-6",
                input_data=f"{len(all_analyses)} accounts, ${total_aum:,.0f} AUM",
            )

        result = await report_aggregator.run(agg_prompt)
        report = result.output

        if tracer and gen:
            tracer.end_generation(gen, output=report.model_dump())

        # Store report in Redis
        date_str = now.strftime('%Y%m%d')
        report_key = f"sidecar:firm_report:{job_ctx.tenant_id}:{report_type}:{date_str}"
        await redis.set(
            report_key,
            report.model_dump_json(),
            ex=REPORT_TTL_S,
        )

        if tracer:
            tracer.complete(output={
                "tenant_id": job_ctx.tenant_id,
                "report_type": report_type,
                "accounts_analysed": len(all_analyses),
                "total_aum": total_aum,
            })

        logger.info(
            "firm_report: completed %s for tenant %s — %d accounts, $%.0f AUM, "
            "%d highlights, %d concerns",
            report_type,
            job_ctx.tenant_id,
            len(all_analyses),
            total_aum,
            len(report.highlights),
            len(report.concerns),
        )

        return {
            "status": "generated",
            "tenant_id": job_ctx.tenant_id,
            "report_type": report_type,
            "report_key": report_key,
            "accounts_analysed": len(all_analyses),
            "total_aum": total_aum,
            "highlights": len(report.highlights),
            "concerns": len(report.concerns),
        }

    except Exception as exc:
        if tracer:
            tracer.fail(exc, category="firm_report_error")
        raise
