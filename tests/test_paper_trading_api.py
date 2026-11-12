"""API tests for paper portfolios, order execution and the double-entry ledger.

Paper fills use the configured defaults: 5 bps commission and 2 bps slippage.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.market_data.exceptions import ProviderConnectionError
from app.models.market_data import MarketDataCandle
from app.models.portfolio import LedgerEntry, Order, OrderEvent, Portfolio
from app.trading.orders import (
    ALLOWED_TRANSITIONS,
    InvalidOrderTransitionError,
    OrderService,
    OrderStatus,
)
from app.trading.quotes import QuoteService
from helpers import FakeProvider

BASE = "/api/v1/portfolios"
D = Decimal


async def open_portfolio(
    client: AsyncClient, headers: dict, capital: str = "100000", currency: str = "USD"
) -> dict:
    response = await client.post(
        BASE,
        json={"name": "Paper account", "base_currency": currency, "initial_capital": capital},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def place(
    client: AsyncClient,
    headers: dict,
    portfolio_id: str,
    instrument_id,
    side: str,
    quantity: str,
    *,
    limit_price: str | None = None,
    key: str | None = None,
):
    body = {"instrument_id": str(instrument_id), "side": side, "quantity": quantity}
    if limit_price is not None:
        body |= {"order_type": "LIMIT", "limit_price": limit_price}
    request_headers = {**headers, **({"Idempotency-Key": key} if key else {})}
    return await client.post(f"{BASE}/{portfolio_id}/orders", json=body, headers=request_headers)


async def balances(client: AsyncClient, headers: dict, portfolio_id: str) -> dict:
    response = await client.get(f"{BASE}/{portfolio_id}/ledger/balances", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def cash(client: AsyncClient, headers: dict, portfolio_id: str) -> Decimal:
    return D((await client.get(f"{BASE}/{portfolio_id}", headers=headers)).json()["cash_balance"])


@pytest.fixture
async def account(async_client: AsyncClient, auth_headers, instruments, fake_provider):
    """A funded USD portfolio with AAPL quoted at 200."""
    fake_provider.set_quote("AAPL", "200")
    portfolio = await open_portfolio(async_client, auth_headers)
    return portfolio["id"], auth_headers, instruments["XNAS:AAPL"]


# ---------------------------------------------------------------------------
# Portfolios and cash
# ---------------------------------------------------------------------------
async def test_create_portfolio_posts_the_opening_deposit(async_client: AsyncClient, auth_headers):
    portfolio = await open_portfolio(async_client, auth_headers, capital="250000.50")
    assert portfolio["account_mode"] == "PAPER"
    assert D(portfolio["initial_capital"]) == D(portfolio["cash_balance"]) == D("250000.50")

    ledger = await balances(async_client, auth_headers, portfolio["id"])
    assert D(ledger["balances"]["CASH"]) == D("250000.50")
    assert D(ledger["balances"]["CONTRIBUTED_CAPITAL"]) == D("-250000.50")
    assert ledger["is_balanced"] and ledger["is_reconciled"]

    journal = (
        await async_client.get(f"{BASE}/{portfolio['id']}/ledger", headers=auth_headers)
    ).json()
    assert len(journal) == 1
    assert journal[0]["kind"] == "DEPOSIT" and journal[0]["sequence"] == 1
    assert [(e["account"], D(e["amount"])) for e in journal[0]["entries"]] == [
        ("CASH", D("250000.50")),
        ("CONTRIBUTED_CAPITAL", D("-250000.50")),
    ]


async def test_portfolio_validation(async_client: AsyncClient, auth_headers):
    for body in [
        {"name": "x", "base_currency": "USD", "initial_capital": "0"},
        {"name": "x", "base_currency": "USD", "initial_capital": "-5"},
        {"name": "x", "base_currency": "US1", "initial_capital": "100"},
        {"name": "  ", "base_currency": "USD", "initial_capital": "100"},
        {"name": "x", "base_currency": "USD", "initial_capital": "1.123456789"},
    ]:
        response = await async_client.post(BASE, json=body, headers=auth_headers)
        assert response.status_code == 422, body

    lower = await async_client.post(
        BASE,
        json={"name": "x", "base_currency": "inr", "initial_capital": "100"},
        headers=auth_headers,
    )
    assert lower.json()["base_currency"] == "INR"


async def test_deposits_and_withdrawals(async_client: AsyncClient, auth_headers):
    portfolio = await open_portfolio(async_client, auth_headers, capital="1000")
    url = f"{BASE}/{portfolio['id']}/cash"

    deposit = await async_client.post(
        url, json={"kind": "DEPOSIT", "amount": "500.25"}, headers=auth_headers
    )
    assert deposit.status_code == 200
    assert D(deposit.json()["cash_balance"]) == D("1500.25")
    assert D(deposit.json()["initial_capital"]) == D("1000")  # history is not rewritten

    withdrawal = await async_client.post(
        url, json={"kind": "WITHDRAWAL", "amount": "1500.25"}, headers=auth_headers
    )
    assert D(withdrawal.json()["cash_balance"]) == 0

    overdraw = await async_client.post(
        url, json={"kind": "WITHDRAWAL", "amount": "0.01"}, headers=auth_headers
    )
    assert overdraw.status_code == 422
    assert "Insufficient cash" in overdraw.json()["detail"]
    # Zero is rendered in plain notation, not as "0E-8".
    assert withdrawal.json()["cash_balance"] == "0.00000000"

    # A deposit that would overflow NUMERIC(24, 8) is refused, not a server error.
    await async_client.post(url, json={"kind": "DEPOSIT", "amount": "1"}, headers=auth_headers)
    overflow = await async_client.post(
        url, json={"kind": "DEPOSIT", "amount": "9999999999999999"}, headers=auth_headers
    )
    assert overflow.status_code == 422
    assert "maximum representable balance" in overflow.json()["detail"]
    await async_client.post(url, json={"kind": "WITHDRAWAL", "amount": "1"}, headers=auth_headers)

    ledger = await balances(async_client, auth_headers, portfolio["id"])
    assert D(ledger["balances"]["CASH"]) == 0
    assert D(ledger["balances"]["CONTRIBUTED_CAPITAL"]) == 0
    assert ledger["is_balanced"] and ledger["is_reconciled"]

    journal = (
        await async_client.get(f"{BASE}/{portfolio['id']}/ledger", headers=auth_headers)
    ).json()
    assert [(t["sequence"], t["kind"]) for t in journal] == [
        (5, "WITHDRAWAL"),
        (4, "DEPOSIT"),
        (3, "WITHDRAWAL"),
        (2, "DEPOSIT"),
        (1, "DEPOSIT"),
    ]
    assert ledger["balances"]["REALIZED_PNL"] == "0.00000000"
    assert ledger["cash_difference"] == ledger["securities_difference"] == "0.00000000"


async def test_rename_and_list(async_client: AsyncClient, auth_headers):
    first = await open_portfolio(async_client, auth_headers)
    second = await open_portfolio(async_client, auth_headers)
    renamed = await async_client.patch(
        f"{BASE}/{first['id']}", json={"name": " Core "}, headers=auth_headers
    )
    assert renamed.json()["name"] == "Core"
    listed = (await async_client.get(BASE, headers=auth_headers)).json()
    assert {p["id"] for p in listed} == {first["id"], second["id"]}


# ---------------------------------------------------------------------------
# Market orders and accounting
# ---------------------------------------------------------------------------
async def test_market_buy_fills_with_slippage_and_commission(async_client: AsyncClient, account):
    portfolio_id, headers, aapl = account

    response = await place(async_client, headers, portfolio_id, aapl.id, "BUY", "10")
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["status"] == "FILLED"
    assert D(order["filled_quantity"]) == 10
    assert order["rejection_reason"] is None

    # 200 * 1.0002 = 200.04 fill; notional 2000.40; commission 5 bps = 1.0002.
    trades = (await async_client.get(f"{BASE}/{portfolio_id}/trades", headers=headers)).json()
    assert len(trades) == 1
    trade = trades[0]
    assert (trade["side"], trade["instrument_id"]) == ("BUY", str(aapl.id))
    assert D(trade["fill_price"]) == D("200.04")
    assert D(trade["commission"]) == D("1.0002")
    assert D(trade["slippage"]) == D("0.4")
    assert trade["currency_code"] == "USD"

    assert await cash(async_client, headers, portfolio_id) == D("100000") - D("2001.4002")

    positions = (
        await async_client.get(f"{BASE}/{portfolio_id}/positions", headers=headers)
    ).json()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAPL"
    assert D(positions[0]["quantity"]) == 10
    assert D(positions[0]["average_price"]) == D("200.04")

    ledger = await balances(async_client, headers, portfolio_id)
    assert D(ledger["balances"]["SECURITIES"]) == D("2000.40")
    assert D(ledger["balances"]["FEES"]) == D("1.0002")
    assert D(ledger["balances"]["CASH"]) == D("97998.5998")
    assert D(ledger["balances"]["REALIZED_PNL"]) == 0
    assert ledger["is_balanced"] and ledger["is_reconciled"]


async def test_round_trip_realises_pnl_and_closes_the_position(
    async_client: AsyncClient, account, fake_provider: FakeProvider
):
    portfolio_id, headers, aapl = account
    await place(async_client, headers, portfolio_id, aapl.id, "BUY", "10")  # @ 200.04
    fake_provider.set_quote("AAPL", "220")
    await place(async_client, headers, portfolio_id, aapl.id, "BUY", "10")  # @ 220.044

    positions = (
        await async_client.get(f"{BASE}/{portfolio_id}/positions", headers=headers)
    ).json()
    assert D(positions[0]["quantity"]) == 20
    assert D(positions[0]["average_price"]) == D("210.042")  # (2000.40 + 2200.44) / 20

    # Partial sale at 230 * 0.9998 = 229.954: releases 5 * 210.042 of cost.
    fake_provider.set_quote("AAPL", "230")
    partial = await place(async_client, headers, portfolio_id, aapl.id, "SELL", "5")
    assert partial.json()["status"] == "FILLED"
    ledger = await balances(async_client, headers, portfolio_id)
    assert D(ledger["balances"]["SECURITIES"]) == D("4200.84") - D("1050.21")
    assert D(ledger["balances"]["REALIZED_PNL"]) == -(D("1149.77") - D("1050.21"))
    assert ledger["is_balanced"] and ledger["is_reconciled"]

    # Selling the rest closes the position and empties the SECURITIES account.
    closing = await place(async_client, headers, portfolio_id, aapl.id, "SELL", "15")
    assert closing.json()["status"] == "FILLED"
    assert (
        await async_client.get(f"{BASE}/{portfolio_id}/positions", headers=headers)
    ).json() == []

    ledger = await balances(async_client, headers, portfolio_id)
    assert D(ledger["balances"]["SECURITIES"]) == 0
    assert ledger["is_balanced"] and ledger["is_reconciled"]

    # Accounting identity: cash = capital + realised gains - fees.
    realised_gain = -D(ledger["balances"]["REALIZED_PNL"])
    fees = D(ledger["balances"]["FEES"])
    assert realised_gain == 20 * D("229.954") - D("4200.84")
    final_cash = await cash(async_client, headers, portfolio_id)
    assert final_cash == D("100000") + realised_gain - fees
    assert D(ledger["balances"]["CASH"]) == final_cash


async def test_every_ledger_transaction_balances(
    async_client: AsyncClient, account, fake_provider: FakeProvider, db_session: AsyncSession
):
    portfolio_id, headers, aapl = account
    for side, quantity, price in [
        ("BUY", "7", "199.99"),
        ("BUY", "3.5", "201.37"),
        ("SELL", "4.25", "187.11"),  # realised loss
        ("SELL", "6.25", "205.5"),
    ]:
        fake_provider.set_quote("AAPL", price)
        response = await place(async_client, headers, portfolio_id, aapl.id, side, quantity)
        assert response.json()["status"] == "FILLED", response.text

    per_transaction = (
        await db_session.execute(
            select(LedgerEntry.transaction_id, func.sum(LedgerEntry.amount))
            .where(LedgerEntry.portfolio_id == uuid.UUID(portfolio_id))
            .group_by(LedgerEntry.transaction_id)
        )
    ).all()
    assert len(per_transaction) == 5  # opening deposit + four trades
    assert all(total == 0 for _, total in per_transaction)

    ledger = await balances(async_client, headers, portfolio_id)
    assert D(ledger["balances"]["REALIZED_PNL"]) != 0
    assert D(ledger["balances"]["SECURITIES"]) == 0
    assert ledger["is_balanced"] and ledger["is_reconciled"]


async def test_orders_the_account_cannot_support_are_rejected(
    async_client: AsyncClient, account, instruments, fake_provider: FakeProvider
):
    portfolio_id, headers, aapl = account
    opening_cash = await cash(async_client, headers, portfolio_id)

    too_big = await place(async_client, headers, portfolio_id, aapl.id, "BUY", "500")
    assert too_big.status_code == 201  # recorded, but refused by the paper broker
    assert too_big.json()["status"] == "REJECTED"
    assert "Insufficient cash" in too_big.json()["rejection_reason"]

    naked = await place(async_client, headers, portfolio_id, aapl.id, "SELL", "1")
    assert naked.json()["status"] == "REJECTED"
    assert "short selling is not supported" in naked.json()["rejection_reason"]

    await place(async_client, headers, portfolio_id, aapl.id, "BUY", "2")
    oversell = await place(async_client, headers, portfolio_id, aapl.id, "SELL", "2.00000001")
    assert oversell.json()["status"] == "REJECTED"
    assert "2.00000000 held" in oversell.json()["rejection_reason"]

    # INR instrument in a USD portfolio: FX is not implemented, so it is refused.
    fake_provider.set_quote("RELIANCE.NS", "2900")
    cross = await place(
        async_client, headers, portfolio_id, instruments["XNSE:RELIANCE"].id, "BUY", "1"
    )
    assert cross.json()["status"] == "REJECTED"
    assert "cross-currency" in cross.json()["rejection_reason"]

    # No quote at all for MSFT.
    unpriced = await place(
        async_client, headers, portfolio_id, instruments["XNAS:MSFT"].id, "BUY", "1"
    )
    assert unpriced.json()["status"] == "REJECTED"
    assert "No usable price" in unpriced.json()["rejection_reason"]

    # Rejections never move money: only the 2-share buy did.
    spent = opening_cash - await cash(async_client, headers, portfolio_id)
    assert spent == D("400.08") + D("0.20004")
    rejected = (
        await async_client.get(f"{BASE}/{portfolio_id}/orders?status=rejected", headers=headers)
    ).json()
    assert len(rejected) == 5
    trades = (await async_client.get(f"{BASE}/{portfolio_id}/trades", headers=headers)).json()
    assert len(trades) == 1


async def test_exact_cash_can_be_spent_but_not_one_unit_more(
    async_client: AsyncClient, auth_headers, instruments, fake_provider: FakeProvider
):
    fake_provider.set_quote("AAPL", "100")
    # 10 shares @ 100.02 = 1000.20 + 0.5001 commission.
    portfolio = await open_portfolio(async_client, auth_headers, capital="1000.7001")
    aapl = instruments["XNAS:AAPL"]

    over = await place(async_client, auth_headers, portfolio["id"], aapl.id, "BUY", "10.00000001")
    assert over.json()["status"] == "REJECTED"
    exact = await place(async_client, auth_headers, portfolio["id"], aapl.id, "BUY", "10")
    assert exact.json()["status"] == "FILLED"
    assert await cash(async_client, auth_headers, portfolio["id"]) == 0


async def test_order_request_validation(async_client: AsyncClient, account):
    portfolio_id, headers, aapl = account
    url = f"{BASE}/{portfolio_id}/orders"
    base = {"instrument_id": str(aapl.id), "side": "BUY", "quantity": "1"}
    for body in [
        {**base, "quantity": "0"},
        {**base, "quantity": "-1"},
        {**base, "quantity": "1.000000001"},
        {**base, "side": "HOLD"},
        {**base, "order_type": "STOP"},
        {**base, "order_type": "LIMIT"},  # limit without a price
        {**base, "limit_price": "10"},  # price on a market order
        {**base, "order_type": "LIMIT", "limit_price": "0"},
    ]:
        response = await async_client.post(url, json=body, headers=headers)
        assert response.status_code == 422, body

    unknown = await async_client.post(
        url, json={**base, "instrument_id": str(uuid.uuid4())}, headers=headers
    )
    assert unknown.status_code == 404
    assert (await async_client.get(url, headers=headers)).json() == []


# ---------------------------------------------------------------------------
# Limit orders
# ---------------------------------------------------------------------------
async def test_marketable_limit_orders_fill_within_their_limit(async_client: AsyncClient, account):
    portfolio_id, headers, aapl = account

    # Quote 200, slipped fill would be 200.04: the limit caps it at 200.01.
    capped = await place(
        async_client, headers, portfolio_id, aapl.id, "BUY", "10", limit_price="200.01"
    )
    assert capped.json()["status"] == "FILLED"
    # A generous limit does not make the fill worse than the market.
    roomy = await place(
        async_client, headers, portfolio_id, aapl.id, "BUY", "10", limit_price="250"
    )
    assert roomy.json()["status"] == "FILLED"
    # Selling: slipped fill 199.96 is floored at the 199.99 limit.
    sell = await place(
        async_client, headers, portfolio_id, aapl.id, "SELL", "10", limit_price="199.99"
    )
    assert sell.json()["status"] == "FILLED"

    trades = (await async_client.get(f"{BASE}/{portfolio_id}/trades", headers=headers)).json()
    prices = {D(t["fill_price"]) for t in trades}
    assert prices == {D("200.01"), D("200.04"), D("199.99")}


async def test_resting_limit_order_fills_when_the_price_arrives(
    async_client: AsyncClient, account, fake_provider: FakeProvider
):
    portfolio_id, headers, aapl = account
    opening_cash = await cash(async_client, headers, portfolio_id)

    resting = await place(
        async_client, headers, portfolio_id, aapl.id, "BUY", "10", limit_price="190"
    )
    assert resting.status_code == 201
    order = resting.json()
    assert order["status"] == "OPEN"
    assert order["filled_quantity"] == "0.00000000"
    assert await cash(async_client, headers, portfolio_id) == opening_cash

    process_url = f"{BASE}/{portfolio_id}/orders/process"
    assert (await async_client.post(process_url, headers=headers)).json() == []  # still 200

    fake_provider.set_quote("AAPL", "189")
    changed = (await async_client.post(process_url, headers=headers)).json()
    assert [(o["id"], o["status"]) for o in changed] == [(order["id"], "FILLED")]
    assert (await async_client.post(process_url, headers=headers)).json() == []  # idempotent

    detail = (
        await async_client.get(f"{BASE}/{portfolio_id}/orders/{order['id']}", headers=headers)
    ).json()
    assert [(e["sequence"], e["from_status"], e["to_status"]) for e in detail["events"]] == [
        (1, None, "PENDING"),
        (2, "PENDING", "OPEN"),
        (3, "OPEN", "FILLED"),
    ]
    assert len(detail["trades"]) == 1
    assert D(detail["trades"][0]["fill_price"]) == D("189.0378")  # 189 * 1.0002, under the limit
    assert await cash(async_client, headers, portfolio_id) < opening_cash


async def test_resting_order_is_rejected_if_funds_are_gone_when_it_becomes_marketable(
    async_client: AsyncClient, account, fake_provider: FakeProvider
):
    portfolio_id, headers, aapl = account
    resting = (
        await place(async_client, headers, portfolio_id, aapl.id, "BUY", "400", limit_price="190")
    ).json()
    assert resting["status"] == "OPEN"  # resting orders do not reserve cash

    await async_client.post(
        f"{BASE}/{portfolio_id}/cash",
        json={"kind": "WITHDRAWAL", "amount": "90000"},
        headers=headers,
    )
    fake_provider.set_quote("AAPL", "185")
    changed = (
        await async_client.post(f"{BASE}/{portfolio_id}/orders/process", headers=headers)
    ).json()
    assert changed[0]["status"] == "REJECTED"
    assert "Insufficient cash" in changed[0]["rejection_reason"]


async def test_cancel_only_applies_to_resting_orders(async_client: AsyncClient, account):
    portfolio_id, headers, aapl = account
    resting = (
        await place(async_client, headers, portfolio_id, aapl.id, "BUY", "1", limit_price="150")
    ).json()
    filled = (await place(async_client, headers, portfolio_id, aapl.id, "BUY", "1")).json()

    cancel_url = f"{BASE}/{portfolio_id}/orders/{resting['id']}/cancel"
    cancelled = await async_client.post(cancel_url, headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    again = await async_client.post(cancel_url, headers=headers)
    assert again.status_code == 409
    done = await async_client.post(
        f"{BASE}/{portfolio_id}/orders/{filled['id']}/cancel", headers=headers
    )
    assert done.status_code == 409
    assert "FILLED" in done.json()["detail"]
    missing = await async_client.post(
        f"{BASE}/{portfolio_id}/orders/{uuid.uuid4()}/cancel", headers=headers
    )
    assert missing.status_code == 404

    # A cancelled order is never filled later.
    assert (
        await async_client.post(f"{BASE}/{portfolio_id}/orders/process", headers=headers)
    ).json() == []


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
async def test_idempotency_key_prevents_duplicate_orders(async_client: AsyncClient, account):
    portfolio_id, headers, aapl = account

    first = await place(async_client, headers, portfolio_id, aapl.id, "BUY", "10", key="order-1")
    assert first.status_code == 201
    cash_after_first = await cash(async_client, headers, portfolio_id)

    replay = await place(async_client, headers, portfolio_id, aapl.id, "BUY", "10", key="order-1")
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert await cash(async_client, headers, portfolio_id) == cash_after_first

    different = await place(
        async_client, headers, portfolio_id, aapl.id, "BUY", "11", key="order-1"
    )
    assert different.status_code == 409

    orders = (await async_client.get(f"{BASE}/{portfolio_id}/orders", headers=headers)).json()
    assert len(orders) == 1
    assert orders[0]["idempotency_key"] == "order-1"

    # Without a key every submission is a new order.
    await place(async_client, headers, portfolio_id, aapl.id, "BUY", "10")
    await place(async_client, headers, portfolio_id, aapl.id, "BUY", "10")
    assert (
        len((await async_client.get(f"{BASE}/{portfolio_id}/orders", headers=headers)).json())
        == 3
    )


async def test_idempotency_keys_are_scoped_to_a_portfolio(
    async_client: AsyncClient, make_user, instruments, fake_provider: FakeProvider
):
    fake_provider.set_quote("AAPL", "200")
    aapl = instruments["XNAS:AAPL"]
    _, alice = await make_user()
    _, bob = await make_user()
    alice_portfolio = await open_portfolio(async_client, alice)
    bob_portfolio = await open_portfolio(async_client, bob)

    mine = await place(async_client, alice, alice_portfolio["id"], aapl.id, "BUY", "1", key="k")
    theirs = await place(async_client, bob, bob_portfolio["id"], aapl.id, "BUY", "2", key="k")
    assert mine.status_code == theirs.status_code == 201
    assert mine.json()["id"] != theirs.json()["id"]


# ---------------------------------------------------------------------------
# Order state machine
# ---------------------------------------------------------------------------
def test_terminal_states_have_no_exits():
    for status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED):
        assert ALLOWED_TRANSITIONS[status] == frozenset()
    assert set(ALLOWED_TRANSITIONS) == set(OrderStatus)


async def test_illegal_transitions_are_refused(
    async_client: AsyncClient, account, db_session: AsyncSession
):
    portfolio_id, headers, aapl = account
    filled = (await place(async_client, headers, portfolio_id, aapl.id, "BUY", "1")).json()
    order = await db_session.get(Order, uuid.UUID(filled["id"]))
    service = OrderService(db_session, QuoteService(db_session))

    with pytest.raises(InvalidOrderTransitionError, match="FILLED to OPEN"):
        await service._transition(order, OrderStatus.OPEN)
    assert order.status == "FILLED"
    events = await db_session.scalar(
        select(func.count()).select_from(OrderEvent).where(OrderEvent.order_id == order.id)
    )
    assert events == 2  # PENDING, FILLED: the refused transition left no trace


async def test_non_paper_portfolios_cannot_be_traded(
    async_client: AsyncClient, account, db_session: AsyncSession
):
    """ADR-006: the paper execution path must never act on another account mode."""
    portfolio_id, headers, aapl = account
    portfolio = await db_session.get(Portfolio, uuid.UUID(portfolio_id))
    portfolio.account_mode = "LIVE"
    await db_session.flush()

    response = await place(async_client, headers, portfolio_id, aapl.id, "BUY", "1")
    assert response.status_code == 422
    assert response.json()["detail"] == "Only paper portfolios can be traded"
    assert (await async_client.get(f"{BASE}/{portfolio_id}/orders", headers=headers)).json() == []


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------
async def seed_candle(db: AsyncSession, instrument, close: str, age_days: int) -> None:
    stamp = (datetime.now(UTC) - timedelta(days=age_days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    db.add(
        MarketDataCandle(
            instrument_id=instrument.id,
            timestamp=stamp,
            open=D(close),
            high=D(close),
            low=D(close),
            close=D(close),
            volume=1,
            currency=instrument.currency_code,
            interval="1d",
            adjustment_mode="RAW",
            provider="YAHOO",
            retrieval_time=datetime.now(UTC),
        )
    )
    await db.flush()


async def test_quotes_fall_back_to_recent_stored_candles(
    async_client: AsyncClient, account, fake_provider: FakeProvider, db_session: AsyncSession
):
    portfolio_id, headers, aapl = account
    fake_provider.quotes.clear()
    fake_provider.error = ProviderConnectionError("offline", provider="YAHOO")
    QuoteService.clear_cache()

    await seed_candle(db_session, aapl, "150", age_days=2)
    filled = await place(async_client, headers, portfolio_id, aapl.id, "BUY", "1")
    assert filled.json()["status"] == "FILLED"
    detail = (
        await async_client.get(
            f"{BASE}/{portfolio_id}/orders/{filled.json()['id']}", headers=headers
        )
    ).json()
    assert D(detail["trades"][0]["fill_price"]) == D("150.03")
    assert "from STORE" in detail["events"][-1]["reason"]


async def test_stale_prices_are_not_traded_on(
    async_client: AsyncClient, account, fake_provider: FakeProvider, db_session: AsyncSession
):
    portfolio_id, headers, aapl = account
    fake_provider.quotes.clear()
    fake_provider.error = ProviderConnectionError("offline", provider="YAHOO")
    QuoteService.clear_cache()

    await seed_candle(db_session, aapl, "150", age_days=30)
    stale = await place(async_client, headers, portfolio_id, aapl.id, "BUY", "1")
    assert stale.json()["status"] == "REJECTED"
    assert "older than the 7-day limit" in stale.json()["rejection_reason"]


async def test_quotes_are_cached_briefly_and_not_persisted(
    async_client: AsyncClient, account, fake_provider: FakeProvider, db_session: AsyncSession
):
    portfolio_id, headers, aapl = account
    calls = fake_provider.calls
    for _ in range(3):
        await place(async_client, headers, portfolio_id, aapl.id, "BUY", "1")
    assert fake_provider.calls == calls + 1

    # Reference quotes must not leak into the candle store used by backtests.
    stored = await db_session.scalar(select(func.count()).select_from(MarketDataCandle))
    assert stored == 0


async def test_provider_quotes_are_rounded_to_candle_precision(
    async_client: AsyncClient, account, fake_provider: FakeProvider
):
    """Provider floats such as 355.6300048828125 must not reach fills at full noise."""
    portfolio_id, headers, aapl = account
    fake_provider.set_quote("AAPL", "355.6300048828125")
    await place(async_client, headers, portfolio_id, aapl.id, "BUY", "1")
    valuation = (
        await async_client.get(f"{BASE}/{portfolio_id}/valuation", headers=headers)
    ).json()
    assert D(valuation["positions"][0]["market_price"]) == D("355.630005")


# ---------------------------------------------------------------------------
# Valuation, snapshots, allocations
# ---------------------------------------------------------------------------
async def test_valuation_marks_positions_to_market(
    async_client: AsyncClient, account, instruments, fake_provider: FakeProvider
):
    portfolio_id, headers, aapl = account
    msft = instruments["XNAS:MSFT"]
    fake_provider.set_quote("MSFT", "400")
    await place(async_client, headers, portfolio_id, aapl.id, "BUY", "10")  # @ 200.04
    await place(async_client, headers, portfolio_id, msft.id, "BUY", "5")  # @ 400.08

    fake_provider.set_quote("AAPL", "210")
    del fake_provider.quotes["MSFT"]  # MSFT can no longer be priced

    valuation = (
        await async_client.get(f"{BASE}/{portfolio_id}/valuation", headers=headers)
    ).json()
    by_symbol = {p["symbol"]: p for p in valuation["positions"]}

    assert D(by_symbol["AAPL"]["market_value"]) == D("2100")
    assert D(by_symbol["AAPL"]["unrealized_pnl"]) == D("2100") - D("2000.40")
    assert by_symbol["AAPL"]["price_source"] == "YAHOO"
    # Unpriceable positions are carried at cost and flagged rather than failing.
    assert by_symbol["MSFT"]["price_source"] == "COST"
    assert D(by_symbol["MSFT"]["unrealized_pnl"]) == 0
    assert any("MSFT" in warning for warning in valuation["warnings"])

    assert D(valuation["positions_value"]) == D("2100") + D("2000.40")
    assert D(valuation["total_equity"]) == D(valuation["cash"]) + D(valuation["positions_value"])
    assert D(valuation["cash"]) == await cash(async_client, headers, portfolio_id)


async def test_equity_snapshots_build_a_history(
    async_client: AsyncClient, account, fake_provider: FakeProvider
):
    portfolio_id, headers, aapl = account
    url = f"{BASE}/{portfolio_id}/snapshots"

    empty = await async_client.post(url, headers=headers)
    assert empty.status_code == 201
    assert D(empty.json()["total_equity"]) == D("100000")
    assert D(empty.json()["positions_value"]) == 0

    await place(async_client, headers, portfolio_id, aapl.id, "BUY", "100")
    fake_provider.set_quote("AAPL", "250")
    after = (await async_client.post(url, headers=headers)).json()
    assert D(after["positions_value"]) == D("25000")
    assert D(after["total_equity"]) == D(after["cash"]) + D("25000")

    history = (await async_client.get(url, headers=headers)).json()
    assert [s["id"] for s in history] == [empty.json()["id"], after["id"]]  # oldest first
    assert D(history[-1]["total_equity"]) > D(history[0]["total_equity"])


async def test_allocations_are_replaced_as_a_set(async_client: AsyncClient, account):
    portfolio_id, headers, _ = account
    url = f"{BASE}/{portfolio_id}/allocations"
    strategy_ids = []
    for name in ("A", "B"):
        created = await async_client.post(
            "/api/v1/strategies",
            json={"name": name, "strategy_type": "BUY_AND_HOLD"},
            headers=headers,
        )
        strategy_ids.append(created.json()["id"])

    def body(*weights: str) -> dict:
        return {
            "allocations": [
                {"strategy_id": sid, "allocation_weight": w}
                for sid, w in zip(strategy_ids, weights, strict=False)
            ]
        }

    saved = await async_client.put(url, json=body("0.6", "0.4"), headers=headers)
    assert saved.status_code == 200
    assert [D(a["allocation_weight"]) for a in saved.json()] == [D("0.6"), D("0.4")]

    over = await async_client.put(url, json=body("0.7", "0.4"), headers=headers)
    assert over.status_code == 422
    assert "must not sum to more than 1" in over.json()["detail"]
    duplicate = await async_client.put(
        url,
        json={"allocations": [{"strategy_id": strategy_ids[0], "allocation_weight": "0.1"}] * 2},
        headers=headers,
    )
    assert duplicate.status_code == 422
    foreign = await async_client.put(
        url,
        json={"allocations": [{"strategy_id": str(uuid.uuid4()), "allocation_weight": "0.1"}]},
        headers=headers,
    )
    assert foreign.status_code == 404

    # Failed updates leave the previous set in place; a new set replaces it entirely.
    assert len((await async_client.get(url, headers=headers)).json()) == 2
    replaced = await async_client.put(url, json=body("0.25"), headers=headers)
    assert [a["strategy_id"] for a in replaced.json()] == [strategy_ids[0]]
    assert (await async_client.put(url, json={"allocations": []}, headers=headers)).json() == []


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------
async def test_portfolios_are_private_to_their_owner(
    async_client: AsyncClient, account, make_user
):
    portfolio_id, alice, aapl = account
    order = (await place(async_client, alice, portfolio_id, aapl.id, "BUY", "1")).json()
    resting = (
        await place(async_client, alice, portfolio_id, aapl.id, "BUY", "1", limit_price="1")
    ).json()
    _, bob = await make_user()
    alice_cash = await cash(async_client, alice, portfolio_id)

    assert (await async_client.get(BASE, headers=bob)).json() == []
    root = f"{BASE}/{portfolio_id}"
    sell_one = {"instrument_id": str(aapl.id), "side": "SELL", "quantity": "1"}
    attempts = [
        ("GET", root, None),
        ("PATCH", root, {"name": "mine now"}),
        ("POST", f"{root}/cash", {"kind": "WITHDRAWAL", "amount": "1"}),
        ("GET", f"{root}/positions", None),
        ("GET", f"{root}/valuation", None),
        ("POST", f"{root}/snapshots", None),
        ("GET", f"{root}/snapshots", None),
        ("POST", f"{root}/orders", {**sell_one}),
        ("GET", f"{root}/orders", None),
        ("POST", f"{root}/orders/process", None),
        ("GET", f"{root}/orders/{order['id']}", None),
        ("POST", f"{root}/orders/{resting['id']}/cancel", None),
        ("GET", f"{root}/trades", None),
        ("GET", f"{root}/ledger", None),
        ("GET", f"{root}/ledger/balances", None),
        ("GET", f"{root}/allocations", None),
        ("PUT", f"{root}/allocations", {"allocations": []}),
    ]
    for method, url, body in attempts:
        as_bob = await async_client.request(method, url, json=body, headers=bob)
        assert as_bob.status_code == 404, f"{method} {url}"
        anonymous = await async_client.request(method, url, json=body)
        assert anonymous.status_code == 401, f"{method} {url}"

    # Nothing Bob tried had any effect.
    assert await cash(async_client, alice, portfolio_id) == alice_cash
    still_resting = await async_client.get(f"{root}/orders/{resting['id']}", headers=alice)
    assert still_resting.json()["status"] == "OPEN"

    # An order id is only valid under its own portfolio.
    other = await open_portfolio(async_client, alice)
    crossed = await async_client.get(f"{BASE}/{other['id']}/orders/{order['id']}", headers=alice)
    assert crossed.status_code == 404
