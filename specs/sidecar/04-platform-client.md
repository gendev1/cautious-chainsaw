# 04 -- Platform Client Implementation

This document specifies the full implementation of the Python sidecar's Platform Client: the narrow, typed, read-only client that serves as the single approved data access path to the platform API. It covers client architecture, access scope propagation, authentication, response models, error handling, timeout/retry policy, request-scoped caching, circuit breaking, adapter readers, and testing.

Reference: `python-sidecar.md` sections 2.3.5, 9, and 13.

---

## 1. Client Architecture

The Platform Client is an `httpx.AsyncClient`-based class with a fixed set of typed methods -- one per approved read operation. There is no generic fetch, no query builder, and no dynamic endpoint construction. Each method maps to a single platform API endpoint and returns a single Pydantic model.

### 1.1 Core Class

```python
# app/services/platform_client.py

from __future__ import annotations

import time
import hashlib
import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel

from app.models.schemas import (
    AccessScope,
    HouseholdSummary,
    AccountSummary,
    ClientProfile,
    TransferCase,
    OrderProjection,
    ExecutionProjection,
    ReportSnapshot,
    DocumentMetadata,
    TimelineEvent,
    ClientSummary,
    DocumentMatch,
    HoldingSummary,
    RealizedGainsSummary,
    BenchmarkData,
    CalendarEvent,
    TaskSummary,
    PriorityEmail,
    AccountAlert,
    EmailThread,
    TeamMember,
    CRMActivity,
)
from app.services.errors import PlatformReadError, classify_platform_error
from app.services.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.services.request_cache import RequestScopedCache

logger = logging.getLogger("sidecar.platform_client")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_S = 3.0
MAX_CONNECTIONS = 20
MAX_KEEPALIVE = 10


class PlatformClientConfig(BaseModel):
    base_url: str
    service_token: str                # service JWT or shared secret
    timeout_s: float = DEFAULT_TIMEOUT_S
    circuit_failure_threshold: int = 5
    circuit_recovery_timeout_s: float = 30.0


# ---------------------------------------------------------------------------
# Platform Client
# ---------------------------------------------------------------------------

class PlatformClient:
    """Narrow typed client for platform API reads.

    Single approved data access path. No mutation methods.
    No generic data fetch. Each method is typed and bounded.
    """

    def __init__(
        self,
        config: PlatformClientConfig,
        *,
        cache: RequestScopedCache | None = None,
    ) -> None:
        self._config = config
        self._cache = cache
        self._circuit = CircuitBreaker(
            failure_threshold=config.circuit_failure_threshold,
            recovery_timeout_s=config.circuit_recovery_timeout_s,
        )
        self._http = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(config.timeout_s, connect=2.0),
            limits=httpx.Limits(
                max_connections=MAX_CONNECTIONS,
                max_keepalive_connections=MAX_KEEPALIVE,
            ),
            headers={
                "Authorization": f"Bearer {config.service_token}",
                "User-Agent": "sidecar/1.0",
            },
        )

    async def close(self) -> None:
        await self._http.aclose()

    # -- helpers ------------------------------------------------------------

    def _scope_headers(
        self,
        access_scope: AccessScope,
        *,
        tenant_id: str | None = None,
        actor_id: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, str]:
        """Build per-request headers that carry identity and access scope."""
        headers: dict[str, str] = {}
        headers["X-Tenant-ID"] = tenant_id or access_scope.tenant_id
        headers["X-Actor-ID"] = actor_id or access_scope.actor_id
        if request_id:
            headers["X-Request-ID"] = request_id
        # Access scope is sent as a JSON-encoded header so the platform can
        # enforce it server-side without requiring query-param encoding of
        # complex nested structures.
        headers["X-Access-Scope"] = access_scope.model_dump_json()
        return headers

    def _cache_key(self, method: str, **kwargs: Any) -> str:
        """Deterministic cache key from method name + call arguments."""
        raw = json.dumps({"m": method, **kwargs}, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    async def _get(
        self,
        path: str,
        *,
        access_scope: AccessScope,
        params: dict[str, Any] | None = None,
        cache_key: str | None = None,
    ) -> httpx.Response:
        """Shared GET helper with circuit breaker, caching, and error handling."""

        # 1. Check request-scoped cache
        if cache_key and self._cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug("cache hit for %s", cache_key[:12])
                return cached

        # 2. Check circuit breaker
        self._circuit.check()  # raises CircuitOpenError if tripped

        # 3. Execute request
        try:
            response = await self._http.get(
                path,
                params=params,
                headers=self._scope_headers(access_scope),
            )
        except httpx.TimeoutException as exc:
            self._circuit.record_failure()
            raise PlatformReadError(
                status_code=0,
                error_code="TIMEOUT",
                message=f"Platform API timeout on {path}: {exc}",
            ) from exc
        except httpx.HTTPError as exc:
            self._circuit.record_failure()
            raise PlatformReadError(
                status_code=0,
                error_code="CONNECTION_ERROR",
                message=f"Platform API connection error on {path}: {exc}",
            ) from exc

        # 4. Classify response
        if response.status_code >= 400:
            self._circuit.record_failure()
            raise classify_platform_error(response)

        # 5. Record success, populate cache
        self._circuit.record_success()
        if cache_key and self._cache:
            self._cache.set(cache_key, response)

        return response

    # -----------------------------------------------------------------------
    # typed read methods -- one per approved operation
    # -----------------------------------------------------------------------

    async def get_household_summary(
        self,
        household_id: str,
        access_scope: AccessScope,
    ) -> HouseholdSummary:
        key = self._cache_key("household_summary", hid=household_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/households/{household_id}/summary",
            access_scope=access_scope,
            cache_key=key,
        )
        return HouseholdSummary.model_validate(resp.json())

    async def get_account_summary(
        self,
        account_id: str,
        access_scope: AccessScope,
    ) -> AccountSummary:
        key = self._cache_key("account_summary", aid=account_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/accounts/{account_id}/summary",
            access_scope=access_scope,
            cache_key=key,
        )
        return AccountSummary.model_validate(resp.json())

    async def get_client_profile(
        self,
        client_id: str,
        access_scope: AccessScope,
    ) -> ClientProfile:
        key = self._cache_key("client_profile", cid=client_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/clients/{client_id}/profile",
            access_scope=access_scope,
            cache_key=key,
        )
        return ClientProfile.model_validate(resp.json())

    async def get_transfer_case(
        self,
        transfer_id: str,
        access_scope: AccessScope,
    ) -> TransferCase:
        key = self._cache_key("transfer_case", tid=transfer_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/transfers/{transfer_id}",
            access_scope=access_scope,
            cache_key=key,
        )
        return TransferCase.model_validate(resp.json())

    async def get_order_projection(
        self,
        order_id: str,
        access_scope: AccessScope,
    ) -> OrderProjection:
        key = self._cache_key("order_projection", oid=order_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/orders/{order_id}/projection",
            access_scope=access_scope,
            cache_key=key,
        )
        return OrderProjection.model_validate(resp.json())

    async def get_execution_projection(
        self,
        execution_id: str,
        access_scope: AccessScope,
    ) -> ExecutionProjection:
        key = self._cache_key("execution_projection", eid=execution_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/executions/{execution_id}/projection",
            access_scope=access_scope,
            cache_key=key,
        )
        return ExecutionProjection.model_validate(resp.json())

    async def get_report_snapshot(
        self,
        report_id: str,
        access_scope: AccessScope,
    ) -> ReportSnapshot:
        key = self._cache_key("report_snapshot", rid=report_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/reports/{report_id}/snapshot",
            access_scope=access_scope,
            cache_key=key,
        )
        return ReportSnapshot.model_validate(resp.json())

    async def get_document_metadata(
        self,
        document_id: str,
        access_scope: AccessScope,
    ) -> DocumentMetadata:
        key = self._cache_key("document_metadata", did=document_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/documents/{document_id}/metadata",
            access_scope=access_scope,
            cache_key=key,
        )
        return DocumentMetadata.model_validate(resp.json())

    async def get_client_timeline(
        self,
        client_id: str,
        access_scope: AccessScope,
        days: int = 90,
    ) -> list[TimelineEvent]:
        key = self._cache_key("client_timeline", cid=client_id, days=days, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/clients/{client_id}/timeline",
            access_scope=access_scope,
            params={"days": days},
            cache_key=key,
        )
        return [TimelineEvent.model_validate(item) for item in resp.json()]

    async def get_advisor_clients(
        self,
        advisor_id: str,
        access_scope: AccessScope,
    ) -> list[ClientSummary]:
        key = self._cache_key("advisor_clients", adv=advisor_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/advisors/{advisor_id}/clients",
            access_scope=access_scope,
            cache_key=key,
        )
        return [ClientSummary.model_validate(item) for item in resp.json()]

    async def get_firm_accounts(
        self,
        filters: dict[str, Any],
        access_scope: AccessScope,
    ) -> list[AccountSummary]:
        key = self._cache_key("firm_accounts", filters=filters, scope=access_scope.fingerprint())
        resp = await self._get(
            "/v1/accounts",
            access_scope=access_scope,
            params=filters,
            cache_key=key,
        )
        return [AccountSummary.model_validate(item) for item in resp.json()]

    async def search_documents_text(
        self,
        query: str,
        filters: dict[str, Any],
        access_scope: AccessScope,
    ) -> list[DocumentMatch]:
        key = self._cache_key("search_documents_text", q=query, filters=filters, scope=access_scope.fingerprint())
        resp = await self._get(
            "/v1/documents/search",
            access_scope=access_scope,
            params={"q": query, **filters},
            cache_key=key,
        )
        return [DocumentMatch.model_validate(item) for item in resp.json()]

    async def get_document_content(
        self,
        document_id: str,
        access_scope: AccessScope,
    ) -> str:
        key = self._cache_key("document_content", did=document_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/documents/{document_id}/content",
            access_scope=access_scope,
            cache_key=key,
        )
        payload = resp.json()
        return payload["content"]

    async def get_client_holdings(
        self,
        client_id: str,
        access_scope: AccessScope,
        *,
        include_cost_basis: bool = False,
    ) -> list[HoldingSummary]:
        key = self._cache_key(
            "client_holdings",
            cid=client_id,
            include_cost_basis=include_cost_basis,
            scope=access_scope.fingerprint(),
        )
        resp = await self._get(
            f"/v1/clients/{client_id}/holdings",
            access_scope=access_scope,
            params={"include_cost_basis": include_cost_basis},
            cache_key=key,
        )
        return [HoldingSummary.model_validate(item) for item in resp.json()]

    async def get_client_realized_gains(
        self,
        client_id: str,
        access_scope: AccessScope,
        *,
        tax_year: int,
    ) -> RealizedGainsSummary:
        key = self._cache_key("client_realized_gains", cid=client_id, tax_year=tax_year, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/clients/{client_id}/realized-gains",
            access_scope=access_scope,
            params={"tax_year": tax_year},
            cache_key=key,
        )
        return RealizedGainsSummary.model_validate(resp.json())

    async def get_client_accounts(
        self,
        client_id: str,
        access_scope: AccessScope,
        *,
        account_ids: list[str] | None = None,
    ) -> list[AccountSummary]:
        key = self._cache_key("client_accounts", cid=client_id, account_ids=account_ids, scope=access_scope.fingerprint())
        params: dict[str, Any] = {}
        if account_ids:
            params["account_ids"] = ",".join(account_ids)
        resp = await self._get(
            f"/v1/clients/{client_id}/accounts",
            access_scope=access_scope,
            params=params or None,
            cache_key=key,
        )
        return [AccountSummary.model_validate(item) for item in resp.json()]

    async def get_benchmark_data(
        self,
        access_scope: AccessScope,
    ) -> BenchmarkData:
        key = self._cache_key("benchmark_data", scope=access_scope.fingerprint())
        resp = await self._get(
            "/v1/benchmarks/default",
            access_scope=access_scope,
            cache_key=key,
        )
        return BenchmarkData.model_validate(resp.json())

    async def get_advisor_calendar(
        self,
        advisor_id: str,
        access_scope: AccessScope,
    ) -> list[CalendarEvent]:
        key = self._cache_key("advisor_calendar", advisor_id=advisor_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/advisors/{advisor_id}/calendar",
            access_scope=access_scope,
            cache_key=key,
        )
        return [CalendarEvent.model_validate(item) for item in resp.json()]

    async def get_advisor_tasks(
        self,
        advisor_id: str,
        access_scope: AccessScope,
    ) -> list[TaskSummary]:
        key = self._cache_key("advisor_tasks", advisor_id=advisor_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/advisors/{advisor_id}/tasks",
            access_scope=access_scope,
            cache_key=key,
        )
        return [TaskSummary.model_validate(item) for item in resp.json()]

    async def get_advisor_priority_emails(
        self,
        advisor_id: str,
        access_scope: AccessScope,
    ) -> list[PriorityEmail]:
        key = self._cache_key("advisor_priority_emails", advisor_id=advisor_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/advisors/{advisor_id}/emails/priority",
            access_scope=access_scope,
            cache_key=key,
        )
        return [PriorityEmail.model_validate(item) for item in resp.json()]

    async def get_account_alerts(
        self,
        advisor_id: str,
        access_scope: AccessScope,
    ) -> list[AccountAlert]:
        key = self._cache_key("account_alerts", advisor_id=advisor_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/advisors/{advisor_id}/account-alerts",
            access_scope=access_scope,
            cache_key=key,
        )
        return [AccountAlert.model_validate(item) for item in resp.json()]

    async def get_email_thread(
        self,
        email_id: str,
        access_scope: AccessScope,
    ) -> EmailThread:
        key = self._cache_key("email_thread", email_id=email_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/emails/{email_id}/thread",
            access_scope=access_scope,
            cache_key=key,
        )
        return EmailThread.model_validate(resp.json())

    async def get_advisor_team(
        self,
        advisor_id: str,
        access_scope: AccessScope,
    ) -> list[TeamMember]:
        key = self._cache_key("advisor_team", advisor_id=advisor_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/advisors/{advisor_id}/team",
            access_scope=access_scope,
            cache_key=key,
        )
        return [TeamMember.model_validate(item) for item in resp.json()]

    async def get_crm_activity_feed(
        self,
        advisor_id: str,
        access_scope: AccessScope,
    ) -> list[CRMActivity]:
        key = self._cache_key("crm_activity_feed", advisor_id=advisor_id, scope=access_scope.fingerprint())
        resp = await self._get(
            f"/v1/advisors/{advisor_id}/crm-activity",
            access_scope=access_scope,
            cache_key=key,
        )
        return [CRMActivity.model_validate(item) for item in resp.json()]
```

