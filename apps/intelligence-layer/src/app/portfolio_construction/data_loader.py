"""DataLoader: typed platform reads with Decimal-to-float conversion and freshness checks."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


def _decimal_to_float(value: Any) -> Any:
    """Recursively convert Decimal values to float."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _decimal_to_float(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decimal_to_float(v) for v in value]
    return value


class DataLoader:
    """Wraps PlatformClient reads with Decimal-to-float conversion and freshness checking."""

    def __init__(self, platform: Any, access_scope: Any, settings: Any) -> None:
        self._platform = platform
        self._scope = access_scope
        self._settings = settings
        self.warnings: list[str] = []

    def _check_freshness(self, data: Any, label: str) -> None:
        """Check freshness metadata and emit warnings if stale."""
        items = data if isinstance(data, list) else [data]
        for item in items:
            freshness = None
            if isinstance(item, dict):
                freshness = item.get("freshness")
            elif hasattr(item, "freshness"):
                freshness = item.freshness

            if freshness is None:
                continue

            staleness = None
            if isinstance(freshness, dict):
                staleness = freshness.get("staleness_seconds")
            elif hasattr(freshness, "staleness_seconds"):
                staleness = freshness.staleness_seconds

            if staleness is not None and staleness > self._settings.portfolio_freshness_warn_s:
                self.warnings.append(
                    f"Stale {label} data: staleness {staleness}s exceeds "
                    f"freshness threshold {self._settings.portfolio_freshness_warn_s}s"
                )

    async def load_universe(self) -> list[dict]:
        """Load security universe and convert Decimal fields to float."""
        raw = await self._platform.get_security_universe(self._scope)
        self._check_freshness(raw, "universe")
        return [_decimal_to_float(item) if isinstance(item, dict) else _decimal_to_float(item.__dict__ if hasattr(item, '__dict__') else item) for item in raw]

    async def load_fundamentals(self, tickers: list[str]) -> list[dict]:
        """Load fundamentals and convert Decimal fields to float."""
        raw = await self._platform.bulk_fundamentals(tickers, self._scope)
        self._check_freshness(raw, "fundamentals")
        return [_decimal_to_float(item) if isinstance(item, dict) else _decimal_to_float(item.__dict__ if hasattr(item, '__dict__') else item) for item in raw]

    async def load_prices(self, tickers: list[str]) -> list[dict]:
        """Load price data and convert Decimal fields to float."""
        raw = await self._platform.bulk_price_data(tickers, self._scope)
        self._check_freshness(raw, "prices")
        return [_decimal_to_float(item) if isinstance(item, dict) else _decimal_to_float(item.__dict__ if hasattr(item, '__dict__') else item) for item in raw]
