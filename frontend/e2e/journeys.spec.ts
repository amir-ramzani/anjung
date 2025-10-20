import { expect, test, type APIRequestContext } from "@playwright/test";

const API = process.env.E2E_API_URL ?? "http://localhost:8080";
const ROOT = process.env.PLATFORM_ADMIN_EMAIL ?? "root@example.com";

async function token(request: APIRequestContext, email: string): Promise<Record<string, string>> {
  const r = await request.post(`${API}/dev/token`, { data: { email } });
  expect(r.ok(), await r.text()).toBeTruthy();
  return { Authorization: `Bearer ${(await r.json()).access_token}` };
}

async function seed(request: APIRequestContext) {
  const run = Math.random().toString(36).slice(2, 8);
  const root = await token(request, ROOT);
  const org = await request.post(`${API}/v1/platform/organizations`, { headers: root, data: { name: `E2E ${run}`, initial_admin: { email: `boss-${run}@example.com` }, reason: "e2e" } });
  expect(org.status()).toBe(201);
  return { run, admin: `boss-${run}@example.com`, tenant: `tenant-${run}@example.com`, adminH: await token(request, `boss-${run}@example.com`) };
}

async function signIn(page: import("@playwright/test").Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
}

test("an administrator builds a portfolio through the interface", async ({ page, request }) => {
  const s = await seed(request);
  await signIn(page, s.admin);
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();

  await page.getByRole("link", { name: "Properties" }).click();
  await page.getByRole("button", { name: "Add property" }).click();
  await page.getByLabel("Name").fill("Menara E2E");
  await page.getByLabel("Address", { exact: true }).fill("1 Jalan Ampang");
  await page.getByLabel("City").fill("Kuala Lumpur");
  await page.getByLabel("State").fill("WP Kuala Lumpur");
  await page.getByLabel("Postcode").fill("50450");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByRole("link", { name: "Menara E2E" }).click();

  await page.getByRole("button", { name: "Add unit" }).click();
  await page.getByLabel("Label").fill("A-12-03");
  await page.getByLabel("Default rent").fill("1,800.00");
  await page.getByLabel("How is it let?").selectOption("rooms");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("cell", { name: "A-12-03" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "RM 1,800.00" })).toBeVisible();

  await page.getByRole("button", { name: "Add room" }).click();
  await page.getByLabel("Label").fill("Room 1");
  await page.getByLabel("Default rent").fill("650");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("cell", { name: "Room 1" })).toBeVisible();

  // the same label twice is refused with the server's message, not a crash
  await page.getByRole("button", { name: "Add room" }).click();
  await page.getByLabel("Label").fill("room 1");
  await page.getByLabel("Default rent").fill("650");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("alert")).toContainText(/already exists/i);

  await page.getByRole("link", { name: "Tenants" }).click();
  await page.getByRole("button", { name: "Add tenant" }).click();
  await page.getByLabel("Name").fill("Siti Aminah");
  await page.getByLabel("Email").fill(s.tenant);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("cell", { name: s.tenant })).toBeVisible();
  // the welcome email with a set-password link (contract v0.14.0) is sent from the tenant's page
  await page.getByRole("link", { name: "Siti Aminah" }).click();
  await page.getByRole("button", { name: "Send email" }).click();
  await expect(page.getByText(`Welcome email sent to ${s.tenant}.`)).toBeVisible();
});

test("a tenant pays through the QR screen and sees the payment arrive", async ({ page, request }) => {
  const s = await seed(request);
  const h = s.adminH;
  // this journey is about paying: activate without the tenancy agreement step (on by default since v0.7.0)
  await request.patch(`${API}/v1/org`, { headers: h, data: { settings: { require_signed_agreement: false } } });
  const prop = await (await request.post(`${API}/v1/properties`, { headers: h, data: { name: "P", type: "condominium", address: { line1: "x", city: "KL", state: "WP", postcode: "50000" } } })).json();
  const unit = await (await request.post(`${API}/v1/properties/${prop.id}/units`, { headers: h, data: { label: "A-1", default_rent: { amount_minor: 150000, currency: "MYR" } } })).json();
  const tenant = await (await request.post(`${API}/v1/tenants`, { headers: h, data: { full_name: "Siti", email: s.tenant } })).json();
  await request.post(`${API}/v1/tenants/${tenant.id}/invite`, { headers: h });
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth() + 1, 1, 12); // the 1st of next month: a full month, no proration
  const lease = await (await request.post(`${API}/v1/leases`, { headers: h, data: {
    unit_id: unit.id, tenant_ids: [tenant.id], start_date: `${start.getFullYear()}-${String(start.getMonth() + 1).padStart(2, "0")}-01`, monthly_rent: { amount_minor: 150000, currency: "MYR" },
    due_day: 1, deposits: [] } })).json();
  expect((await request.post(`${API}/v1/leases/${lease.id}/activate`, { headers: h })).ok()).toBeTruthy();
  const period = `${start.getFullYear()}-${String(start.getMonth() + 1).padStart(2, "0")}`;
  await request.post(`${API}/v1/invoice-runs`, { headers: { ...h, "Idempotency-Key": `e2e-run-${s.run}-1` }, data: { period } });

  await signIn(page, s.tenant);
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Properties" })).toHaveCount(0); // a tenant does not see staff navigation
  await page.getByRole("link", { name: "Pay", exact: true }).first().click();

  await expect(page).toHaveURL(/\/pay\//); // the dashboard tiles show the same amount: be sure we have moved on
  await expect(page.locator(".amount")).toHaveText("RM 1,500.00");
  await expect(page.getByLabel("DuitNow QR code")).toBeVisible();
  await expect(page.getByRole("timer")).toContainText(/expires in \d+ seconds/);
  await expect(page.getByRole("button", { name: "Save QR image" })).toBeVisible();

  // the gateway reports the payment: find the reference the screen created, then pay it
  const invoices = await (await request.get(`${API}/v1/invoices?limit=10`, { headers: h })).json();
  const inv = invoices.items.find((i: { qr: unknown }) => i.qr);
  expect(inv.qr.reference).toMatch(/^ANJ/);
  const paid = await request.post(`${API}/dev/pay`, { data: { reference: inv.qr.reference, amount_minor: 150000 } });
  expect((await paid.json()).webhook_status).toBe(200);
  await expect(page.getByText("Payment received. Thank you!")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "Receipt" })).toBeVisible();
});

