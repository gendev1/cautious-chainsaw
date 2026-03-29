"""
Beneficiary completeness audit.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.analytics.registry import (
    ModelCategory,
    ModelKind,
    ModelMetadata,
)

# Account types where missing beneficiary is critical
RETIREMENT_ACCOUNT_TYPES = {
    "traditional_ira",
    "roth_ira",
    "sep_ira",
    "simple_ira",
    "401k",
    "403b",
    "457b",
    "inherited_ira",
}

# How old a beneficiary designation can be before "stale"
STALE_THRESHOLD_DAYS = 365 * 3  # 3 years


class BeneficiaryCompletenessAudit:
    """
    Deterministic model: audit accounts for missing or
    outdated beneficiary designations.
    """

    metadata = ModelMetadata(
        name="beneficiary_audit",
        version="1.0.0",
        owner="compliance",
        category=ModelCategory.COMPLIANCE,
        kind=ModelKind.DETERMINISTIC,
        description=(
            "Scan accounts for missing or outdated "
            "beneficiary designations. Flag retirement "
            "accounts without beneficiaries as high "
            "severity."
        ),
        use_case=(
            "Ensure all accounts — especially qualified "
            "retirement accounts — have current "
            "beneficiaries."
        ),
        input_freshness_seconds=86_400,
        known_limitations=(
            "Cannot verify beneficiary identity "
            "correctness, only presence.",
            "Stale threshold is calendar-based; life "
            "events (marriage, divorce, death) are "
            "not detected.",
        ),
    )

    def score(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        inputs:
            accounts: list[dict]
                Each has: account_id, account_type,
                    account_title, client_id, client_name,
                    market_value,
                    beneficiaries: list[dict] | None
            as_of: str
        """
        as_of = date.fromisoformat(inputs["as_of"])
        accounts = inputs["accounts"]
        findings: list[dict[str, Any]] = []

        for acct in accounts:
            account_type = (
                acct["account_type"]
                .lower()
                .replace(" ", "_")
            )
            beneficiaries = (
                acct.get("beneficiaries") or []
            )
            is_retirement = (
                account_type in RETIREMENT_ACCOUNT_TYPES
            )
            market_value = float(
                acct.get("market_value", 0)
            )

            issues: list[str] = []
            severity = "ok"

            # --- Check: no beneficiaries at all ---
            if not beneficiaries:
                issues.append(
                    "No beneficiary designated"
                )
                severity = (
                    "action_needed"
                    if is_retirement
                    else "warning"
                )

            else:
                # --- Check: shares don't sum to 100% ---
                total_share = sum(
                    float(b.get("share_pct", 0))
                    for b in beneficiaries
                )
                if abs(total_share - 100.0) > 0.01:
                    issues.append(
                        f"Beneficiary shares sum to "
                        f"{total_share:.1f}%, not 100%"
                    )
                    severity = max(
                        severity,
                        "warning",
                        key=lambda s: _sev_rank(s),
                    )

                # --- Check: stale designations ---
                for ben in beneficiaries:
                    desg_date_str = ben.get(
                        "designation_date"
                    )
                    if desg_date_str:
                        desg_date = date.fromisoformat(
                            desg_date_str
                        )
                        age_days = (
                            as_of - desg_date
                        ).days
                        if age_days > STALE_THRESHOLD_DAYS:
                            name = ben.get(
                                "name", "Unknown"
                            )
                            years = age_days // 365
                            issues.append(
                                f"Beneficiary '{name}' "
                                f"designation is "
                                f"{years} years old "
                                f"— may need review"
                            )
                            severity = max(
                                severity,
                                "warning",
                                key=lambda s: _sev_rank(
                                    s
                                ),
                            )

            if issues:
                findings.append(
                    {
                        "account_id": acct[
                            "account_id"
                        ],
                        "account_type": account_type,
                        "account_title": acct.get(
                            "account_title", ""
                        ),
                        "client_id": acct.get(
                            "client_id"
                        ),
                        "client_name": acct.get(
                            "client_name", ""
                        ),
                        "market_value": market_value,
                        "is_retirement_account": (
                            is_retirement
                        ),
                        "beneficiary_count": len(
                            beneficiaries
                        ),
                        "issues": issues,
                        "severity": severity,
                    }
                )

        # Sort: action_needed first, then market_value desc
        findings.sort(
            key=lambda f: (
                _sev_rank(f["severity"]),
                -f["market_value"],
            )
        )

        action_count = sum(
            1
            for f in findings
            if f["severity"] == "action_needed"
        )
        warning_count = sum(
            1
            for f in findings
            if f["severity"] == "warning"
        )

        return {
            "as_of": as_of.isoformat(),
            "total_accounts_scanned": len(accounts),
            "findings_count": len(findings),
            "action_needed_count": action_count,
            "warning_count": warning_count,
            "severity": (
                "action_needed"
                if action_count > 0
                else "warning"
                if warning_count > 0
                else "ok"
            ),
            "findings": findings,
        }


def _sev_rank(severity: str) -> int:
    """Lower number = higher severity (for sorting)."""
    return {
        "action_needed": 0,
        "warning": 1,
        "info": 2,
        "ok": 3,
    }.get(severity, 9)
