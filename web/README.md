# QuantTrade web

The browser application for the Quant API: Next.js (App Router), TypeScript, Tailwind,
shadcn/ui components and Recharts.

## Run it

The API must be running (see the repository README). Then:

```bash
pnpm install
pnpm dev                 # http://localhost:3000
```

`API_BASE_URL` is where the API lives (default `http://127.0.0.1:8000`). It is read on
the server only.

## How it talks to the API

The browser never holds a token and never calls the API directly.

- Signing in posts to this app's `/api/session/login`, which calls the API and stores
  the access and refresh tokens in **httpOnly cookies** on this origin.
- Every other request goes to this app's `/api/v1/*`, a gateway that adds the token on
  the server and forwards the request to the same path on the API. When the access
  token has expired the gateway renews it with the refresh token and retries once.
- `src/proxy.ts` redirects signed-out visitors to `/login` (and signed-in ones away
  from it). That is a convenience; the API authorises every request itself.

So there is no CORS between browser and API, nothing for a script on the page to
steal, and cross-site requests carry no session (`SameSite=Lax`, plus an Origin check
on anything that changes data).

## API types

`openapi.json` is the API's OpenAPI document and `src/lib/api/schema.d.ts` is generated
from it. After changing the API:

```bash
python scripts/export_openapi.py     # in the repository root
pnpm gen:api                         # here
```

CI fails if either file is out of date.

## Checks

```bash
pnpm lint
pnpm typecheck
pnpm test          # unit tests (Vitest + Testing Library)
pnpm build
pnpm e2e           # browser tests (Playwright), see below
```

The browser tests walk a new user through the whole product (register, strategy,
backtest, paper orders, ledger, risk, account deletion) and check each main screen for
accessibility problems with axe. They need:

1. the API running with `MARKET_DATA_PROVIDER=synthetic`, so prices are repeatable and
   nothing outside is called, reachable at `API_BASE_URL` (default
   `http://127.0.0.1:8765` for the tests);
2. a production build (`pnpm build`);
3. the browser: `pnpm exec playwright install chromium`.

`SCREENSHOT_DIR=../docs/screenshots pnpm e2e` refreshes the screenshots in the
repository README.

## Deploying to Vercel

1. Import the repository in Vercel and set the **Root Directory** to `web`.
2. Add the environment variable `API_BASE_URL`: the public address of the API, for
   example `https://quanttrade-api.onrender.com`.
3. Deploy, then set the API's `CORS_ORIGINS` and `FRONTEND_BASE_URL` to the address
   Vercel gave the site (emailed links point there).

## Layout

```
src/
  app/
    (auth)/        sign in, register, email confirmation, password reset
    (app)/         the signed-in application (dashboard, market, strategies,
                   backtests, portfolios, risk, settings)
    api/session/   sign in, sign out, change password, delete account (manage cookies)
    api/v1/        gateway to the API
  components/      shared UI, charts, portfolio panels; ui/ is shadcn
  hooks/use-api.ts typed data hooks (TanStack Query)
  lib/api/         generated types, typed client, error handling
  lib/server/      server-only: API access and session cookies
  proxy.ts         redirects by session
e2e/               Playwright tests
```
