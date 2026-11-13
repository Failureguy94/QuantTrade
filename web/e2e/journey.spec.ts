import AxeBuilder from "@axe-core/playwright";
import { type Page, expect, test } from "@playwright/test";

const PASSWORD = "E2ePassword1!";

function uniqueEmail(): string {
  return `e2e_${Date.now()}_${Math.floor(Math.random() * 1e6)}@quant.example`;
}

/**
 * Text on screen. Next keeps the page you came from mounted but hidden, so a plain
 * text lookup can match that page too.
 */
function text(page: Page, value: string | RegExp) {
  return page.getByText(value).filter({ visible: true });
}

/** Save a screenshot when SCREENSHOT_DIR is set (used to refresh the README images). */
async function snap(page: Page, name: string) {
  const directory = process.env.SCREENSHOT_DIR;
  if (!directory) return;
  await page.waitForTimeout(400); // let charts finish drawing
  await page.screenshot({ path: `${directory}/${name}.png`, fullPage: true });
}

/** A form control on screen, by its label (see `text` for why visibility matters). */
function field(page: Page, label: string | RegExp, options?: { exact?: boolean }) {
  return page.getByLabel(label, options).filter({ visible: true });
}

/** An error message. Matched by its text: Next adds an empty alert of its own. */
function alert(page: Page, message: string) {
  return page.getByRole("alert").filter({ hasText: message });
}

/** No serious or critical accessibility violations on the current page. */
async function expectAccessible(page: Page) {
  // Toasts fade in and out; caught mid-fade their text is momentarily translucent,
  // which reads as low contrast. At rest they use the standard foreground colours.
  const results = await new AxeBuilder({ page }).exclude("[data-sonner-toaster]").analyze();
  const blocking = results.violations.filter(
    (violation) => violation.impact === "serious" || violation.impact === "critical",
  );
  expect(
    blocking.map((violation) => `${violation.id}: ${violation.nodes[0]?.target.join(" ")}`),
  ).toEqual([]);
}

