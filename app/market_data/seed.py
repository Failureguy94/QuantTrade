"""Explicitly verified seed reference data for India and US markets.

Supports:
- India: National Stock Exchange of India (XNSE), BSE Ltd. (XBOM)
- United States: NASDAQ Stock Market (XNAS), New York Stock Exchange (XNYS)
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Exchange, Instrument, ProviderSymbol

VERIFIED_EXCHANGES: list[dict[str, Any]] = [
    {
        "mic": "XNSE",
        "name": "National Stock Exchange of India",
        "country_code": "IN",
        "timezone": "Asia/Kolkata",
        "currency_code": "INR",
        "calendar_code": "XNSE",
    },
    {
        "mic": "XBOM",
        "name": "BSE Ltd.",
        "country_code": "IN",
        "timezone": "Asia/Kolkata",
        "currency_code": "INR",
        "calendar_code": "XBOM",
    },
    {
        "mic": "XNAS",
        "name": "NASDAQ Stock Market",
        "country_code": "US",
        "timezone": "America/New_York",
        "currency_code": "USD",
        "calendar_code": "XNAS",
    },
    {
        "mic": "XNYS",
        "name": "New York Stock Exchange",
        "country_code": "US",
        "timezone": "America/New_York",
        "currency_code": "USD",
        "calendar_code": "XNYS",
    },
]

VERIFIED_INSTRUMENTS: list[dict[str, Any]] = [
    # India - NSE
    {
        "exchange_mic": "XNSE",
        "symbol": "RELIANCE",
        "name": "Reliance Industries Limited",
        "asset_type": "EQUITY",
        "currency_code": "INR",
        "isin": "INE002A01018",
        "provider_symbols": [{"provider": "YAHOO", "symbol": "RELIANCE.NS", "primary": True}],
    },
    {
        "exchange_mic": "XNSE",
        "symbol": "TCS",
        "name": "Tata Consultancy Services Limited",
        "asset_type": "EQUITY",
        "currency_code": "INR",
        "isin": "INE467B01029",
        "provider_symbols": [{"provider": "YAHOO", "symbol": "TCS.NS", "primary": True}],
    },
    {
        "exchange_mic": "XNSE",
        "symbol": "INFY",
        "name": "Infosys Limited",
        "asset_type": "EQUITY",
        "currency_code": "INR",
        "isin": "INE009A01021",
        "provider_symbols": [{"provider": "YAHOO", "symbol": "INFY.NS", "primary": True}],
    },
    # India - BSE
    {
        "exchange_mic": "XBOM",
        "symbol": "RELIANCE",
        "name": "Reliance Industries Limited",
        "asset_type": "EQUITY",
        "currency_code": "INR",
        "isin": "INE002A01018",
        "provider_symbols": [{"provider": "YAHOO", "symbol": "RELIANCE.BO", "primary": True}],
    },
    # United States - NASDAQ
    {
        "exchange_mic": "XNAS",
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "asset_type": "EQUITY",
        "currency_code": "USD",
        "isin": "US0378331005",
        "provider_symbols": [{"provider": "YAHOO", "symbol": "AAPL", "primary": True}],
    },
    {
        "exchange_mic": "XNAS",
        "symbol": "MSFT",
        "name": "Microsoft Corporation",
        "asset_type": "EQUITY",
        "currency_code": "USD",
        "isin": "US5949181045",
        "provider_symbols": [{"provider": "YAHOO", "symbol": "MSFT", "primary": True}],
    },
    # United States - NYSE
    {
        "exchange_mic": "XNYS",
        "symbol": "SPY",
        "name": "SPDR S&P 500 ETF Trust",
        "asset_type": "ETF",
        "currency_code": "USD",
        "isin": "US78462F1030",
        "provider_symbols": [{"provider": "YAHOO", "symbol": "SPY", "primary": True}],
    },
]


async def seed_market_reference_data(db: AsyncSession) -> dict[str, int]:
    """
    Idempotently seed verified exchanges, instruments, and provider mappings.
    Does not duplicate existing records.
    """
    exchanges_seeded = 0
    instruments_seeded = 0
    provider_symbols_seeded = 0

    # 1. Seed exchanges
    exchange_map: dict[str, Exchange] = {}
    for ex_data in VERIFIED_EXCHANGES:
        stmt = select(Exchange).where(Exchange.mic == ex_data["mic"])
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing is None:
            exchange = Exchange(
                mic=ex_data["mic"],
                name=ex_data["name"],
                country_code=ex_data["country_code"],
                timezone=ex_data["timezone"],
                currency_code=ex_data["currency_code"],
                calendar_code=ex_data["calendar_code"],
                is_active=True,
            )
            db.add(exchange)
            await db.flush()
            exchange_map[ex_data["mic"]] = exchange
            exchanges_seeded += 1
        else:
            exchange_map[ex_data["mic"]] = existing

    # 2. Seed instruments and provider symbols
    for inst_data in VERIFIED_INSTRUMENTS:
        exchange = exchange_map[inst_data["exchange_mic"]]
        inst_stmt = select(Instrument).where(
            Instrument.exchange_id == exchange.id,
            Instrument.symbol == inst_data["symbol"],
        )
        existing_inst = (await db.execute(inst_stmt)).scalar_one_or_none()
        if existing_inst is None:
            inst = Instrument(
                exchange_id=exchange.id,
                symbol=inst_data["symbol"],
                name=inst_data["name"],
                asset_type=inst_data["asset_type"],
                currency_code=inst_data["currency_code"],
                isin=inst_data["isin"],
                is_active=True,
            )
            db.add(inst)
            await db.flush()
            instruments_seeded += 1
        else:
            inst = existing_inst

        # Provider symbols
        for ps_data in inst_data["provider_symbols"]:
            ps_stmt = select(ProviderSymbol).where(
                ProviderSymbol.provider == ps_data["provider"],
                ProviderSymbol.provider_symbol == ps_data["symbol"],
            )
            existing_ps = (await db.execute(ps_stmt)).scalar_one_or_none()
            if existing_ps is None:
                ps = ProviderSymbol(
                    instrument_id=inst.id,
                    provider=ps_data["provider"],
                    provider_symbol=ps_data["symbol"],
                    is_primary=ps_data["primary"],
                )
                db.add(ps)
                await db.flush()
                provider_symbols_seeded += 1

    return {
        "exchanges": exchanges_seeded,
        "instruments": instruments_seeded,
        "provider_symbols": provider_symbols_seeded,
    }
