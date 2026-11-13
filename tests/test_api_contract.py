"""Cross-cutting API behaviour clients rely on: error shape, request ids, totals, names."""

import json
import logging
import uuid

from httpx import AsyncClient

from app.core.logging import JsonFormatter, RequestContextFilter, request_id_var
from app.main import app
from helpers import FakeProvider

API = "/api/v1"


# ------------------------------------------------------------------ error shape
async def test_every_error_has_a_detail_and_a_code(async_client: AsyncClient, auth_headers):
    cases = [
        (await async_client.get(f"{API}/auth/me"), 401, "unauthorized"),
        (await async_client.get(f"{API}/nope"), 404, "not_found"),
        (await async_client.get(f"{API}/portfolios/{uuid.uuid4()}", headers=auth_headers),
         404, "not_found"),
        (await async_client.post(f"{API}/auth/login", json={"email": "nope"}),
         422, "validation_error"),
        (await async_client.delete(f"{API}/auth/me", headers=auth_headers),
         405, "method_not_allowed"),
        (await async_client.post(
            f"{API}/strategies",
            json={"name": "x", "strategy_type": "NOT_A_TYPE", "parameters": {}},
            headers=auth_headers,
        ), 422, "unprocessable"),
    ]
    for response, status, code in cases:
        assert response.status_code == status, response.text
        body = response.json()
        assert set(body) == {"detail", "code"}, body
        assert body["code"] == code


