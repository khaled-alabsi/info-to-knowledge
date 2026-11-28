import { defineConfig, devices } from "@playwright/test";
import { pickTestWorkers } from "./testWorkers";

// `npm run test:e2e`: the page served by Vite on its own port, every /api call answered by the tests (e2e/fixtures/),
// in chromium only. Port 4173, never 5173: `npm run dev` there proxies /api to the real `i2k serve`. The server reads
// e2e/vite.e2e.config.ts, which has no /api proxy, so no test can reach a real run.
const PORT = 4173;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  workers: pickTestWorkers(),
  forbidOnly: Boolean(process.env.CI),
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npx vite --config e2e/vite.e2e.config.ts --port ${PORT} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
