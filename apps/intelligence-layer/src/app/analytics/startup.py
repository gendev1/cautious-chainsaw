"""
Called from FastAPI lifespan to populate the registry.
"""
from __future__ import annotations

from app.analytics.beneficiary_audit import (
    BeneficiaryCompletenessAudit,
)
from app.analytics.cash_drag import CashDragDetector
from app.analytics.concentration_risk import (
    ConcentrationRiskScorer,
)
from app.analytics.drift_detection import DriftDetector
from app.analytics.firm_ranker import FirmWideOpportunityRanker
from app.analytics.registry import get_registry
from app.analytics.rmd_calculator import RMDCalculator
from app.analytics.style_profile import StyleProfileExtractor
from app.analytics.tax_loss_harvesting import (
    TaxLossHarvestingScorer,
)
from app.analytics.tax_scenario_engine import TaxScenarioEngine
from app.analytics.portfolio_factor_model_v2 import PortfolioFactorModelV2


def register_all_models() -> None:
    registry = get_registry()
    registry.register(TaxLossHarvestingScorer())
    registry.register(ConcentrationRiskScorer())
    registry.register(DriftDetector())
    registry.register(RMDCalculator())
    registry.register(TaxScenarioEngine())
    registry.register(FirmWideOpportunityRanker())
    registry.register(BeneficiaryCompletenessAudit())
    registry.register(CashDragDetector())
    registry.register(StyleProfileExtractor())
    registry.register(PortfolioFactorModelV2())
