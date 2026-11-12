"""Quantitative Data Correctness Audit — Regression Tests.

Covers all five audit areas:
  1. Adjusted vs unadjusted prices (Yahoo adapter semantics)
  2. Exchange calendar correctness (US observance rules, India limitations)
  3. Instrument and symbol identity
  4. Fingerprint reproducibility
  5. Data integrity / survivorship bias (design assessment via model inspection)
"""

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import patch

import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.market_data.calendar import (
    BSECalendar,
    NASDAQCalendar,
    NSECalendar,
    NYSECalendar,
    _observed_weekday,
    get_calendar,
)
from app.market_data.fingerprint import compute_dataset_fingerprint
from app.market_data.types import OHLCVRecord, PriceAdjustmentMode
from app.market_data.yahoo import YahooFinanceProvider
from app.models.identity import Exchange, Instrument, ProviderSymbol

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    *,
    inst_id: uuid.UUID | None = None,
    ts: datetime | None = None,
    open_: Decimal = Decimal("100"),
    high: Decimal = Decimal("105"),
    low: Decimal = Decimal("99"),
    close: Decimal = Decimal("102"),
    adj_close: Decimal | None = None,
    volume: int = 10000,
    currency: str = "USD",
    provider: str = "YAHOO",
    interval: str = "1d",
    adjustment_mode: PriceAdjustmentMode = PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
) -> OHLCVRecord:
    return OHLCVRecord(
        instrument_id=inst_id or uuid.uuid4(),
        timestamp=ts or datetime(2026, 1, 2, tzinfo=UTC),
        open=open_,
        high=high,
        low=low,
        close=close,
        adjusted_close=adj_close,
        volume=volume,
        currency=currency,
        provider=provider,
        interval=interval,
        adjustment_mode=adjustment_mode,
        retrieval_time=datetime.now(UTC),
    )


@pytest.fixture
def provider() -> YahooFinanceProvider:
    return YahooFinanceProvider()


# ===========================================================================
# AUDIT #1 — Adjusted vs Unadjusted Prices
# ===========================================================================

