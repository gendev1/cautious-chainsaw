# Test Manifest: portfolio-construction-v2

## Test Files Created

| # | File | Tests | Category |
|---|------|-------|----------|
| 1 | `tests/test_portfolio_construction_models.py` | 32 | Unit (Pydantic models) |
| 2 | `tests/test_portfolio_data_loader.py` | 10 | Integration (mocked platform) |
| 3 | `tests/test_portfolio_factor_model_v2.py` | 28 | Unit (factor math) |
| 4 | `tests/test_portfolio_recall_pool.py` | 13 | Unit (recall pool logic) |
| 5 | `tests/test_portfolio_theme_scorer.py` | 16 | Contract (prompt, cache) |
| 6 | `tests/test_portfolio_composite_scorer.py` | 19 | Unit (7-step gating) |
| 7 | `tests/test_portfolio_optimizer.py` | 26 | Unit (weighting, clamping) |
| 8 | `tests/test_portfolio_agent_contracts.py` | 19 | Contract (agent schemas) |
| 9 | `tests/test_portfolio_construction_events.py` | 14 (24 with parametrize) | Integration (Redis Streams) |
| 10 | `tests/test_portfolio_construction_orchestrator.py` | 10 | Integration (full pipeline) |
| 11 | `tests/test_portfolio_construction_router.py` | 13 | Integration (FastAPI endpoints) |

**Total: 11 files, 200 test functions (210 test cases with parametrize expansion)**

## Spec to Test Mapping

### Step 1: Domain Models and Configuration
- `test_portfolio_construction_models.py::test_construct_request_minimal` -- ConstructPortfolioRequest with required fields only
- `test_portfolio_construction_models.py::test_construct_request_with_all_fields` -- ConstructPortfolioRequest with optional fields
- `test_portfolio_construction_models.py::test_construct_request_serialization_roundtrip` -- model_dump / model_validate
- `test_portfolio_construction_models.py::test_parsed_intent_*` -- ParsedIntent construction and serialization
- `test_portfolio_construction_models.py::test_factor_preferences_defaults` -- Default weights sum to 1.0
- `test_portfolio_construction_models.py::test_factor_preferences_normalization` -- Non-unit weights are normalized
- `test_portfolio_construction_models.py::test_intent_constraints_*` -- Optional fields, defaults, serialization
- `test_portfolio_construction_models.py::test_theme_score_result_*` -- ThemeScoreResult construction and anti-goal
- `test_portfolio_construction_models.py::test_factor_score_result_*` -- FactorScoreResult construction
- `test_portfolio_construction_models.py::test_composite_score_result_*` -- CompositeScoreResult construction and gating
- `test_portfolio_construction_models.py::test_proposed_holding_*` -- ProposedHolding construction
- `test_portfolio_construction_models.py::test_critic_feedback_*` -- CriticFeedback status enum validation
- `test_portfolio_construction_models.py::test_portfolio_rationale_*` -- PortfolioRationale construction
- `test_portfolio_construction_models.py::test_portfolio_construction_accepted` -- Job ID accepted response
- `test_portfolio_construction_models.py::test_construct_response_*` -- Full response assembly
- `test_portfolio_construction_models.py::test_job_event_*` -- JobEvent construction and serialization

### Step 2: PlatformClient Extensions and Data Loader
- `test_portfolio_data_loader.py::test_load_universe_returns_data` -- DataLoader loads securities
- `test_portfolio_data_loader.py::test_load_fundamentals_returns_data` -- DataLoader loads fundamentals
- `test_portfolio_data_loader.py::test_load_prices_returns_data` -- DataLoader loads price data
- `test_portfolio_data_loader.py::test_decimal_to_float_conversion_*` -- Decimal to float conversion for fundamentals, prices, market cap
- `test_portfolio_data_loader.py::test_freshness_warning_when_stale` -- Freshness warning emitted for stale data
- `test_portfolio_data_loader.py::test_no_freshness_warning_when_fresh` -- No warning for fresh data
- `test_portfolio_data_loader.py::test_freshness_warning_accumulates` -- Warnings accumulate across loads
- `test_portfolio_data_loader.py::test_load_universe_propagates_platform_error` -- Platform errors propagate