async function register(page: Page, email: string) {
  await page.goto("/register");
  await field(page, "Email").fill(email);
  await field(page, "Password", { exact: true }).fill(PASSWORD);
  await field(page, "Confirm password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
}

test("signed-out visitors are sent to sign in and back again", async ({ page }) => {
  await page.goto("/portfolios");
  await expect(page).toHaveURL(/\/login\?next=%2Fportfolios$/);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expectAccessible(page);

  const email = uniqueEmail();
  await register(page, email);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.goto("/portfolios");
  await field(page, "Email").fill(email);
  await field(page, "Password").fill("wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(alert(page, "Incorrect email or password")).toBeVisible();

  await field(page, "Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/portfolios$/);
  await expect(page.getByRole("heading", { name: "Portfolios" })).toBeVisible();
});

test("landing page", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Test a trading idea");
  await snap(page, "landing");
  await expectAccessible(page);
  await page.getByRole("link", { name: "Get started" }).click();
  await expect(page).toHaveURL(/\/register$/);
  await expectAccessible(page);
});

test("from a new account to a measured result", async ({ page }) => {
  const email = uniqueEmail();
  await register(page, email);

  // A new account is walked through the four steps.
  await expect(text(page, "Getting started")).toBeVisible();
  await expect(page.getByRole("link", { name: "Create a strategy" })).toBeVisible();
  await expectAccessible(page);

  // --- strategy
  await page.getByRole("link", { name: "Strategies", exact: true }).click();
  await page.getByRole("button", { name: "New strategy" }).first().click();
  await field(page, "Type").selectOption({ label: "Moving-average crossover" });
  await field(page, "Name").fill("E2E crossover");
  await field(page, "Fast window").fill("10");
  await field(page, "Slow window").fill("30");
  await page.getByRole("button", { name: "Create strategy" }).click();
  await expect(page.getByRole("heading", { name: "E2E crossover" })).toBeVisible();
  await expect(text(page, "fast_window=10")).toBeVisible();

  // A new version is added rather than the old one edited.
  await field(page, "Fast window").fill("12");
  await page.getByRole("button", { name: "Save as new version" }).click();
  await expect(page.getByRole("cell", { name: "v2", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "v1", exact: true })).toBeVisible();

  // --- backtest (queued, run by the worker, page updates itself)
  await page.getByRole("link", { name: "Backtest this" }).click();
  await expect(page.getByRole("heading", { name: "New backtest" })).toBeVisible();
  await expect(field(page, "Strategy")).toContainText("E2E crossover");
  await expect(field(page, "Instrument").locator("option:checked")).toContainText("AAPL");
  await page.getByRole("button", { name: "Run backtest" }).click();
  await expect(page).toHaveURL(/\/backtests\/[0-9a-f-]{36}$/);
  await expect(text(page, "Total return")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("img", { name: "Equity over time" })).toBeVisible();
  await expect(page.getByRole("img", { name: "Drawdown over time" })).toBeVisible();
  await expect(text(page, "SYNTHETIC")).toBeVisible();
  await snap(page, "backtest");
  await expectAccessible(page);
  const backtestUrl = page.url();

  await page.getByRole("link", { name: "Backtests", exact: true }).first().click();
  await expect(page.getByRole("link", { name: /SMA crossover v2/ })).toBeVisible();
  await expect(page.getByRole("cell", { name: "completed", exact: true })).toBeVisible();

  // --- paper portfolio
  await page.getByRole("link", { name: "Portfolios", exact: true }).click();
  await page.getByRole("button", { name: "New portfolio" }).first().click();
  await field(page, "Name").fill("E2E paper");
  await page.getByRole("button", { name: "Open portfolio" }).click();
  await expect(page.getByRole("heading", { name: "E2E paper" })).toBeVisible();
  await expect(text(page, "No positions")).toBeVisible();

  await page.getByRole("tab", { name: "Orders" }).click();
  await expect(field(page, "Instrument").locator("option:checked")).toContainText("AAPL");
  await field(page, "Quantity").fill("10");
  await page.getByRole("button", { name: "Buy AAPL", exact: true }).click();
  await expect(page.getByRole("cell", { name: "filled" })).toBeVisible();

  // More than the account can pay for is rejected, with the reason shown.
  await field(page, "Quantity").fill("1000000");
  await page.getByRole("button", { name: "Buy AAPL", exact: true }).click();
  await expect(alert(page, "Rejected")).toBeVisible();

  // A limit far below the market rests, and can be cancelled.
  await field(page, "Type").selectOption("LIMIT");
  await field(page, "Quantity").fill("1");
  await field(page, /Limit price/).fill("0.01");
  await page.getByRole("button", { name: "Buy AAPL", exact: true }).click();
  await expect(page.getByRole("cell", { name: "open" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("cell", { name: "cancelled" })).toBeVisible();

  await page.getByRole("tab", { name: "Overview" }).click();
  await expect(page.getByRole("cell", { name: "AAPL", exact: true })).toBeVisible();
  await snap(page, "portfolio");

  await page.getByRole("tab", { name: "Trades" }).click();
  await expect(page.getByRole("cell", { name: "Buy AAPL" })).toBeVisible();

  await page.getByRole("tab", { name: "Ledger" }).click();
  await expect(text(page, "Debits and credits sum to zero")).toBeVisible();
  await expect(text(page, "Ledger matches the portfolio's cash and positions")).toBeVisible();
  await expectAccessible(page);

  // --- risk, for the portfolio and then for the backtest
  await page.getByRole("link", { name: "Measure risk" }).click();
  await expect(field(page, "Portfolio")).toContainText("E2E paper");
  await page.getByRole("button", { name: "Calculate" }).click();
  await expect(text(page, "Expected shortfall").first()).toBeVisible();
  await expect(page.getByRole("cell", { name: "Historical" })).toBeVisible();

  await field(page, "Method").selectOption("PARAMETRIC");
  await page.getByRole("button", { name: "Calculate" }).click();
  await expect(page.getByRole("img", { name: /Value at risk and expected shortfall/ })).toBeVisible();
  await snap(page, "risk");

  await page.goto(backtestUrl);
  await page.getByRole("link", { name: "Measure risk" }).click();
  await expect(field(page, "Backtest")).toContainText("SMA crossover v2 on AAPL");
  await page.getByRole("button", { name: "Calculate" }).click();
  await expect(page.getByRole("cell", { name: /SMA crossover v2 on AAPL/ })).toBeVisible();
  await expectAccessible(page);

  // --- the dashboard now reflects all of it
  await page.getByRole("link", { name: "Dashboard" }).click();
  await expect(text(page, "Getting started")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "E2E paper" })).toBeVisible();
  await snap(page, "dashboard");

  // --- market data
  await page.getByRole("link", { name: "Market data" }).click();
  await field(page, "Search instruments").fill("micro");
  await page.getByRole("button", { name: /MSFT/ }).click();
  await expect(page.getByRole("img", { name: "Price history" })).toBeVisible();
  await expect(text(page, "Last close")).toBeVisible();
  await snap(page, "market");

  // --- account
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(text(page, email).first()).toBeVisible();
  await field(page, "Current password").fill(PASSWORD);
  await field(page, "New password", { exact: true }).fill("E2eChanged2!");
  await field(page, "Confirm new password").fill("E2eChanged2!");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(text(page, "Password changed")).toBeVisible();
  // Still signed in on this device after the change.
  await page.reload();
  await expect(text(page, email).first()).toBeVisible();

  await page.getByRole("button", { name: "Delete my account" }).click();
  await page.getByRole("dialog").getByLabel("Password").fill("E2eChanged2!");
  await page.getByRole("button", { name: "Delete everything" }).click();
  await expect(page).toHaveURL(/\/$/);

  await page.goto("/login");
  await field(page, "Email").fill(email);
  await field(page, "Password").fill("E2eChanged2!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(alert(page, "Incorrect email or password")).toBeVisible();
});