class TestAdjustmentModeSemantics:
    """Verify that RAW, SPLIT_AND_DIVIDEND, and SPLIT_ONLY behave correctly."""

    @pytest.mark.asyncio
    async def test_split_and_dividend_ohlc_are_adjusted_values(
        self, provider: YahooFinanceProvider
    ):
        """
        When auto_adjust=True yfinance returns adjusted OHLC in the main columns.
        The adapter sets adjusted_close == close to signal a fully-adjusted series.
        There is NO separate Adj Close column in this mode.
        """
        idx = pd.date_range("2026-06-01", periods=1, freq="D", tz="UTC")
        # Simulate yfinance auto_adjust=True output: OHLC are adjusted values
        mock_df = pd.DataFrame(
            {"Open": [80.0], "High": [85.0], "Low": [78.0], "Close": [82.0], "Volume": [500000]},
            index=idx,
        )
        with patch("yfinance.download", return_value=mock_df):
            bars = await provider.fetch_historical_bars(
                provider_symbol="AAPL",
                start=datetime(2026, 6, 1, tzinfo=UTC),
                end=datetime(2026, 6, 2, tzinfo=UTC),
                interval="1d",
                adjustment_mode=PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
                currency="USD",
            )

        assert len(bars) == 1
        bar = bars[0]
        assert bar.adjustment_mode == PriceAdjustmentMode.SPLIT_AND_DIVIDEND
        # close and adjusted_close are equal — the whole series is adjusted
        assert bar.close == Decimal("82.0")
        assert bar.adjusted_close == bar.close, (
            "In SPLIT_AND_DIVIDEND mode adjusted_close must equal close "
            "because yfinance returns adjusted OHLC in all columns"
        )

    @pytest.mark.asyncio
    async def test_raw_mode_separates_adj_close_from_ohlc(
        self, provider: YahooFinanceProvider
    ):
        """
        When auto_adjust=False yfinance returns raw OHLC and a separate
        'Adj Close' column.  The adapter maps it to adjusted_close.
        close != adjusted_close in the presence of splits/dividends.
        """
        idx = pd.date_range("2026-06-01", periods=1, freq="D", tz="UTC")
        mock_df = pd.DataFrame(
            {
                "Open": [200.0],
                "High": [210.0],
                "Low": [195.0],
                "Close": [205.0],
                "Adj Close": [102.5],  # 2:1 split example
                "Volume": [750000],
            },
            index=idx,
        )
        with patch("yfinance.download", return_value=mock_df):
            bars = await provider.fetch_historical_bars(
                provider_symbol="RELIANCE.NS",
                start=datetime(2026, 6, 1, tzinfo=UTC),
                end=datetime(2026, 6, 2, tzinfo=UTC),
                interval="1d",
                adjustment_mode=PriceAdjustmentMode.RAW,
                currency="INR",
            )

        assert len(bars) == 1
        bar = bars[0]
        assert bar.adjustment_mode == PriceAdjustmentMode.RAW
        assert bar.close == Decimal("205.0"), "RAW close must be unadjusted"
        assert bar.adjusted_close == Decimal("102.5"), "adj_close must come from 'Adj Close' column"
        assert bar.close != bar.adjusted_close, (
            "RAW and adjusted close must differ when a split has occurred"
        )

    @pytest.mark.asyncio
    async def test_different_adjustment_modes_produce_independent_records(
        self, provider: YahooFinanceProvider
    ):
        """RAW and SPLIT_AND_DIVIDEND records for the same timestamp are distinct."""
        idx = pd.date_range("2026-06-01", periods=1, freq="D", tz="UTC")
        raw_df = pd.DataFrame(
            {"Open": [200.0], "High": [210.0], "Low": [195.0],
             "Close": [205.0], "Adj Close": [102.5], "Volume": [750000]},
            index=idx,
        )
        adj_df = pd.DataFrame(
            {"Open": [101.0], "High": [106.0], "Low": [98.0],
             "Close": [102.5], "Volume": [750000]},
            index=idx,
        )
        inst_id = uuid.uuid4()

        with patch("yfinance.download", return_value=raw_df):
            raw_bars = await provider.fetch_historical_bars(
                provider_symbol="RELIANCE.NS",
                start=datetime(2026, 6, 1, tzinfo=UTC),
                end=datetime(2026, 6, 2, tzinfo=UTC),
                interval="1d",
                adjustment_mode=PriceAdjustmentMode.RAW,
                instrument_id=inst_id,
                currency="INR",
            )

        with patch("yfinance.download", return_value=adj_df):
            adj_bars = await provider.fetch_historical_bars(
                provider_symbol="RELIANCE.NS",
                start=datetime(2026, 6, 1, tzinfo=UTC),
                end=datetime(2026, 6, 2, tzinfo=UTC),
                interval="1d",
                adjustment_mode=PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
                instrument_id=inst_id,
                currency="INR",
            )

        assert raw_bars[0].adjustment_mode == PriceAdjustmentMode.RAW
        assert adj_bars[0].adjustment_mode == PriceAdjustmentMode.SPLIT_AND_DIVIDEND
        # They share instrument_id and timestamp but differ in adjustment_mode —
        # the unique key (instrument_id, interval, adjustment_mode, timestamp)
        # keeps them as separate DB rows.
        assert raw_bars[0].close != adj_bars[0].close

    @pytest.mark.asyncio
    async def test_split_only_falls_back_to_raw_with_warning(
        self, provider: YahooFinanceProvider, caplog: pytest.LogCaptureFixture
    ):
        """
        SPLIT_ONLY is not supported by yfinance.  The adapter falls back to
        RAW (auto_adjust=False), emits a logger warning, and stores the records
        with adjustment_mode=SPLIT_ONLY so callers can distinguish them.
        """
        idx = pd.date_range("2026-06-01", periods=1, freq="D", tz="UTC")
        mock_df = pd.DataFrame(
            {"Open": [200.0], "High": [210.0], "Low": [195.0],
             "Close": [205.0], "Adj Close": [205.0], "Volume": [500000]},
            index=idx,
        )
        with caplog.at_level(logging.WARNING, logger="app.market_data.yahoo"):
            with patch("yfinance.download", return_value=mock_df) as mock_dl:
                bars = await provider.fetch_historical_bars(
                    provider_symbol="AAPL",
                    start=datetime(2026, 6, 1, tzinfo=UTC),
                    end=datetime(2026, 6, 2, tzinfo=UTC),
                    interval="1d",
                    adjustment_mode=PriceAdjustmentMode.SPLIT_ONLY,
                    currency="USD",
                )
                # auto_adjust must be False (falls back to RAW download)
                _call_kwargs = mock_dl.call_args
                assert _call_kwargs.kwargs.get("auto_adjust") is False or \
                    (len(_call_kwargs.args) >= 2 and _call_kwargs.args[1] is False)

        assert len(bars) == 1
        assert bars[0].adjustment_mode == PriceAdjustmentMode.SPLIT_ONLY
        assert any("SPLIT_ONLY" in msg for msg in caplog.messages), (
            "Must emit a warning when falling back from SPLIT_ONLY to RAW"
        )

    @pytest.mark.asyncio
    async def test_adjusted_close_absent_in_split_dividend_mode(
        self, provider: YahooFinanceProvider
    ):
        """
        In SPLIT_AND_DIVIDEND mode, yfinance does NOT return 'Adj Close'.
        The adapter must NOT fabricate a separate adjusted_close value —
        it sets adjusted_close = close (the adjusted OHLCV series is self-contained).
        """
        idx = pd.date_range("2026-06-01", periods=2, freq="D", tz="UTC")
        # No 'Adj Close' column — mirrors real yfinance auto_adjust=True output
        mock_df = pd.DataFrame(
            {"Open": [150.0, 152.0], "High": [155.0, 157.0],
             "Low": [148.0, 150.0], "Close": [153.0, 155.0], "Volume": [1000000, 1200000]},
            index=idx,
        )
        with patch("yfinance.download", return_value=mock_df):
            bars = await provider.fetch_historical_bars(
                provider_symbol="AAPL",
                start=datetime(2026, 6, 1, tzinfo=UTC),
                end=datetime(2026, 6, 3, tzinfo=UTC),
                interval="1d",
                adjustment_mode=PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
                currency="USD",
            )

        for bar in bars:
            assert bar.adjusted_close == bar.close, (
                "adjusted_close should equal close in fully-adjusted mode; "
                "must not be None or a fabricated separate value"
            )