### Step 3: Factor Model v2
- `test_portfolio_factor_model_v2.py::test_model_metadata_*` -- Metadata name, version, category, kind, limitations
- `test_portfolio_factor_model_v2.py::test_percentile_ranking_*` -- Bounded scores [0,100], identical values center at 50
- `test_portfolio_factor_model_v2.py::test_winsorization_clips_outliers` -- 5th/95th percentile clipping
- `test_portfolio_factor_model_v2.py::test_peer_bucket_falls_back_*` -- Industry to sector to universe fallback
- `test_portfolio_factor_model_v2.py::test_correlation_adjusted_weights_sum_to_one` -- Effective weights sum to 1.0
- `test_portfolio_factor_model_v2.py::test_redundant_metrics_get_lower_weight` -- Correlated sub-factors
- `test_portfolio_factor_model_v2.py::test_reliability_shrinkage_*` -- Shrinkage toward 50 for low reliability
- `test_portfolio_factor_model_v2.py::test_breadth_cap_*` -- Cap at 65 (1 sub-factor), 75 (low support), no cap (high support)
- `test_portfolio_factor_model_v2.py::test_geometric_mean_*` -- Known-value geometric mean calculations
- `test_portfolio_factor_model_v2.py::test_full_score_produces_valid_output` -- Full score() on 25-security universe
- `test_portfolio_factor_model_v2.py::test_factor_deactivation_*` -- Low coverage and few sub-factors
- `test_portfolio_factor_model_v2.py::test_activation_report_populated` -- Universe stats fields
- `test_portfolio_factor_model_v2.py::test_lower_is_better_inversion` -- PE ratio inversion
- `test_portfolio_factor_model_v2.py::test_missing_subfactors_omitted_not_zero_filled` -- No zero-filling
- `test_portfolio_factor_model_v2.py::test_empty_universe` -- Empty input
- `test_portfolio_factor_model_v2.py::test_single_security` -- Single security
- `test_portfolio_factor_model_v2.py::test_metadata_output` -- Model version and universe size in output

### Step 4: Recall Pool Builder
- `test_portfolio_recall_pool.py::test_factor_top_n_*` -- Top-N selection by factor score
- `test_portfolio_recall_pool.py::test_metadata_matching_*` -- Sector and name keyword matching
- `test_portfolio_recall_pool.py::test_include_tickers_*` -- Force-include regardless of score
- `test_portfolio_recall_pool.py::test_excluded_tickers_*` -- Remove even if top scorer
- `test_portfolio_recall_pool.py::test_excluded_overrides_include` -- Exclude wins over include
- `test_portfolio_recall_pool.py::test_cap_enforced_at_250` -- Cap at 250
- `test_portfolio_recall_pool.py::test_pool_smaller_than_cap_for_small_universe` -- Small universe
- `test_portfolio_recall_pool.py::test_deduplication_*` -- No duplicates
- `test_portfolio_recall_pool.py::test_empty_*` -- Empty factor scores and empty themes

### Step 5: Theme Scorer Agent and Cache
- `test_portfolio_theme_scorer.py::test_theme_scorer_output_parses_from_json` -- Sample LLM JSON parses
- `test_portfolio_theme_scorer.py::test_theme_scorer_output_all_fields_present` -- All fields present
- `test_portfolio_theme_scorer.py::test_anti_goal_hit_forces_score_zero` -- Anti-goal => score 0
- `test_portfolio_theme_scorer.py::test_confidence_range_valid` -- Confidence [0,1]
- `test_portfolio_theme_scorer.py::test_score_range_valid` -- Score [0,100]
- `test_portfolio_theme_scorer.py::test_build_theme_scorer_prompt_*` -- Prompt includes themes, anti-goals, tickers
- `test_portfolio_theme_scorer.py::test_cache_key_same_inputs_same_key` -- Deterministic cache key
- `test_portfolio_theme_scorer.py::test_cache_key_different_*` -- Different inputs, different keys
- `test_portfolio_theme_scorer.py::test_cache_key_order_independent` -- Canonical sort
- `test_portfolio_theme_scorer.py::test_cache_miss_returns_none` -- Cache miss
- `test_portfolio_theme_scorer.py::test_cache_hit_returns_scores` -- Cache hit
- `test_portfolio_theme_scorer.py::test_cache_set_*` -- Cache write with TTL

