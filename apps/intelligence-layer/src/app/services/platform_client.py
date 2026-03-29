"""
app/services/platform_client.py — Typed read-only client for the platform API.

No generic fetch. No mutation methods. Each public method is typed
end-to-end with a named platform endpoint and Pydantic return type.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel

from app.errors import (
    PlatformReadError,
    classify_platform_error,
)
from app.models.access_scope import AccessScope
from app.models.platform_models import (
    AccountAlert,
    AccountSummary,
    BenchmarkData,
    CalendarEvent,
    ClientProfile,
    ClientSummary,
    CRMActivity,
    DocumentMatch,
    DocumentMetadata,
    EmailThread,
    ExecutionProjection,
    HoldingSummary,
    HouseholdSummary,
    OrderProjection,
    PriorityEmail,
    RealizedGainsSummary,
    ReportSnapshot,
    TaskSummary,
    TeamMember,
    TimelineEvent,
    TransferCase,
)
from app.services.circuit_breaker import CircuitBreaker
from app.services.request_cache import RequestScopedCache

logger = logging.getLogger("sidecar.platform_client")

DEFAULT_TIMEOUT_S = 3.0
MAX_CONNECTIONS = 20
MAX_KEEPALIVE = 10


class PlatformClientConfig(BaseModel):
    base_url: str
    service_token: str
    timeout_s: float = DEFAULT_TIMEOUT_S
    circuit_failure_threshold: int = 5
    circuit_recovery_timeout_s: float = 30.0


class PlatformClient:
    """Narrow typed client for platform API reads."""

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
            timeout=httpx.Timeout(
                config.timeout_s, connect=2.0
            ),
            limits=httpx.Limits(
                max_connections=MAX_CONNECTIONS,
                max_keepalive_connections=MAX_KEEPALIVE,
            ),
            headers={
                "Authorization": (
                    f"Bearer {config.service_token}"
                ),
                "User-Agent": "sidecar/1.0",
            },
        )

    async def close(self) -> None:
        await self._http.aclose()

    # -- helpers ---------------------------------------------------

    def _scope_headers(
        self,
        access_scope: AccessScope,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        headers["X-Tenant-ID"] = access_scope.tenant_id
        headers["X-Actor-ID"] = access_scope.actor_id
        if access_scope.request_id:
            headers["X-Request-ID"] = (
                access_scope.request_id
            )
        headers["X-Access-Scope"] = (
            access_scope.model_dump_json()
        )
        return headers

    def _cache_key(
        self, method: str, **kwargs: Any
    ) -> str:
        raw = json.dumps(
            {"m": method, **kwargs},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    async def _get(
        self,
        path: str,
        *,
        access_scope: AccessScope,
        params: dict[str, Any] | None = None,
        cache_key: str | None = None,
    ) -> httpx.Response:
        if cache_key and self._cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        self._circuit.check()

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
                message=(
                    f"Platform API timeout on {path}: "
                    f"{exc}"
                ),
            ) from exc
        except httpx.HTTPError as exc:
            self._circuit.record_failure()
            raise PlatformReadError(
                status_code=0,
                error_code="CONNECTION_ERROR",
                message=(
                    f"Platform API connection error on "
                    f"{path}: {exc}"
                ),
            ) from exc

        if response.status_code >= 400:
            self._circuit.record_failure()
            raise classify_platform_error(response)

        self._circuit.record_success()
        if cache_key and self._cache:
            self._cache.set(cache_key, response)

        return response

    # -- typed read methods ----------------------------------------

    async def get_household_summary(
        self,
        household_id: str,
        access_scope: AccessScope,
    ) -> HouseholdSummary:
        key = self._cache_key(
            "household_summary",
            hid=household_id,
            scope=access_scope.fingerprint(),
        )
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
        key = self._cache_key(
            "account_summary",
            aid=account_id,
            scope=access_scope.fingerprint(),
        )
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
        key = self._cache_key(
            "client_profile",
            cid=client_id,
            scope=access_scope.fingerprint(),
        )
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
        key = self._cache_key(
            "transfer_case",
            tid=transfer_id,
            scope=access_scope.fingerprint(),
        )
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
        key = self._cache_key(
            "order_projection",
            oid=order_id,
            scope=access_scope.fingerprint(),
        )
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
        key = self._cache_key(
            "execution_projection",
            eid=execution_id,
            scope=access_scope.fingerprint(),
        )
        resp = await self._get(
            f"/v1/executions/{execution_id}/projection",
            access_scope=access_scope,
            cache_key=key,
        )
        return ExecutionProjection.model_validate(
            resp.json()
        )

    async def get_report_snapshot(
        self,
        report_id: str,
        access_scope: AccessScope,
    ) -> ReportSnapshot:
        key = self._cache_key(
            "report_snapshot",
            rid=report_id,
            scope=access_scope.fingerprint(),
        )
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
        key = self._cache_key(
            "document_metadata",
            did=document_id,
            scope=access_scope.fingerprint(),
        )
        resp = await self._get(
            f"/v1/documents/{document_id}/metadata",
            access_scope=access_scope,
            cache_key=key,
        )
        return DocumentMetadata.model_validate(
            resp.json()
        )

    async def get_client_timeline(
        self,
        client_id: str,
        access_scope: AccessScope,
        days: int = 90,
    ) -> list[TimelineEvent]:
        key = self._cache_key(
            "client_timeline",
            cid=client_id,
            days=days,
            scope=access_scope.fingerprint(),
        )
        resp = await self._get(
            f"/v1/clients/{client_id}/timeline",
            access_scope=access_scope,
            params={"days": days},
            cache_key=key,
        )
        return [
            TimelineEvent.model_validate(item)
            for item in resp.json()
        ]

    async def get_advisor_clients(
        self,
        advisor_id: str,
        access_scope: AccessScope,
    ) -> list[ClientSummary]:
        key = self._cache_key(
            "advisor_clients",
            adv=advisor_id,
            scope=access_scope.fingerprint(),
        )
        resp = await self._get(
            f"/v1/advisors/{advisor_id}/clients",
            access_scope=access_scope,
            cache_key=key,
        )
        return [
            ClientSummary.model_validate(item)
            for item in resp.json()
        ]

    async def get_firm_accounts(
        self,
        filters: dict[str, Any],
        access_scope: AccessScope,
    ) -> list[AccountSummary]:
        key = self._cache_key(
            "firm_accounts",
            filters=filters,
            scope=access_scope.fingerprint(),
        )
        resp = await self._get(
            "/v1/accounts",
            access_scope=access_scope,
            params=filters,
            cache_key=key,
        )
        return [
            AccountSummary.model_validate(item)
            for item in resp.json()
        ]

    async def search_documents_text(
        self,
        query: str,
        filters: dict[str, Any],
        access_scope: AccessScope,
    ) -> list[DocumentMatch]:
        key = self._cache_key(
            "search_documents_text",
            q=query,
            filters=filters,
            scope=access_scope.fingerprint(),
        )
        resp = await self._get(
            "/v1/documents/search",
            access_scope=access_scope,
            params={"q": query, **filters},
            cache_key=key,
        )
        return [
            DocumentMatch.model_validate(item)
            for item in resp.json()
        ]

    async def get_document_content(
        self,
        document_id: str,
        access_scope: AccessScope,
    ) -> str:
        key = self._cache_key(
            "document_content",
            did=document_id,
            scope=access_scope.fingerprint(),
        )
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
            params={
                "include_cost_basis": include_cost_basis
            },
            cache_key=key,
        )
        return [
            HoldingSummary.model_validate(item)
            for item in resp.json()
        ]

    async def get_client_realized_gains(
        self,
        client_id: str,
        access_scope: AccessScope,
        *,
        tax_year: int,
    ) -> RealizedGainsSummary:
        key = self._cache_key(
            "client_realized_gains",
            cid=client_id,
            tax_year=tax_year,
            scope=access_scope.fingerprint(),
        )
        resp = await self._get(
            f"/v1/clients/{client_id}/realized-gains",
            access_scope=access_scope,
            params={"tax_year": tax_year},
            cache_key=key,
        )
        return RealizedGainsSummary.model_validate(
            resp.json()
        )

    async def get_client_accounts(
        self,
        client_id: str,
        access_scope: AccessScope,
        *,
        account_ids: list[str] | None = None,
    ) -> list[AccountSummary]:
        key = self._cache_key(
            "client_accounts",
            cid=client_id,
            account_ids=account_ids,
            scope=access_scope.fingerprint(),
        )
        params: dict[str, Any] = {}
        if account_ids:
            params["account_ids"] = ",".join(account_ids)
        resp = await self._get(
            f"/v1/clients/{client_id}/accounts",
            access_scope=access_scope,
            params=params or None,
            cache_key=key,
        )
        return [
            AccountSummary.model_validate(item)
            for item in resp.json()
        ]

    async def get_benchmark_data(
        self,
        access_scope: AccessScope,
    ) -> BenchmarkData:
        key = self._cache_key(
            "benchmark_data",
            scope=access_scope.fingerprint(),
        )
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
        key = self._cache_key(
            "advisor_calendar",
            advisor_id=advisor_id,
            scope=access_scope.fingerprint(),
        )
        resp = await self._get(
            f"/v1/advisors/{advisor_id}/calendar",
            access_scope=access_scope,
            cache_key=key,
        )
        return [
            CalendarEvent.model_validate(item)
            for item in resp.json()
        ]

    async def get_advisor_tasks(
        self,
        advisor_id: str,
        access_scope: AccessScope,
    ) -> list[TaskSummary]:
        key = self._cache_key(
            "advisor_tasks",
            advisor_id=advisor_id,
            scope=access_scope.fingerprint(),
        )
        resp = await self._get(
            f"/v1/advisors/{advisor_id}/tasks",
            access_scope=access_scope,
            cache_key=key,
        )
        return [
            TaskSummary.model_validate(item)
            for item in resp.json()
        ]

    async def get_advisor_priority_emails(
        self,
        advisor_id: str,
        access_scope: AccessScope,
    ) -> list[PriorityEmail]:
        key = self._cache_key(
            "advisor_priority_emails",
            advisor_id=advisor_id,
            scope=access_scope.fingerprint(),
        )
        resp = await self._get(
            f"/v1/advisors/{advisor_id}/emails/priority",
            access_scope=access_scope,
            cache_key=key,
        )
        return [
            PriorityEmail.model_validate(item)
            for item in resp.json()
        ]

    async def get_account_alerts(
        self,
        advisor_id: str,
        access_scope: AccessScope,
    ) -> list[AccountAlert]:
        key = self._cache_key(
            "account_alerts",
            advisor_id=advisor_id,
            scope=access_scope.fingerprint(),
        )
        resp = await self._get(
            f"/v1/advisors/{advisor_id}/account-alerts",
            access_scope=access_scope,
            cache_key=key,
        )
        return [
            AccountAlert.model_validate(item)
            for item in resp.json()
        ]

    async def get_email_thread(
        self,
        email_id: str,
        access_scope: AccessScope,
    ) -> EmailThread:
        key = self._cache_key(
            "email_thread",
            email_id=email_id,
            scope=access_scope.fingerprint(),
        )
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
        key = self._cache_key(
            "advisor_team",
            advisor_id=advisor_id,
            scope=access_scope.fingerprint(),
        )
        resp = await self._get(
            f"/v1/advisors/{advisor_id}/team",
            access_scope=access_scope,
            cache_key=key,
        )
        return [
            TeamMember.model_validate(item)
            for item in resp.json()
        ]

    async def get_crm_activity_feed(
        self,
        advisor_id: str,
        access_scope: AccessScope,
    ) -> list[CRMActivity]:
        key = self._cache_key(
            "crm_activity_feed",
            advisor_id=advisor_id,
            scope=access_scope.fingerprint(),
        )
        resp = await self._get(
            f"/v1/advisors/{advisor_id}/crm-activity",
            access_scope=access_scope,
            cache_key=key,
        )
        return [
            CRMActivity.model_validate(item)
            for item in resp.json()
        ]

    # -- admin / sweep methods ---------------------------------

    async def list_active_tenants(
        self,
    ) -> list[dict[str, Any]]:
        """List all active tenants. Used by cron sweep
        jobs to fan out per-advisor work.
        """
        resp = await self._http.get(
            "/v1/admin/tenants",
            params={"status": "active"},
            headers={
                "Authorization": (
                    f"Bearer {self._config.service_token}"
                ),
                "User-Agent": "sidecar/1.0",
            },
        )
        if resp.status_code >= 400:
            from app.errors import classify_platform_error

            raise classify_platform_error(resp)
        self._circuit.record_success()
        return resp.json()

    async def list_advisors(
        self,
        tenant_id: str,
    ) -> list[dict[str, Any]]:
        """List all advisors in a tenant."""
        resp = await self._http.get(
            f"/v1/admin/tenants/{tenant_id}/advisors",
            headers={
                "Authorization": (
                    f"Bearer {self._config.service_token}"
                ),
                "User-Agent": "sidecar/1.0",
                "X-Tenant-ID": tenant_id,
            },
        )
        if resp.status_code >= 400:
            from app.errors import classify_platform_error

            raise classify_platform_error(resp)
        self._circuit.record_success()
        return resp.json()

    async def get_meeting_metadata(
        self,
        meeting_id: str,
        access_scope: AccessScope,
    ) -> dict[str, Any]:
        """Fetch meeting metadata (participants, duration,
        date).
        """
        key = self._cache_key(
            "meeting_metadata",
            mid=meeting_id,
            scope=access_scope.fingerprint(),
        )
        resp = await self._get(
            f"/v1/meetings/{meeting_id}/metadata",
            access_scope=access_scope,
            cache_key=key,
        )
        return resp.json()