### 1.2 Design Rationale

- **No generic fetch.** There is no `self._http.get(arbitrary_path)` exposed publicly. Every public method is typed end-to-end: typed input parameters, typed return value, named platform endpoint. If the sidecar needs data from a new platform endpoint, a new typed method must be added to this class and reviewed.
- **Fixed typed surface.** Adding a new method is an architectural decision, not an incidental convenience. The surface may grow as new read contracts are formalized, but each addition must remain explicit and typed.
- **`httpx.AsyncClient` as transport.** Async-native, connection pooling, timeout control, HTTP/2 support. Created once at startup and reused across requests.

---

## 2. Access Scope Propagation

Every method on `PlatformClient` requires an `AccessScope` parameter. The sidecar never decides permissions independently -- it forwards the scope the platform computed at request entry.

### 2.1 AccessScope Model

```python
# app/models/schemas.py (partial)

import hashlib
import json
from pydantic import BaseModel


class AccessScope(BaseModel):
    """Access scope computed by the platform and forwarded to the sidecar.

    The sidecar does not decide these permissions independently.
    The platform provides the scope, and the sidecar enforces it
    in every read call.
    """

    tenant_id: str
    actor_id: str
    actor_type: str                                  # "advisor", "admin", "service"
    request_id: str
    conversation_id: str | None = None
    visibility_mode: str = "scoped"                  # "full_tenant" or "scoped"
    household_ids: list[str] = []
    client_ids: list[str] = []
    account_ids: list[str] = []
    document_ids: list[str] = []
    advisor_ids: list[str] = []

    def fingerprint(self) -> str:
        """Stable hash of the scope for use in cache keys."""
        raw = self.model_dump_json()
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

### 2.2 Propagation Mechanism

The scope is propagated to the platform API via a single header `X-Access-Scope` containing the JSON-serialized `AccessScope` model. This approach is used instead of query parameters because:

1. The scope is a complex nested object (lists of IDs, visibility mode). Query-param encoding would be fragile and non-standard.
2. Headers keep the scope out of server access logs, which is appropriate for authorization metadata.
3. The platform API already requires `X-Tenant-ID` and `X-Actor-ID` as top-level headers; the full scope rides alongside them.

```python
def _scope_headers(
    self,
    access_scope: AccessScope,
    *,
    tenant_id: str | None = None,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    headers["X-Tenant-ID"] = tenant_id or access_scope.tenant_id
    headers["X-Actor-ID"] = actor_id or access_scope.actor_id
    if request_id or access_scope.request_id:
        headers["X-Request-ID"] = request_id or access_scope.request_id
    headers["X-Access-Scope"] = access_scope.model_dump_json()
    return headers
```

Every `_get` call passes through `_scope_headers`. There is no code path that can reach the platform API without attaching the scope.

### 2.3 Scope Lifecycle

```
platform API (TypeScript)
  -> authenticates user, resolves tenant, computes access scope
  -> serializes scope into sidecar request body/headers
  -> calls sidecar endpoint

sidecar FastAPI route
  -> deserializes AccessScope from request context
  -> passes AccessScope to agent as dependency
  -> agent tools call PlatformClient methods with AccessScope
  -> PlatformClient attaches scope as X-Access-Scope header
  -> platform API validates scope server-side before returning data
```

The sidecar never widens, narrows, or recomputes the scope. It is a pass-through enforcement point.

---

## 3. Authentication

### 3.1 Service-to-Service Auth

The sidecar authenticates to the platform API using a service JWT or shared secret. This is not an end-user token -- it is a long-lived service credential that identifies the sidecar as a trusted internal caller.

```python
self._http = httpx.AsyncClient(
    base_url=config.base_url,
    headers={
        "Authorization": f"Bearer {config.service_token}",
        "User-Agent": "sidecar/1.0",
    },
    # ...
)
```

The `service_token` is loaded from environment configuration at startup:

```python
# app/config.py

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    platform_api_url: str
    platform_service_token: str          # service JWT or shared secret
    platform_timeout_s: float = 3.0
    platform_circuit_threshold: int = 5
    platform_circuit_recovery_s: float = 30.0

    model_config = {"env_prefix": "SIDECAR_"}
```

### 3.2 Per-Request Identity Headers

Every call to the platform API includes three identity headers in addition to the `Authorization` header:

| Header | Source | Purpose |
|--------|--------|---------|
| `X-Tenant-ID` | `access_scope.tenant_id` | Tenant isolation. Platform uses this to scope all database queries. |
| `X-Actor-ID` | `access_scope.actor_id` | Audit trail. Platform records which actor triggered each read. |
| `X-Request-ID` | `access_scope.request_id` | Distributed tracing. Correlates sidecar reads with the originating platform request. |
| `X-Access-Scope` | Full JSON scope | Authorization. Platform validates the scope before returning data. |

These headers are attached by `_scope_headers` and applied to every outgoing request. There is no way to call a platform endpoint without them.

---

## 4. Response Models

All response types are Pydantic v2 models defined in `app/models/schemas.py`. Each model corresponds to one return type from the approved typed methods.

```python
# app/models/schemas.py

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AccountType(str, Enum):
    INDIVIDUAL = "individual"
    JOINT = "joint"
    IRA = "ira"
    ROTH_IRA = "roth_ira"
    TRUST = "trust"
    CUSTODIAL = "custodial"
    CORPORATE = "corporate"
    FOUNDATION = "foundation"


class AccountStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    PENDING = "pending"
    RESTRICTED = "restricted"


class TransferStatus(str, Enum):
    INITIATED = "initiated"
    PENDING_APPROVAL = "pending_approval"
    IN_TRANSIT = "in_transit"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class TimelineEventType(str, Enum):
    MEETING = "meeting"
    EMAIL = "email"
    CALL = "call"
    NOTE = "note"
    TRADE = "trade"
    TRANSFER = "transfer"
    DOCUMENT = "document"
    TASK = "task"
    ACCOUNT_ALERT = "account_alert"


class DocumentCategory(str, Enum):
    TAX_RETURN = "tax_return"
    ESTATE_PLAN = "estate_plan"
    TRUST_DOCUMENT = "trust_document"
    FINANCIAL_STATEMENT = "financial_statement"
    INSURANCE_POLICY = "insurance_policy"
    CORRESPONDENCE = "correspondence"
    COMPLIANCE = "compliance"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Freshness metadata (spec section 2.7: all financial answers include freshness)
# ---------------------------------------------------------------------------

class FreshnessMeta(BaseModel):
    as_of: datetime
    source: str                          # e.g. "custodian_feed", "platform_cache"
    staleness_seconds: int | None = None


# ---------------------------------------------------------------------------
# Response models -- one per PlatformClient method return type
# ---------------------------------------------------------------------------

class Holding(BaseModel):
    symbol: str
    name: str
    quantity: Decimal
    market_value: Decimal
    cost_basis: Decimal | None = None
    weight_pct: Decimal
    asset_class: str | None = None


class HoldingSummary(Holding):
    account_id: str
    unrealized_gain_loss: Decimal | None = None
    cost_basis_total: Decimal | None = None


class AccountSummary(BaseModel):
    account_id: str
    account_name: str
    account_type: AccountType
    status: AccountStatus
    custodian: str
    total_value: Decimal
    cash_balance: Decimal
    holdings: list[Holding]
    performance_ytd_pct: Decimal | None = None
    performance_1y_pct: Decimal | None = None
    model_name: str | None = None
    drift_pct: Decimal | None = None
    household_id: str
    client_id: str
    freshness: FreshnessMeta


class HouseholdSummary(BaseModel):
    household_id: str
    household_name: str
    primary_advisor_id: str
    accounts: list[AccountSummary]
    total_aum: Decimal
    client_ids: list[str]
    freshness: FreshnessMeta


class ContactInfo(BaseModel):
    email: str | None = None
    phone: str | None = None
    address: str | None = None


class ClientProfile(BaseModel):
    client_id: str
    first_name: str
    last_name: str
    date_of_birth: date | None = None
    household_id: str
    contact: ContactInfo
    risk_tolerance: str | None = None
    investment_objective: str | None = None
    account_ids: list[str]
    tags: list[str] = []
    notes: str | None = None
    freshness: FreshnessMeta


class TransferAsset(BaseModel):
    symbol: str
    name: str
    quantity: Decimal | None = None
    estimated_value: Decimal | None = None


class TransferCase(BaseModel):
    transfer_id: str
    account_id: str
    direction: str                       # "inbound" or "outbound"
    status: TransferStatus
    transfer_type: str                   # "ACAT", "wire", "journal", etc.
    assets: list[TransferAsset]
    estimated_value: Decimal | None = None
    initiated_at: datetime
    updated_at: datetime
    expected_completion: date | None = None
    notes: str | None = None
    freshness: FreshnessMeta


class OrderProjection(BaseModel):
    order_id: str
    account_id: str
    symbol: str
    side: str                            # "buy" or "sell"
    quantity: Decimal
    order_type: str                      # "market", "limit", etc.
    status: OrderStatus
    submitted_at: datetime | None = None
    filled_quantity: Decimal | None = None
    filled_avg_price: Decimal | None = None
    freshness: FreshnessMeta


class ExecutionProjection(BaseModel):
    execution_id: str
    order_id: str
    account_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    executed_at: datetime
    settlement_date: date | None = None
    commission: Decimal | None = None
    freshness: FreshnessMeta


class ReportSnapshot(BaseModel):
    report_id: str
    report_type: str                     # "performance", "billing", "compliance", etc.
    title: str
    generated_at: datetime
    period_start: date
    period_end: date
    data: dict                           # report-type-specific payload
    freshness: FreshnessMeta


class DocumentMetadata(BaseModel):
    document_id: str
    filename: str
    category: DocumentCategory
    mime_type: str
    size_bytes: int
    uploaded_at: datetime
    client_id: str | None = None
    household_id: str | None = None
    account_id: str | None = None
    tags: list[str] = []
    freshness: FreshnessMeta


class TimelineEvent(BaseModel):
    event_id: str
    event_type: TimelineEventType
    timestamp: datetime
    title: str
    summary: str | None = None
    client_id: str
    household_id: str | None = None
    actor_id: str | None = None
    related_ids: dict[str, str] = {}     # e.g. {"account_id": "acc_123"}


class ClientSummary(BaseModel):
    client_id: str
    first_name: str
    last_name: str
    household_id: str
    total_aum: Decimal
    account_count: int
    last_contact_date: date | None = None
    tags: list[str] = []


class DocumentMatch(BaseModel):
    document_id: str
    filename: str
    category: DocumentCategory
    relevance_score: float
    matched_excerpt: str
    client_id: str | None = None
    household_id: str | None = None
    uploaded_at: datetime


class RealizedGainsSummary(BaseModel):
    tax_year: int
    short_term_gains: Decimal = Decimal("0")
    long_term_gains: Decimal = Decimal("0")
    realized_losses: Decimal = Decimal("0")
    net_realized_gain_loss: Decimal = Decimal("0")
    freshness: FreshnessMeta


class BenchmarkData(BaseModel):
    benchmark_id: str
    benchmark_name: str
    as_of: datetime
    returns: dict[str, Decimal]
    allocations: dict[str, Decimal] | None = None
    freshness: FreshnessMeta


class CalendarEvent(BaseModel):
    event_id: str
    subject: str
    start: datetime
    end: datetime
    location: str | None = None
    attendees: list[str] = []
    organizer: str | None = None
    client_id: str | None = None
    is_recurring: bool = False
    body_preview: str | None = None


class TaskSummary(BaseModel):
    task_id: str
    title: str
    description: str | None = None
    client_id: str | None = None
    assigned_to: str
    due_date: datetime | None = None
    status: str
    priority: str = "normal"


class PriorityEmail(BaseModel):
    email_id: str
    from_address: str
    subject: str
    received_at: datetime
    priority: str
    thread_id: str | None = None
    body_preview: str | None = None
    client_id: str | None = None


class AccountAlert(BaseModel):
    alert_id: str
    account_id: str
    client_id: str
    alert_type: str
    severity: str
    title: str
    description: str
    as_of: datetime


class EmailThread(BaseModel):
    thread_id: str
    subject: str
    participants: list[str]
    latest_message_at: datetime
    messages: list[dict[str, Any]]


class TeamMember(BaseModel):
    user_id: str
    display_name: str
    role: str
    email: str | None = None


class CRMActivity(BaseModel):
    activity_id: str
    activity_type: str
    client_id: str
    advisor_id: str
    subject: str
    description: str | None = None
    occurred_at: datetime
    status: str = "completed"
```

All models use `Decimal` for monetary values (never `float`). All data-bearing models include `FreshnessMeta` so downstream consumers know the age of the data.

---

## 5. Error Handling

### 5.1 PlatformReadError

All platform API failures are normalized into a single exception type with three fields: HTTP status code, a classified error code, and a human-readable message.

```python
# app/services/errors.py

from __future__ import annotations

import httpx


class PlatformReadError(Exception):
    """Raised when a platform API read fails.

    Attributes:
        status_code: HTTP status code (0 for connection/timeout errors).
        error_code: Classified error code string.
        message: Human-readable description.
    """

    def __init__(self, status_code: int, error_code: str, message: str) -> None:
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        super().__init__(f"[{error_code}] {status_code}: {message}")


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

def classify_platform_error(response: httpx.Response) -> PlatformReadError:
    """Classify an HTTP error response into a PlatformReadError.

    Categories:
      - 400  BAD_REQUEST        Malformed request from sidecar (bug).
      - 401  UNAUTHORIZED       Service token expired or invalid.
      - 403  FORBIDDEN          Access scope does not permit this read.
      - 404  NOT_FOUND          Resource does not exist or is not visible.
      - 409  CONFLICT           Resource state conflict.
      - 422  VALIDATION_ERROR   Platform rejected the request parameters.
      - 429  RATE_LIMITED       Sidecar is calling too fast.
      - 5xx  PLATFORM_ERROR    Platform internal failure.
    """
    status = response.status_code

    # Try to extract a structured error body
    try:
        body = response.json()
        detail = body.get("detail", body.get("message", response.text[:500]))
    except Exception:
        detail = response.text[:500]

    error_map: dict[int, str] = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
    }

    if status in error_map:
        code = error_map[status]
    elif 400 <= status < 500:
        code = "CLIENT_ERROR"
    else:
        code = "PLATFORM_ERROR"

    return PlatformReadError(
        status_code=status,
        error_code=code,
        message=str(detail),
    )