async def test_specific_codes_let_clients_branch_without_parsing_text(async_client: AsyncClient):
    body = {"email": "dup@quant.example", "password": "Password123!"}
    assert (await async_client.post(f"{API}/auth/register", json=body)).status_code == 201
    duplicate = await async_client.post(f"{API}/auth/register", json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "email_taken"

    wrong = await async_client.post(
        f"{API}/auth/login", json={"email": "dup@quant.example", "password": "nope-nope-nope"}
    )
    assert wrong.json()["code"] == "invalid_credentials"
    assert wrong.headers["WWW-Authenticate"] == "Bearer"


async def test_validation_errors_keep_the_field_list(async_client: AsyncClient):
    response = await async_client.post(
        f"{API}/auth/register", json={"email": "a@quant.example", "password": "short"}
    )
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert detail[0]["loc"] == ["body", "password"]


# ------------------------------------------------------------------ request ids
async def test_each_response_carries_a_request_id(async_client: AsyncClient):
    first = await async_client.get(f"{API}/health")
    second = await async_client.get(f"{API}/health")
    assert len(first.headers["X-Request-ID"]) == 32
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


async def test_a_supplied_request_id_is_kept_and_an_unsafe_one_replaced(
    async_client: AsyncClient,
):
    kept = await async_client.get(f"{API}/health", headers={"X-Request-ID": "trace-abc.123"})
    assert kept.headers["X-Request-ID"] == "trace-abc.123"

    for unsafe in ("has spaces", "x" * 65, "new\tline"):
        replaced = await async_client.get(f"{API}/health", headers={"X-Request-ID": unsafe})
        assert replaced.headers["X-Request-ID"] != unsafe
        assert len(replaced.headers["X-Request-ID"]) == 32


async def test_error_responses_carry_a_request_id_too(async_client: AsyncClient):
    response = await async_client.get(f"{API}/auth/me")
    assert response.status_code == 401
    assert "X-Request-ID" in response.headers


async def test_requests_are_logged_once_with_their_id(async_client: AsyncClient, caplog):
    with caplog.at_level(logging.INFO, logger="app.request"):
        response = await async_client.get(f"{API}/auth/me", headers={"X-Request-ID": "req-42"})
    records = [r for r in caplog.records if r.name == "app.request"]
    assert len(records) == 1
    record = records[0]
    assert (record.method, record.path, record.status) == ("GET", f"{API}/auth/me", 401)
    assert record.duration_ms >= 0
    assert response.headers["X-Request-ID"] == "req-42"


async def test_health_checks_are_not_logged_at_info(async_client: AsyncClient, caplog):
    with caplog.at_level(logging.INFO, logger="app.request"):
        await async_client.get(f"{API}/health")
        await async_client.get(f"{API}/health/ready")
    assert [r for r in caplog.records if r.name == "app.request"] == []


def test_json_log_lines_include_request_id_and_extra_fields():
    formatter = JsonFormatter()
    token = request_id_var.set("req-7")
    try:
        record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello %s", ("x",), None)
        record.status = 201
        RequestContextFilter().filter(record)
        line = json.loads(formatter.format(record))
    finally:
        request_id_var.reset(token)
    assert line["message"] == "hello x"
    assert line["level"] == "INFO"
    assert line["logger"] == "app.test"
    assert line["request_id"] == "req-7"
    assert line["status"] == 201
    assert line["time"].endswith("+00:00")


def test_json_log_lines_include_the_traceback():
    try:
        raise ValueError("bad")
    except ValueError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    line = json.loads(JsonFormatter().format(record))
    assert "ValueError: bad" in line["exception"]
    assert "request_id" not in line


# ----------------------------------------------------------------------- health
async def test_liveness_and_readiness(async_client: AsyncClient):
    for path in ("/health", "/health/live"):
        response = await async_client.get(API + path)
        assert response.json() == {"status": "ok", "service": "quant-api"}
    for path in ("/health/ready", "/health/database"):
        response = await async_client.get(API + path)
        assert response.json() == {"status": "ok", "database": "connected"}


# ----------------------------------------------------------------- total counts
async def test_lists_report_the_total_in_a_header(async_client: AsyncClient, auth_headers):
    for index in range(3):
        created = await async_client.post(
            f"{API}/strategies",
            json={"name": f"S{index}", "strategy_type": "BUY_AND_HOLD", "parameters": {}},
            headers=auth_headers,
        )
        assert created.status_code == 201
    archived = created.json()["id"]
    await async_client.patch(
        f"{API}/strategies/{archived}", json={"is_archived": True}, headers=auth_headers
    )

    page = await async_client.get(f"{API}/strategies?limit=1", headers=auth_headers)
    assert len(page.json()) == 1
    assert page.headers["X-Total-Count"] == "2"  # filters apply, limit does not

    everything = await async_client.get(
        f"{API}/strategies?limit=1&offset=2&include_archived=true", headers=auth_headers
    )
    assert len(everything.json()) == 1
    assert everything.headers["X-Total-Count"] == "3"

    empty = await async_client.get(f"{API}/experiments", headers=auth_headers)
    assert empty.json() == []
    assert empty.headers["X-Total-Count"] == "0"


async def test_totals_are_scoped_to_the_caller(async_client: AsyncClient, make_user):
    _, mine = await make_user()
    _, theirs = await make_user()
    for headers, count in ((mine, 2), (theirs, 1)):
        for index in range(count):
            await async_client.post(
                f"{API}/portfolios",
                json={"name": f"P{index}", "base_currency": "USD", "initial_capital": "1000"},
                headers=headers,
            )
    assert (
        await async_client.get(f"{API}/portfolios", headers=mine)
    ).headers["X-Total-Count"] == "2"
    assert (
        await async_client.get(f"{API}/portfolios", headers=theirs)
    ).headers["X-Total-Count"] == "1"


async def test_trading_lists_report_totals(
    async_client: AsyncClient, auth_headers, instruments, fake_provider: FakeProvider
):
    fake_provider.set_quote("AAPL", "100")
    aapl = str(instruments["XNAS:AAPL"].id)
    portfolio = (
        await async_client.post(
            f"{API}/portfolios",
            json={"name": "P", "base_currency": "USD", "initial_capital": "100000"},
            headers=auth_headers,
        )
    ).json()
    base = f"{API}/portfolios/{portfolio['id']}"
    for _ in range(3):
        order = await async_client.post(
            f"{base}/orders",
            json={"instrument_id": aapl, "side": "BUY", "quantity": "1"},
            headers=auth_headers,
        )
        assert order.json()["status"] == "FILLED"
    await async_client.post(
        f"{base}/orders",
        json={"instrument_id": aapl, "side": "BUY", "quantity": "1", "order_type": "LIMIT",
              "limit_price": "1"},
        headers=auth_headers,
    )

    orders = await async_client.get(f"{base}/orders?limit=2", headers=auth_headers)
    assert (len(orders.json()), orders.headers["X-Total-Count"]) == (2, "4")
    filled = await async_client.get(f"{base}/orders?status=filled&limit=1", headers=auth_headers)
    assert filled.headers["X-Total-Count"] == "3"
    trades = await async_client.get(f"{base}/trades?limit=1", headers=auth_headers)
    assert (len(trades.json()), trades.headers["X-Total-Count"]) == (1, "3")
    ledger = await async_client.get(f"{base}/ledger?limit=2", headers=auth_headers)
    assert (len(ledger.json()), ledger.headers["X-Total-Count"]) == (2, "4")  # deposit + 3 fills


async def test_instrument_search_reports_totals_in_a_stable_order(
    async_client: AsyncClient, auth_headers, instruments
):
    first = await async_client.get(f"{API}/market-data/instruments?limit=3", headers=auth_headers)
    second = await async_client.get(
        f"{API}/market-data/instruments?limit=3&offset=3", headers=auth_headers
    )
    assert first.headers["X-Total-Count"] == str(len(instruments))
    symbols = [row["symbol"] for row in first.json() + second.json()]
    assert symbols == sorted(symbols)
    assert len(set(row["id"] for row in first.json() + second.json())) == 6

    nasdaq = await async_client.get(
        f"{API}/market-data/instruments?exchange_mic=XNAS&limit=1", headers=auth_headers
    )
    assert int(nasdaq.headers["X-Total-Count"]) >= 2


async def test_browsers_may_read_the_custom_headers(async_client: AsyncClient):
    response = await async_client.get(
        f"{API}/health", headers={"Origin": "http://localhost:3000"}
    )
    exposed = response.headers["access-control-expose-headers"]
    for header in ("X-Total-Count", "X-Request-ID", "Retry-After"):
        assert header in exposed


# ---------------------------------------------------------------- operation ids
def test_operation_ids_are_the_endpoint_names_and_unique():
    schema = app.openapi()
    ids = [
        operation["operationId"]
        for path in schema["paths"].values()
        for operation in path.values()
    ]
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)
    for expected in ("login", "refresh_tokens", "run_backtest", "submit_order", "list_strategies"):
        assert expected in ids
    assert all(i.isidentifier() for i in ids)


# ------------------------------------------------------------------------ email
async def test_console_email_logs_the_link_only_outside_production(caplog):
    from test_config import build

    from app.core.email import ConsoleEmailSender, Email, build_email_sender

    email = Email(to="a@quant.example", subject="Reset", body="link?token=SECRET-TOKEN")
    with caplog.at_level(logging.INFO, logger="app.core.email"):
        await ConsoleEmailSender().send(email)
        assert "SECRET-TOKEN" in caplog.text
        caplog.clear()
        await build_email_sender(build()).send(email)  # production settings
    assert "SECRET-TOKEN" not in caplog.text
    assert "was not sent" in caplog.text


def test_smtp_backend_is_selected_from_settings():
    from test_config import build

    from app.core.email import SmtpEmailSender, build_email_sender

    sender = build_email_sender(build(email_backend="smtp", smtp_host="smtp.example.com"))
    assert isinstance(sender, SmtpEmailSender)
