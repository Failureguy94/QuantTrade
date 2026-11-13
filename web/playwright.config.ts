import { defineConfig, devices } from "@playwright/test";

/**
 * Browser tests. They need the API running with MARKET_DATA_PROVIDER=synthetic (so
 * prices are repeatable and no outside service is called) and a production build of
 * this app (`pnpm build`). See the README.
 */
const PORT = Number(process.env.E2E_PORT ?? 3100);

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `pnpm start --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: !process.env.CI,
    env: { API_BASE_URL: process.env.API_BASE_URL ?? "http://127.0.0.1:8765" },
    timeout: 60_000,
  },
});
