# Verify Report: analytical-models

**Date:** 2026-03-28
**Result:** pass

## 1. Tests

```
182 passed, 1 warning in 3.14s
```

All 182 tests pass. The single warning is a `datetime.utcnow()` deprecation in
`calendar_adapter.py` (unrelated to this feature).

## 2. Ruff

```
All checks passed!
```

Zero lint violations in `src/` and `tests/`.

## 3. Structural Contracts

### 3a. Files (11 of 11 present)

| # | File | Present |
|---|------|---------|
| 1 | `src/app/analytics/__init__.py` | yes |
| 2 | `src/app/analytics/registry.py` | yes |
| 3 | `src/app/analytics/startup.py` | yes |
| 4 | `src/app/analytics/beneficiary_audit.py` | yes |
| 5 | `src/app/analytics/cash_drag.py` | yes |
| 6 | `src/app/analytics/concentration_risk.py` | yes |
| 7 | `src/app/analytics/drift_detection.py` | yes |
| 8 | `src/app/analytics/firm_ranker.py` | yes |
| 9 | `src/app/analytics/rmd_calculator.py` | yes |
| 10 | `src/app/analytics/style_profile.py` | yes |
| 11 | `src/app/analytics/tax_loss_harvesting.py` | yes |

### 3b. registry.py exports

- `ModelRegistry` class: yes
- `ModelMetadata` dataclass: yes
- `ModelKind` enum with 3 values (`deterministic`, `heuristic`, `learned`): yes
- `ModelCategory` enum with 5 values (`tax`, `portfolio`, `compliance`, `personalization`, `firm_analytics`): yes

### 3c. startup.py

`register_all_models()` imports and registers all 9 model classes: yes

### 3d. Model classes (metadata + score)

| Model Class | `metadata` | `score()` | kind |
|---|---|---|---|
| `TaxLossHarvestingScorer` | yes | yes | deterministic |
| `ConcentrationRiskScorer` | yes | yes | deterministic |
| `DriftDetector` | yes | yes | deterministic |
| `RMDCalculator` | yes | yes | deterministic |
| `TaxScenarioEngine` | yes | yes | heuristic |
| `FirmWideOpportunityRanker` | yes | yes | heuristic |
| `BeneficiaryCompletenessAudit` | yes | yes | deterministic |
| `CashDragDetector` | yes | yes | deterministic |
| `StyleProfileExtractor` | yes | yes | heuristic |

### 3e. Runtime registration

```
9 models registered
  tax_loss_harvesting v1.0.0 (deterministic)
  concentration_risk v1.0.0 (deterministic)
  drift_detection v1.0.0 (deterministic)
  rmd_calculator v1.0.0 (deterministic)
  tax_scenario_engine v1.0.0 (heuristic)
  firm_opportunity_ranker v1.0.0 (heuristic)
  beneficiary_audit v1.0.0 (deterministic)
  cash_drag_detector v1.0.0 (deterministic)
  style_profile_extractor v1.0.0 (heuristic)
```

## Notes

- The runtime registry check required mocking `scipy.stats` due to a NumPy 1.x/2.x
  binary incompatibility in the local Anaconda environment. This is an environment
  issue, not a code defect -- the test suite (which exercises the same code paths)
  passes cleanly.
