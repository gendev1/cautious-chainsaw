# Implementation Manifest: Analytical Models

## Files Created

| File | Purpose |
|---|---|
| `src/app/analytics/__init__.py` | Package init |
| `src/app/analytics/registry.py` | ModelRegistry, ModelMetadata, ModelKind, ModelCategory |
| `src/app/analytics/startup.py` | register_all_models() |
| `src/app/analytics/tax_loss_harvesting.py` | TaxLossHarvestingScorer — wash-sale, replacements |
| `src/app/analytics/concentration_risk.py` | ConcentrationRiskScorer — HHI, position/sector flags |
| `src/app/analytics/drift_detection.py` | DriftDetector — RMS drift vs target allocation |
| `src/app/analytics/rmd_calculator.py` | RMDCalculator — IRS life expectancy, RMD amounts |
| `src/app/analytics/tax_scenario_engine.py` | TaxScenarioEngine — multi-action projections |
| `src/app/analytics/firm_ranker.py` | FirmWideOpportunityRanker — rank by impact |
| `src/app/analytics/beneficiary_audit.py` | BeneficiaryCompletenessAudit — compliance checks |
| `src/app/analytics/cash_drag.py` | CashDragDetector — excess cash flagging |
| `src/app/analytics/style_profile.py` | StyleProfileExtractor — email writing style |

## Files Modified

| File | Change |
|---|---|
| `pyproject.toml` | Added scipy>=1.12.0 |

## Test Results

```
182 passed in 2.96s
Ruff: All checks passed!
```
