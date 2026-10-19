import { expect, test, type Page } from "@playwright/test";

const PAGES = [
  ["/", "Overview"],
  ["/market", "Market Terminal"],
  ["/strategies", "Strategy Management"],
  ["/backtesting", "Backtesting Laboratory"],
  ["/paper", "Paper Trading"],
  ["/portfolio", "Portfolio & History"],
  ["/risk", "Risk Management"],
  ["/analytics", "Performance Analytics"],
  ["/logs", "Logs & Diagnostics"],
  ["/settings", "Settings"],
] as const;

function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

for (const [path, title] of PAGES) {
  test(`${title} renders from real backend state`, async ({ page }) => {
    const errors = collectErrors(page);
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
    await expect(page.getByText("PAPER", { exact: true })).toBeVisible();
    await page.waitForLoadState("networkidle");
    expect(errors).toEqual([]);
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
  });
}

test("the sidebar reaches every page", async ({ page }) => {
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Main" });
  for (const [path, title] of PAGES) {
    await nav.getByRole("link", { name: title }).click();
    await expect(page).toHaveURL(new RegExp(`${path === "/" ? "/$" : path}$`));
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
  }
});

test("pages fit a phone screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const [path, title] of PAGES) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
    await page.waitForLoadState("networkidle");
    expect(await horizontalOverflow(page), `${path} overflows horizontally`).toBeLessThanOrEqual(0);
  }
});

test("portfolio values are labelled as simulated", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("SIMULATED").first()).toBeVisible();
  await expect(page.getByText(/not realised money/)).toBeVisible();
});