```

### 5.2 Error Handling in Agent Code

Agents catch `PlatformReadError` and degrade gracefully. A 404 might be normal (resource not found); a 403 means the scope is too narrow; a 5xx means the platform is struggling.

```python
# Example: agent tool using PlatformClient

from src.services.errors import PlatformReadError
from src.services.circuit_breaker import CircuitOpenError


async def get_household_tool(
    household_id: str,
    platform: PlatformClient,
    access_scope: AccessScope,
) -> HouseholdSummary | str:
    """Tool wrapper that degrades gracefully on platform errors."""
    try:
        return await platform.get_household_summary(household_id, access_scope)
    except PlatformReadError as exc:
        if exc.error_code == "NOT_FOUND":
            return f"Household {household_id} not found."
        if exc.error_code == "FORBIDDEN":
            return f"Access denied for household {household_id}."
        # Log and return a degraded message for other errors
        logger.warning("platform read failed: %s", exc)
        return f"Unable to load household data at this time."
    except CircuitOpenError:
        return "Platform API is temporarily unavailable."
```

---

## 6. Timeout and Retry

### 6.1 Default Policy: 3-Second Timeout, No Retries

Interactive requests (chat, meeting prep, portfolio analysis) need sub-second platform reads. The default policy is:

- **3-second total timeout** per HTTP call (2-second connect timeout, 3-second overall).
- **No automatic retries.** If a read fails, the agent gets a `PlatformReadError` immediately and must degrade. Retrying inside an interactive request adds latency that the advisor will feel.

```python
self._http = httpx.AsyncClient(
    base_url=config.base_url,
    timeout=httpx.Timeout(config.timeout_s, connect=2.0),
    # ...
)
```

### 6.2 Batch Retry Policy

Background jobs (daily digest, firm reports, analytical sweeps) can tolerate retries. The `RetryPolicy` wrapper adds exponential backoff for batch callers.

```python
# app/services/retry.py