### Step 6: Composite Scorer
- `test_portfolio_composite_scorer.py::test_excluded_ticker_gated_*` -- Gate 1: hard exclusion
- `test_portfolio_composite_scorer.py::test_anti_goal_hit_ticker_gated` -- Gate 2: anti-goal
- `test_portfolio_composite_scorer.py::test_below_factor_floor_gated` -- Gate 3: factor floor
- `test_portfolio_composite_scorer.py::test_below_theme_floor_gated` -- Gate 3: theme floor
- `test_portfolio_composite_scorer.py::test_uncertainty_adjustment_*` -- Step 4: shrink toward 50
- `test_portfolio_composite_scorer.py::test_geometric_mean_*` -- Step 5: weighted geometric mean
- `test_portfolio_composite_scorer.py::test_coherence_bonus_*` -- Step 6: +5 when both >= 70
- `test_portfolio_composite_scorer.py::test_weak_link_penalty_*` -- Step 6: -5 when gap >= 35
- `test_portfolio_composite_scorer.py::test_composite_score_clamped_*` -- Step 7: clamp [0,100]
- `test_portfolio_composite_scorer.py::test_speculative_lowers_factor_floor` -- Speculative overrides
- `test_portfolio_composite_scorer.py::test_results_ranked_by_composite_descending` -- Ranking order
- `test_portfolio_composite_scorer.py::test_multiple_tickers_scored` -- All tickers scored
- `test_portfolio_composite_scorer.py::test_empty_input_*` -- Edge cases

### Step 7: Optimizer and Constraint Relaxation
- `test_portfolio_optimizer.py::test_equal_weighting_*` -- Equal: sums to 1.0, each 1/N, single ticker
- `test_portfolio_optimizer.py::test_conviction_*` -- Conviction: sums to 1.0, proportional to scores
- `test_portfolio_optimizer.py::test_risk_parity_*` -- Risk parity: sums to 1.0, inverse vol, imputation
- `test_portfolio_optimizer.py::test_min_variance_*` -- Min variance: sums to 1.0, fallback to risk_parity
- `test_portfolio_optimizer.py::test_clamp_positions_*` -- Min/max bounds, sum to 1.0, many positions
- `test_portfolio_optimizer.py::test_select_candidates_*` -- Include/exclude, sector cap, target count
- `test_portfolio_optimizer.py::test_auto_relax_*` -- Relaxation trigger, sequence order, notes

### Step 8: Agent Contracts
- `test_portfolio_agent_contracts.py::test_intent_parser_output_*` -- ParsedIntent from JSON
- `test_portfolio_agent_contracts.py::test_intent_parser_default_factor_preferences_normalize` -- Sum to 1.0
- `test_portfolio_agent_contracts.py::test_intent_parser_speculative_flag` -- Speculative parsing
- `test_portfolio_agent_contracts.py::test_intent_parser_ambiguity_flags` -- Ambiguity flags preserved
- `test_portfolio_agent_contracts.py::test_intent_parser_conservative_inference` -- Conservative intent
- `test_portfolio_agent_contracts.py::test_rationale_output_*` -- PortfolioRationale from JSON
- `test_portfolio_agent_contracts.py::test_rationale_core_and_supporting_disjoint` -- Disjoint sets
- `test_portfolio_agent_contracts.py::test_critic_output_*` -- CriticFeedback from JSON
- `test_portfolio_agent_contracts.py::test_critic_cannot_*` -- Hard rules enforcement
- `test_portfolio_agent_contracts.py::test_all_agent_outputs_roundtrip` -- All 3 agents round-trip
- `test_portfolio_agent_contracts.py::test_*_agent_importable` -- Agent registration (4 agents)

