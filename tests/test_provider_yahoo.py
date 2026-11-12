"""Tests for YahooFinanceProvider adapter using mocked responses (no live calls)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import pandas as pd
import pytest

from app.market_data.exceptions import (
    InvalidIntervalError,
    NoDataFoundError,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from app.market_data.types import PriceAdjustmentMode
from app.market_data.yahoo import YahooFinanceProvider


@pytest.fixture
def provider() -> YahooFinanceProvider:
    return YahooFinanceProvider()


def test_provider_contract_and_capabilities(provider: YahooFinanceProvider):
    assert provider.name == "YAHOO"
    caps = provider.capabilities
    assert caps.name == "YAHOO"
    assert "1d" in caps.supported_intervals
    assert "1m" in caps.supported_intervals
    assert caps.supports_adjusted_close is True
    assert caps.supports_unadjusted is True
    assert caps.is_realtime is False
    assert caps.is_exchange_grade is False

    assert provider.supports_interval("1d") is True
    assert provider.supports_interval("5m") is True
    assert provider.supports_interval("invalid_interval") is False


def test_symbol_resolution_india_and_us(provider: YahooFinanceProvider):
    # India NSE -> .NS
    assert provider.resolve_symbol("XNSE", "RELIANCE") == "RELIANCE.NS"
    assert provider.resolve_symbol("NSE", "TCS") == "TCS.NS"

    # India BSE -> .BO
    assert provider.resolve_symbol("XBOM", "RELIANCE") == "RELIANCE.BO"
    assert provider.resolve_symbol("BSE", "TCS") == "TCS.BO"

    # US NASDAQ and NYSE -> raw symbol (with dot-to-dash conversion for share classes)
    assert provider.resolve_symbol("XNAS", "AAPL") == "AAPL"
    assert provider.resolve_symbol("XNYS", "SPY") == "SPY"
    assert provider.resolve_symbol("XNYS", "BRK.A") == "BRK-A"


@pytest.mark.asyncio
async def test_fetch_historical_bars_mocked_success(provider: YahooFinanceProvider):
    inst_id = uuid.uuid4()
    # Create sample DataFrame
    idx = pd.date_range("2026-11-02", periods=3, freq="D", tz="UTC")
    data = {
        "Open": [150.0, 152.0, 151.0],
        "High": [155.0, 156.0, 154.0],
        "Low": [149.0, 150.5, 150.0],
        "Close": [153.0, 151.5, 153.5],
        "Volume": [1000000, 1200000, 1100000],
    }
    mock_df = pd.DataFrame(data, index=idx)

    with patch("yfinance.download", return_value=mock_df) as mock_download:
        bars = await provider.fetch_historical_bars(
            provider_symbol="AAPL",
            start=datetime(2026, 11, 1, tzinfo=UTC),
            end=datetime(2026, 11, 5, tzinfo=UTC),
            interval="1d",
            adjustment_mode=PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
            instrument_id=inst_id,
            currency="USD",
        )

        assert mock_download.called
        assert len(bars) == 3
        first_bar = bars[0]
        assert first_bar.instrument_id == inst_id
        assert isinstance(first_bar.open, Decimal)
        assert first_bar.open == Decimal("150.0")
        assert first_bar.high == Decimal("155.0")
        assert first_bar.volume == 1000000
        assert first_bar.currency == "USD"
        assert first_bar.provider == "YAHOO"
        assert first_bar.timestamp.tzinfo == UTC


@pytest.mark.asyncio
async def test_multiindex_dataframe_normalization(provider: YahooFinanceProvider):
    inst_id = uuid.uuid4()
    idx = pd.date_range("2026-11-02", periods=2, freq="D", tz="UTC")
    cols = pd.MultiIndex.from_tuples([
        ("Open", "AAPL"),
        ("High", "AAPL"),
        ("Low", "AAPL"),
        ("Close", "AAPL"),
        ("Volume", "AAPL"),
    ])
    mock_multi_df = pd.DataFrame(
        [[100.0, 105.0, 98.0, 103.0, 50000], [103.0, 107.0, 101.0, 106.0, 60000]],
        index=idx,
        columns=cols,
    )

    with patch("yfinance.download", return_value=mock_multi_df):
        bars = await provider.fetch_historical_bars(
            provider_symbol="AAPL",
            start=datetime(2026, 11, 1, tzinfo=UTC),
            end=datetime(2026, 11, 4, tzinfo=UTC),
            interval="1d",
            instrument_id=inst_id,
        )
        assert len(bars) == 2
        assert bars[0].close == Decimal("103.0")
        assert bars[1].close == Decimal("106.0")


@pytest.mark.asyncio
async def test_unadjusted_mode_preserves_adj_close(provider: YahooFinanceProvider):
    inst_id = uuid.uuid4()
    idx = pd.date_range("2026-11-02", periods=1, freq="D", tz="UTC")
    data = {
        "Open": [200.0],
        "High": [210.0],
        "Low": [195.0],
        "Close": [205.0],
        "Adj Close": [102.5],  # e.g. 2:1 split effect
        "Volume": [750000],
    }
    mock_df = pd.DataFrame(data, index=idx)

    with patch("yfinance.download", return_value=mock_df):
        bars = await provider.fetch_historical_bars(
            provider_symbol="RELIANCE.NS",
            start=datetime(2026, 11, 1, tzinfo=UTC),
            end=datetime(2026, 11, 3, tzinfo=UTC),
            interval="1d",
            adjustment_mode=PriceAdjustmentMode.RAW,
            instrument_id=inst_id,
            currency="INR",
        )
        assert len(bars) == 1
        bar = bars[0]
        assert bar.close == Decimal("205.0")
        assert bar.adjusted_close == Decimal("102.5")
        assert bar.adjustment_mode == PriceAdjustmentMode.RAW


@pytest.mark.asyncio
async def test_invalid_interval_raises(provider: YahooFinanceProvider):
    with pytest.raises(InvalidIntervalError):
        await provider.fetch_historical_bars(
            provider_symbol="AAPL",
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 10, tzinfo=UTC),
            interval="bad_interval",
        )


@pytest.mark.asyncio
async def test_empty_response_raises_no_data_found(provider: YahooFinanceProvider):
    with patch("yfinance.download", return_value=pd.DataFrame()):
        with pytest.raises(NoDataFoundError):
            await provider.fetch_historical_bars(
                provider_symbol="AAPL",
                start=datetime(2026, 1, 1, tzinfo=UTC),
                end=datetime(2026, 1, 10, tzinfo=UTC),
                interval="1d",
            )


@pytest.mark.asyncio
async def test_rate_limit_error_normalization(provider: YahooFinanceProvider):
    with patch("yfinance.download", side_effect=Exception("HTTP 429 Too Many Requests")):
        with pytest.raises(ProviderRateLimitError) as exc_info:
            await provider.fetch_historical_bars(
                provider_symbol="AAPL",
                start=datetime(2026, 1, 1, tzinfo=UTC),
                end=datetime(2026, 1, 10, tzinfo=UTC),
                interval="1d",
            )
        assert exc_info.value.is_retryable is True
        assert exc_info.value.retry_after == 60.0


@pytest.mark.asyncio
async def test_timeout_and_connection_error_normalization(provider: YahooFinanceProvider):
    with patch("yfinance.download", side_effect=TimeoutError("Request timed out")):
        with pytest.raises(ProviderTimeoutError) as exc_info:
            await provider.fetch_historical_bars(
                provider_symbol="AAPL",
                start=datetime(2026, 1, 1, tzinfo=UTC),
                end=datetime(2026, 1, 10, tzinfo=UTC),
                interval="1d",
            )
        assert exc_info.value.is_retryable is True

    conn_err = ConnectionError("Failed to establish a new connection")
    with patch("yfinance.download", side_effect=conn_err):
        with pytest.raises(ProviderConnectionError) as exc_info:
            await provider.fetch_historical_bars(
                provider_symbol="AAPL",
                start=datetime(2026, 1, 1, tzinfo=UTC),
                end=datetime(2026, 1, 10, tzinfo=UTC),
                interval="1d",
            )
        assert exc_info.value.is_retryable is True