from __future__ import annotations

import asyncio
import logging
from typing import TypeVar, Callable, Awaitable

from app.services.errors import PlatformReadError

logger = logging.getLogger("sidecar.retry")

T = TypeVar("T")


class RetryPolicy:
    """Retry wrapper for batch jobs that can tolerate added latency.

    Not used for interactive requests. Interactive callers use the
    default PlatformClient which fails fast on first error.
    """

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay_s: float = 0.5,
        max_delay_s: float = 5.0,
        retryable_codes: frozenset[str] = frozenset({
            "TIMEOUT",
            "CONNECTION_ERROR",
            "PLATFORM_ERROR",
            "RATE_LIMITED",
        }),
    ) -> None:
        self.max_attempts = max_attempts
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self.retryable_codes = retryable_codes

    async def execute(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Execute an async callable with retry logic."""
        last_error: PlatformReadError | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                return await fn()
            except PlatformReadError as exc:
                last_error = exc
                if exc.error_code not in self.retryable_codes:
                    raise  # non-retryable errors propagate immediately

                if attempt == self.max_attempts:
                    raise

                delay = min(
                    self.base_delay_s * (2 ** (attempt - 1)),
                    self.max_delay_s,
                )
                logger.info(
                    "retrying platform read (attempt %d/%d) after %.1fs: %s",
                    attempt, self.max_attempts, delay, exc.error_code,
                )
                await asyncio.sleep(delay)

        # Unreachable, but satisfies type checker
        assert last_error is not None
        raise last_error
```

Usage in a batch job:

```python
retry = RetryPolicy(max_attempts=3, base_delay_s=1.0)

household = await retry.execute(
    lambda: platform.get_household_summary("hh_123", scope)
)
```

---

## 7. Request-Scoped Caching

Within a single agent run, multiple tools may need the same platform data. For example, the copilot agent might call `get_household_summary` from both the portfolio analysis tool and the tax planning tool in the same request. Without caching, this doubles the platform API load for no reason.

The `RequestScopedCache` is a simple in-memory dict that lives for exactly one request. It is created at request entry, passed to `PlatformClient`, and discarded when the request completes.

### 7.1 Implementation

```python
# app/services/request_cache.py

from __future__ import annotations

import time
import logging
from typing import Any

logger = logging.getLogger("sidecar.request_cache")


class RequestScopedCache:
    """Per-request in-memory cache for platform reads.

    Lifecycle:
      - Created when a FastAPI request begins.
      - Injected into PlatformClient for that request.
      - Discarded when the request completes (goes out of scope).

    This is NOT a cross-request cache. It prevents duplicate reads
    within a single agent run where multiple tools need the same data.
    """

    def __init__(self, max_entries: int = 100) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._max_entries = max_entries
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is not None:
            self._hits += 1
            return entry[0]  # value
        self._misses += 1
        return None

    def set(self, key: str, value: Any) -> None:
        if len(self._store) >= self._max_entries:
            # Evict oldest entry (by insertion time)
            oldest_key = min(self._store, key=lambda k: self._store[k][1])
            del self._store[oldest_key]
        self._store[key] = (value, time.monotonic())

    def clear(self) -> None:
        self._store.clear()

    @property
    def stats(self) -> dict[str, int]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "entries": len(self._store),
        }
```

### 7.2 Integration with FastAPI

The cache is created per-request via a FastAPI dependency:

```python
# src/dependencies.py

from src.services.request_cache import RequestScopedCache
from src.services.platform_client import PlatformClient, PlatformClientConfig


def get_request_cache() -> RequestScopedCache:
    """Fresh cache per request. Garbage collected when request ends."""
    return RequestScopedCache()


async def get_platform_client(
    cache: RequestScopedCache = Depends(get_request_cache),
    settings: Settings = Depends(get_settings),
) -> PlatformClient:
    """Platform client with per-request cache attached."""
    config = PlatformClientConfig(
        base_url=settings.platform_api_url,
        service_token=settings.platform_service_token,
        timeout_s=settings.platform_timeout_s,
        circuit_failure_threshold=settings.platform_circuit_threshold,
        circuit_recovery_timeout_s=settings.platform_circuit_recovery_s,
    )
    return PlatformClient(config, cache=cache)
```

The cache key is computed from the method name plus all call arguments (including the access scope fingerprint), so the same logical read with different scopes produces different cache entries.

---

## 8. Circuit Breaker

If the platform API is consistently failing, there is no point in sending more requests. The circuit breaker tracks consecutive failures and short-circuits after a threshold.

### 8.1 Implementation

```python
# app/services/circuit_breaker.py

from __future__ import annotations

import time
import logging

logger = logging.getLogger("sidecar.circuit_breaker")


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open (platform deemed unavailable)."""

    def __init__(self, failures: int, recovery_at: float) -> None:
        remaining = max(0.0, recovery_at - time.monotonic())
        super().__init__(
            f"Circuit open after {failures} consecutive failures. "
            f"Recovery in {remaining:.1f}s."
        )
        self.failures = failures
        self.recovery_at = recovery_at


class CircuitBreaker:
    """Simple consecutive-failure circuit breaker.

    States:
      CLOSED  -- normal operation, requests pass through
      OPEN    -- too many consecutive failures, requests are rejected
      HALF_OPEN -- recovery timeout elapsed, one probe request is allowed

    No external dependencies. State is in-memory per PlatformClient
    instance.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_s: float = 30.0,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout_s = recovery_timeout_s
        self._consecutive_failures = 0
        self._last_failure_time: float = 0.0
        self._state: str = "CLOSED"      # CLOSED | OPEN | HALF_OPEN

    @property
    def state(self) -> str:
        return self._state

    def check(self) -> None:
        """Check whether a request is allowed. Raises CircuitOpenError if not."""
        if self._state == "CLOSED":
            return

        if self._state == "OPEN":
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._recovery_timeout_s:
                # Transition to half-open: allow one probe request
                self._state = "HALF_OPEN"
                logger.info("circuit breaker transitioning to HALF_OPEN")
                return
            raise CircuitOpenError(
                failures=self._consecutive_failures,
                recovery_at=self._last_failure_time + self._recovery_timeout_s,
            )

        # HALF_OPEN: allow the probe request through
        return

    def record_success(self) -> None:
        """Record a successful request."""
        if self._state == "HALF_OPEN":
            logger.info("circuit breaker closing after successful probe")
        self._consecutive_failures = 0
        self._state = "CLOSED"

    def record_failure(self) -> None:
        """Record a failed request."""
        self._consecutive_failures += 1
        self._last_failure_time = time.monotonic()

        if self._consecutive_failures >= self._failure_threshold:
            if self._state != "OPEN":
                logger.warning(
                    "circuit breaker opening after %d consecutive failures",
                    self._consecutive_failures,
                )
            self._state = "OPEN"
        elif self._state == "HALF_OPEN":
            # Probe failed, reopen
            logger.warning("circuit breaker reopening after failed probe")
            self._state = "OPEN"
```

### 8.2 Behavior Summary

| Consecutive Failures | State | Behavior |
|---|---|---|
| 0-4 | CLOSED | Requests pass through normally |
| 5+ | OPEN | All requests immediately raise `CircuitOpenError` |
| (after 30s) | HALF_OPEN | One probe request is allowed through |
| Probe succeeds | CLOSED | Normal operation resumes |
| Probe fails | OPEN | Back to rejecting, timer resets |

The circuit breaker is shared across all 12 methods on a single `PlatformClient` instance. If the platform is down, it is down for all endpoints -- there is no per-endpoint circuit.

---

## 9. Adapter Read Methods

The sidecar reads from three non-platform data sources: email (Microsoft Graph API), CRM (via platform integration endpoints), and calendar (Microsoft Graph API or Google Calendar API). Each adapter is a separate typed client that follows the same patterns as `PlatformClient`: typed methods, access scope propagation, no generic fetch.

### 9.1 Email Adapter

```python
# app/tools/email_adapter.py

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel
import httpx

from src.models.schemas import AccessScope
from src.services.errors import PlatformReadError


class EmailMessage(BaseModel):
    message_id: str
    subject: str
    sender: str
    recipients: list[str]
    received_at: datetime
    body_preview: str
    has_attachments: bool
    client_id: str | None = None         # Matched to platform client if recognized
    thread_id: str | None = None
    importance: str = "normal"


class EmailSearchResult(BaseModel):
    messages: list[EmailMessage]
    total_count: int


class EmailAdapter:
    """Typed read-only client for advisor email via Microsoft Graph API.

    The sidecar reads email history for context (meeting prep, email
    drafting, triage). It does not send emails -- that is a platform
    write operation.
    """

    def __init__(
        self,
        graph_base_url: str,
        token_provider: callable,        # async () -> str (OAuth token)
        timeout_s: float = 5.0,
    ) -> None:
        self._graph_base_url = graph_base_url
        self._token_provider = token_provider
        self._http = httpx.AsyncClient(
            base_url=graph_base_url,
            timeout=httpx.Timeout(timeout_s, connect=3.0),
        )

    async def _get_headers(self, access_scope: AccessScope) -> dict[str, str]:
        token = await self._token_provider()
        return {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": access_scope.tenant_id,
            "X-Actor-ID": access_scope.actor_id,
        }

    async def search_emails(
        self,
        advisor_email: str,
        query: str,
        access_scope: AccessScope,
        *,
        max_results: int = 25,
        since: datetime | None = None,
        client_email: str | None = None,
    ) -> EmailSearchResult:
        """Search advisor's mailbox by keyword, date range, or client."""
        headers = await self._get_headers(access_scope)

        # Build OData filter for Graph API
        filters: list[str] = []
        if since:
            filters.append(f"receivedDateTime ge {since.isoformat()}")
        if client_email:
            filters.append(
                f"(from/emailAddress/address eq '{client_email}' "
                f"or toRecipients/any(r:r/emailAddress/address eq '{client_email}'))"
            )

        params: dict[str, str] = {
            "$search": f'"{query}"',
            "$top": str(max_results),
            "$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview,hasAttachments,conversationId,importance",
            "$orderby": "receivedDateTime desc",
        }
        if filters:
            params["$filter"] = " and ".join(filters)

        resp = await self._http.get(
            f"/v1.0/users/{advisor_email}/messages",
            headers=headers,
            params=params,
        )
        if resp.status_code >= 400:
            raise PlatformReadError(
                status_code=resp.status_code,
                error_code="EMAIL_READ_ERROR",
                message=f"Graph API error: {resp.text[:300]}",
            )

        data = resp.json()
        messages = [
            EmailMessage(
                message_id=msg["id"],
                subject=msg.get("subject", ""),
                sender=msg.get("from", {}).get("emailAddress", {}).get("address", ""),
                recipients=[
                    r["emailAddress"]["address"]
                    for r in msg.get("toRecipients", [])
                ],
                received_at=msg["receivedDateTime"],
                body_preview=msg.get("bodyPreview", ""),
                has_attachments=msg.get("hasAttachments", False),
                thread_id=msg.get("conversationId"),
                importance=msg.get("importance", "normal"),
            )
            for msg in data.get("value", [])
        ]
        return EmailSearchResult(
            messages=messages,
            total_count=data.get("@odata.count", len(messages)),
        )

    async def get_recent_emails(
        self,
        advisor_email: str,
        access_scope: AccessScope,
        *,
        hours: int = 24,
        max_results: int = 50,
    ) -> list[EmailMessage]:
        """Get recent emails from the last N hours."""
        since = datetime.utcnow().replace(
            hour=0, minute=0, second=0
        )  # simplified; real impl uses timedelta
        result = await self.search_emails(
            advisor_email=advisor_email,
            query="*",
            access_scope=access_scope,
            max_results=max_results,
            since=since,
        )
        return result.messages

    async def close(self) -> None:
        await self._http.aclose()
```

### 9.2 CRM Adapter

The CRM adapter reads CRM data through the platform's own integration endpoints (not directly from Salesforce/Wealthbox). The platform owns the CRM connector; the sidecar reads through the platform's unified CRM read API.

```python
# app/tools/crm_adapter.py

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel
import httpx

from app.models.access_scope import AccessScope
from app.services.errors import PlatformReadError, classify_platform_error


class CRMNote(BaseModel):
    note_id: str
    client_id: str
    author_id: str
    content: str
    created_at: datetime
    updated_at: datetime | None = None
    tags: list[str] = []


class CRMActivity(BaseModel):
    activity_id: str
    activity_type: str                   # "call", "meeting", "email", "task"
    client_id: str
    advisor_id: str
    subject: str
    description: str | None = None
    occurred_at: datetime
    status: str = "completed"


class CRMTask(BaseModel):
    task_id: str
    title: str
    description: str | None = None
    client_id: str | None = None
    assigned_to: str
    due_date: datetime | None = None
    status: str                          # "open", "in_progress", "completed"
    priority: str = "normal"


class CRMAdapter:
    """Typed read-only client for CRM data via platform integration endpoints.

    The sidecar does NOT call CRM APIs (Salesforce, Wealthbox, Redtail)
    directly. It reads through the platform's unified CRM read surface.
    """

    def __init__(
        self,
        base_url: str,
        service_token: str,
        timeout_s: float = 3.0,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_s, connect=2.0),
            headers={
                "Authorization": f"Bearer {service_token}",
                "User-Agent": "sidecar/1.0",
            },
        )

    def _scope_headers(self, access_scope: AccessScope) -> dict[str, str]:
        return {
            "X-Tenant-ID": access_scope.tenant_id,
            "X-Actor-ID": access_scope.actor_id,
            "X-Request-ID": access_scope.request_id,
            "X-Access-Scope": access_scope.model_dump_json(),
        }

    async def search_notes(
        self,
        client_id: str,
        access_scope: AccessScope,
        *,
        query: str | None = None,
        max_results: int = 25,
    ) -> list[CRMNote]:
        """Search CRM notes for a client."""
        params: dict[str, str] = {"client_id": client_id, "limit": str(max_results)}
        if query:
            params["q"] = query

        resp = await self._http.get(
            "/v1/crm/notes",
            headers=self._scope_headers(access_scope),
            params=params,
        )
        if resp.status_code >= 400:
            raise classify_platform_error(resp)
        return [CRMNote.model_validate(item) for item in resp.json()]

    async def get_activities(
        self,
        client_id: str,
        access_scope: AccessScope,
        *,
        activity_type: str | None = None,
        max_results: int = 25,
    ) -> list[CRMActivity]:
        """Get CRM activities for a client."""
        params: dict[str, str] = {"client_id": client_id, "limit": str(max_results)}
        if activity_type:
            params["type"] = activity_type

        resp = await self._http.get(
            "/v1/crm/activities",
            headers=self._scope_headers(access_scope),
            params=params,
        )
        if resp.status_code >= 400:
            raise classify_platform_error(resp)
        return [CRMActivity.model_validate(item) for item in resp.json()]

    async def get_open_tasks(
        self,
        advisor_id: str,
        access_scope: AccessScope,
    ) -> list[CRMTask]:
        """Get open tasks assigned to an advisor."""
        resp = await self._http.get(
            "/v1/crm/tasks",
            headers=self._scope_headers(access_scope),
            params={"assigned_to": advisor_id, "status": "open"},
        )
        if resp.status_code >= 400:
            raise classify_platform_error(resp)
        return [CRMTask.model_validate(item) for item in resp.json()]

    async def close(self) -> None:
        await self._http.aclose()
