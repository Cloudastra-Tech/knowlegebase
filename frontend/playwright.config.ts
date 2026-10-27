import { defineConfig, devices } from "@playwright/test"

// End-to-end smoke tests (e2e/): the built site and the API on one port, as
// production serves them, on throwaway stores with a fake OpenAI
// (../tests/e2e/server.py). Run: `npm run build && npm run e2e`.
//   E2E_PYTHON           the Python with requirements.txt (default: python)
//   E2E_PORT             the app's port (default 8045; the fake OpenAI takes the next)
//   PLAYWRIGHT_CHROMIUM  a Chrome or Chromium already on this machine, instead of the
//                        one `npx playwright install chromium` downloads (CI does that)
const port = Number(process.env.E2E_PORT ?? 8045)
const python = process.env.E2E_PYTHON ?? "python"
const executablePath = process.env.PLAYWRIGHT_CHROMIUM || undefined

export default defineConfig({
  testDir: "./e2e",
  // One server with one database: the specs run one at a time, in file order.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], launchOptions: { executablePath } },
    },
  ],
  webServer: {
    command: `${python} ../tests/e2e/server.py`,
    url: `http://127.0.0.1:${port}/api/health/ready`,
    env: { E2E_PORT: String(port) },
    timeout: 120_000,
    stdout: "pipe",
    // SIGTERM, so the server deletes its temp folder on the way out.
    gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
  },
})