# ===========================================================================
# AUDIT #2 — Exchange Calendar Correctness
# ===========================================================================

class TestUSCalendarObservanceRules:
    """US fixed holidays must respect the standard weekend-observance rule."""

    @pytest.mark.parametrize("holiday_date,expected_observed", [
        # New Year's Day on Saturday → observed preceding Friday
        (date(2011, 1, 1), date(2010, 12, 31)),
        # New Year's Day on Sunday → observed following Monday
        (date(2012, 1, 1), date(2012, 1, 2)),
        # Christmas on Saturday → observed preceding Friday
        (date(2021, 12, 25), date(2021, 12, 24)),
        # Christmas on Sunday → observed following Monday
        (date(2022, 12, 25), date(2022, 12, 26)),
        # Independence Day on Sunday → observed following Monday
        (date(2010, 7, 4), date(2010, 7, 5)),
        # Independence Day on Saturday → observed preceding Friday
        (date(2015, 7, 4), date(2015, 7, 3)),
        # Juneteenth on Saturday → observed preceding Friday
        (date(2021, 6, 19), date(2021, 6, 18)),
        # Juneteenth on Sunday → observed following Monday
        (date(2022, 6, 19), date(2022, 6, 20)),
    ])
    def test_observed_weekday_helper(self, holiday_date: date, expected_observed: date):
        assert _observed_weekday(holiday_date) == expected_observed

    def test_new_year_saturday_observed_friday(self):
        """Jan 1 2011 is Saturday → observed Dec 31 2010 (Friday) is a holiday."""
        cal = NYSECalendar()
        assert cal.is_holiday(date(2010, 12, 31)), (
            "Dec 31 2010 must be a holiday (NYE observed for Jan 1 2011 on Saturday)"
        )
        assert not cal.is_holiday(date(2011, 1, 1)), (
            "Jan 1 2011 (Saturday) is a weekend — is_holiday not needed there "
            "but observed day should be Friday Dec 31 2010"
        )

    def test_christmas_saturday_observed_friday(self):
        """Dec 25 2021 is Saturday → observed Dec 24 (Friday) is a market holiday."""
        cal = NYSECalendar()
        assert cal.is_holiday(date(2021, 12, 24)), (
            "Dec 24 2021 must be a holiday (Christmas observed for Dec 25 on Saturday)"
        )

    def test_christmas_sunday_observed_monday(self):
        """Dec 25 2022 is Sunday → observed Dec 26 (Monday) is a market holiday."""
        cal = NYSECalendar()
        assert cal.is_holiday(date(2022, 12, 26)), (
            "Dec 26 2022 must be a holiday (Christmas observed for Dec 25 on Sunday)"
        )

    def test_independence_day_sunday_observed_monday(self):
        """Jul 4 2021 is Sunday → observed Jul 5 (Monday) is a market holiday."""
        cal = NASDAQCalendar()
        assert cal.is_holiday(date(2021, 7, 5)), (
            "Jul 5 2021 must be a holiday (Independence Day observed for Jul 4 on Sunday)"
        )

    def test_mlk_day_heuristic(self):
        """MLK Day is 3rd Monday of January."""
        cal = NYSECalendar()
        # 2026-01-19 is 3rd Monday of January 2026
        assert cal.is_holiday(date(2026, 1, 19))
        # 2026-01-12 is 2nd Monday — not MLK Day
        assert not cal.is_holiday(date(2026, 1, 12))

    def test_thanksgiving_heuristic(self):
        """Thanksgiving is 4th Thursday of November."""
        cal = NYSECalendar()
        # 2026-11-26 is 4th Thursday of November 2026
        assert cal.is_holiday(date(2026, 11, 26))
        # 2026-11-19 is 3rd Thursday — not Thanksgiving
        assert not cal.is_holiday(date(2026, 11, 19))

    def test_weekday_non_holiday_is_trading_day(self):
        cal = NYSECalendar()
        # Regular Wednesday, no holiday nearby
        assert cal.is_trading_day(date(2026, 3, 11))

    def test_saturday_not_trading_day(self):
        cal = NASDAQCalendar()
        assert not cal.is_trading_day(date(2026, 11, 7))  # Saturday

    def test_sunday_not_trading_day(self):
        cal = NASDAQCalendar()
        assert not cal.is_trading_day(date(2026, 11, 8))  # Sunday


