import { defineConfig, devices } from "@playwright/test";

// End-to-end tests drive the real backend and the built interface in a browser. The backend is
// started by e2e/server.mjs with its own databases, so the tests never touch your data.
export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:8765",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: "npm run build && node e2e/server.mjs",
    url: "http://127.0.0.1:8765/api/health",
    timeout: 240_000,
    reuseExistingServer: false,
    stdout: "ignore",
    stderr: "pipe",
  },
});
