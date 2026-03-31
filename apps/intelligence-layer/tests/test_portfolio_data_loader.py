"""Tests for portfolio construction DataLoader."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.portfolio_construction.data_loader import DataLoader


# ---------------------------------------------------------------------------
# Synthetic platform data helpers
# ---------------------------------------------------------------------------


def _make_security_snapshot(ticker: str, sector: str = "Technology", market_cap: Decimal = Decimal("50000000000")) -> dict:
    """Build a synthetic SecuritySnapshot-like dict with Decimal fields."""
    return {
        "ticker": ticker,
        "name": f"{ticker} Corp",
        "sector": sector,
        "industry": "Software",
        "market_cap": market_cap,
        "description": f"{ticker} is a leading company.",
        "tags": ["large_cap"],
        "freshness": {
            "as_of": datetime(2026, 3, 28, 12, 0, 0),
            "source": "platform",
            "staleness_seconds": 100,
        },
    }


def _make_fundamentals(ticker: str, pe_ratio: Decimal = Decimal("25.5")) -> dict:
    """Build a synthetic FundamentalsV2-like dict with Decimal fields."""
    return {
        "ticker": ticker,
        "pe_ratio": pe_ratio,
        "pb_ratio": Decimal("5.2"),
        "roe": Decimal("0.25"),
        "roa": Decimal("0.12"),
        "debt_to_equity": Decimal("0.80"),
        "revenue_growth": Decimal("0.15"),
        "earnings_growth": Decimal("0.18"),
        "dividend_yield": Decimal("0.01"),
        "rnd_intensity": Decimal("0.10"),
        "free_cash_flow_yield": Decimal("0.04"),
        "current_ratio": Decimal("1.5"),
        "gross_margin": Decimal("0.65"),
        "operating_margin": Decimal("0.30"),
        "net_margin": Decimal("0.22"),
        "freshness": {
            "as_of": datetime(2026, 3, 28, 12, 0, 0),
            "source": "platform",
            "staleness_seconds": 200,
        },
    }


def _make_price_data(ticker: str, staleness: int = 100) -> dict:
    """Build a synthetic PriceDataV2-like dict with Decimal fields."""
    return {
        "ticker": ticker,
        "prices": [
            {"date": "2026-03-27", "close": Decimal("150.00"), "volume": 1_000_000},
            {"date": "2026-03-26", "close": Decimal("148.50"), "volume": 900_000},
        ],
        "realized_vol_1y": Decimal("0.25"),
        "beta": Decimal("1.10"),
        "momentum_3m": Decimal("0.08"),
        "momentum_6m": Decimal("0.15"),
        "momentum_12m": Decimal("0.22"),
        "freshness": {
            "as_of": datetime(2026, 3, 28, 12, 0, 0),
            "source": "platform",
            "staleness_seconds": staleness,
        },
    }


def _build_mock_platform(
    tickers: list[str] | None = None,
    staleness_seconds: int = 100,
) -> MagicMock:
    """Build a mock PlatformClient with canned data."""
    tickers = tickers or ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]
    platform = MagicMock()

    platform.get_security_universe = AsyncMock(
        return_value=[_make_security_snapshot(t) for t in tickers],
    )
    platform.bulk_fundamentals = AsyncMock(
        return_value=[_make_fundamentals(t) for t in tickers],
    )
    platform.bulk_price_data = AsyncMock(
        return_value=[_make_price_data(t, staleness_seconds) for t in tickers],
    )
    return platform


def _build_mock_settings(freshness_warn_s: int = 86400) -> MagicMock:
    """Build a mock Settings object."""
    settings = MagicMock()
    settings.portfolio_freshness_warn_s = freshness_warn_s
    return settings


def _build_mock_access_scope() -> MagicMock:
    """Build a mock AccessScope."""
    scope = MagicMock()
    scope.fingerprint.return_value = "test_fingerprint"
    return scope


# ---------------------------------------------------------------------------
# Tests: Data loading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_universe_returns_data() -> None:
    """DataLoader.load_universe returns security snapshots."""
    platform = _build_mock_platform()
    settings = _build_mock_settings()
    scope = _build_mock_access_scope()

    loader = DataLoader(platform=platform, access_scope=scope, settings=settings)
    result = await loader.load_universe()

    assert len(result) == 5
    platform.get_security_universe.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_fundamentals_returns_data() -> None:
    """DataLoader.load_fundamentals returns fundamentals for requested tickers."""
    platform = _build_mock_platform()
    settings = _build_mock_settings()
    scope = _build_mock_access_scope()

    loader = DataLoader(platform=platform, access_scope=scope, settings=settings)
    result = await loader.load_fundamentals(["AAPL", "MSFT"])

    platform.bulk_fundamentals.assert_awaited_once()
    assert len(result) >= 1


@pytest.mark.asyncio
async def test_load_prices_returns_data() -> None:
    """DataLoader.load_prices returns price data."""
    platform = _build_mock_platform()
    settings = _build_mock_settings()
    scope = _build_mock_access_scope()

    loader = DataLoader(platform=platform, access_scope=scope, settings=settings)
    result = await loader.load_prices(["AAPL", "MSFT"])

    platform.bulk_price_data.assert_awaited_once()
    assert len(result) >= 1


# ---------------------------------------------------------------------------
# Tests: Decimal-to-float conversion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decimal_to_float_conversion_fundamentals() -> None:
    """Fundamentals Decimal fields are converted to float."""
    platform = _build_mock_platform(tickers=["AAPL"])
    settings = _build_mock_settings()
    scope = _build_mock_access_scope()

    loader = DataLoader(platform=platform, access_scope=scope, settings=settings)
    result = await loader.load_fundamentals(["AAPL"])

    # At least one entry should exist
    assert len(result) > 0
    entry = result[0] if isinstance(result, list) else list(result.values())[0]
    # Verify float conversion -- pe_ratio was Decimal("25.5")
    pe = entry.get("pe_ratio") if isinstance(entry, dict) else getattr(entry, "pe_ratio", None)
    assert isinstance(pe, float), f"Expected float, got {type(pe)}"
    assert pe == 25.5


@pytest.mark.asyncio
async def test_decimal_to_float_conversion_prices() -> None:
    """Price data Decimal fields are converted to float."""
    platform = _build_mock_platform(tickers=["MSFT"])
    settings = _build_mock_settings()
    scope = _build_mock_access_scope()

    loader = DataLoader(platform=platform, access_scope=scope, settings=settings)
    result = await loader.load_prices(["MSFT"])

    assert len(result) > 0
    entry = result[0] if isinstance(result, list) else list(result.values())[0]
    beta = entry.get("beta") if isinstance(entry, dict) else getattr(entry, "beta", None)
    assert isinstance(beta, float), f"Expected float, got {type(beta)}"
    assert beta == 1.10


@pytest.mark.asyncio
async def test_decimal_to_float_conversion_market_cap() -> None:
    """Security snapshot market_cap is converted to float."""
    platform = _build_mock_platform(tickers=["NVDA"])
    settings = _build_mock_settings()
    scope = _build_mock_access_scope()

    loader = DataLoader(platform=platform, access_scope=scope, settings=settings)
    result = await loader.load_universe()

    assert len(result) > 0
    entry = result[0] if isinstance(result, list) else list(result.values())[0]
    mc = entry.get("market_cap") if isinstance(entry, dict) else getattr(entry, "market_cap", None)
    assert isinstance(mc, float), f"Expected float, got {type(mc)}"


# ---------------------------------------------------------------------------
# Tests: Freshness warnings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_freshness_warning_when_stale() -> None:
    """DataLoader emits a warning when staleness exceeds threshold."""
    # Set staleness above threshold
    platform = _build_mock_platform(staleness_seconds=100_000)
    settings = _build_mock_settings(freshness_warn_s=86_400)  # 1 day
    scope = _build_mock_access_scope()

    loader = DataLoader(platform=platform, access_scope=scope, settings=settings)
    await loader.load_prices(["AAPL"])

    assert len(loader.warnings) > 0, "Expected freshness warning for stale data"
    assert any("stale" in w.lower() or "fresh" in w.lower() for w in loader.warnings)


@pytest.mark.asyncio
async def test_no_freshness_warning_when_fresh() -> None:
    """DataLoader does not warn when data is fresh."""
    platform = _build_mock_platform(staleness_seconds=100)
    settings = _build_mock_settings(freshness_warn_s=86_400)
    scope = _build_mock_access_scope()

    loader = DataLoader(platform=platform, access_scope=scope, settings=settings)
    await loader.load_prices(["AAPL"])

    stale_warnings = [w for w in loader.warnings if "stale" in w.lower() or "fresh" in w.lower()]
    assert len(stale_warnings) == 0, "No freshness warning expected for fresh data"


@pytest.mark.asyncio
async def test_freshness_warning_accumulates() -> None:
    """Warnings accumulate across multiple loads."""
    platform = _build_mock_platform(staleness_seconds=100_000)
    settings = _build_mock_settings(freshness_warn_s=86_400)
    scope = _build_mock_access_scope()

    loader = DataLoader(platform=platform, access_scope=scope, settings=settings)
    await loader.load_fundamentals(["AAPL"])
    await loader.load_prices(["AAPL"])

    assert len(loader.warnings) >= 1, "Expected at least one freshness warning"


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_universe_propagates_platform_error() -> None:
    """Platform errors propagate through DataLoader."""
    platform = MagicMock()
    platform.get_security_universe = AsyncMock(
        side_effect=Exception("Platform unavailable"),
    )
    settings = _build_mock_settings()
    scope = _build_mock_access_scope()

    loader = DataLoader(platform=platform, access_scope=scope, settings=settings)

    with pytest.raises(Exception, match="Platform unavailable"):
        await loader.load_universe()
