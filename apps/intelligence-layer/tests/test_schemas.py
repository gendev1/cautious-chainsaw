"""Tests for structured output result type models."""
from __future__ import annotations

from app.models.schemas import (
    Action,
    ChatRequest,
    Citation,
    DailyDigest,
    DigestItem,
    DigestSection,
    HazelCopilot,
    MeetingSummary,
    PriorityItem,
    TaxPlan,
    TaxSituation,
    TopicSection,
)


def test_hazel_copilot_validates() -> None:
    """T4: HazelCopilot model validates with all required fields."""
    result = HazelCopilot(
        answer="The AUM is $2.4M.",
        confidence=0.95,
        as_of="2026-03-28T08:00:00Z",
    )
    assert result.answer == "The AUM is $2.4M."
    assert result.confidence == 0.95
    assert result.citations == []
    assert result.recommended_actions == []
    assert result.follow_up_questions == []


def test_hazel_copilot_with_citations() -> None:
    """HazelCopilot validates with citations and actions."""
    result = HazelCopilot(
        answer="Test answer",
        citations=[
            Citation(
                source_type="account_data",
                source_id="acc_001",
                title="Test",
                excerpt="excerpt",
                relevance_score=0.9,
            )
        ],
        confidence=0.8,
        as_of="2026-03-28T08:00:00Z",
        recommended_actions=[
            Action(type="CREATE_TASK", reason="Follow up needed")
        ],
    )
    assert len(result.citations) == 1
    assert len(result.recommended_actions) == 1


def test_daily_digest_validates() -> None:
    """T5: DailyDigest model validates with sections and priority items."""
    digest = DailyDigest(
        advisor_id="adv_001",
        generated_at="2026-03-28T06:00:00Z",
        greeting="Good morning!",
        sections=[
            DigestSection(
                title="Today's Meetings",
                items=[
                    DigestItem(
                        type="meeting",
                        title="Smith Review",
                        summary="Quarterly portfolio review",
                        urgency="medium",
                    )
                ],
            )
        ],
        priority_items=[
            PriorityItem(
                title="RMD deadline",
                reason="Client Jones approaching RMD deadline",
                urgency="high",
            )
        ],
        suggested_actions=[],
    )
    assert digest.advisor_id == "adv_001"
    assert len(digest.sections) == 1
    assert len(digest.priority_items) == 1


def test_tax_plan_has_default_disclaimer() -> None:
    """T6: TaxPlan model includes default disclaimer."""
    plan = TaxPlan(
        client_id="cl_001",
        tax_year=2026,
        current_situation=TaxSituation(
            filing_status="married_joint",
            estimated_income=350000.0,
            estimated_tax_bracket=0.32,
            capital_gains_summary={},
            loss_harvesting_potential=15000.0,
        ),
        opportunities=[],
        scenarios=[],
        warnings=[],
    )
    assert "not tax advice" in plan.disclaimer.lower()


def test_chat_request_validates() -> None:
    """T15: ChatRequest model validates with required fields."""
    req = ChatRequest(message="What is the Smith AUM?")
    assert req.message == "What is the Smith AUM?"
    assert req.conversation_id is None
    assert req.client_id is None
    assert req.household_id is None


def test_meeting_summary_validates() -> None:
    """MeetingSummary validates with topics and action items."""
    summary = MeetingSummary(
        meeting_id="mtg_001",
        duration_minutes=45,
        participants=["John Smith", "Advisor"],
        executive_summary="Discussed portfolio allocation.",
        key_topics=[
            TopicSection(
                topic="Portfolio Review",
                summary="Reviewed current allocation",
                decisions_made=["Increase bond allocation"],
            )
        ],
        action_items=[],
        next_steps=["Schedule follow-up"],
    )
    assert summary.meeting_id == "mtg_001"
    assert len(summary.key_topics) == 1
