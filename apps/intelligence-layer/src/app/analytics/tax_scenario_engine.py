"""
Tax scenario engine — what-if tax modeling.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analytics.registry import (
    ModelCategory,
    ModelKind,
    ModelMetadata,
)

# -------------------------------------------------------------------
# 2025/2026 Federal tax brackets (simplified)
# -------------------------------------------------------------------

MFJ_BRACKETS_2026: list[tuple[float, float]] = [
    (23_850, 0.10),
    (96_950, 0.12),
    (206_700, 0.22),
    (394_600, 0.24),
    (501_050, 0.32),
    (751_600, 0.35),
    (float("inf"), 0.37),
]

SINGLE_BRACKETS_2026: list[tuple[float, float]] = [
    (11_925, 0.10),
    (48_475, 0.12),
    (103_350, 0.22),
    (197_300, 0.24),
    (250_525, 0.32),
    (626_350, 0.35),
    (float("inf"), 0.37),
]

BRACKET_TABLES = {
    "mfj": MFJ_BRACKETS_2026,
    "single": SINGLE_BRACKETS_2026,
    "married_filing_jointly": MFJ_BRACKETS_2026,
    "married_filing_separately": SINGLE_BRACKETS_2026,
    "head_of_household": SINGLE_BRACKETS_2026,
}

# Long-term capital gains brackets (MFJ, 2026 est.)
LTCG_BRACKETS_MFJ: list[tuple[float, float]] = [
    (94_050, 0.00),
    (583_750, 0.15),
    (float("inf"), 0.20),
]

# Net Investment Income Tax threshold
NIIT_THRESHOLD_MFJ = 250_000
NIIT_RATE = 0.038


@dataclass
class ScenarioAction:
    """A single proposed action within a what-if scenario."""

    action_type: str  # "roth_conversion", "harvest_loss", etc.
    amount: float
    details: dict  # action-specific parameters


class TaxScenarioEngine:
    """
    Heuristic model: project federal tax liability under
    baseline and one or more proposed actions.
    """

    metadata = ModelMetadata(
        name="tax_scenario_engine",
        version="1.0.0",
        owner="tax-planning",
        category=ModelCategory.TAX,
        kind=ModelKind.HEURISTIC,
        description=(
            "What-if tax modeling: project federal tax "
            "liability delta for proposed actions such as "
            "Roth conversions, loss harvesting, charitable "
            "gifts, and gain realization."
        ),
        use_case=(
            "Compare tax outcomes of proposed planning "
            "actions against baseline."
        ),
        input_freshness_seconds=604_800,
        known_limitations=(
            "Uses simplified federal brackets; does not "
            "model AMT.",
            "State taxes are not included.",
            "NIIT is approximated; does not model all "
            "investment income components.",
            "Charitable deduction limited to simplified "
            "AGI caps.",
            "Multi-year projections assume constant "
            "tax rates.",
        ),
    )

    def score(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        inputs:
            filing_status: str
            ordinary_income: float
            lt_capital_gains: float
            st_capital_gains: float
            deductions: float
            investment_income: float
            scenarios: list[dict]
            as_of: str
        """
        filing_status = inputs.get(
            "filing_status", "mfj"
        ).lower()
        ordinary = float(inputs["ordinary_income"])
        lt_gains = float(
            inputs.get("lt_capital_gains", 0)
        )
        st_gains = float(
            inputs.get("st_capital_gains", 0)
        )
        deductions = float(
            inputs.get("deductions", 29_200)
        )
        investment_income = float(
            inputs.get(
                "investment_income", lt_gains + st_gains
            )
        )
        as_of = inputs["as_of"]

        brackets = BRACKET_TABLES.get(
            filing_status, MFJ_BRACKETS_2026
        )

        # --- Baseline tax computation ---
        baseline = self._compute_tax(
            ordinary_income=ordinary,
            st_capital_gains=st_gains,
            lt_capital_gains=lt_gains,
            deductions=deductions,
            investment_income=investment_income,
            brackets=brackets,
            filing_status=filing_status,
        )

        # --- Scenario computations ---
        scenario_results: list[dict[str, Any]] = []

        for scenario_input in inputs.get("scenarios", []):
            scenario_name = scenario_input["name"]
            actions = [
                ScenarioAction(
                    action_type=a["action_type"],
                    amount=float(a["amount"]),
                    details=a.get("details", {}),
                )
                for a in scenario_input["actions"]
            ]

            # Apply actions to adjust income components
            adj_ordinary = ordinary
            adj_st_gains = st_gains
            adj_lt_gains = lt_gains
            adj_deductions = deductions
            adj_investment_income = investment_income
            trade_offs: list[str] = []

            for action in actions:
                if action.action_type == "roth_conversion":
                    adj_ordinary += action.amount
                    trade_offs.append(
                        f"Roth conversion of "
                        f"${action.amount:,.0f} adds to "
                        f"ordinary income this year but "
                        f"provides tax-free growth and "
                        f"withdrawals in retirement."
                    )

                elif action.action_type == "harvest_loss":
                    remaining_loss = action.amount
                    # Offset ST gains first
                    offset_st = min(
                        remaining_loss,
                        max(adj_st_gains, 0),
                    )
                    adj_st_gains -= offset_st
                    remaining_loss -= offset_st
                    # Then LT gains
                    offset_lt = min(
                        remaining_loss,
                        max(adj_lt_gains, 0),
                    )
                    adj_lt_gains -= offset_lt
                    remaining_loss -= offset_lt
                    # Then up to $3,000 ordinary income
                    offset_ordinary = min(
                        remaining_loss, 3_000
                    )
                    adj_ordinary -= offset_ordinary
                    remaining_loss -= offset_ordinary
                    if remaining_loss > 0:
                        trade_offs.append(
                            f"${remaining_loss:,.0f} in "
                            f"excess losses carry forward "
                            f"to future years."
                        )
                    trade_offs.append(
                        f"Harvesting "
                        f"${action.amount:,.0f} in losses "
                        f"offsets gains and up to $3K "
                        f"ordinary income."
                    )
                    adj_investment_income -= (
                        offset_st + offset_lt
                    )

                elif (
                    action.action_type == "charitable_gift"
                ):
                    agi = (
                        adj_ordinary
                        + adj_st_gains
                        + adj_lt_gains
                    )
                    max_deduction = agi * 0.60
                    actual_deduction = min(
                        action.amount, max_deduction
                    )
                    adj_deductions += actual_deduction
                    if action.amount > max_deduction:
                        excess = (
                            action.amount - max_deduction
                        )
                        trade_offs.append(
                            f"Gift exceeds 60% AGI limit; "
                            f"${excess:,.0f} carries "
                            f"forward."
                        )
                    trade_offs.append(
                        f"Charitable gift of "
                        f"${action.amount:,.0f} adds "
                        f"${actual_deduction:,.0f} to "
                        f"deductions."
                    )

                elif action.action_type == "realize_gain":
                    term = action.details.get(
                        "term", "long"
                    )
                    if term == "short":
                        adj_st_gains += action.amount
                    else:
                        adj_lt_gains += action.amount
                    adj_investment_income += action.amount
                    trade_offs.append(
                        f"Realizing ${action.amount:,.0f} "
                        f"in {term}-term gains."
                    )

            # Compute scenario tax
            scenario_tax = self._compute_tax(
                ordinary_income=adj_ordinary,
                st_capital_gains=adj_st_gains,
                lt_capital_gains=adj_lt_gains,
                deductions=adj_deductions,
                investment_income=adj_investment_income,
                brackets=brackets,
                filing_status=filing_status,
            )

            delta = (
                scenario_tax["total_tax"]
                - baseline["total_tax"]
            )

            delta_pct = (
                round(
                    delta / baseline["total_tax"] * 100,
                    2,
                )
                if baseline["total_tax"] > 0
                else 0.0
            )

            scenario_results.append(
                {
                    "name": scenario_name,
                    "actions": [
                        {
                            "action_type": a.action_type,
                            "amount": a.amount,
                        }
                        for a in actions
                    ],
                    "projected_tax": scenario_tax,
                    "baseline_tax": baseline["total_tax"],
                    "delta": round(delta, 2),
                    "delta_pct": delta_pct,
                    "trade_offs": trade_offs,
                }
            )

        return {
            "as_of": as_of,
            "filing_status": filing_status,
            "baseline": baseline,
            "scenarios": scenario_results,
            "disclaimer": (
                "This is decision-support modeling, not "
                "tax advice. Consult a qualified tax "
                "professional before taking action."
            ),
        }

    # ---------------------------------------------------------------
    # Tax computation engine
    # ---------------------------------------------------------------

    def _compute_tax(
        self,
        ordinary_income: float,
        st_capital_gains: float,
        lt_capital_gains: float,
        deductions: float,
        investment_income: float,
        brackets: list[tuple[float, float]],
        filing_status: str,
    ) -> dict[str, Any]:
        """Compute federal tax liability for one scenario."""
        # Short-term gains are taxed as ordinary income
        total_ordinary = ordinary_income + max(
            st_capital_gains, 0
        )
        taxable_ordinary = max(
            total_ordinary - deductions, 0
        )

        # Ordinary income tax (progressive brackets)
        ordinary_tax = self._apply_brackets(
            taxable_ordinary, brackets
        )

        # Long-term capital gains tax (preferential rates)
        lt_tax = self._compute_ltcg_tax(
            max(lt_capital_gains, 0),
            taxable_ordinary,
            filing_status,
        )

        # Net Investment Income Tax (3.8%)
        niit_threshold = (
            NIIT_THRESHOLD_MFJ
            if "mfj" in filing_status
            or "jointly" in filing_status
            else 200_000
        )
        agi = total_ordinary + max(lt_capital_gains, 0)
        niit = 0.0
        if agi > niit_threshold:
            niit_base = min(
                max(investment_income, 0),
                agi - niit_threshold,
            )
            niit = niit_base * NIIT_RATE

        total_tax = ordinary_tax + lt_tax + niit

        # Effective and marginal rates
        total_income = taxable_ordinary + max(
            lt_capital_gains, 0
        )
        effective_rate = (
            total_tax / total_income
            if total_income > 0
            else 0.0
        )
        marginal_rate = self._marginal_rate(
            taxable_ordinary, brackets
        )

        return {
            "taxable_ordinary_income": round(
                taxable_ordinary, 2
            ),
            "ordinary_tax": round(ordinary_tax, 2),
            "lt_capital_gains": round(
                max(lt_capital_gains, 0), 2
            ),
            "lt_gains_tax": round(lt_tax, 2),
            "niit": round(niit, 2),
            "total_tax": round(total_tax, 2),
            "effective_rate": round(effective_rate, 4),
            "marginal_rate": marginal_rate,
        }

    @staticmethod
    def _apply_brackets(
        taxable_income: float,
        brackets: list[tuple[float, float]],
    ) -> float:
        """Progressive bracket computation."""
        tax = 0.0
        prev_limit = 0.0
        for limit, rate in brackets:
            if taxable_income <= prev_limit:
                break
            taxable_in_bracket = (
                min(taxable_income, limit) - prev_limit
            )
            tax += taxable_in_bracket * rate
            prev_limit = limit
        return tax

    @staticmethod
    def _compute_ltcg_tax(
        lt_gains: float,
        taxable_ordinary: float,
        filing_status: str,
    ) -> float:
        """Apply preferential LTCG rates (0/15/20)."""
        if lt_gains <= 0:
            return 0.0
        ltcg_brackets = LTCG_BRACKETS_MFJ
        tax = 0.0
        # LTCG stacks on top of ordinary income
        base = taxable_ordinary
        remaining = lt_gains
        for limit, rate in ltcg_brackets:
            if base >= limit:
                continue
            room = limit - base
            taxable = min(remaining, room)
            tax += taxable * rate
            remaining -= taxable
            base += taxable
            if remaining <= 0:
                break
        return tax

    @staticmethod
    def _marginal_rate(
        taxable_income: float,
        brackets: list[tuple[float, float]],
    ) -> float:
        """Return the marginal ordinary income tax rate."""
        for limit, rate in brackets:
            if taxable_income <= limit:
                return rate
        return brackets[-1][1]