```

### 9.3 Calendar Adapter

```python
# app/tools/calendar_adapter.py

from __future__ import annotations

from datetime import datetime, date
from pydantic import BaseModel
import httpx

from app.models.access_scope import AccessScope
from app.services.errors import PlatformReadError


class CalendarEvent(BaseModel):
    event_id: str
    subject: str
    start: datetime
    end: datetime
    location: str | None = None
    attendees: list[str] = []
    organizer: str | None = None
    client_id: str | None = None         # Matched to platform client if recognized
    is_recurring: bool = False
    body_preview: str | None = None


class CalendarAdapter:
    """Typed read-only client for advisor calendar via Graph API.

    Used for meeting prep (upcoming meetings) and daily digest
    (today's schedule). Does not create or modify events.
    """

    def __init__(
        self,
        graph_base_url: str,
        token_provider: callable,        # async () -> str
        timeout_s: float = 5.0,
    ) -> None:
        self._graph_base_url = graph_base_url
        self._token_provider = token_provider
        self._http = httpx.AsyncClient(
            base_url=graph_base_url,
            timeout=httpx.Timeout(timeout_s, connect=3.0),
        )

    async def _get_headers(self, access_scope: AccessScope) -> dict[str, str]:
        token = await self._token_provider()
        return {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": access_scope.tenant_id,
            "X-Actor-ID": access_scope.actor_id,
        }

    async def get_upcoming_events(
        self,
        advisor_email: str,
        access_scope: AccessScope,
        *,
        days: int = 7,
        max_results: int = 50,
    ) -> list[CalendarEvent]:
        """Get calendar events for the next N days."""
        headers = await self._get_headers(access_scope)
        now = datetime.utcnow()
        end = datetime(now.year, now.month, now.day + days)  # simplified

        resp = await self._http.get(
            f"/v1.0/users/{advisor_email}/calendarView",
            headers=headers,
            params={
                "startDateTime": now.isoformat() + "Z",
                "endDateTime": end.isoformat() + "Z",
                "$top": str(max_results),
                "$select": "id,subject,start,end,location,attendees,organizer,isRecurring,bodyPreview",
                "$orderby": "start/dateTime",
            },
        )
        if resp.status_code >= 400:
            raise PlatformReadError(
                status_code=resp.status_code,
                error_code="CALENDAR_READ_ERROR",
                message=f"Graph API calendar error: {resp.text[:300]}",
            )

        data = resp.json()
        events: list[CalendarEvent] = []
        for evt in data.get("value", []):
            events.append(CalendarEvent(
                event_id=evt["id"],
                subject=evt.get("subject", ""),
                start=evt["start"]["dateTime"],
                end=evt["end"]["dateTime"],
                location=evt.get("location", {}).get("displayName"),
                attendees=[
                    a["emailAddress"]["address"]
                    for a in evt.get("attendees", [])
                ],
                organizer=evt.get("organizer", {}).get("emailAddress", {}).get("address"),
                is_recurring=evt.get("isRecurring", False),
                body_preview=evt.get("bodyPreview"),
            ))
        return events

    async def get_today_schedule(
        self,
        advisor_email: str,
        access_scope: AccessScope,
    ) -> list[CalendarEvent]:
        """Convenience: today's events only."""
        return await self.get_upcoming_events(
            advisor_email, access_scope, days=1,
        )

    async def close(self) -> None:
        await self._http.aclose()
