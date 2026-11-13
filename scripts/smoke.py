"""End-to-end smoke test against a running API (real HTTP, real database, live provider).

Usage::

    python scripts/smoke.py                         # http://127.0.0.1:8000
    python scripts/smoke.py https://api.example.com # any deployment

It registers throwaway users, so point it at a development or scratch database
unless you mean to leave those rows behind. Exits non-zero on the first failure.
"""

import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx

ROOT = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
BASE = ROOT + "/api/v1"
PASSWORD = "SmokeTest123!"
client = httpx.Client(timeout=90)


def fail(message: str) -> None:
    print("FAIL", message)
    sys.exit(1)


def call(method: str, path: str, expect: int | None = None, **kwargs) -> httpx.Response:
    response = client.request(method, BASE + path, **kwargs)
    if expect is not None and response.status_code != expect:
        fail(f"{method} {path}: {response.status_code} (wanted {expect}) {response.text[:600]}")
    return response


def check(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def new_user() -> dict[str, str]:
    email = f"smoke_{uuid.uuid4().hex[:10]}@quant.example"
    call("POST", "/auth/register", 201, json={"email": email, "password": PASSWORD})
    token = call("POST", "/auth/login", 200, json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {token.json()['access_token']}"}


def check_sessions() -> None:
    email = f"smoke_{uuid.uuid4().hex[:10]}@quant.example"
    call("POST", "/auth/register", 201, json={"email": email, "password": PASSWORD})
    first = call("POST", "/auth/login", 200, json={"email": email, "password": PASSWORD}).json()
    second = call("POST", "/auth/refresh", 200,
                  json={"refresh_token": first["refresh_token"]}).json()
    check(second["refresh_token"] != first["refresh_token"], "refresh token was not rotated")
    bearer = {"Authorization": f"Bearer {second['access_token']}"}
    call("GET", "/auth/me", 200, headers=bearer)
    call("POST", "/auth/logout-all", 204, headers=bearer)
    call("GET", "/auth/me", 401, headers=bearer)
    call("POST", "/auth/refresh", 401, json={"refresh_token": second["refresh_token"]})
    call("POST", "/auth/login", 401, json={"email": email, "password": "wrong-password"})
    relogin = call("POST", "/auth/login", 200, json={"email": email, "password": PASSWORD}).json()
    call("POST", "/auth/delete-account", 204, json={"password": PASSWORD},
         headers={"Authorization": f"Bearer {relogin['access_token']}"})
    call("POST", "/auth/login", 401, json={"email": email, "password": PASSWORD})
    print("sessions: refresh rotates, sign-out-everywhere revokes, account deletion works")


for _ in range(60):
    try:
        if client.get(BASE + "/health").status_code == 200:
            break
    except httpx.HTTPError:
        pass
    time.sleep(0.5)
else:
    fail(f"no healthy API at {BASE}")

print("database:", call("GET", "/health/ready", 200).json())
check_sessions()
headers = new_user()

# --- market data needs a token
call("GET", "/market-data/instruments", 401)
listing = call("GET", "/market-data/instruments", 200, headers=headers)
check(listing.headers["X-Total-Count"].isdigit(), "list did not report a total")
check("X-Request-ID" in listing.headers, "response has no request id")
instruments = listing.json()
by_symbol = {(i["exchange"]["mic"], i["symbol"]): i["id"] for i in instruments}
check(("XNAS", "AAPL") in by_symbol, "reference data is not seeded (python -m app.cli seed)")
aapl = by_symbol[("XNAS", "AAPL")]
msft = by_symbol[("XNAS", "MSFT")]
spy = by_symbol[("XNYS", "SPY")]
print("instruments:", len(instruments), "(anonymous market data: 401)")

# --- strategies and a backtest on live data
types = call("GET", "/strategies/types", 200, headers=headers).json()
print("strategy types:", [t["type"] for t in types])
strategy = call(
    "POST", "/strategies", 201, headers=headers,
    json={"name": "SMA 20/50", "strategy_type": "SMA_CROSSOVER", "parameters": {}},
).json()

request = {
    "strategy_version_id": strategy["latest_version"]["id"], "instrument_id": aapl,
    "start": "2022-01-01", "end": "2024-12-31", "initial_capital": "100000",
}
started = time.time()
experiment = call("POST", "/experiments/backtests", 201, headers=headers, json=request).json()
print(f"backtest: {experiment['status']} in {time.time() - started:.1f}s",
      experiment["error_message"] or "")
check(experiment["status"] == "COMPLETED", "backtest did not complete")
metrics = experiment["results"]["metrics"]
print("  data:", experiment["results"]["data"])
print("  metrics:", json.dumps({k: metrics[k] for k in (
    "total_return", "cagr", "sharpe_ratio", "max_drawdown", "closed_trades",
    "benchmark_buy_and_hold_return", "exposure")}))

started = time.time()
again = call("POST", "/experiments/backtests", 201, headers=headers, json=request).json()
check(again["data_fingerprint"] == experiment["data_fingerprint"], "fingerprint changed on rerun")
check(again["results"]["final_equity"] == experiment["results"]["final_equity"],
      "rerun on stored candles gave a different result")
print(f"  rerun from the candle store in {time.time() - started:.2f}s: identical")

# --- paper trading at live quotes
portfolio = call(
    "POST", "/portfolios", 201, headers=headers,
    json={"name": "Smoke", "base_currency": "USD", "initial_capital": "100000"},
).json()
pid = portfolio["id"]
orders = f"/portfolios/{pid}/orders"

buy = call("POST", orders, 201, headers={**headers, "Idempotency-Key": "s-1"},
           json={"instrument_id": aapl, "side": "BUY", "quantity": "50"}).json()
check(buy["status"] == "FILLED", f"buy not filled: {buy['rejection_reason']}")
replay = call("POST", orders, 200, headers={**headers, "Idempotency-Key": "s-1"},
              json={"instrument_id": aapl, "side": "BUY", "quantity": "50"}).json()
check(replay["id"] == buy["id"], "idempotent replay created a second order")
call("POST", orders, 409, headers={**headers, "Idempotency-Key": "s-1"},
     json={"instrument_id": aapl, "side": "BUY", "quantity": "51"})
call("POST", orders, 201, headers=headers,
     json={"instrument_id": msft, "side": "BUY", "quantity": "20"})
limit = call("POST", orders, 201, headers=headers,
             json={"instrument_id": spy, "side": "BUY", "order_type": "LIMIT",
                   "quantity": "5", "limit_price": "1"}).json()
check(limit["status"] == "OPEN", "far limit order should rest")
sell = call("POST", orders, 201, headers=headers,
            json={"instrument_id": aapl, "side": "SELL", "quantity": "20"}).json()
check(sell["status"] == "FILLED", "sell not filled")
reject = call("POST", orders, 201, headers=headers,
              json={"instrument_id": aapl, "side": "BUY", "quantity": "100000"}).json()
check(reject["status"] == "REJECTED", "oversized order should be rejected")
cancelled = call("POST", f"{orders}/{limit['id']}/cancel", 200, headers=headers).json()
check(cancelled["status"] == "CANCELLED", "cancel failed")
print("orders: fill, replay (200), key reuse (409), resting limit, sell, reject, cancel")

valuation = call("GET", f"/portfolios/{pid}/valuation", 200, headers=headers).json()
snapshot = call("POST", f"/portfolios/{pid}/snapshots", 201, headers=headers).json()
check(
    Decimal(snapshot["total_equity"]) == Decimal(valuation["total_equity"]),
    "snapshot != valuation",
)
ledger = call("GET", f"/portfolios/{pid}/ledger/balances", 200, headers=headers).json()
check(ledger["is_balanced"] and ledger["is_reconciled"], f"ledger does not reconcile: {ledger}")
print("valuation: equity", valuation["total_equity"], "cash", valuation["cash"],
      "| ledger balanced and reconciled")

# --- risk
for method in ("HISTORICAL", "PARAMETRIC", "MONTE_CARLO"):
    report = call("POST", "/risk/reports", 201, headers=headers,
                  json={"portfolio_id": pid, "method": method, "confidence_level": 0.95}).json()
    r = report["results"]
    print(f"risk {method}: VaR {r['value_at_risk']:.4%} ES {r['expected_shortfall']:.4%} "
          f"obs {r['observations']}")
    check(0 < r["value_at_risk"] <= r["expected_shortfall"], "VaR/ES out of order")
exp_report = call("POST", "/risk/reports", 201, headers=headers,
                  json={"experiment_id": experiment["id"], "method": "HISTORICAL"}).json()

# --- isolation
other = new_user()
for path in (f"/portfolios/{pid}", f"/experiments/{experiment['id']}",
             f"/strategies/{strategy['id']}", f"/risk/reports/{exp_report['id']}"):
    call("GET", path, 404, headers=other)
    call("GET", path, 401)
print("isolation: another user gets 404, anonymous gets 401")

# --- concurrency: cash is never overspent, a key creates one order
racer = call("POST", "/portfolios", 201, headers=headers,
             json={"name": "Race", "base_currency": "USD", "initial_capital": "100000"}).json()
race_orders = f"/portfolios/{racer['id']}/orders"
price = Decimal(next(p["market_price"] for p in valuation["positions"] if p["symbol"] == "AAPL"))
quantity = int(Decimal("45000") / price)  # two fit in 100k, a third does not


def submit(extra_headers: dict[str, str]) -> httpx.Response:
    return httpx.post(BASE + race_orders, headers={**headers, **extra_headers}, timeout=90,
                      json={"instrument_id": aapl, "side": "BUY", "quantity": str(quantity)})


with ThreadPoolExecutor(max_workers=12) as pool:
    results = list(pool.map(submit, [{}] * 12))
filled = sum(1 for r in results if r.json()["status"] == "FILLED")
check(filled == 2, f"expected exactly 2 of 12 racing buys to fill, got {filled}")

with ThreadPoolExecutor(max_workers=10) as pool:
    results = list(pool.map(submit, [{"Idempotency-Key": "race"}] * 10))
codes = sorted(r.status_code for r in results)
check(len({r.json()["id"] for r in results}) == 1, "one idempotency key produced several orders")
check(codes.count(201) == 1 and codes.count(200) == 9, f"unexpected statuses {codes}")
race_ledger = call("GET", f"/portfolios/{racer['id']}/ledger/balances", 200, headers=headers).json()
check(race_ledger["is_balanced"] and race_ledger["is_reconciled"], "ledger broke under concurrency")
journal = call("GET", f"/portfolios/{racer['id']}/ledger", 200, headers=headers).json()
sequences = sorted(t["sequence"] for t in journal)
check(sequences == list(range(1, len(sequences) + 1)), f"ledger sequence has gaps: {sequences}")
print("concurrency: 2 of 12 racing buys filled; 10 same-key submissions made one order")

print("SMOKE OK")
