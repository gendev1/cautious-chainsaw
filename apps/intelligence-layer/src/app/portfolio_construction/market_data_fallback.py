"""Market data fallback: yfinance dev-time fallback with field-by-field merge."""
from __future__ import annotations

from typing import Any


class MarketDataFallback:
    """Wraps yfinance calls for dev-time fallback when platform data has gaps."""

    def __init__(self, environment: str = "development") -> None:
        self._enabled = environment != "production"

    def merge(self, platform_data: dict[str, Any], fallback_data: dict[str, Any]) -> dict[str, Any]:
        """Merge platform and fallback data. Platform wins when present."""
        if not self._enabled:
            return platform_data

        merged = dict(platform_data)
        for key, value in fallback_data.items():
            if key not in merged or merged[key] is None:
                merged[key] = value
                merged.setdefault("_provenance", {})[key] = "yfinance"
            else:
                merged.setdefault("_provenance", {})[key] = "platform"
        return merged
