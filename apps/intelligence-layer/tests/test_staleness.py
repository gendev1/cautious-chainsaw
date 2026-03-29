"""Tests for data staleness checks."""
from __future__ import annotations

import datetime

from app.models.base import check_staleness


def test_recent_data_not_stale() -> None:
    """Data from 5 minutes ago is not stale."""
    recent = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)
    result = check_staleness(recent)
    assert result.is_stale is False


def test_old_data_is_stale() -> None:
    """Data from 2 hours ago is stale."""
    old = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)
    result = check_staleness(old)
    assert result.is_stale is True
    assert result.warning is not None