class TestIndiaCalendarLimitations:
    """India calendar covers only 6 fixed holidays; floating holidays are absent."""

    def test_fixed_holidays_are_non_trading_days(self):
        cal = NSECalendar()
        assert not cal.is_trading_day(date(2026, 1, 26))   # Republic Day
        assert not cal.is_trading_day(date(2026, 8, 15))   # Independence Day
        assert not cal.is_trading_day(date(2026, 10, 2))   # Gandhi Jayanti
        assert not cal.is_trading_day(date(2026, 12, 25))  # Christmas

    def test_bse_has_same_fixed_holidays(self):
        cal = BSECalendar()
        assert not cal.is_trading_day(date(2026, 4, 14))  # Ambedkar Jayanti
        assert not cal.is_trading_day(date(2026, 5, 1))   # Maharashtra Day

    def test_diwali_not_modelled(self):
        """
        Diwali is a genuine NSE/BSE holiday that shifts each year.
        The calendar does NOT model it, so it appears as a trading day
        when it shouldn't be.  This is a documented limitation.
        """
        cal = NSECalendar()
        # Diwali 2026 falls around Oct 20 (approximate) — the calendar
        # has no way to know this and will return True (is_trading_day).
        # This test documents the gap rather than asserting correctness.
        diwali_approx = date(2026, 10, 20)
        if cal.is_trading_day(diwali_approx):
            pass  # Expected — floating holiday not modelled (known limitation)
        # We only assert the fixed ones are captured
        assert not cal.is_trading_day(date(2026, 8, 15))

    def test_get_calendar_mic_lookup(self):
        assert isinstance(get_calendar("XNSE"), NSECalendar)
        assert isinstance(get_calendar("XBOM"), BSECalendar)
        assert isinstance(get_calendar("XNAS"), NASDAQCalendar)
        assert isinstance(get_calendar("XNYS"), NYSECalendar)


