"""Tests for meeting summary transcript truncation."""
from __future__ import annotations

from app.jobs.meeting_summary import _truncate_transcript


def test_short_transcript_unchanged() -> None:
    """Transcript under limit is returned as-is."""
    text = "Short meeting about portfolio review."
    assert _truncate_transcript(text) == text


def test_long_transcript_truncated_with_marker() -> None:
    """Long transcript is truncated with head/tail and gap marker."""
    # 80K tokens * 4 chars = 320K chars. Make text larger.
    text = "A" * 400_000
    result = _truncate_transcript(text)
    assert len(result) < len(text)
    assert "characters omitted from middle" in result
    assert result.startswith("A")
    assert result.endswith("A")
