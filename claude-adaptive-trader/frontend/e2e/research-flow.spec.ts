import { expect, test } from "@playwright/test";

// One story, in order: research on stored data, a paper replay, then the safety controls.
test.describe.configure({ mode: "serial" });

test("a backtest runs as a background job and opens its stored results", async ({ page }) => {
  await page.goto("/backtesting");
  await page.getByLabel("Strategy", { exact: true }).selectOption("momentum_breakout");
  await page.getByRole("button", { name: "Run backtest" }).click();
  await expect(page.getByText("Equity · Base costs (1x)")).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole("button", { name: "Report" })).toBeVisible();
  await expect(page.getByText("momentum_breakout on development").first()).toBeVisible();
  await page.getByRole("tab", { name: "Configuration" }).click();
  await expect(page.getByText("Software version", { exact: true })).toBeVisible();
});

test("the frozen test segment is not offered for ordinary backtests", async ({ page }) => {
  await page.goto("/backtesting");
  const options = await page.getByLabel("Segment", { exact: true }).locator("option").allTextContents();
  expect(options.join(" ").toLowerCase()).not.toContain("test");
  const response = await page.request.post("/api/backtests", {
    headers: { "X-CAT-Client": "e2e" },
    data: { strategy: "momentum_breakout", segment: "test" },
  });
  expect(response.status()).toBe(422);
});

test("a historical replay trades on paper and its results appear on every page", async ({ page }) => {
  await page.goto("/paper");
  await page.getByRole("tab", { name: "Historical replay", exact: true }).click();
  await page.getByRole("tab", { name: "Forward test one strategy", exact: true }).click();
  await page.getByLabel("Strategy", { exact: true }).selectOption("momentum_breakout");
  await page.getByLabel("Speed (candles/s)").fill("3000");
  await page.getByRole("button", { name: "Start replay" }).click();
  await expect(page.getByText("HISTORICAL REPLAY - simulated trading on stored candles").first()).toBeVisible();
  await expect
    .poll(async () => (await (await page.request.get("/api/paper/status-lite")).json()).status, { timeout: 90_000, intervals: [1000] })
    .toBe("stopped");

  const trades = await (await page.request.get("/api/portfolio/trades")).json();
  expect(trades.length).toBeGreaterThan(0);

  await page.goto("/portfolio");
  await expect(page.getByText(`${trades.length} trades`, { exact: false })).toBeVisible();
  await page.goto("/analytics");
  await expect(page.getByText("Monthly returns")).toBeVisible();
  await expect(page.getByText("Strategy comparison")).toBeVisible();
  await page.goto("/");
  await expect(page.getByText("HISTORICAL REPLAY - simulated trading on stored candles")).toBeVisible();
  await expect(page.getByText(`${trades.length} completed trade`)).toBeVisible();
});

test("the kill switch is turned on and off with a recorded reason", async ({ page }) => {
  await page.goto("/risk");
  await page.getByRole("button", { name: "Kill switch", exact: true }).click();
  await page.getByLabel("Reason (recorded in the log)").fill("end-to-end test");
  await page.getByRole("button", { name: "Turn on", exact: true }).click();
  await expect(page.getByText("ON: new entries are blocked")).toBeVisible();
  await expect(page.getByText("KILL SWITCH", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Turn kill switch off" }).click();
  await page.getByLabel("Reason (recorded in the log)").fill("end-to-end test finished");
  await page.getByRole("button", { name: "Turn off", exact: true }).click();
  await expect(page.getByText("Off: entries allowed by the risk checks")).toBeVisible();
});

test("settings above a hard safety limit are refused", async ({ page }) => {
  await page.goto("/settings");
  await page.getByLabel("Risk Per Trade Pct", { exact: true }).fill("5");
  await page.getByRole("button", { name: /^Save/ }).click();
  await expect(page.getByText("Settings were not saved")).toBeVisible();
  const settings = await (await page.request.get("/api/settings")).json();
  expect(settings.effective.risk.risk_per_trade_pct).toBeLessThanOrEqual(1);
});

test("state-changing requests from other pages are refused", async ({ page }) => {
  const noHeader = await page.request.post("/api/paper/kill-switch", { data: { on: true, reason: "no header" } });
  expect(noHeader.status()).toBe(403);
  const foreign = await page.request.post("/api/paper/kill-switch", {
    headers: { "X-CAT-Client": "e2e", Origin: "https://evil.example" },
    data: { on: true, reason: "foreign origin" },
  });
  expect(foreign.status()).toBe(403);
  const status = await (await page.request.get("/api/paper/status-lite")).json();
  expect(status.kill_switch).toBe(false);
});