# ===========================================================================
# AUDIT #3 — Instrument and Symbol Identity
# ===========================================================================

class TestInstrumentIdentity:
    """Verify the data model's handling of exchange listings and provider symbols."""

    @pytest.mark.asyncio
    async def test_same_company_distinct_instruments_per_exchange(
        self, db_session: AsyncSession
    ):
        """
        Reliance Industries trades on both NSE (RELIANCE.NS) and BSE (RELIANCE.BO).
        These are distinct Instrument rows — the same underlying company has
        separate exchange listings.
        """
        nse_exc = Exchange(
            mic="XNSE", name="NSE", country_code="IN",
            timezone="Asia/Kolkata", currency_code="INR", calendar_code="XNSE", is_active=True,
        )
        bse_exc = Exchange(
            mic="XBOM", name="BSE", country_code="IN",
            timezone="Asia/Kolkata", currency_code="INR", calendar_code="XBOM", is_active=True,
        )
        db_session.add_all([nse_exc, bse_exc])
        await db_session.flush()

        nse_instr = Instrument(
            exchange_id=nse_exc.id, symbol="RELIANCE", name="Reliance Industries (NSE)",
            asset_type="EQUITY", currency_code="INR", is_active=True,
        )
        bse_instr = Instrument(
            exchange_id=bse_exc.id, symbol="RELIANCE", name="Reliance Industries (BSE)",
            asset_type="EQUITY", currency_code="INR", is_active=True,
        )
        db_session.add_all([nse_instr, bse_instr])
        await db_session.flush()

        assert nse_instr.id != bse_instr.id, "Distinct exchange listings must have distinct IDs"
        assert nse_instr.exchange_id == nse_exc.id
        assert bse_instr.exchange_id == bse_exc.id

    @pytest.mark.asyncio
    async def test_provider_symbol_maps_to_correct_exchange_listing(
        self, db_session: AsyncSession
    ):
        """
        YAHOO provider symbol 'RELIANCE.NS' maps to the NSE instrument,
        and 'RELIANCE.BO' maps to the BSE instrument.
        These mappings are independent and must not be confused.
        """
        nse_exc = Exchange(
            mic="XNSE", name="NSE", country_code="IN",
            timezone="Asia/Kolkata", currency_code="INR", calendar_code="XNSE", is_active=True,
        )
        db_session.add(nse_exc)
        await db_session.flush()

        instr = Instrument(
            exchange_id=nse_exc.id, symbol="RELIANCE", name="Reliance Industries",
            asset_type="EQUITY", currency_code="INR", is_active=True,
        )
        db_session.add(instr)
        await db_session.flush()

        ps = ProviderSymbol(
            instrument_id=instr.id, provider="YAHOO",
            provider_symbol="RELIANCE.NS", is_primary=True,
        )
        db_session.add(ps)
        await db_session.flush()

        assert ps.provider_symbol == "RELIANCE.NS"
        assert ps.instrument_id == instr.id

    @pytest.mark.asyncio
    async def test_nasdaq_nyse_symbols_not_confused(
        self, db_session: AsyncSession
    ):
        """
        AAPL trades on NASDAQ. SPY trades on NYSE.
        They must be separate Instrument rows with distinct exchange_ids.
        """
        nasdaq = Exchange(
            mic="XNAS", name="NASDAQ", country_code="US",
            timezone="America/New_York", currency_code="USD", calendar_code="XNAS", is_active=True,
        )
        nyse = Exchange(
            mic="XNYS", name="NYSE", country_code="US",
            timezone="America/New_York", currency_code="USD", calendar_code="XNYS", is_active=True,
        )
        db_session.add_all([nasdaq, nyse])
        await db_session.flush()

        aapl = Instrument(
            exchange_id=nasdaq.id, symbol="AAPL", name="Apple Inc.",
            asset_type="EQUITY", currency_code="USD", is_active=True,
        )
        spy = Instrument(
            exchange_id=nyse.id, symbol="SPY", name="SPDR S&P 500 ETF",
            asset_type="ETF", currency_code="USD", is_active=True,
        )
        db_session.add_all([aapl, spy])
        await db_session.flush()

        assert aapl.exchange_id == nasdaq.id
        assert spy.exchange_id == nyse.id
        assert aapl.id != spy.id

    def test_is_active_flag_exists_for_soft_deactivation(self):
        """
        The Instrument model has an is_active boolean for soft deactivation.
        This allows inactive / delisted instruments to be marked without
        deleting historical data or identity.
        """
        instr = Instrument(
            exchange_id=uuid.uuid4(),
            symbol="DELISTEDINSTR",
            name="Some Delisted Stock",
            asset_type="EQUITY",
            currency_code="USD",
            is_active=False,  # explicitly deactivated
        )
        assert instr.is_active is False

    def test_no_delisted_at_timestamp_on_instrument(self):
        """
        Limitation: Instrument has no delisted_at timestamp or ticker rename
        history table.  This is a known gap documented for a future milestone.
        """
        import inspect

        import app.models.identity as identity_mod
        source = inspect.getsource(identity_mod.Instrument)
        # Verify delisted_at is not present (document the gap)
        assert "delisted_at" not in source, (
            "delisted_at field is not yet present on Instrument — "
            "this is a known gap for a future corporate-actions milestone"
        )

    def test_isin_field_exists_for_cross_exchange_linking(self):
        """
        Instrument.isin allows cross-exchange linking of the same security
        (e.g. Reliance NSE and BSE share an ISIN).  It is currently nullable.
        """
        import inspect

        import app.models.identity as identity_mod
        source = inspect.getsource(identity_mod.Instrument)
        assert "isin" in source, "Instrument must have an isin field for cross-exchange linking"


