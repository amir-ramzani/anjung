import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";

import { lastSixMonths, periodEnd, todayIso } from "./Admin";
import { daysLeft } from "./Home";
import { nextStatuses } from "./Maintenance";
import { secondsLeft } from "./Pay";
import { ME, mockApi, money, page, renderApp, signedIn } from "../test/helpers";

vi.mock("qrcode", () => ({ default: { toCanvas: vi.fn(async () => undefined) } }));

const invoice = (over: object = {}) => ({
  id: "01JD8Z3K9Q4M7T2V6X8B5N1C0A", number: "INV-2018-000001", lease_id: "L1", period: "2018-12", status: "open",
  issue_date: "2018-11-20", due_date: "2018-12-05", line_items: [{ description: "Rent Dec 2018", type: "rent", amount: money(150000) }],
  total: money(150000), paid: money(0), outstanding: money(150000), qr: null, created_at: "2018-11-20T00:00:00Z",
  updated_at: "2018-11-20T00:00:00Z", version: 1, ...over,
});
const qr = (seconds: number, ref = "ANJAAAA") => ({
  reference: ref, qr_payload: `FAKE|${ref}`, amount: money(150000), provider: "fake",
  expires_at: new Date(Date.now() + seconds * 1000).toISOString(),
});

beforeEach(() => signedIn());
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("sign in", () => {
  it("mock mode: signs in with an email and lands on the dashboard", async () => {
    sessionStorage.clear();
    const api = mockApi({
      "POST /dev/token": { access_token: "tok" },
      "GET /v1/me": ME(["org_admin"]),
      "GET /v1/reports/arrears": { as_of: "x", rows: [], total_outstanding: money(0) },
      "GET /v1/reports/occupancy": { as_of: "x", rows: [] },
    });
    renderApp("/");
    await userEvent.type(await screen.findByLabelText("Email"), "boss@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(api.calls.find((c) => c.path === "/dev/token")?.body).toEqual({ email: "boss@example.com" });
    expect(api.calls.find((c) => c.path === "/v1/me")?.headers.get("Authorization")).toBe("Bearer tok");
  });

  it("shows why the sign-in was refused", async () => {
    sessionStorage.clear();
    mockApi({ "POST /dev/token": { status: 403, body: { code: "sign_in_denied", message: "x", details: [{ field: "email", issue: "no_unique_membership" }], request_id: "r" } } });
    renderApp("/login");
    await userEvent.type(await screen.findByLabelText("Email"), "x@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText(/No account uses this email/)).toBeInTheDocument(); // plain words, not the code
  });

  it("an expired token that cannot be refreshed signs the user out", async () => {
    mockApi({ "GET /v1/me": { status: 401, body: { code: "unauthorized", message: "no", request_id: "r" } } });
    renderApp("/");
    expect(await screen.findByLabelText("Email")).toBeInTheDocument(); // back at the login form
    expect(sessionStorage.getItem("anjung.tokens")).toBeNull();
  });
});

describe("navigation and roles", () => {
  it("a tenant sees tenant navigation only, and is refused staff pages", async () => {
    mockApi({
      "GET /v1/me": ME(["tenant"], { tenant_id: "T1" }),
      "GET /v1/leases": page([]), "GET /v1/invoices": page([]), "GET /v1/maintenance-requests": page([]),
    });
    renderApp("/properties");
    expect(await screen.findByText("You do not have access to this page.")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).queryByText("Properties")).not.toBeInTheDocument();
    expect(within(nav).getByText("Notifications")).toBeInTheDocument();
    expect(within(nav).queryByText("Audit log")).not.toBeInTheDocument();
  });

  it("a property manager does not get administrator pages", async () => {
    mockApi({ "GET /v1/me": ME(["property_manager"]), "GET /v1/reports/arrears": { as_of: "x", rows: [], total_outstanding: money(0) }, "GET /v1/reports/occupancy": { as_of: "x", rows: [] } });
    renderApp("/users");
    expect(await screen.findByText("You do not have access to this page.")).toBeInTheDocument();
  });

  it("switches the whole interface to Malay", async () => {
    mockApi({ "GET /v1/me": ME(["org_admin"]), "GET /v1/reports/arrears": { as_of: "x", rows: [], total_outstanding: money(0) }, "GET /v1/reports/occupancy": { as_of: "x", rows: [] } });
    renderApp("/");
    await userEvent.selectOptions(await screen.findByLabelText("Language"), "ms");
    expect(await screen.findByRole("heading", { name: "Papan pemuka" })).toBeInTheDocument();
    expect(localStorage.getItem("lang")).toBe("ms");
  });
});

describe("properties", () => {
  it("creates a property with the country, and shows the API's field errors beside the inputs", async () => {
    const api = mockApi({
      "GET /v1/me": ME(["org_admin"]),
      "GET /v1/properties": page([]),
      "POST /v1/properties": { status: 422, body: { code: "validation_error", message: "Validation failed", request_id: "r1", details: [{ field: "address.postcode", issue: "string should match pattern" }] } },
    });
    renderApp("/properties");
    await userEvent.click(await screen.findByRole("button", { name: "Add property" }));
    await userEvent.type(screen.getByLabelText("Name"), "Menara");
    await userEvent.type(screen.getByLabelText("Address"), "1 Jalan A");
    await userEvent.type(screen.getByLabelText("City"), "KL");
    await userEvent.type(screen.getByLabelText("State"), "WP");
    await userEvent.type(screen.getByLabelText("Postcode"), "50000");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("string should match pattern")).toBeInTheDocument();
    expect(screen.getByText(/check the highlighted fields/)).toBeInTheDocument();
    expect(api.calls.find((c) => c.method === "POST")?.body).toMatchObject({ name: "Menara", address: { country: "MY", postcode: "50000" } });
  });

  it("sends the rent in sen, never as a decimal", async () => {
    const api = mockApi({
      "GET /v1/me": ME(["org_admin"]),
      "GET /v1/properties/P1": { id: "P1", name: "Menara", type: "condominium", address: { line1: "x", city: "KL", state: "WP", postcode: "50000", country: "MY" }, status: "active", created_at: "x", updated_at: "x" },
      "GET /v1/properties/P1/units": page([]),
      "POST /v1/properties/P1/units": (_r: Request, b: unknown) => ({ id: "U1", ...(b as object) }),
    });
    renderApp("/properties/P1");
    await userEvent.click(await screen.findByRole("button", { name: "Add unit" }));
    await userEvent.type(screen.getByLabelText("Label"), "A-12");
    await userEvent.type(screen.getByLabelText("Default rent"), "1,299.90");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.count("POST /v1/properties/P1/units")).toBe(1));
    expect(api.calls.find((c) => c.method === "POST")?.body).toMatchObject({ label: "A-12", default_rent: { amount_minor: 129990, currency: "MYR" }, letting_mode: "whole" });
  });

  it("refuses an amount it cannot read instead of guessing", async () => {
    mockApi({ "GET /v1/me": ME(["org_admin"]), "GET /v1/properties/P1": { id: "P1", name: "M", type: "other", address: { line1: "x", city: "x", state: "x", postcode: "50000", country: "MY" }, status: "active", created_at: "x", updated_at: "x" }, "GET /v1/properties/P1/units": page([]) });
    renderApp("/properties/P1");
    await userEvent.click(await screen.findByRole("button", { name: "Add unit" }));
    await userEvent.type(screen.getByLabelText("Label"), "A");
    await userEvent.type(screen.getByLabelText("Default rent"), "12.345");
    expect(await screen.findByText("Enter an amount like 1500.00")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

describe("pay screen", () => {
  it("creates the QR when it opens, counts down, and replaces it when it expires", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let n = 0;
    const api = mockApi({
      "GET /v1/me": ME(["tenant"], { tenant_id: "T1" }),
      "GET /v1/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A": invoice(),
      "POST /v1/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A/qr": () => ({ status: 201, body: qr(60, `ANJ${++n}`) }),
    });
    renderApp("/pay/01JD8Z3K9Q4M7T2V6X8B5N1C0A");
    expect(await screen.findByText("RM 1,500.00")).toBeInTheDocument();
    expect(await screen.findByRole("timer")).toHaveTextContent(/expires in (59|60) seconds/);
    expect(api.count("POST /v1/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A/qr")).toBe(1); // exactly one, even with strict re-renders
    await act(async () => { await vi.advanceTimersByTimeAsync(61_000); });
    await waitFor(() => expect(api.count("POST /v1/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A/qr")).toBe(2)); // a fresh code, no reload
    expect(await screen.findByRole("timer")).toHaveTextContent(/expires in/);
  });

  it("shows the receipt once the webhook has marked the invoice paid", async () => {
    let paid = false;
    mockApi({
      "GET /v1/me": ME(["tenant"], { tenant_id: "T1" }),
      "GET /v1/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A": () => (paid ? invoice({ status: "paid", paid: money(150000), outstanding: money(0) }) : invoice()),
      "POST /v1/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A/qr": { status: 201, body: qr(300) },
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderApp("/pay/01JD8Z3K9Q4M7T2V6X8B5N1C0A");
    await screen.findByRole("timer");
    paid = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(4500); }); // the screen polls while unpaid
    expect(await screen.findByText("Payment received. Thank you!")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Receipt" })).toBeInTheDocument();
    expect(screen.queryByRole("timer")).not.toBeInTheDocument();
  });

  it("does not hammer the API when it is rate limited, and offers a retry", async () => {
    const api = mockApi({
      "GET /v1/me": ME(["tenant"], { tenant_id: "T1" }),
      "GET /v1/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A": invoice(),
      "POST /v1/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A/qr": { status: 429, body: { code: "rate_limited", message: "Too many requests", request_id: "r" } },
    });
    renderApp("/pay/01JD8Z3K9Q4M7T2V6X8B5N1C0A");
    expect(await screen.findByText("Too many requests")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Try again in a moment" })).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 300));
    expect(api.count("POST /v1/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A/qr")).toBe(1);
  });

  it("explains that a void invoice cannot be paid and creates no QR", async () => {
    const api = mockApi({ "GET /v1/me": ME(["tenant"], { tenant_id: "T1" }), "GET /v1/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A": invoice({ status: "void", outstanding: money(0) }) });
    renderApp("/pay/01JD8Z3K9Q4M7T2V6X8B5N1C0A");
    expect(await screen.findByText(/cannot be paid/)).toBeInTheDocument();
    expect(api.count("POST /v1/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A/qr")).toBe(0);
  });

  it("counts whole seconds and never goes negative", () => {
    const now = Date.parse("2018-11-20T00:00:00Z");
    expect(secondsLeft("2018-11-20T00:01:00Z", now)).toBe(60);
    expect(secondsLeft("2018-11-20T00:00:00.400Z", now)).toBe(1);
    expect(secondsLeft("2018-11-19T00:00:00Z", now)).toBe(0);
  });
});

describe("invoices", () => {
  it("records a manual payment with an idempotency key and the amount in sen", async () => {
    const api = mockApi({
      "GET /v1/me": ME(["property_manager"]),
      "GET /v1/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A": invoice(),
      "POST /v1/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A/payments/manual": { status: 201, body: { id: "P1" } },
    });
    renderApp("/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A");
    await userEvent.click(await screen.findByRole("button", { name: "Record payment" }));
    expect(screen.getByLabelText("Amount")).toHaveValue("1500.00");
    await userEvent.type(screen.getByLabelText("Reason"), "paid cash at the office");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.count("POST /v1/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A/payments/manual")).toBe(1));
    const call = api.calls.find((c) => c.method === "POST")!;
    expect(call.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(call.body).toMatchObject({ method: "cash", amount: { amount_minor: 150000, currency: "MYR" }, reason: "paid cash at the office" });
  });

  it("a tenant gets a Pay button and no staff actions", async () => {
    mockApi({ "GET /v1/me": ME(["tenant"], { tenant_id: "T1" }), "GET /v1/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A": invoice() });
    renderApp("/invoices/01JD8Z3K9Q4M7T2V6X8B5N1C0A");
    expect(await screen.findByRole("link", { name: "Pay" })).toHaveAttribute("href", "/pay/01JD8Z3K9Q4M7T2V6X8B5N1C0A");
    expect(screen.queryByRole("button", { name: "Record payment" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Void invoice" })).not.toBeInTheDocument();
  });
});

describe("leases", () => {
  it("offers the notice waiver only for rolling leases and sends deposits by type", async () => {
    const lease = { id: "L1", unit_id: "U1", property_id: "P1", tenant_ids: ["T1"], status: "active", start_date: "2018-12-01", end_date: null, notice_days: 30,
      monthly_rent: money(150000), deposits: [], deposits_held: [{ type: "security", amount: money(300000) }], due_day: 5, balance: money(0), credit: money(0), created_at: "x", updated_at: "x" };
    const api = mockApi({
      "GET /v1/me": ME(["org_admin"]), "GET /v1/leases/L1": lease, "GET /v1/invoices": page([]),
      "POST /v1/leases/L1/terminate": { ...lease, status: "terminated" },
    });
    renderApp("/leases/L1");
    await userEvent.click(await screen.findByRole("button", { name: "End tenancy" }));
    expect(screen.getByLabelText("Waive the 30 days notice")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Termination date"), "2018-12-20");
    await userEvent.type(screen.getByLabelText("Reason"), "moving");
    await userEvent.click(screen.getByLabelText("Waive the 30 days notice"));
    await userEvent.type(screen.getByLabelText(/Keep from Security deposit/), "50.00");
    await userEvent.click(screen.getAllByRole("button", { name: "End tenancy" }).at(-1)!);
    await waitFor(() => expect(api.count("POST /v1/leases/L1/terminate")).toBe(1));
    expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({
      termination_date: "2018-12-20", reason: "moving", waive_notice: true,
      deposit_deduction: [{ type: "security", amount: { amount_minor: 5000, currency: "MYR" } }],
    });
  });
});

describe("who rents what", () => {
  const lease = { id: "L1", unit_id: "U1", property_id: "P1", tenant_ids: ["T1", "T2"], status: "active", start_date: "2018-12-01", end_date: null, notice_days: 30,
    monthly_rent: money(150000), deposits: [], due_day: 5, balance: money(0), credit: money(0), created_at: "x", updated_at: "x",
    property_name: "Pelangi", unit_label: "Master", parent_unit_label: "D-5-8",
    tenants: [{ id: "T1", full_name: "Siti Aminah" }, { id: "T2", full_name: "Ali Hassan" }] };

  it("the lease list names the tenants and the room, and the whole row opens the lease", async () => {
    mockApi({ "GET /v1/me": ME(["org_admin"]), "GET /v1/leases": page([lease]), "GET /v1/leases/L1": lease, "GET /v1/invoices": page([]) });
    renderApp("/leases");
    const name = await screen.findByRole("link", { name: "Siti Aminah, Ali Hassan" });
    expect(name).toHaveAttribute("href", "/leases/L1");
    const row = name.closest("tr")!;
    expect(within(row).getByText("Pelangi · D-5-8 › Master")).toBeInTheDocument();
    await userEvent.click(within(row).getByText("Pelangi · D-5-8 › Master")); // not the link: anywhere on the row
    expect(await screen.findByRole("heading", { name: "Lease" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ali Hassan" })).toHaveAttribute("href", "/tenants/T2");
  });

  it("the tenant list says what each tenant rents, upcoming leases marked", async () => {
    const tenant = (id: string, name: string, rentals: object[]) => ({ id, full_name: name, email: `${id}@example.com`, status: "active", linked: true, created_at: "x", updated_at: "x", rentals });
    mockApi({ "GET /v1/me": ME(["org_admin"]), "GET /v1/tenants": page([
      tenant("T1", "Siti Aminah", [
        { lease_id: "L1", status: "active", property_name: "Pelangi", unit_label: "Master", parent_unit_label: "D-5-8" },
        { lease_id: "L2", status: "draft", property_name: "Pelangi", unit_label: "B-2", parent_unit_label: null },
      ]),
      tenant("T2", "No Lease", []),
    ]) });
    renderApp("/tenants");
    const siti = (await screen.findByRole("link", { name: "Siti Aminah" })).closest("tr")!;
    expect(within(siti).getByText("Pelangi · D-5-8 › Master")).toBeInTheDocument();
    expect(within(siti).getByText(/Pelangi · B-2/)).toBeInTheDocument();
    expect(within(siti).getByText("Upcoming")).toBeInTheDocument();
    expect(within(screen.getByRole("link", { name: "No Lease" }).closest("tr")!).getByText("Not renting")).toBeInTheDocument();
  });
});

describe("maintenance", () => {
  it("offers only the legal next steps", () => {
    expect(nextStatuses("open")).toEqual(["triaged", "cancelled"]);
    expect(nextStatuses("resolved")).toEqual(["closed", "in_progress"]);
    expect(nextStatuses("closed")).toEqual([]);
    expect(nextStatuses("nonsense")).toEqual([]);
  });
});

describe("reports", () => {
  it("asks for the last six months of income", async () => {
    expect(lastSixMonths(new Date(2018, 10, 20))).toEqual({ from: "2018-06", to: "2018-11" });
    expect(lastSixMonths(new Date(2019, 1, 3))).toEqual({ from: "2018-09", to: "2019-02" });
    const api = mockApi({
      "GET /v1/me": ME(["org_admin"]), "GET /v1/reports/arrears": { as_of: "x", rows: [], total_outstanding: money(0) },
      "GET /v1/reports/occupancy": { as_of: "x", rows: [] }, "GET /v1/reports/income": { as_of: "x", rows: [] },
      "GET /v1/reports/maintenance": { as_of: "x", rows: [] }, "GET /v1/properties": page([]),
    });
    renderApp("/reports");
    await screen.findByRole("heading", { name: "Reports" });
    await waitFor(() => expect(api.count("GET /v1/reports/income")).toBe(1));
  });
});

describe("settings", () => {
  it("sends the late fee percentage as integer basis points", async () => {
    const api = mockApi({
      "GET /v1/me": ME(["org_admin"]),
      "GET /v1/org": { id: "o1", name: "Acme", status: "active", settings: { default_due_day: 1, invoice_lead_days: 7, late_fee: { type: "none", grace_days: 0 } }, created_at: "x", updated_at: "x" },
      "PATCH /v1/org": { id: "o1" },
    });
    renderApp("/settings");
    await userEvent.selectOptions(await screen.findByLabelText("Late fee"), "percent_per_month");
    await userEvent.type(screen.getByLabelText("Percent of what is owed, per month"), "1.5");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.count("PATCH /v1/org")).toBe(1));
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toMatchObject({ settings: { late_fee: { type: "percent_per_month", percent_bp: 150, grace_days: 0 } } });
  });
});

describe("staff access", () => {
  const user = (over: object = {}) => ({ user_id: "pm1", email: "pm1@example.com", name: "Manager Two", roles: ["property_manager"], status: "active",
    access: "active", active_from: null, active_until: null, created_at: "x", ...over });

  it("works out the last day of a period, clamping month ends", () => {
    expect(periodEnd("2018-11-21", 12)).toBe("2019-11-20");
    expect(periodEnd("2018-11-21", 1)).toBe("2018-12-20");
    expect(periodEnd("2018-01-31", 1)).toBe("2018-02-27"); // to 28 Feb, the day before
    expect(periodEnd("2020-02-29", 12)).toBe("2021-02-27");
  });

  it("invites a manager for one year from today", async () => {
    const api = mockApi({ "GET /v1/me": ME(["org_admin"]), "GET /v1/users": page([]), "POST /v1/users/invite": user({ status: "invited", access: "invited" }) });
    renderApp("/users");
    await userEvent.type(await screen.findByLabelText("Email"), "manager2@example.com");
    await userEvent.selectOptions(screen.getByLabelText("Access"), "1y");
    await userEvent.click(screen.getByRole("button", { name: "Invite" }));
    await waitFor(() => expect(api.count("POST /v1/users/invite")).toBe(1));
    expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({ email: "manager2@example.com", roles: ["property_manager"], active_until: periodEnd(todayIso(), 12) });
  });

  it("shows each person's access, and deactivates and reactivates", async () => {
    window.confirm = vi.fn(() => true);
    const api = mockApi({ "GET /v1/me": ME(["org_admin"]), "GET /v1/users": page([
      user(), user({ user_id: "pm2", email: "pm2@example.com", name: null, status: "disabled", access: "disabled" }),
      user({ user_id: "pm3", email: "pm3@example.com", name: null, access: "expired", active_until: "2018-10-31" }),
    ]), "PATCH /v1/users/pm1": user({ status: "disabled", access: "disabled" }), "PATCH /v1/users/pm2": user({ user_id: "pm2" }) });
    renderApp("/users");
    const expired = (await screen.findByText("pm3@example.com")).closest("tr")!;
    expect(within(expired).getByText("Expired")).toBeInTheDocument();
    expect(within(expired).getByText("Until 31-10-2018")).toBeInTheDocument();
    await userEvent.click(within(screen.getByText("pm1@example.com").closest("tr")!).getByRole("button", { name: "Deactivate" }));
    await waitFor(() => expect(api.count("PATCH /v1/users/pm1")).toBe(1));
    expect(api.calls.find((c) => c.path === "/v1/users/pm1")?.body).toEqual({ status: "disabled" });
    await userEvent.click(within(screen.getByText("pm2@example.com").closest("tr")!).getByRole("button", { name: "Reactivate" }));
    await waitFor(() => expect(api.count("PATCH /v1/users/pm2")).toBe(1));
    expect(api.calls.find((c) => c.path === "/v1/users/pm2")?.body).toEqual({ status: "active" });
  });

  it("someone whose access ends while signed in is sent to sign-in with the reason", async () => {
    mockApi({ "GET /v1/me": ME(["property_manager"]),
      "GET /v1/properties": { status: 403, body: { code: "account_inactive", message: "Your access ended on 2018-12-31. Ask your administrator to reactivate it.", request_id: "r" } } });
    renderApp("/properties");
    expect(await screen.findByText(/Your access ended on 2018-12-31/)).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument(); // the sign-in form
    expect(sessionStorage.getItem("anjung.tokens")).toBeNull();
  });
});

describe("property managers see their limits", () => {
  const manager = ME(["property_manager"], { active_until: "2019-11-20" });
  const property = { id: "P1", name: "Pelangi", type: "condominium", status: "active", unit_count: 2, manager_id: "u1",
    address: { line1: "1 Jalan A", city: "KL", state: "WP", postcode: "50000", country: "MY" }, created_at: "x", updated_at: "x" };

  it("counts whole days to the last day of access", () => {
    expect(daysLeft("2018-11-21", new Date(2018, 10, 21))).toBe(0);
    expect(daysLeft("2019-11-20", new Date(2018, 10, 21))).toBe(364);
  });

  it("the Properties page shows 5 / 5 units and explains the limit, but an empty property can still be added", async () => {
    mockApi({ "GET /v1/me": manager, "GET /v1/properties": { items: [property], next_cursor: null, quota: { used: 5, limit: 5 } } });
    renderApp("/properties");
    expect(await screen.findByText("5 / 5 units")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add property" })).toBeEnabled();
    expect(screen.getByText(/reached your limit of 5 units/)).toBeInTheDocument();
  });

  it("the dashboard shows how long their access lasts and their property usage", async () => {
    mockApi({ "GET /v1/me": manager, "GET /v1/properties": { items: [property], next_cursor: null, quota: { used: 1, limit: 5 } },
      "GET /v1/reports/arrears": { as_of: "x", rows: [], total_outstanding: money(0) }, "GET /v1/reports/occupancy": { as_of: "x", rows: [] } });
    renderApp("/");
    expect(await screen.findByText("Your access is valid until")).toBeInTheDocument();
    expect(screen.getByText("20-11-2019")).toBeInTheDocument();
    expect(await screen.findByText("1 / 5")).toBeInTheDocument();
  });
});

describe("maintenance says who and where", () => {
  it("lists the reporter and the unit or room", async () => {
    mockApi({ "GET /v1/me": ME(["property_manager"]), "GET /v1/maintenance-requests": page([{
      id: "M1", area: "unit", property_id: "P1", unit_id: "U2", tenant_id: "T2", title: "Aircon leaking", description: "Drips", category: "appliance",
      priority: "normal", status: "open", attachment_ids: [], created_at: "2018-11-21T00:00:00Z", updated_at: "x",
      property_name: "Pelangi", unit_label: "Master", parent_unit_label: "D-5-8", reported_by_name: "Ali Hassan", assigned_to_name: null,
    }]) });
    renderApp("/maintenance");
    const row = (await screen.findByRole("link", { name: "Aircon leaking" })).closest("tr")!;
    expect(within(row).getByText("Pelangi · D-5-8 › Master")).toBeInTheDocument();
    expect(within(row).getByText("Ali Hassan")).toBeInTheDocument();
  });
});

describe("tenancy agreement", () => {
  const draft = (agreement: object) => ({ id: "L1", unit_id: "U1", property_id: "P1", tenant_ids: ["T1"], status: "draft", start_date: "2018-12-01", end_date: null,
    notice_days: 30, monthly_rent: money(150000), deposits: [], due_day: 5, balance: money(0), credit: money(0), created_at: "x", updated_at: "x",
    property_name: "Pelangi", unit_label: "A-1", parent_unit_label: null, tenants: [{ id: "T1", full_name: "Siti Aminah" }], agreement });
  const org = { id: "o1", name: "Demo", status: "active", settings: {}, created_at: "x", updated_at: "x" };

  it("staff cannot activate until it is signed, and send it to the tenants", async () => {
    const notSent = { status: "not_sent", signatures: [], pending_tenant_ids: ["T1"] };
    const api = mockApi({ "GET /v1/me": ME(["org_admin"]), "GET /v1/org": org, "GET /v1/leases/L1": draft(notSent), "GET /v1/documents": page([]),
      "POST /v1/leases/L1/agreement/send": draft({ ...notSent, status: "sent", document_id: "D1", sent_at: "x" }) });
    renderApp("/leases/L1");
    expect(await screen.findByRole("button", { name: "Activate" })).toBeDisabled();
    expect(screen.getAllByText(/must sign the tenancy agreement/).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "Send to tenants" }));
    await waitFor(() => expect(api.count("POST /v1/leases/L1/agreement/send")).toBe(1));
  });

  it("a tenant signs by typing their name and agreeing", async () => {
    const sent = { status: "sent", document_id: "D1", sent_at: "2018-11-21T00:00:00Z", signatures: [], pending_tenant_ids: ["T1"] };
    const api = mockApi({ "GET /v1/me": ME(["tenant"], { tenant_id: "T1" }), "GET /v1/leases/L1": draft(sent), "GET /v1/documents": page([]),
      "POST /v1/leases/L1/agreement/sign": draft({ ...sent, status: "signed", signatures: [{ tenant_id: "T1", full_name: "Siti Aminah", method: "portal", signed_at: "2018-11-21T02:00:00Z" }], pending_tenant_ids: [] }) });
    renderApp("/leases/L1");
    const sign = await screen.findByRole("button", { name: "Sign agreement" });
    await userEvent.type(screen.getByLabelText("Your full name"), "Siti Aminah");
    expect(sign).toBeDisabled(); // must tick the box too
    await userEvent.click(screen.getByLabelText(/I have read the tenancy agreement/));
    await userEvent.click(sign);
    await waitFor(() => expect(api.count("POST /v1/leases/L1/agreement/sign")).toBe(1));
    expect(api.calls.find((c) => c.path === "/v1/leases/L1/agreement/sign")?.body).toEqual({ full_name: "Siti Aminah", agree: true });
    expect(api.count("GET /v1/invoices")).toBe(0); // a draft has no invoices to load
  });

  it("the tenant dashboard says an agreement is waiting", async () => {
    const sent = { status: "sent", document_id: "D1", sent_at: "x", signatures: [], pending_tenant_ids: ["T1"] };
    mockApi({ "GET /v1/me": ME(["tenant"], { tenant_id: "T1" }), "GET /v1/leases": page([draft(sent)]),
      "GET /v1/invoices": page([]), "GET /v1/maintenance-requests": page([]) });
    renderApp("/");
    expect(await screen.findByText(/tenancy agreement for Pelangi · A-1 is ready to sign/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review and sign" })).toHaveAttribute("href", "/leases/L1");
  });
});