### Step 9: Progress Events
- `test_portfolio_construction_events.py::test_emit_*` -- XADD calls, stream key, fields, version, event_type, payload
- `test_portfolio_construction_events.py::test_all_event_types_emit_successfully` -- All 11 event types (parametrized)
- `test_portfolio_construction_events.py::test_read_events_*` -- XREAD returns ordered, empty stream, last_id
- `test_portfolio_construction_events.py::test_get_job_status_*` -- Latest event, no events

### Step 10: Orchestrator
- `test_portfolio_construction_orchestrator.py::test_pipeline_happy_path_*` -- APPROVED first iteration
- `test_portfolio_construction_orchestrator.py::test_pipeline_events_emitted_in_order` -- Event sequence
- `test_portfolio_construction_orchestrator.py::test_pipeline_response_weights_sum_to_one` -- Weight validation
- `test_portfolio_construction_orchestrator.py::test_pipeline_review_loop_*` -- NEEDS_REVISION then APPROVED
- `test_portfolio_construction_orchestrator.py::test_pipeline_best_effort_*` -- Max iterations
- `test_portfolio_construction_orchestrator.py::test_theme_scores_reused_*` -- Theme scorer called once
- `test_portfolio_construction_orchestrator.py::test_pipeline_account_refresh_mode` -- Account mode
- `test_portfolio_construction_orchestrator.py::test_pipeline_idea_mode_no_account` -- Idea mode
- `test_portfolio_construction_orchestrator.py::test_pipeline_response_has_all_fields` -- Response completeness
- `test_portfolio_construction_orchestrator.py::test_pipeline_platform_error_propagates` -- Error propagation

### Step 12: Router Endpoints
- `test_portfolio_construction_router.py::test_construct_endpoint_exists` -- POST routable
- `test_portfolio_construction_router.py::test_construct_returns_202` -- 202 with job_id
- `test_portfolio_construction_router.py::test_construct_with_optional_fields` -- Optional fields accepted
- `test_portfolio_construction_router.py::test_construct_missing_message_returns_422` -- Validation error
- `test_portfolio_construction_router.py::test_construct_missing_headers_returns_400` -- Missing tenant headers
- `test_portfolio_construction_router.py::test_construct_scope_propagation` -- JobContext from headers
- `test_portfolio_construction_router.py::test_job_status_*` -- GET status endpoint
- `test_portfolio_construction_router.py::test_events_*` -- GET events SSE endpoint

## Edge Cases Covered

