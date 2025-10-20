import { defineConfig } from "@playwright/test";

// Needs the API running locally: `docker compose up` from the repository root (mock auth).
// Another stack with the mock sign-in, such as the NAS demo: E2E_BASE_URL (the app) and E2E_API_URL (its API, often
// the same address), plus PLATFORM_ADMIN_EMAIL. E2E_INSECURE=1 accepts the NAS demo's own HTTPS certificate.
const target = process.env.E2E_BASE_URL;
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  use: { baseURL: target ?? "http://localhost:5173", trace: "retain-on-failure", locale: "en-MY", ignoreHTTPSErrors: !!process.env.E2E_INSECURE },
  webServer: target ? undefined : { command: "npm run dev -- --port 5173", url: "http://localhost:5173", reuseExistingServer: true, timeout: 60_000 },
});
