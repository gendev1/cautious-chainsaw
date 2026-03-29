# Exploration: Platform Client

## Structural Patterns

- **Layered DI**: Tools -> AgentDeps -> PlatformClient -> httpx -> Platform API
- **AccessScope flow**: middleware/tenant.py parses headers -> RequestContext -> AgentDeps -> PlatformClient methods
- **Config**: Pydantic BaseSettings with `SIDECAR_` prefix, lru_cache singleton
- **Errors**: SidecarError hierarchy with PlatformReadError already defined (status_code, error_code, message)
- **Lifespan**: main.py init_platform_client() stores in app.state, dependencies.py provides via Depends
- **Models split**: access_scope.py for AccessScope, schemas.py for agent output models

## Key Files

| File | Role | Lines | Notes |
|---|---|---|---|
| `src/app/services/platform_client.py` | PlatformClient stub | 122 | 12 methods all return None/[]. Constructor takes httpx.AsyncClient. Replace entirely. |
| `src/app/models/access_scope.py` | AccessScope model | 78 | Has visibility_mode, ID lists, allows_*() methods, to_vector_filter(). Add fingerprint(), tenant_id, actor_id, actor_type, request_id, conversation_id. |
| `src/app/models/schemas.py` | Agent output models | 382 | 22 models (ChatRequest, HazelCopilot, DailyDigest, etc.). Platform response models should go in separate file. |
| `src/app/config.py` | Settings | 113 | Already has platform_api_url, platform_service_token, platform_timeout_s, platform_circuit_threshold, platform_circuit_recovery_s. |
| `src/app/errors.py` | Error hierarchy | 199 | PlatformReadError exists with status_code, error_code, message. Missing classify_platform_error(). |
| `src/app/dependencies.py` | FastAPI DI | 146 | Has get_platform_client(), get_agent_deps(). Needs get_request_cache(). |
| `src/app/tools/email_adapter.py` | Stub | 17 | Single get_unread_priority_emails() stub. Replace. |
| `src/app/tools/crm_adapter.py` | Stub | 17 | Single get_pending_tasks() stub. Replace. |
| `src/app/tools/calendar_adapter.py` | Stub | 17 | Single get_todays_meetings() stub. Replace. |
| `src/app/tools/platform.py` | Agent tools | 120 | 7 tools using ctx.deps.platform. Will work with new PlatformClient (same method names). |
| `src/app/tools/search.py` | Search tools | 93 | 4 search tools using ctx.deps.platform. Same method names. |
| `src/app/agents/base_deps.py` | AgentDeps | ~30 | Has platform: PlatformClient field. Will work if PlatformClient is replaced in-place. |
| `src/app/middleware/tenant.py` | Scope parsing | ~50 | Parses X-Access-Scope header, builds RequestContext. |

## Downstream Consumers

- `tools/platform.py` calls: get_household_summary, get_account_summary, get_client_timeline, get_transfer_case, get_order_projection, get_report_snapshot, get_advisor_clients
- `tools/search.py` calls: search_documents_text, search_emails, search_crm_notes, search_meeting_transcripts
- `agents/digest.py` calls adapter stubs: get_todays_meetings, get_unread_priority_emails, get_pending_tasks
- `rag/retrieval.py` calls: access_scope.to_vector_filter()
- `tests/test_access_scope.py`: 8 tests for allows_*() and to_vector_filter()

## Risks

- AccessScope field additions must preserve allows_*() and to_vector_filter() used by RAG pipeline
- Existing tool functions reference PlatformClient methods by name; new client must keep compatible signatures
- schemas.py already has agent output models; platform response models need clean separation
