# Test Manifest: Analytical Models

## Test Files Created

| File | Tests | Purpose |
|---|---|---|
| `tests/test_analytics_registry.py` | 5 | Registry register/get/invoke/list |
| `tests/test_tax_loss_harvesting.py` | 4 | Loss detection, wash-sale, replacements |
| `tests/test_concentration_risk.py` | 3 | Position flagging, HHI computation |
| `tests/test_cash_drag.py` | 2 | High/low cash detection |

## Test File Checksums

| File | SHA256 |
|---|---|
| tests/test_analytics_registry.py | 84cadc56abc1799316f803881cccdb80d7f15c1d96fca0e79286715f8b383f65 |
| tests/test_tax_loss_harvesting.py | 8239e8d3e0d5e8c920c0f0a50f0bfca0a638a2a18217ec1dbdec395e0a7c91d5 |
| tests/test_concentration_risk.py | 13bf6c40cfcf228c1d2c8487495c23398fe9b314866e88b04c08a3b58fb4f51c |
| tests/test_cash_drag.py | e1bf40a948bf88adb3d9f6cd31be9647e37ebfeb69601f25f1a5f5f8399e7d82 |

## Spec → Test Mapping

| Spec Requirement | Test |
|---|---|
| Registry register + get | test_analytics_registry.py::test_register_and_get |
| Registry versioned lookup | test_analytics_registry.py::test_get_specific_version |
| Registry invoke metadata | test_analytics_registry.py::test_invoke_adds_metadata |
| TLH finds loss candidate | test_tax_loss_harvesting.py::test_finds_loss_candidate |
| TLH ignores gains | test_tax_loss_harvesting.py::test_ignores_gains |
| TLH wash-sale blocks | test_tax_loss_harvesting.py::test_wash_sale_blocks |
| Concentration flags >10% | test_concentration_risk.py::test_concentrated_position_flagged |
| Concentration HHI computed | test_concentration_risk.py::test_hhi_computed |
| Cash drag >5% flagged | test_cash_drag.py::test_high_cash_flagged |

## Edge Cases Covered

- Duplicate registration raises ValueError
- Gains positions excluded from TLH
- Diversified portfolio produces no flags
- Low cash not flagged

## Run Command

```bash
cd apps/intelligence-layer && python -m pytest tests/test_analytics_registry.py tests/test_tax_loss_harvesting.py tests/test_concentration_risk.py tests/test_cash_drag.py -v
```