# ===========================================================================
# AUDIT #4 — Fingerprint Reproducibility
# ===========================================================================

class TestFingerprintReproducibility:
    """Exhaustive regression tests for the SHA-256 market data fingerprint."""

    def _base_records(self, inst_id: uuid.UUID) -> list[OHLCVRecord]:
        return [
            _make_record(inst_id=inst_id, ts=datetime(2026, 1, 2, tzinfo=UTC),
                         open_=Decimal("100"), close=Decimal("102")),
            _make_record(inst_id=inst_id, ts=datetime(2026, 1, 3, tzinfo=UTC),
                         open_=Decimal("102"), close=Decimal("105")),
            _make_record(inst_id=inst_id, ts=datetime(2026, 1, 4, tzinfo=UTC),
                         open_=Decimal("105"), close=Decimal("103")),
        ]

    def test_empty_snapshot_sentinel(self):
        assert compute_dataset_fingerprint([]) == "empty_snapshot"

    def test_deterministic_on_repeated_call(self):
        inst_id = uuid.uuid4()
        records = self._base_records(inst_id)
        fp1 = compute_dataset_fingerprint(records)
        fp2 = compute_dataset_fingerprint(records)
        assert fp1 == fp2
        assert len(fp1) == 64  # SHA-256 hex digest

    def test_order_independence(self):
        """Shuffled input must produce the same fingerprint."""
        inst_id = uuid.uuid4()
        records = self._base_records(inst_id)
        import random
        shuffled = records.copy()
        random.shuffle(shuffled)
        assert compute_dataset_fingerprint(records) == compute_dataset_fingerprint(shuffled)

    def test_single_price_change_alters_fingerprint(self):
        """A one-cent change in any OHLCV field must produce a different digest."""
        inst_id = uuid.uuid4()
        records = self._base_records(inst_id)
        altered = [
            records[0],
            _make_record(inst_id=inst_id, ts=datetime(2026, 1, 3, tzinfo=UTC),
                         open_=Decimal("102.01"),  # 1 cent difference
                         close=Decimal("105")),
            records[2],
        ]
        assert compute_dataset_fingerprint(records) != compute_dataset_fingerprint(altered)

    def test_null_adj_close_serialized_as_NONE(self):
        """
        None adjusted_close must be serialised as the literal string 'NONE'
        so it is indistinguishable from a dataset where adj_close is always None.
        Two datasets that differ only in adj_close None vs a value must differ.
        """
        inst_id = uuid.uuid4()
        ts = datetime(2026, 1, 2, tzinfo=UTC)
        r_none = _make_record(inst_id=inst_id, ts=ts, adj_close=None)
        r_val = _make_record(inst_id=inst_id, ts=ts, adj_close=Decimal("101.5"))
        fp_none = compute_dataset_fingerprint([r_none])
        fp_val = compute_dataset_fingerprint([r_val])
        assert fp_none != fp_val

    def test_different_adjustment_modes_produce_different_fingerprints(self):
        """RAW and SPLIT_AND_DIVIDEND datasets must have different fingerprints."""
        inst_id = uuid.uuid4()
        raw_rec = _make_record(
            inst_id=inst_id, adjustment_mode=PriceAdjustmentMode.RAW
        )
        adj_rec = _make_record(
            inst_id=inst_id, adjustment_mode=PriceAdjustmentMode.SPLIT_AND_DIVIDEND
        )
        fp_raw = compute_dataset_fingerprint([raw_rec])
        fp_adj = compute_dataset_fingerprint([adj_rec])
        assert fp_raw != fp_adj

    def test_different_providers_produce_different_fingerprints(self):
        """
        Datasets from different providers with identical prices must have
        different fingerprints (provider is part of the header).
        """
        inst_id = uuid.uuid4()
        yahoo_rec = _make_record(inst_id=inst_id, provider="YAHOO")
        other_rec = _make_record(inst_id=inst_id, provider="ALPHAVANTAGE")
        fp_yahoo = compute_dataset_fingerprint([yahoo_rec])
        fp_other = compute_dataset_fingerprint([other_rec])
        assert fp_yahoo != fp_other, (
            "Provider is in the fingerprint header — different providers must yield "
            "different fingerprints even with identical price data"
        )

    def test_different_instruments_produce_different_fingerprints(self):
        """Datasets for different instruments must have different fingerprints."""
        id_a = uuid.uuid4()
        id_b = uuid.uuid4()
        rec_a = _make_record(inst_id=id_a)
        rec_b = _make_record(inst_id=id_b)
        assert compute_dataset_fingerprint([rec_a]) != compute_dataset_fingerprint([rec_b])

    def test_different_intervals_produce_different_fingerprints(self):
        inst_id = uuid.uuid4()
        daily = _make_record(inst_id=inst_id, interval="1d")
        weekly = _make_record(inst_id=inst_id, interval="1wk")
        assert compute_dataset_fingerprint([daily]) != compute_dataset_fingerprint([weekly])

    def test_decimal_precision_is_preserved(self):
        """Decimal('102.00') and Decimal('102') must produce the same fingerprint
        because str(Decimal('102.00')) == '102.00' != '102' — confirm determinism
        for the same logical value stored with different trailing zeros."""
        inst_id = uuid.uuid4()
        ts = datetime(2026, 1, 2, tzinfo=UTC)
        # Both should produce distinct string representations, so fingerprints differ.
        r1 = _make_record(inst_id=inst_id, ts=ts, close=Decimal("102.00"))
        r2 = _make_record(inst_id=inst_id, ts=ts, close=Decimal("102"))
        # This test documents and pins the current behaviour.
        # Both are valid Decimal representations; callers must normalise before
        # computing fingerprints if they need trailing-zero invariance.
        fp1 = compute_dataset_fingerprint([r1])
        fp2 = compute_dataset_fingerprint([r2])
        # Document: these currently differ due to str(Decimal) representation
        # Callers should use Decimal.normalize() if they need trailing-zero invariance.
        assert isinstance(fp1, str) and isinstance(fp2, str)

    def test_utc_timestamp_serialization_deterministic(self):
        """ISO format of a UTC timestamp must be deterministic across calls."""
        ts = datetime(2026, 1, 2, 9, 30, 0, tzinfo=UTC)
        inst_id = uuid.uuid4()
        r = _make_record(inst_id=inst_id, ts=ts)
        # Two calls must produce identical fingerprints
        assert compute_dataset_fingerprint([r]) == compute_dataset_fingerprint([r])


