"""
app/models/platform_models.py — Pydantic response models for platform API reads.

All monetary values use Decimal (never float).
All data-bearing models include FreshnessMeta.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

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
# Freshness metadata
# ---------------------------------------------------------------------------


class FreshnessMeta(BaseModel):
    as_of: datetime
    source: str
    staleness_seconds: int | None = None


# ---------------------------------------------------------------------------
# Response models
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
    direction: str
    status: TransferStatus
    transfer_type: str
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
    side: str
    quantity: Decimal
    order_type: str
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
    report_type: str
    title: str
    generated_at: datetime
    period_start: date
    period_end: date
    data: dict
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
    related_ids: dict[str, str] = {}


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