```

### 9.4 Adapter Boundary Rules

| Rule | Rationale |
|---|---|
| Each adapter is a separate class | Different auth models (Graph OAuth vs platform service token) |
| No shared base class with PlatformClient | The platform client is the single approved path; adapters are separate approved paths for non-platform data |
| All adapters are read-only | The sidecar never sends emails, creates calendar events, or writes CRM records |
| All methods take AccessScope | Even non-platform reads must respect tenant and actor scope |
| CRM reads go through platform integration endpoints, not directly to Salesforce/Wealthbox | The platform owns CRM connectivity; the sidecar reads through the platform's unified surface |

---

## 10. Testing

### 10.1 MockPlatformClient

Agent unit tests must not make real HTTP calls to the platform API. The `MockPlatformClient` provides canned responses for all 12 methods.

```python
# tests/mocks/mock_platform_client.py

from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal
from typing import Any

from src.models.schemas import (
    AccessScope,
    AccountSummary,
    AccountStatus,
    AccountType,
    ClientProfile,
    ClientSummary,
    ContactInfo,
    DocumentCategory,
    DocumentMatch,
    DocumentMetadata,
    ExecutionProjection,
    FreshnessMeta,
    Holding,
    HouseholdSummary,
    OrderProjection,
    OrderStatus,
    ReportSnapshot,
    TimelineEvent,
    TimelineEventType,
    TransferCase,
    TransferStatus,
)
from src.services.errors import PlatformReadError