| Category | Edge Case | Test |
|----------|-----------|------|
| Models | FactorPreferences weights normalized when not summing to 1.0 | `test_factor_preferences_normalization` |
| Models | CriticFeedback invalid status enum | `test_critic_feedback_invalid_status` |
| Models | Empty proposed holdings in response | `test_construct_response_serialization_roundtrip` |
| Data Loader | Stale data freshness warning | `test_freshness_warning_when_stale` |
| Data Loader | Fresh data no warning | `test_no_freshness_warning_when_fresh` |
| Data Loader | Platform error propagation | `test_load_universe_propagates_platform_error` |
| Factor Model | Empty universe | `test_empty_universe` |
| Factor Model | Single security | `test_single_security` |
| Factor Model | Identical values centering | `test_percentile_ranking_with_identical_values` |
| Factor Model | Extreme outlier winsorization | `test_winsorization_clips_outliers` |
| Factor Model | Small peer bucket fallback | `test_peer_bucket_falls_back_to_sector`, `test_peer_bucket_falls_back_to_universe` |
| Factor Model | Missing sub-factor fields | `test_missing_subfactors_omitted_not_zero_filled`, `test_breadth_cap_single_subfactor` |
| Factor Model | Factor deactivation low coverage | `test_factor_deactivation_low_coverage` |
| Recall Pool | Include ticker not in universe | `test_include_tickers_not_in_universe` |
| Recall Pool | Exclude overrides include | `test_excluded_overrides_include` |
| Recall Pool | Empty factor scores | `test_empty_factor_scores` |
| Recall Pool | Empty themes | `test_empty_intent_themes` |
| Recall Pool | Small universe below cap | `test_pool_smaller_than_cap_for_small_universe` |
| Theme Scorer | Cache key order independence | `test_cache_key_order_independent` |
| Theme Scorer | Cache miss returns None | `test_cache_miss_returns_none` |
| Composite | Empty input | `test_empty_input_returns_empty_results` |
| Composite | Missing theme score for ticker | `test_missing_theme_score_for_ticker` |
| Composite | Score clamped above 100 | `test_composite_score_clamped_upper` |
| Composite | Score clamped below 0 | `test_composite_score_clamped_lower` |
| Composite | Speculative factor floor override | `test_speculative_lowers_factor_floor` |
| Optimizer | Empty ticker list | `test_equal_weighting_empty_list` |
| Optimizer | All-zero composite scores | `test_conviction_weighting_zero_scores` |
| Optimizer | Missing volatility imputation | `test_risk_parity_sector_median_imputation` |
| Optimizer | Solver failure fallback | `test_min_variance_fallback_to_risk_parity` |
| Optimizer | Empty composites | `test_select_candidates_empty_composites` |
| Agent Contracts | Critic adds excluded ticker | `test_critic_cannot_add_excluded_ticker` |
| Agent Contracts | Critic exceeds max position | `test_critic_cannot_violate_max_single_position` |
| Events | Redis error propagation | `test_emit_propagates_redis_error` |
| Events | Empty stream read | `test_read_events_empty_stream` |
| Events | No events for status | `test_get_job_status_returns_none_no_events` |
| Orchestrator | Platform error during pipeline | `test_pipeline_platform_error_propagates` |
| Orchestrator | Best-effort after max iterations | `test_pipeline_best_effort_after_max_iterations` |
| Router | Missing message body | `test_construct_missing_message_returns_422` |
| Router | Missing headers | `test_construct_missing_headers_returns_400` |
| Router | Nonexistent job | `test_nonexistent_job_returns_appropriate_status` |
| Router | Empty POST body | `test_construct_empty_body_returns_422` |

## Test File Checksums

| File | MD5 |
|------|-----|
| `tests/test_portfolio_construction_models.py` | `dedb7d0206fb8505cb6867d35da20ac5` |
| `tests/test_portfolio_data_loader.py` | `8ded3e8b0967352fb5f9663f5bd1c0c3` |
| `tests/test_portfolio_factor_model_v2.py` | `bf9ce4e65d2ffc00a3ba9dd63646fe10` |
| `tests/test_portfolio_recall_pool.py` | `3749248e98a8aadefb62fef812709ea9` |
| `tests/test_portfolio_theme_scorer.py` | `5e78fdfa1b486e5f7f79164d5dead8dd` |
| `tests/test_portfolio_composite_scorer.py` | `70030bb74659c65096c1698b4704c95b` |
| `tests/test_portfolio_optimizer.py` | `3a1c2301137bc24e5618497bf77403f3` |
| `tests/test_portfolio_agent_contracts.py` | `d27c311a57606914a71e4f97fe28f09f` |
| `tests/test_portfolio_construction_events.py` | `548645c93d2419faf601cb4fe16bbf41` |
| `tests/test_portfolio_construction_orchestrator.py` | `d4a79987044b8050c2772490b045b867` |
| `tests/test_portfolio_construction_router.py` | `7312156c34bce34b23920f15f54e2cc1` |

## Run Command

```bash
cd apps/intelligence-layer && python -m pytest tests/test_portfolio_construction_models.py tests/test_portfolio_data_loader.py tests/test_portfolio_factor_model_v2.py tests/test_portfolio_recall_pool.py tests/test_portfolio_theme_scorer.py tests/test_portfolio_composite_scorer.py tests/test_portfolio_optimizer.py tests/test_portfolio_agent_contracts.py tests/test_portfolio_construction_events.py tests/test_portfolio_construction_orchestrator.py tests/test_portfolio_construction_router.py -v
```

Or run all portfolio construction tests with pattern match:

```bash
cd apps/intelligence-layer && python -m pytest tests/test_portfolio_*.py -v
```
