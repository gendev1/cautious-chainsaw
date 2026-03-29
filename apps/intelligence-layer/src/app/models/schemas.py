"""
app/models/schemas.py — Structured output types and request models.

All Pydantic AI agents declare a result_type from this module.
Field descriptions are visible to the LLM for schema-guided generation.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------

class Citation(BaseModel):
    source_type: str = Field(
        description="One of: document, email, crm_note, meeting_transcript, account_data"
    )
    source_id: str
    title: str
    excerpt: str = Field(description="Relevant excerpt from the source, max 200 chars")
    relevance_score: float = Field(ge=0.0, le=1.0)


class Action(BaseModel):
    type: str = Field(
        description=(
            "Action type: CREATE_REBALANCE_PROPOSAL, SCHEDULE_MEETING, "
            "DRAFT_EMAIL, CREATE_TASK, REVIEW_DOCUMENT, etc."
        )
    )
    target_id: str | None = None
    reason: str


class CRMSyncPayload(BaseModel):
    entity_type: str = Field(description="CRM entity: contact, activity, note, task")
    action: str = Field(description="sync_action: create, update")
    data: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    client_id: str | None = None
    household_id: str | None = None


# ---------------------------------------------------------------------------
# Copilot result
# ---------------------------------------------------------------------------

class HazelCopilot(BaseModel):
    """Structured response from the Hazel copilot agent."""

    answer: str = Field(
        description="Markdown-formatted response to the advisor's question"
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="Sources referenced in the answer",
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in the answer's accuracy",
    )
    as_of: str = Field(
        description="ISO 8601 timestamp of the freshest data used"
    )
    recommended_actions: list[Action] = Field(
        default_factory=list,
        description="Structured recommendations the advisor can act on",
    )
    follow_up_questions: list[str] = Field(
        default_factory=list,
        description="Suggested follow-up questions",
    )


# ---------------------------------------------------------------------------
# Daily digest result
# ---------------------------------------------------------------------------

class DigestItem(BaseModel):
    type: str = Field(
        description="One of: meeting, task, email, alert, crm_update"
    )
    title: str
    summary: str
    client_id: str | None = None
    urgency: str = Field(description="One of: high, medium, low")
    action_url: str | None = None


class DigestSection(BaseModel):
    title: str = Field(
        description="Section header: Today's Meetings, Pending Tasks, etc."
    )
    items: list[DigestItem]


class PriorityItem(BaseModel):
    title: str
    reason: str
    urgency: str


class DailyDigest(BaseModel):
    """Structured daily briefing for an advisor."""

    advisor_id: str
    generated_at: str
    greeting: str = Field(
        description="Personalized greeting for the advisor"
    )
    sections: list[DigestSection]
    priority_items: list[PriorityItem] = Field(
        description="Top 3-5 items requiring immediate attention"
    )
    suggested_actions: list[Action]


# ---------------------------------------------------------------------------
# Email results
# ---------------------------------------------------------------------------

class EmailDraft(BaseModel):
    """Structured email draft."""

    subject: str
    to: list[str]
    cc: list[str] = Field(default_factory=list)
    body: str = Field(description="Markdown-formatted email body")
    tone: str = Field(
        default="professional",
        description="One of: professional, friendly, formal",
    )
    client_id: str | None = None


class TriagedEmail(BaseModel):
    """Single triaged email with classification."""

    email_id: str
    subject: str
    sender: str
    urgency: str = Field(description="One of: high, medium, low")
    category: str = Field(
        description=(
            "One of: client_request, meeting_followup, compliance, "
            "marketing, internal, other"
        )
    )
    summary: str
    suggested_action: str
    client_id: str | None = None


# ---------------------------------------------------------------------------
# Task extraction result
# ---------------------------------------------------------------------------

class ExtractedTask(BaseModel):
    """Task extracted from a meeting or email."""

    title: str
    description: str
    assignee: str | None = None
    due_date: str | None = None
    priority: str = Field(description="One of: high, medium, low")
    source_type: str = Field(
        description="One of: meeting, email, crm_note"
    )
    source_id: str | None = None
    client_id: str | None = None


# ---------------------------------------------------------------------------
# Meeting results
# ---------------------------------------------------------------------------

class MeetingPrep(BaseModel):
    """Structured meeting preparation brief."""

    meeting_id: str | None = None
    client_id: str | None = None
    household_id: str | None = None
    agenda_summary: str
    client_context: str = Field(
        description="Summary of recent client activity and key facts"
    )
    portfolio_highlights: str = Field(
        description="Key portfolio metrics and recent changes"
    )
    talking_points: list[str]
    open_items: list[str] = Field(
        default_factory=list,
        description="Unresolved items from prior interactions",
    )
    suggested_questions: list[str] = Field(default_factory=list)


class TopicSection(BaseModel):
    topic: str
    summary: str
    speaker_attribution: dict[str, str] = Field(
        default_factory=dict,
        description="Speaker name to their key points",
    )
    decisions_made: list[str]


class MeetingSummary(BaseModel):
    """Structured meeting summary with action items."""

    meeting_id: str
    duration_minutes: int
    participants: list[str]
    executive_summary: str = Field(
        description="3-5 sentence executive summary of the meeting"
    )
    key_topics: list[TopicSection]
    action_items: list[ExtractedTask] = Field(default_factory=list)
    follow_up_drafts: list[EmailDraft] = Field(
        default_factory=list,
        description="Suggested follow-up email drafts",
    )
    client_sentiment: str | None = Field(
        default=None,
        description="One of: positive, neutral, concerned. Null if not determinable.",
    )
    next_steps: list[str]
    crm_sync_payloads: list[CRMSyncPayload] = Field(
        default_factory=list,
        description="CRM payloads for platform to execute",
    )


# ---------------------------------------------------------------------------
# Tax planning result
# ---------------------------------------------------------------------------

class TaxSituation(BaseModel):
    filing_status: str
    estimated_income: float
    estimated_tax_bracket: float
    capital_gains_summary: dict
    rmd_status: dict | None = None
    loss_harvesting_potential: float


class TaxOpportunity(BaseModel):
    type: str = Field(
        description=(
            "One of: tax_loss_harvest, roth_conversion, "
            "charitable_qcd, gain_deferral"
        )
    )
    description: str
    estimated_impact: float = Field(
        description="Estimated dollars saved or deferred"
    )
    confidence: str = Field(description="One of: high, medium, low")
    action: Action
    assumptions: list[str]


class TaxScenario(BaseModel):
    name: str = Field(
        description="Scenario label: Harvest all losses, Convert $50K to Roth"
    )
    inputs: dict
    projected_tax_liability: float
    compared_to_baseline: float = Field(
        description="Delta from baseline scenario"
    )
    trade_offs: list[str]


class TaxPlan(BaseModel):
    """Structured tax planning analysis."""

    client_id: str
    tax_year: int
    current_situation: TaxSituation
    opportunities: list[TaxOpportunity]
    scenarios: list[TaxScenario]
    warnings: list[str]
    disclaimer: str = Field(
        default=(
            "This is decision support, not tax advice. "
            "Consult a qualified tax professional before taking action."
        ),
        description="Always-present disclaimer",
    )


# ---------------------------------------------------------------------------
# Portfolio analysis result
# ---------------------------------------------------------------------------

class PortfolioAnalysis(BaseModel):
    """Structured portfolio analysis."""

    account_id: str | None = None
    household_id: str | None = None
    total_aum: float
    allocation_summary: dict = Field(
        description="Asset class to percentage mapping"
    )
    drift_from_model: float | None = None
    performance_ytd: float | None = None
    risk_metrics: dict = Field(default_factory=dict)
    recommendations: list[Action] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    as_of: str


# ---------------------------------------------------------------------------
# Firm-wide report result
# ---------------------------------------------------------------------------

class FirmWideReport(BaseModel):
    """Structured firm-wide analytical report."""

    tenant_id: str
    report_type: str = Field(
        description="One of: quarterly_review, aum_summary, client_activity"
    )
    period: str
    total_aum: float
    total_accounts: int
    total_households: int
    highlights: list[str]
    concerns: list[str] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    generated_at: str


# ---------------------------------------------------------------------------
# Document results
# ---------------------------------------------------------------------------

class DocClassification(BaseModel):
    """Document classification result."""

    document_id: str
    document_type: str = Field(
        description=(
            "One of: tax_return, estate_plan, trust_document, "
            "insurance_policy, statement, correspondence, other"
        )
    )
    confidence: float = Field(ge=0.0, le=1.0)
    entities_detected: list[str] = Field(
        default_factory=list,
        description="Named entities found in the document",
    )
    suggested_client_id: str | None = None
    suggested_household_id: str | None = None


class DocExtraction(BaseModel):
    """Structured data extracted from a document."""

    document_id: str
    document_type: str
    extracted_fields: dict = Field(
        description="Key-value pairs extracted from the document"
    )
    tables: list[dict] = Field(
        default_factory=list,
        description="Tabular data extracted from the document",
    )
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
