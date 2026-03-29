"""
tests/mocks/mock_platform_client.py — Drop-in mock for PlatformClient.

Provides canned responses for all typed read methods.
Override with set_*() or inject errors with set_error().
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.errors import PlatformReadError
from app.models.access_scope import AccessScope
from app.models.platform_models import (
    AccountStatus,
    AccountSummary,
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

# ---------------------------------------------------------------------------
# Factory helpers
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
    """Drop-in mock for PlatformClient in agent unit tests."""

    def __init__(self) -> None:
        self._households: dict[str, HouseholdSummary] = {}
        self._accounts: dict[str, AccountSummary] = {}
        self._clients: dict[str, ClientProfile] = {}
        self._transfers: dict[str, TransferCase] = {}
        self._orders: dict[str, OrderProjection] = {}
        self._executions: dict[
            str, ExecutionProjection
        ] = {}
        self._reports: dict[str, ReportSnapshot] = {}
        self._documents: dict[str, DocumentMetadata] = {}
        self._timelines: dict[
            str, list[TimelineEvent]
        ] = {}
        self._advisor_clients: dict[
            str, list[ClientSummary]
        ] = {}
        self._errors: dict[str, PlatformReadError] = {}

    # -- Configuration -----------------------------------------

    def set_household(
        self,
        household_id: str,
        summary: HouseholdSummary,
    ) -> None:
        self._households[household_id] = summary

    def set_account(
        self,
        account_id: str,
        summary: AccountSummary,
    ) -> None:
        self._accounts[account_id] = summary

    def set_client(
        self, client_id: str, profile: ClientProfile
    ) -> None:
        self._clients[client_id] = profile

    def set_transfer(
        self, transfer_id: str, case: TransferCase
    ) -> None:
        self._transfers[transfer_id] = case

    def set_error(
        self,
        resource_key: str,
        error: PlatformReadError,
    ) -> None:
        self._errors[resource_key] = error

    def _check_error(self, key: str) -> None:
        if key in self._errors:
            raise self._errors[key]

    # -- Typed read methods ------------------------------------

    async def get_household_summary(
        self,
        household_id: str,
        access_scope: AccessScope,
    ) -> HouseholdSummary:
        self._check_error(f"household:{household_id}")
        if household_id in self._households:
            return self._households[household_id]
        return HouseholdSummary(
            household_id=household_id,
            household_name="Test Household",
            primary_advisor_id="adv_001",
            accounts=[
                _account(household_id=household_id)
            ],
            total_aum=Decimal("500000.00"),
            client_ids=["cl_001"],
            freshness=_freshness(),
        )

    async def get_account_summary(
        self,
        account_id: str,
        access_scope: AccessScope,
    ) -> AccountSummary:
        self._check_error(f"account:{account_id}")
        if account_id in self._accounts:
            return self._accounts[account_id]
        return _account(account_id=account_id)

    async def get_client_profile(
        self,
        client_id: str,
        access_scope: AccessScope,
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
            contact=ContactInfo(
                email="jane@example.com",
                phone="555-0100",
            ),
            risk_tolerance="moderate",
            investment_objective="growth",
            account_ids=["acc_001"],
            freshness=_freshness(),
        )

    async def get_transfer_case(
        self,
        transfer_id: str,
        access_scope: AccessScope,
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
        self,
        order_id: str,
        access_scope: AccessScope,
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
        self,
        execution_id: str,
        access_scope: AccessScope,
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
        self,
        report_id: str,
        access_scope: AccessScope,
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
            data={
                "total_return_pct": 8.5,
                "benchmark_return_pct": 7.2,
            },
            freshness=_freshness(),
        )

    async def get_document_metadata(
        self,
        document_id: str,
        access_scope: AccessScope,
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
        self,
        client_id: str,
        access_scope: AccessScope,
        days: int = 90,
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
                summary=(
                    "Discussed portfolio allocation "
                    "and retirement timeline."
                ),
                client_id=client_id,
                household_id="hh_001",
                actor_id="adv_001",
            ),
        ]

    async def get_advisor_clients(
        self,
        advisor_id: str,
        access_scope: AccessScope,
    ) -> list[ClientSummary]:
        self._check_error(
            f"advisor_clients:{advisor_id}"
        )
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
        self,
        filters: dict[str, Any],
        access_scope: AccessScope,
    ) -> list[AccountSummary]:
        self._check_error("firm_accounts")
        return [_account()]

    async def search_documents_text(
        self,
        query: str,
        filters: dict[str, Any],
        access_scope: AccessScope,
    ) -> list[DocumentMatch]:
        self._check_error("search_documents")
        return [
            DocumentMatch(
                document_id="doc_001",
                filename="2025_tax_return.pdf",
                category=DocumentCategory.TAX_RETURN,
                relevance_score=0.92,
                matched_excerpt=(
                    "...capital gains of $45,000 "
                    "from the sale of..."
                ),
                client_id="cl_001",
                household_id="hh_001",
                uploaded_at=datetime(2026, 2, 15),
            ),
        ]
