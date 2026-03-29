"""
Required Minimum Distribution calculator.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.analytics.registry import (
    ModelCategory,
    ModelKind,
    ModelMetadata,
)

# -------------------------------------------------------------------
# IRS Uniform Lifetime Table (2024 revision, effective 2022+)
# Maps age -> distribution period
# -------------------------------------------------------------------

UNIFORM_LIFETIME_TABLE: dict[int, float] = {
    72: 27.4,
    73: 26.5,
    74: 25.5,
    75: 24.6,
    76: 23.7,
    77: 22.9,
    78: 22.0,
    79: 21.1,
    80: 20.2,
    81: 19.4,
    82: 18.5,
    83: 17.7,
    84: 16.8,
    85: 16.0,
    86: 15.2,
    87: 14.4,
    88: 13.7,
    89: 12.9,
    90: 12.2,
    91: 11.5,
    92: 10.8,
    93: 10.1,
    94: 9.5,
    95: 8.9,
    96: 8.4,
    97: 7.8,
    98: 7.3,
    99: 6.8,
    100: 6.4,
    101: 6.0,
    102: 5.6,
    103: 5.2,
    104: 4.9,
    105: 4.6,
    106: 4.3,
    107: 4.1,
    108: 3.9,
    109: 3.7,
    110: 3.5,
    111: 3.4,
    112: 3.3,
    113: 3.1,
    114: 3.0,
    115: 2.9,
    116: 2.8,
    117: 2.7,
    118: 2.5,
    119: 2.3,
    120: 2.0,
}

# Account types subject to RMD
RMD_ACCOUNT_TYPES = {
    "traditional_ira",
    "sep_ira",
    "simple_ira",
    "401k",
    "403b",
    "457b",
    "inherited_ira",
}


class RMDCalculator:
    """
    Deterministic model: calculate required minimum
    distributions and flag clients approaching the RMD
    start age.
    """

    metadata = ModelMetadata(
        name="rmd_calculator",
        version="1.0.0",
        owner="portfolio-analytics",
        category=ModelCategory.TAX,
        kind=ModelKind.DETERMINISTIC,
        description=(
            "Calculate RMD amounts using the IRS Uniform "
            "Lifetime Table. Flag clients approaching "
            "age 73."
        ),
        use_case=(
            "Ensure clients take timely RMDs and avoid "
            "IRS penalties."
        ),
        input_freshness_seconds=86_400,
        known_limitations=(
            "Uses Uniform Lifetime Table only; does not "
            "handle Joint Life Table for spouses more "
            "than 10 years younger.",
            "Does not track whether RMD has already been "
            "partially satisfied.",
            "Inherited IRA RMD rules (10-year rule) "
            "require separate handling.",
        ),
    )

    def score(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        inputs:
            accounts: list[dict]
                Each has: account_id, account_type,
                    prior_year_end_balance,
                    owner_date_of_birth, owner_name
            as_of: str — ISO date
        """
        as_of = date.fromisoformat(inputs["as_of"])
        accounts = inputs["accounts"]
        results: list[dict[str, Any]] = []

        for acct in accounts:
            account_type = (
                acct["account_type"]
                .lower()
                .replace(" ", "_")
            )
            if account_type not in RMD_ACCOUNT_TYPES:
                continue

            dob = date.fromisoformat(
                acct["owner_date_of_birth"]
            )
            age = self._age_at_year_end(dob, as_of.year)
            balance = float(
                acct["prior_year_end_balance"]
            )

            rmd_required = age >= 73
            approaching = 70 <= age < 73

            rmd_amount = 0.0
            distribution_period = None
            deadline = None

            if rmd_required:
                clamped_age = min(age, 120)
                distribution_period = (
                    UNIFORM_LIFETIME_TABLE.get(
                        clamped_age, 2.0
                    )
                )
                rmd_amount = balance / distribution_period

                # First RMD year: deadline is April 1
                # of following year
                first_rmd_age = 73
                first_rmd_year = dob.year + first_rmd_age
                if as_of.year == first_rmd_year:
                    deadline = (
                        f"{first_rmd_year + 1}-04-01"
                    )
                else:
                    deadline = f"{as_of.year}-12-31"

            # Severity
            if rmd_required and rmd_amount > 0:
                days_to_deadline = (
                    (
                        date.fromisoformat(deadline)
                        - as_of
                    ).days
                    if deadline
                    else 365
                )
                if days_to_deadline <= 30:
                    severity = "action_needed"
                elif days_to_deadline <= 90:
                    severity = "warning"
                else:
                    severity = "info"
            elif approaching:
                severity = "info"
            else:
                continue  # not relevant

            results.append(
                {
                    "account_id": acct["account_id"],
                    "account_type": account_type,
                    "owner_name": acct.get(
                        "owner_name", ""
                    ),
                    "owner_age": age,
                    "prior_year_end_balance": balance,
                    "rmd_required": rmd_required,
                    "approaching_rmd_age": approaching,
                    "rmd_amount": round(rmd_amount, 2),
                    "distribution_period": (
                        distribution_period
                    ),
                    "deadline": deadline,
                    "severity": severity,
                }
            )

        # Sort by severity then rmd_amount descending
        severity_order = {
            "action_needed": 0,
            "warning": 1,
            "info": 2,
        }
        results.sort(
            key=lambda r: (
                severity_order.get(r["severity"], 9),
                -r["rmd_amount"],
            )
        )

        total_rmd = sum(
            r["rmd_amount"]
            for r in results
            if r["rmd_required"]
        )
        action_needed_count = sum(
            1
            for r in results
            if r["severity"] == "action_needed"
        )

        return {
            "as_of": as_of.isoformat(),
            "accounts_evaluated": len(results),
            "total_rmd_due": round(total_rmd, 2),
            "action_needed_count": action_needed_count,
            "severity": (
                "action_needed"
                if action_needed_count > 0
                else (
                    "warning"
                    if any(
                        r["severity"] == "warning"
                        for r in results
                    )
                    else "info"
                )
            ),
            "accounts": results,
        }

    @staticmethod
    def _age_at_year_end(dob: date, year: int) -> int:
        """Age on December 31 of the given year."""
        return year - dob.year
