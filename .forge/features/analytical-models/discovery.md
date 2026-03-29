# Discovery: Analytical Models

## Requirements

1. **Model Registry** — ModelRegistry with register/get/invoke/list. ModelMetadata governance. ModelKind + ModelCategory enums. Module-level singleton.
2. **Startup wiring** — register_all_models() called from lifespan.
3. **9 analytical models:**
   - TaxLossHarvestingScorer — scan lots for unrealized losses, wash-sale check, replacement map, tax savings estimate
   - ConcentrationRiskScorer — HHI-based position/sector concentration, flagging
   - DriftDetector — target vs actual allocation drift, RMS deviation
   - RMDCalculator — IRS life expectancy table, required minimum distributions
   - TaxScenarioEngine — multi-action tax scenario projections
   - FirmWideOpportunityRanker — aggregate per-account findings, rank by impact
   - BeneficiaryCompletenessAudit — check beneficiary designations on retirement/trust accounts
   - CashDragDetector — uninvested cash detection, opportunity cost estimation
   - StyleProfileExtractor — advisor email writing style analysis (vocabulary, formality, patterns)
4. All models: deterministic/heuristic, no LLM calls, numpy for math
5. Each model: metadata with governance fields, score(inputs) -> dict

## Decisions Already Made

- All models under app/analytics/ package
- ModelRegistry singleton pattern
- No LLM calls in any model
- numpy for HHI, drift RMS, statistics
- scipy for style profiling statistics
- All models testable with deterministic inputs/outputs

## Constraints

- numpy already installed (from RAG pipeline)
- scipy needs to be added to dependencies
- Must not conflict with existing app/ modules
- Models must implement AnalyticalModel protocol (metadata + score())

## Open Questions

None — spec is comprehensive and self-contained.