# ---------------------------------------------------------------------------
# Factory helpers for canned test data
# ---------------------------------------------------------------------------

def _freshness(source: str = "test") -> FreshnessMeta:
    return FreshnessMeta(
        as_of=datetime(2026, 3, 26, 12, 0, 0),
        source=source,
        staleness_seconds=0,
    )


def _account(
    account_id: str = "acc_001",
    household_id: str = "hh_001",
    client_id: str = "cl_001",
) -> AccountSummary:
    return AccountSummary(
        account_id=account_id,
        account_name="Test Brokerage",
        account_type=AccountType.INDIVIDUAL,
        status=AccountStatus.ACTIVE,
        custodian="schwab",
        total_value=Decimal("500000.00"),
        cash_balance=Decimal("25000.00"),
        holdings=[
            Holding(
                symbol="VTI",
                name="Vanguard Total Stock Market ETF",
                quantity=Decimal("500"),
                market_value=Decimal("125000.00"),
                cost_basis=Decimal("100000.00"),
                weight_pct=Decimal("25.00"),
                asset_class="US Equity",
            ),
        ],
        performance_ytd_pct=Decimal("8.5"),
        model_name="Growth 80/20",
        drift_pct=Decimal("2.1"),
        household_id=household_id,
        client_id=client_id,
        freshness=_freshness(),
    )


# ---------------------------------------------------------------------------
# Mock client
# ---------------------------------------------------------------------------

class MockPlatformClient:
    """Drop-in mock for PlatformClient in agent unit tests.

    Usage:
        mock = MockPlatformClient()
        mock.set_household("hh_001", custom_household_summary)
        agent = build_copilot_agent(platform=mock)
        result = await agent.run(...)

    By default, all methods return plausible canned data. Override
    specific resources with set_* methods or raise_* methods to
    simulate errors.
    """

    def __init__(self) -> None:
        self._households: dict[str, HouseholdSummary] = {}
        self._accounts: dict[str, AccountSummary] = {}
        self._clients: dict[str, ClientProfile] = {}
        self._transfers: dict[str, TransferCase] = {}
        self._orders: dict[str, OrderProjection] = {}
        self._executions: dict[str, ExecutionProjection] = {}
        self._reports: dict[str, ReportSnapshot] = {}
        self._documents: dict[str, DocumentMetadata] = {}
        self._timelines: dict[str, list[TimelineEvent]] = {}
        self._advisor_clients: dict[str, list[ClientSummary]] = {}
        self._errors: dict[str, PlatformReadError] = {}

    # -- Configuration methods -----------------------------------------------

    def set_household(self, household_id: str, summary: HouseholdSummary) -> None:
        self._households[household_id] = summary

    def set_account(self, account_id: str, summary: AccountSummary) -> None:
        self._accounts[account_id] = summary

    def set_client(self, client_id: str, profile: ClientProfile) -> None:
        self._clients[client_id] = profile

    def set_transfer(self, transfer_id: str, case: TransferCase) -> None:
        self._transfers[transfer_id] = case

    def set_error(self, resource_key: str, error: PlatformReadError) -> None:
        """Configure a specific resource to raise an error.

        resource_key is the method-specific ID, e.g. "household:hh_001".
        """
        self._errors[resource_key] = error

    def _check_error(self, key: str) -> None:
        if key in self._errors:
            raise self._errors[key]

    # -- Typed read methods used by agents and router tests ------------------

    async def get_household_summary(
        self, household_id: str, access_scope: AccessScope,
    ) -> HouseholdSummary:
        self._check_error(f"household:{household_id}")
        if household_id in self._households:
            return self._households[household_id]
        # Default canned response
        return HouseholdSummary(
            household_id=household_id,
            household_name="Test Household",
            primary_advisor_id="adv_001",
            accounts=[_account(household_id=household_id)],
            total_aum=Decimal("500000.00"),
            client_ids=["cl_001"],
            freshness=_freshness(),
        )

    async def get_account_summary(
        self, account_id: str, access_scope: AccessScope,
    ) -> AccountSummary:
        self._check_error(f"account:{account_id}")
        if account_id in self._accounts:
            return self._accounts[account_id]
        return _account(account_id=account_id)

    async def get_client_profile(
        self, client_id: str, access_scope: AccessScope,
    ) -> ClientProfile:
        self._check_error(f"client:{client_id}")
        if client_id in self._clients:
            return self._clients[client_id]
        return ClientProfile(
            client_id=client_id,
            first_name="Jane",
            last_name="Smith",
            date_of_birth=date(1965, 4, 15),
            household_id="hh_001",
            contact=ContactInfo(email="jane@example.com", phone="555-0100"),
            risk_tolerance="moderate",
            investment_objective="growth",
            account_ids=["acc_001"],
            freshness=_freshness(),
        )

    async def get_transfer_case(
        self, transfer_id: str, access_scope: AccessScope,
    ) -> TransferCase:
        self._check_error(f"transfer:{transfer_id}")
        if transfer_id in self._transfers:
            return self._transfers[transfer_id]
        return TransferCase(
            transfer_id=transfer_id,
            account_id="acc_001",
            direction="inbound",
            status=TransferStatus.IN_TRANSIT,
            transfer_type="ACAT",
            assets=[],
            estimated_value=Decimal("250000.00"),
            initiated_at=datetime(2026, 3, 20),
            updated_at=datetime(2026, 3, 25),
            expected_completion=date(2026, 4, 3),
            freshness=_freshness(),
        )

    async def get_order_projection(
        self, order_id: str, access_scope: AccessScope,
    ) -> OrderProjection:
        self._check_error(f"order:{order_id}")
        if order_id in self._orders:
            return self._orders[order_id]
        return OrderProjection(
            order_id=order_id,
            account_id="acc_001",
            symbol="VTI",
            side="buy",
            quantity=Decimal("100"),
            order_type="market",
            status=OrderStatus.FILLED,
            submitted_at=datetime(2026, 3, 25, 14, 30),
            filled_quantity=Decimal("100"),
            filled_avg_price=Decimal("250.50"),
            freshness=_freshness(),
        )

    async def get_execution_projection(
        self, execution_id: str, access_scope: AccessScope,
    ) -> ExecutionProjection:
        self._check_error(f"execution:{execution_id}")
        if execution_id in self._executions:
            return self._executions[execution_id]
        return ExecutionProjection(
            execution_id=execution_id,
            order_id="ord_001",
            account_id="acc_001",
            symbol="VTI",
            side="buy",
            quantity=Decimal("100"),
            price=Decimal("250.50"),
            executed_at=datetime(2026, 3, 25, 14, 31),
            settlement_date=date(2026, 3, 27),
            freshness=_freshness(),
        )

    async def get_report_snapshot(
        self, report_id: str, access_scope: AccessScope,
    ) -> ReportSnapshot:
        self._check_error(f"report:{report_id}")
        if report_id in self._reports:
            return self._reports[report_id]
        return ReportSnapshot(
            report_id=report_id,
            report_type="performance",
            title="Q1 2026 Performance Summary",
            generated_at=datetime(2026, 3, 26),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 3, 31),
            data={"total_return_pct": 8.5, "benchmark_return_pct": 7.2},
            freshness=_freshness(),
        )

    async def get_document_metadata(
        self, document_id: str, access_scope: AccessScope,
    ) -> DocumentMetadata:
        self._check_error(f"document:{document_id}")
        if document_id in self._documents:
            return self._documents[document_id]
        return DocumentMetadata(
            document_id=document_id,
            filename="2025_tax_return.pdf",
            category=DocumentCategory.TAX_RETURN,
            mime_type="application/pdf",
            size_bytes=245_000,
            uploaded_at=datetime(2026, 2, 15),
            client_id="cl_001",
            household_id="hh_001",
            freshness=_freshness(),
        )

    async def get_client_timeline(
        self, client_id: str, access_scope: AccessScope, days: int = 90,
    ) -> list[TimelineEvent]:
        self._check_error(f"timeline:{client_id}")
        if client_id in self._timelines:
            return self._timelines[client_id]
        return [
            TimelineEvent(
                event_id="evt_001",
                event_type=TimelineEventType.MEETING,
                timestamp=datetime(2026, 3, 20, 10, 0),
                title="Annual Review",
                summary="Discussed portfolio allocation and retirement timeline.",
                client_id=client_id,
                household_id="hh_001",
                actor_id="adv_001",
            ),
            TimelineEvent(
                event_id="evt_002",
                event_type=TimelineEventType.TRADE,
                timestamp=datetime(2026, 3, 22, 14, 30),
                title="Rebalance executed",
                summary="Sold AAPL, bought BND to reduce equity overweight.",
                client_id=client_id,
                household_id="hh_001",
            ),
        ]

    async def get_advisor_clients(
        self, advisor_id: str, access_scope: AccessScope,
    ) -> list[ClientSummary]:
        self._check_error(f"advisor_clients:{advisor_id}")
        if advisor_id in self._advisor_clients:
            return self._advisor_clients[advisor_id]
        return [
            ClientSummary(
                client_id="cl_001",
                first_name="Jane",
                last_name="Smith",
                household_id="hh_001",
                total_aum=Decimal("500000.00"),
                account_count=2,
                last_contact_date=date(2026, 3, 20),
            ),
        ]

    async def get_firm_accounts(
        self, filters: dict[str, Any], access_scope: AccessScope,
    ) -> list[AccountSummary]:
        self._check_error("firm_accounts")
        return [_account()]

    async def search_documents_text(
        self, query: str, filters: dict[str, Any], access_scope: AccessScope,
    ) -> list[DocumentMatch]:
        self._check_error("search_documents")
        return [
            DocumentMatch(
                document_id="doc_001",
                filename="2025_tax_return.pdf",
                category=DocumentCategory.TAX_RETURN,
                relevance_score=0.92,
                matched_excerpt="...capital gains of $45,000 from the sale of...",
                client_id="cl_001",
                household_id="hh_001",
                uploaded_at=datetime(2026, 2, 15),
            ),
        ]
