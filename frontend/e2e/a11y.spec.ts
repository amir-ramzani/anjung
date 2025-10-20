import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Accessibility pass (P4-F4): every main screen, for each role, scanned with axe-core against WCAG 2.1 A and AA,
// plus the keyboard basics a scanner cannot see. Needs the local stack (`docker compose up`), like journeys.spec.ts.

const API = process.env.E2E_API_URL ?? "http://localhost:8080";
const ROOT = process.env.PLATFORM_ADMIN_EMAIL ?? "root@example.com";
const ADDR = { line1: "1 Jalan Ampang", city: "Kuala Lumpur", state: "WP Kuala Lumpur", postcode: "50450" };

async function token(request: APIRequestContext, email: string) {
  const r = await request.post(`${API}/dev/token`, { data: { email } });
  expect(r.ok(), await r.text()).toBeTruthy();
  return { Authorization: `Bearer ${(await r.json()).access_token}` };
}

/** An organisation with a property, a unit, a tenant with an active lease, an invoice and a repair request. */
async function seed(request: APIRequestContext) {
  const run = Math.random().toString(36).slice(2, 8);
  const admin = `a11y-${run}@example.com`, tenant = `a11y-t-${run}@example.com`;
  const root = await token(request, ROOT);
  expect((await request.post(`${API}/v1/platform/organizations`, { headers: root, data: { name: `A11y ${run}`, initial_admin: { email: admin }, reason: "a11y" } })).status()).toBe(201);
  const h = await token(request, admin);
  await request.patch(`${API}/v1/org`, { headers: h, data: { settings: { require_signed_agreement: false } } });
  const prop = await (await request.post(`${API}/v1/properties`, { headers: h, data: { name: "Menara A11y", type: "condominium", address: ADDR } })).json();
  const unit = await (await request.post(`${API}/v1/properties/${prop.id}/units`, { headers: h, data: { label: "A-1", default_rent: { amount_minor: 150000, currency: "MYR" } } })).json();
  const t = await (await request.post(`${API}/v1/tenants`, { headers: h, data: { full_name: "Siti A11y", email: tenant } })).json();
  await request.post(`${API}/v1/tenants/${t.id}/invite`, { headers: h, data: { channel: "link" } });
  const start = new Date(); start.setDate(1);
  const lease = await (await request.post(`${API}/v1/leases`, { headers: h, data: {
    unit_id: unit.id, tenant_ids: [t.id], start_date: start.toISOString().slice(0, 10), end_date: `${start.getFullYear() + 1}-12-31`,
    monthly_rent: { amount_minor: 150000, currency: "MYR" }, deposits: [], due_day: 5,
  } })).json();
  await request.post(`${API}/v1/leases/${lease.id}/activate`, { headers: h });
  await request.post(`${API}/v1/maintenance-requests`, { headers: h, data: { unit_id: unit.id, title: "Leak", description: "Kitchen tap", category: "plumbing" } });
  return { admin, tenant, prop, t, lease };
}

async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
}

const found: string[] = [];

async function scan(page: Page, path: string, { stay = false } = {}) {
  if (!stay) await page.goto(path);
  await page.waitForLoadState("networkidle");
  await expect(page.locator("main")).toBeVisible();
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  for (const v of r.violations) {
    found.push(`${path} [${v.impact}] ${v.id}: ${v.help}\n    ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join("\n    ")}`);
  }
}

test.afterAll(() => {
  if (found.length) console.log(`\naxe violations (${found.length}):\n${found.join("\n")}`);
});

test("screens for staff, tenants and the platform admin pass WCAG 2.1 AA checks", async ({ page, request }) => {
  const s = await seed(request);
  await scan(page, "/login");
  await scan(page, "/login/no-such-company"); // a company page that does not exist
  await signIn(page, s.admin);
  for (const path of ["/", "/properties", `/properties/${s.prop.id}`, "/tenants", `/tenants/${s.t.id}`, "/leases", `/leases/${s.lease.id}`,
    "/leases/new", "/invoices", "/maintenance", "/reports", "/users", "/settings", "/audit", "/privacy", "/payments/unmatched"]) {
    await scan(page, path);
  }
  await page.evaluate(() => sessionStorage.clear());
  await signIn(page, s.tenant);
  for (const path of ["/", "/invoices", "/maintenance", "/preferences", "/documents", "/privacy"]) await scan(page, path);
  await page.evaluate(() => sessionStorage.clear());
  await signIn(page, ROOT);
  await scan(page, "/platform/organizations");
  expect(found, found.join("\n")).toEqual([]);
});

test("the keyboard reaches the content first, and focus is always visible", async ({ page, request }) => {
  const s = await seed(request);
  await signIn(page, s.admin);
  await page.goto("/properties");
  await expect(page.getByRole("heading", { name: "Properties" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page).toHaveTitle("Properties · Anjung"); // the tab says which page this is
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible(); // shown when focused
  await page.keyboard.press("Enter");
  await expect(page.locator("main")).toBeFocused();
  // every focusable control shows a focus ring
  await page.keyboard.press("Tab");
  const outline = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement;
    const st = getComputedStyle(el);
    return st.outlineStyle !== "none" && parseFloat(st.outlineWidth) > 0 ? "outline" : st.boxShadow !== "none" ? "shadow" : "none";
  });
  expect(outline).not.toBe("none");
  // moving to another page puts focus on its heading, so screen readers announce it
  await page.getByRole("link", { name: "Tenants" }).click();
  await expect(page.getByRole("heading", { name: "Tenants" })).toBeFocused();
  await expect(page).toHaveTitle("Tenants · Anjung");
  // switching language updates the page language for screen readers
  await page.getByRole("combobox", { name: "Language" }).selectOption("ms");
  await expect(page.locator("html")).toHaveAttribute("lang", "ms");
});

test("dark mode at phone width, in Malay, passes too", async ({ page, request }) => {
  const s = await seed(request);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.addInitScript(() => localStorage.setItem("lang", "ms"));
  await page.goto("/login");
  await page.getByLabel("E-mel").fill(s.admin);
  await page.getByRole("button", { name: "Log masuk" }).click();
  await expect(page.getByRole("navigation")).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "ms");
  for (const path of ["/", "/properties", `/properties/${s.prop.id}`, "/invoices", "/reports", "/users"]) await scan(page, path);
  // the folded menu opens and closes from the keyboard
  const menu = page.locator("button[aria-controls=nav-menu]"); // its label changes to "Tutup" when open
  await menu.focus();
  await page.keyboard.press("Enter");
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  await scan(page, "/users (menu open)", { stay: true });
  await page.keyboard.press("Enter");
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  expect(found, found.join("\n")).toEqual([]);
});