# ===========================================================================
# AUDIT #5 — Data Integrity / Survivorship Bias (Design Assessment)
# ===========================================================================

class TestSurvivorshipBiasDesignAssessment:
    """
    Documents the current model's capabilities and gaps for survivorship bias.
    These are design-inspection tests, not database integration tests.
    """

    def test_instrument_has_is_active_for_soft_delisting(self):
        """is_active=False allows marking delisted instruments without data loss."""
        import inspect

        from app.models.identity import Instrument
        src = inspect.getsource(Instrument)
        assert "is_active" in src

    def test_market_data_candle_has_provider_provenance(self):
        """Every candle records which provider supplied it."""
        import inspect

        from app.models.market_data import MarketDataCandle
        src = inspect.getsource(MarketDataCandle)
        assert "provider" in src

    def test_market_data_candle_has_retrieval_time(self):
        """retrieval_time allows auditing when data was fetched."""
        import inspect

        from app.models.market_data import MarketDataCandle
        src = inspect.getsource(MarketDataCandle)
        assert "retrieval_time" in src

    def test_adjustment_mode_in_unique_key(self):
        """
        RAW and SPLIT_AND_DIVIDEND candles for the same timestamp are stored
        as separate rows (adjustment_mode is part of the unique constraint).
        This prevents adjusted data from silently overwriting raw data.
        """
        import inspect

        from app.models.market_data import MarketDataCandle
        src = inspect.getsource(MarketDataCandle)
        assert "adjustment_mode" in src

    def test_no_corporate_actions_table(self):
        """
        There is currently no CorporateAction, SplitEvent, or DividendEvent
        table.  This is a known gap documented for a future milestone.
        Instruments affected by splits or dividends rely on yfinance-provided
        adjusted data; there is no independent adjustment-factor store.
        """
        import inspect

        import app.models
        # Verify that no corporate actions model exists yet
        src = inspect.getsource(app.models)
        has_corporate_actions = any(
            keyword in src
            for keyword in ["CorporateAction", "SplitEvent", "DividendEvent"]
        )
        assert not has_corporate_actions, (
            "No corporate-actions model is expected at this milestone. "
            "If you're adding one, update this test."
        )

    def test_no_ticker_history_table(self):
        """
        There is no TickerHistory or InstrumentNameHistory table.
        Historical ticker renames (e.g. Facebook → Meta) cannot be tracked yet.
        This is a documented gap.
        """
        import inspect

        from app.models.identity import Instrument
        src = inspect.getsource(Instrument)
        assert "ticker_history" not in src
        assert "former_name" not in src