test("the interface switches to Malay and stays that way", async ({ page, request }) => {
  const s = await seed(request);
  await signIn(page, s.admin);
  await page.getByLabel("Language").selectOption("ms");
  await expect(page.getByRole("heading", { name: "Papan pemuka" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Papan pemuka" })).toBeVisible();
});

test("signing out returns to the login form and protects the pages", async ({ page, request }) => {
  const s = await seed(request);
  await signIn(page, s.admin);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByLabel("Email")).toBeVisible();
  await page.goto("/properties");
  await expect(page).toHaveURL(/\/login$/);
});

const PDF = Buffer.from("%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n");

test("a tenant uploads an identity document and the landlord downloads it", async ({ page, request, context }) => {
  const s = await seed(request);
  const h = s.adminH;
  const tenant = await (await request.post(`${API}/v1/tenants`, { headers: h, data: { full_name: "Siti", email: s.tenant } })).json();
  await request.post(`${API}/v1/tenants/${tenant.id}/invite`, { headers: h });

  // the tenant, in their own browser session
  await signIn(page, s.tenant);
  await page.getByRole("link", { name: "My documents" }).click();
  await expect(page.getByText("No documents yet.")).toBeVisible();
  await page.getByLabel("Type").selectOption("identity");
  await page.getByLabel("File").setInputFiles({ name: "ic.pdf", mimeType: "application/pdf", buffer: PDF });
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByText("Uploaded and checked.")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("cell", { name: "ic.pdf" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Clean" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Remove" })).toHaveCount(0); // tenants cannot delete

  // the file is refused in the browser, before anything is sent
  const sent: string[] = [];
  page.on("request", (r) => sent.push(r.url()));
  await page.getByLabel("File").setInputFiles({ name: "x.html", mimeType: "text/html", buffer: Buffer.from("<script>") });
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByText("Only PDF, JPEG, PNG and WebP files are accepted.")).toBeVisible();
  expect(sent.filter((u) => u.includes("upload-url"))).toHaveLength(0);

  // the landlord finds it on the tenant's page and downloads exactly what was uploaded
  const staff = await context.newPage();
  // a PDF comes with Content-Disposition: attachment, so the browser downloads it instead of loading a page.
  // Record the address the app opens and fetch that, which is the same request the browser would make.
  await staff.addInitScript(() => {
    (window as unknown as { __opened: string[] }).__opened = [];
    window.open = ((url: string) => { (window as unknown as { __opened: string[] }).__opened.push(url); return null; }) as typeof window.open;
  });
  await signIn(staff, s.admin);
  await staff.goto(`/tenants/${tenant.id}`);
  await expect(staff.getByRole("cell", { name: "ic.pdf" })).toBeVisible();
  await staff.getByRole("button", { name: "Download" }).click();
  await expect.poll(() => staff.evaluate(() => (window as unknown as { __opened: string[] }).__opened.length)).toBe(1);
  const url = await staff.evaluate(() => (window as unknown as { __opened: string[] }).__opened[0]!);
  expect(url).toMatch(/\/anjung-docs\/org\//); // the documents store: localhost:9000 locally, through Caddy on the NAS
  const res = await request.get(url);
  expect(res.ok()).toBeTruthy();
  expect(res.headers()["content-disposition"]).toContain("attachment");
  expect((await res.body()).toString()).toBe(PDF.toString());
});

test("staff drop an inspection report onto a unit's documents", async ({ page, request }) => {
  const s = await seed(request);
  const h = s.adminH;
  const prop = await (await request.post(`${API}/v1/properties`, { headers: h, data: { name: "Drop Tower", type: "condominium", address: { line1: "x", city: "KL", state: "WP", postcode: "50000" } } })).json();
  await request.post(`${API}/v1/properties/${prop.id}/units`, { headers: h, data: { label: "B-2", default_rent: { amount_minor: 100000, currency: "MYR" } } });
  await signIn(page, s.admin);
  await page.goto(`/properties/${prop.id}`);
  await page.getByRole("button", { name: "Documents", expanded: false }).click();
  await expect(page.getByRole("heading", { name: "Documents for B-2" })).toBeVisible();
  const form = page.locator("#docs-unit").locator("..").locator("form");
  await page.getByLabel("Type").first().selectOption("inspection_report");
  // a real drop: the browser's own DataTransfer, carrying a PDF
  const dt = await page.evaluateHandle((bytes) => {
    const d = new DataTransfer();
    d.items.add(new File([new Uint8Array(bytes)], "inventory.pdf", { type: "application/pdf" }));
    return d;
  }, [...PDF]);
  await form.dispatchEvent("dragover", { dataTransfer: dt });
  await expect(form).toHaveClass(/dragging/);
  await form.dispatchEvent("drop", { dataTransfer: dt });
  await expect(form.getByText(/Chosen: inventory\.pdf\./)).toBeVisible();
  await form.getByRole("button", { name: "Upload" }).click();
  await expect(form.getByText("Uploaded and checked.")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("#docs-unit").locator("..").getByRole("cell", { name: "inventory.pdf" })).toBeVisible();
});
