# Test Manifest: Agents and Tools

## Test Files Created

| File | Test count | Purpose |
|---|---|---|
| `tests/test_registry.py` | 5 | Agent registry register/get/list/duplicates/full count |
| `tests/test_schemas.py` | 6 | Result type model validation |
| `tests/test_tool_safety.py` | 2 | AST-based mutation and httpx safety checks |
| `tests/test_message_codec.py` | 10 | Message serialize/deserialize/trim/extract |
| `tests/test_conversation_memory.py` | 5 | Redis memory save/load/isolation/clear/state |
| `tests/test_llm_client.py` | 4 | Model tier definitions and frozen check |

**Total: 6 files, 32 test cases**

## Spec → Test Mapping

| Spec case | Test location |
|---|---|
| T1: Registry register and get | `test_registry.py::test_register_and_get` |
| T2: Registry KeyError for unknown | `test_registry.py::test_get_unknown_raises_key_error` |
| T3: Registry has 12 agents | `test_registry.py::test_full_registry_has_12_agents` |
| T4: HazelCopilot validates | `test_schemas.py::test_hazel_copilot_validates` |
| T5: DailyDigest validates | `test_schemas.py::test_daily_digest_validates` |
| T6: TaxPlan disclaimer | `test_schemas.py::test_tax_plan_has_default_disclaimer` |
| T7: No mutation methods in tools | `test_tool_safety.py::test_no_mutation_methods_in_tools` |
| T8: No direct httpx in tools | `test_tool_safety.py::test_no_httpx_direct_calls_in_tools` |
| T9: Codec round-trip | `test_message_codec.py::test_serialize_deserialize_*` (4 tests) |
| T10: Trim caps at max | `test_message_codec.py::test_trim_caps_at_max` |
| T11: Trim preserves system prompt | `test_message_codec.py::test_trim_preserves_system_prompt` |
| T12: Memory save/load | `test_conversation_memory.py::test_save_and_load` |
| T13: Tenant isolation | `test_conversation_memory.py::test_tenant_isolation` |
| T14: TIERS has 5 entries | `test_llm_client.py::test_tiers_has_five_entries` |
| T15: ChatRequest validates | `test_schemas.py::test_chat_request_validates` |

## Edge Cases Covered

- [x] Duplicate agent registration raises ValueError
- [x] HazelCopilot with citations and actions
- [x] Empty conversation load returns []
- [x] Memory clear removes data
- [x] load_state returns None for nonexistent conversation
- [x] extract_active_*_id returns None when no matching ID
- [x] Trim under limit returns as-is
- [x] Tool return part round-trip preserves nested dict content
- [x] Analysis tier has no fallback
- [x] ModelTier is frozen (immutable)

## Test File Checksums

| File | SHA256 |
|---|---|
| `apps/intelligence-layer/tests/test_registry.py` | `45f28756b3d0af0fbe1d1a2a5fef0d1ee52aeb4aa61e54ba2c7258ce2f9e92a9` |
| `apps/intelligence-layer/tests/test_schemas.py` | `7abb564d7c60dd9219a41c5408f87e79913f5bc90f450b9b773ee5227eb696a7` |
| `apps/intelligence-layer/tests/test_tool_safety.py` | `5ac49007d7b93cad731d2d4ebadb455d8992ef1679a10ee33d958de264e7d42b` |
| `apps/intelligence-layer/tests/test_message_codec.py` | `1a39d923bb1c5a3c8f29d99a8642aad0d8b0de5d467a8931bf13ed59c0a4cc4a` |
| `apps/intelligence-layer/tests/test_conversation_memory.py` | `5ac218726210f3e93f6cc612a69bdcd25359cc77a2b3065dc6e676415e323f3e` |
| `apps/intelligence-layer/tests/test_llm_client.py` | `085f3a887c8de7adb40ce53fe77a322bb799aa8194885a98bfc77f059ee42749` |

## Run Command

```bash
cd apps/intelligence-layer && uv run pytest tests/ -v
```