```

### 10.2 Using the Mock in Tests

```python
# tests/agents/test_copilot.py

import pytest
from decimal import Decimal

from tests.mocks.mock_platform_client import MockPlatformClient
from src.models.schemas import AccessScope, HouseholdSummary, FreshnessMeta


@pytest.fixture
def access_scope() -> AccessScope:
    return AccessScope(
        tenant_id="tenant_001",
        actor_id="adv_001",
        actor_type="advisor",
        request_id="req_test_001",
        visibility_mode="scoped",
        household_ids=["hh_001"],
        advisor_ids=["adv_001"],
    )


@pytest.fixture
def platform() -> MockPlatformClient:
    return MockPlatformClient()


@pytest.mark.asyncio
async def test_get_household_returns_canned_data(
    platform: MockPlatformClient,
    access_scope: AccessScope,
) -> None:
    result = await platform.get_household_summary("hh_001", access_scope)
    assert result.household_id == "hh_001"
    assert result.total_aum == Decimal("500000.00")
    assert len(result.accounts) == 1


@pytest.mark.asyncio
async def test_get_household_with_custom_data(
    platform: MockPlatformClient,
    access_scope: AccessScope,
) -> None:
    custom = HouseholdSummary(
        household_id="hh_999",
        household_name="VIP Household",
        primary_advisor_id="adv_002",
        accounts=[],
        total_aum=Decimal("10000000.00"),
        client_ids=["cl_999"],
        freshness=FreshnessMeta(
            as_of="2026-03-26T12:00:00",
            source="test",
        ),
    )
    platform.set_household("hh_999", custom)

    result = await platform.get_household_summary("hh_999", access_scope)
    assert result.total_aum == Decimal("10000000.00")
    assert result.household_name == "VIP Household"


@pytest.mark.asyncio
async def test_platform_error_simulation(
    platform: MockPlatformClient,
    access_scope: AccessScope,
) -> None:
    from src.services.errors import PlatformReadError

    platform.set_error(
        "household:hh_404",
        PlatformReadError(
            status_code=404,
            error_code="NOT_FOUND",
            message="Household not found",
        ),
    )

    with pytest.raises(PlatformReadError) as exc_info:
        await platform.get_household_summary("hh_404", access_scope)

    assert exc_info.value.status_code == 404
    assert exc_info.value.error_code == "NOT_FOUND"
```

### 10.3 Integration Testing with httpx MockTransport

For tests that need to verify actual HTTP behavior (header propagation, timeout handling, circuit breaker transitions), use `httpx.MockTransport`:

```python
# tests/services/test_platform_client_integration.py

import httpx
import pytest
import json

from src.services.platform_client import PlatformClient, PlatformClientConfig
from src.models.schemas import AccessScope
from src.services.errors import PlatformReadError
from src.services.circuit_breaker import CircuitOpenError


def make_scope() -> AccessScope:
    return AccessScope(
        tenant_id="t_001",
        actor_id="a_001",
        actor_type="advisor",
        request_id="r_001",
        household_ids=["hh_001"],
    )


@pytest.mark.asyncio
async def test_scope_headers_are_sent() -> None:
    """Verify that every request includes identity and scope headers."""
    captured_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        return httpx.Response(
            200,
            json={
                "household_id": "hh_001",
                "household_name": "Test",
                "primary_advisor_id": "adv_001",
                "accounts": [],
                "total_aum": "500000.00",
                "client_ids": [],
                "freshness": {
                    "as_of": "2026-03-26T12:00:00",
                    "source": "test",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    config = PlatformClientConfig(
        base_url="http://platform:8000",
        service_token="test-token",
    )
    client = PlatformClient(config)
    client._http = httpx.AsyncClient(transport=transport, base_url="http://platform:8000")

    scope = make_scope()
    await client.get_household_summary("hh_001", scope)

    assert captured_headers["x-tenant-id"] == "t_001"
    assert captured_headers["x-actor-id"] == "a_001"
    assert captured_headers["x-request-id"] == "r_001"
    assert "x-access-scope" in captured_headers

    # Verify the scope is valid JSON containing the expected fields
    scope_json = json.loads(captured_headers["x-access-scope"])
    assert scope_json["tenant_id"] == "t_001"
    assert "hh_001" in scope_json["household_ids"]

    await client.close()


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_threshold() -> None:
    """Verify the circuit opens after N consecutive failures."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(500, json={"detail": "Internal error"})

    transport = httpx.MockTransport(handler)
    config = PlatformClientConfig(
        base_url="http://platform:8000",
        service_token="test-token",
        circuit_failure_threshold=3,
    )
    client = PlatformClient(config)
    client._http = httpx.AsyncClient(transport=transport, base_url="http://platform:8000")

    scope = make_scope()

    # First 3 calls hit the server and fail
    for _ in range(3):
        with pytest.raises(PlatformReadError):
            await client.get_household_summary("hh_001", scope)

    assert call_count == 3

    # 4th call should be rejected by circuit breaker without hitting server
    with pytest.raises(CircuitOpenError):
        await client.get_household_summary("hh_001", scope)

    assert call_count == 3  # no additional HTTP call

    await client.close()
```

---

## Summary

| Component | File | Purpose |
|---|---|---|
| `PlatformClient` | `app/services/platform_client.py` | typed read methods, httpx transport, scope headers, caching, circuit breaker |
| `AccessScope` | `app/models/schemas.py` | Pydantic model for platform-computed access scope |
| Response models | `app/models/schemas.py` | Pydantic models for platform read responses |
| `PlatformReadError` | `app/services/errors.py` | Classified exception for all platform API failures |
| `classify_platform_error` | `app/services/errors.py` | Maps HTTP status codes to error codes |
| `RetryPolicy` | `app/services/retry.py` | Exponential backoff for batch jobs only |
| `RequestScopedCache` | `app/services/request_cache.py` | Per-request in-memory cache |
| `CircuitBreaker` | `app/services/circuit_breaker.py` | Consecutive-failure circuit breaker |
| `EmailAdapter` | `app/tools/email_adapter.py` | Microsoft Graph API email reads |
| `CRMAdapter` | `app/tools/crm_adapter.py` | CRM reads via platform integration endpoints |
| `CalendarAdapter` | `app/tools/calendar_adapter.py` | Microsoft Graph API calendar reads |
| `MockPlatformClient` | `tests/mocks/mock_platform_client.py` | Canned responses for agent unit tests |

The platform client is intentionally narrow. If a new agent needs data that is not covered by these 12 methods, the correct response is to add a new typed method to this class and a corresponding platform API endpoint -- not to add a generic fetch or bypass the client.
