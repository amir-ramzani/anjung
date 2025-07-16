import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { ME, mockApi, money, page, renderApp, signedIn } from "../test/helpers";

beforeEach(() => signedIn());
afterEach(() => vi.unstubAllGlobals());

const stamp = { created_at: "2018-11-20T00:00:00Z", updated_at: "2018-11-20T00:00:00Z" };
const property = { id: "P1", name: "Menara", type: "condominium", status: "active", address: { line1: "1 Jalan A", city: "KL", state: "WP", postcode: "50000", country: "MY" }, ...stamp };
const unit = (over: object = {}) => ({ id: "U1", property_id: "P1", type: "whole_unit", parent_unit_id: null, letting_mode: "whole", label: "A-1", default_rent: money(180000), status: "vacant", ...stamp, ...over });
const tenant = (over: object = {}) => ({ id: "T1", full_name: "Siti", email: "siti@example.com", phone: "+60123456789", status: "invited", linked: false, ...stamp, ...over });
const lease = (over: object = {}) => ({
  id: "L1", unit_id: "U1", property_id: "P1", tenant_ids: ["T1"], status: "active", start_date: "2018-12-01", end_date: "2019-11-30", notice_days: 30,
  monthly_rent: money(150000), deposits: [], deposits_held: [], due_day: 5, balance: money(0), credit: money(0), ...stamp, ...over,
});
const admin = { "GET /v1/me": ME(["org_admin"]) };

describe("property and unit editing", () => {
  it("edits a property and sends the whole address back", async () => {
    const api = mockApi({ ...admin, "GET /v1/properties/P1": property, "GET /v1/properties/P1/units": page([]), "GET /v1/documents": page([]), "PATCH /v1/properties/P1": property });
    renderApp("/properties/P1");
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const name = screen.getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Menara Baru");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.count("PATCH /v1/properties/P1")).toBe(1));
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toEqual({ name: "Menara Baru", address: { line1: "1 Jalan A", city: "KL", state: "WP", postcode: "50000", country: "MY" } });
  });

  it("an administrator hands a property to another manager", async () => {
    const user = (user_id: string, email: string, roles: string[], status = "active") => ({ user_id, email, roles, status, created_at: "x" });
    const api = mockApi({ ...admin, "GET /v1/properties/P1": { ...property, manager_id: "boss" }, "GET /v1/properties/P1/units": page([]), "GET /v1/documents": page([]),
      "GET /v1/users": page([user("boss", "boss@example.com", ["org_admin"]), user("pm1", "pm1@example.com", ["property_manager"]), user("gone", "gone@example.com", ["property_manager"], "disabled")]),
      "PATCH /v1/properties/P1": { ...property, manager_id: "pm1" } });
    renderApp("/properties/P1");
    const picker = await screen.findByLabelText("Managed by");
    await waitFor(() => expect(screen.getByRole("option", { name: "pm1@example.com" })).toBeInTheDocument());
    expect(screen.queryByRole("option", { name: "gone@example.com" })).not.toBeInTheDocument(); // disabled staff cannot manage
    await userEvent.selectOptions(picker, "pm1");
    await userEvent.click(screen.getByRole("button", { name: "Change manager" }));
    await waitFor(() => expect(api.count("PATCH /v1/properties/P1")).toBe(1));
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toEqual({ manager_id: "pm1" });
  });

  it("a property manager gets no manager picker", async () => {
    mockApi({ "GET /v1/me": ME(["property_manager"]), "GET /v1/properties/P1": property, "GET /v1/properties/P1/units": page([]), "GET /v1/documents": page([]) });
    renderApp("/properties/P1");
    expect(await screen.findByRole("heading", { name: "Menara" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Managed by")).not.toBeInTheDocument();
  });

  it("edits a unit's rent in sen and does not offer a status change for an occupied unit", async () => {
    const api = mockApi({ ...admin, "GET /v1/properties/P1": property, "GET /v1/documents": page([]),
      "GET /v1/properties/P1/units": page([unit({ status: "occupied" })]), "PATCH /v1/units/U1": unit() });
    renderApp("/properties/P1");
    const row = (await screen.findByText("A-1")).closest("tr")!;
    await userEvent.click(row.querySelector("button")!); // Edit
    expect(screen.queryByLabelText("Status")).not.toBeInTheDocument(); // an occupied unit's status follows its lease
    const rent = screen.getByLabelText("Default rent");
    expect(rent).toHaveValue("1800.00");
    await userEvent.clear(rent);
    await userEvent.type(rent, "1,950.50");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.count("PATCH /v1/units/U1")).toBe(1));
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toEqual({ label: "A-1", default_rent: { amount_minor: 195050, currency: "MYR" } });
  });

  it("shows the server's reason when a label is already taken", async () => {
    mockApi({ ...admin, "GET /v1/properties/P1": property, "GET /v1/documents": page([]), "GET /v1/properties/P1/units": page([unit()]),
      "PATCH /v1/units/U1": { status: 409, body: { code: "conflict", message: "A unit with this label already exists", request_id: "r" } } });
    renderApp("/properties/P1");
    await userEvent.click((await screen.findByText("A-1")).closest("tr")!.querySelector("button")!);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("A unit with this label already exists")).toBeInTheDocument();
  });
});

describe("tenant editing and consent", () => {
  const base = { ...admin, "GET /v1/documents": page([]), "GET /v1/tenants/T1/consents": page([]) };

  it("locks the email once the tenant is linked and clears a phone with null", async () => {
    const api = mockApi({ ...base, "GET /v1/tenants/T1": tenant({ status: "active", linked: true }), "PATCH /v1/tenants/T1": tenant() });
    renderApp("/tenants/T1");
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText("Email", { selector: "input" })).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("Phone"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.count("PATCH /v1/tenants/T1")).toBe(1));
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toEqual({ full_name: "Siti", phone: null });
  });

  it("changes the email only while the tenant has not been invited", async () => {
    const api = mockApi({ ...base, "GET /v1/tenants/T1": tenant(), "PATCH /v1/tenants/T1": tenant() });
    renderApp("/tenants/T1");
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const email = screen.getByLabelText("Email", { selector: "input" });
    await userEvent.clear(email);
    await userEvent.type(email, "new@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.count("PATCH /v1/tenants/T1")).toBe(1));
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toMatchObject({ email: "new@example.com" });
  });

  it("shows the current decision per purpose from the history, newest first, and records a new one", async () => {
    const api = mockApi({
      ...admin, "GET /v1/documents": page([]), "GET /v1/tenants/T1": tenant({ status: "active", linked: true }),
      "GET /v1/tenants/T1/consents": page([
        { purpose: "email_messaging", policy_version: "2", granted: false, recorded_at: "2018-11-20T10:00:00Z", source: "portal" },
        { purpose: "email_messaging", policy_version: "1", granted: true, recorded_at: "2018-11-01T10:00:00Z", source: "staff_on_behalf" },
      ]),
      "POST /v1/tenants/T1/consents": { status: 201, body: {} },
    });
    renderApp("/tenants/T1");
    const current = await screen.findByText(/Email messages:/);
    expect(current.parentElement).toHaveTextContent("Withdrawn"); // the later record wins: consent was withdrawn
    expect(screen.getByText("Staff, on their behalf")).toBeInTheDocument(); // the history is kept
    await userEvent.selectOptions(screen.getByLabelText("Purpose"), "marketing");
    await userEvent.click(screen.getByRole("button", { name: "Record consent" }));
    await waitFor(() => expect(api.count("POST /v1/tenants/T1/consents")).toBe(1));
    expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({ purpose: "marketing", policy_version: "2025-11", granted: true });
  });
});

describe("lease amend and renew", () => {
  const base = (l: object) => ({ ...admin, "GET /v1/leases/L1": l, "GET /v1/invoices": page([]), "GET /v1/documents": page([]), "GET /v1/leases/L1/ledger": page([]) });

  it("amends an active lease from a chosen date", async () => {
    const api = mockApi({ ...base(lease()), "PATCH /v1/leases/L1": lease() });
    renderApp("/leases/L1");
    await userEvent.click(await screen.findByRole("button", { name: "Amend" }));
    expect(screen.getByText(/Invoices already issued are not changed/)).toBeInTheDocument();
    const rent = screen.getByLabelText("Monthly rent");
    await userEvent.clear(rent);
    await userEvent.type(rent, "1700");
    await userEvent.type(screen.getByLabelText("Applies from"), "2019-02-01");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.count("PATCH /v1/leases/L1")).toBe(1));
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toEqual({
      monthly_rent: { amount_minor: 170000, currency: "MYR" }, due_day: 5, notice_days: 30, end_date: "2019-11-30", effective_from: "2019-02-01" });
  });

  it("makes a lease rolling by sending an explicit null end date, and a draft has no 'applies from'", async () => {
    const api = mockApi({ ...base(lease({ status: "draft" })), "PATCH /v1/leases/L1": lease() });
    renderApp("/leases/L1");
    await userEvent.click(await screen.findByRole("button", { name: "Amend" }));
    expect(screen.queryByLabelText("Applies from")).not.toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/Rolling/));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.count("PATCH /v1/leases/L1")).toBe(1));
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toMatchObject({ end_date: null });
  });

  it("explains an overlap when the new end date collides with another lease", async () => {
    mockApi({ ...base(lease()), "PATCH /v1/leases/L1": { status: 409, body: { code: "lease_overlap", message: "overlap", request_id: "r" } } });
    renderApp("/leases/L1");
    await userEvent.click(await screen.findByRole("button", { name: "Amend" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Another lease already covers some of these dates on this unit.")).toBeInTheDocument();
  });

  it("offers renewal only for an active fixed-term lease", async () => {
    mockApi({ ...base(lease({ end_date: null })) });
    renderApp("/leases/L1");
    await screen.findByRole("button", { name: "Amend" });
    expect(screen.queryByRole("button", { name: "Renew" })).not.toBeInTheDocument();
  });

  it("renews and goes to the new draft", async () => {
    const api = mockApi({ ...base(lease()), "POST /v1/leases/L1/renew": { status: 201, body: lease({ id: "L2", status: "draft" }) },
      "GET /v1/leases/L2": lease({ id: "L2", status: "draft" }), "GET /v1/leases/L2/ledger": page([]) });
    renderApp("/leases/L1");
    await userEvent.click(await screen.findByRole("button", { name: "Renew" }));
    await userEvent.type(screen.getByLabelText("New end date"), "2020-11-30");
    await userEvent.type(screen.getByLabelText("New monthly rent (optional)"), "1650");
    await userEvent.click(screen.getAllByRole("button", { name: "Renew" }).at(-1)!);
    await waitFor(() => expect(api.count("POST /v1/leases/L1/renew")).toBe(1));
    expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({ new_end_date: "2020-11-30", monthly_rent: { amount_minor: 165000, currency: "MYR" } });
    await waitFor(() => expect(api.count("GET /v1/leases/L2")).toBeGreaterThan(0));
  });
});

describe("ledger and one-off charges", () => {
  it("shows the ledger with running balances and a statement for a range", async () => {
    const api = mockApi({
      ...admin, "GET /v1/leases/L1": lease(), "GET /v1/invoices": page([]), "GET /v1/documents": page([]),
      "GET /v1/leases/L1/ledger": page([
        { id: "E1", lease_id: "L1", type: "charge_rent", amount: money(150000), balance_after: money(150000), memo: null, created_at: "2018-11-20T00:00:00Z", created_by: "system" },
        { id: "E2", lease_id: "L1", type: "payment", amount: money(-150000), balance_after: money(0), memo: "cash payment", created_at: "2018-12-01T00:00:00Z", created_by: "u" },
      ]),
      "GET /v1/leases/L1/statement": { lease_id: "L1", from: "2018-11-01", to: "2018-11-30", opening_balance: money(0), closing_balance: money(150000), entries: [{}] },
    });
    renderApp("/leases/L1");
    expect(await screen.findByRole("cell", { name: "Rent" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "-RM 1,500.00" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "cash payment" })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("From"), "2018-11-01");
    await userEvent.type(screen.getByLabelText("To"), "2018-11-30");
    await userEvent.click(screen.getByRole("button", { name: "Show statement" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Opening balance: RM 0.00 · Closing balance: RM 1,500.00");
    expect(api.calls.find((c) => c.path === "/v1/leases/L1/statement")).toBeDefined();
  });

  it("raises a charge with one idempotency key and the amount in sen", async () => {
    const api = mockApi({
      ...admin, "GET /v1/leases/L1": lease({ status: "terminated" }), "GET /v1/invoices": page([]), "GET /v1/documents": page([]), "GET /v1/leases/L1/ledger": page([]),
      "POST /v1/leases/L1/charges": { status: 201, body: {} },
    });
    renderApp("/leases/L1");
    await userEvent.click(await screen.findByRole("button", { name: "Add a charge" })); // allowed after move-out
    await userEvent.type(screen.getByLabelText("Description"), "Electricity share, Oct 2018");
    await userEvent.type(screen.getByLabelText("Amount"), "42.50");
    await userEvent.type(screen.getByLabelText("Due"), "2018-12-20");
    await userEvent.click(screen.getByRole("button", { name: "Create charge" }));
    await waitFor(() => expect(api.count("POST /v1/leases/L1/charges")).toBe(1));
    const call = api.calls.find((c) => c.method === "POST")!;
    expect(call.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(call.body).toEqual({ description: "Electricity share, Oct 2018", category: "utility", amount: { amount_minor: 4250, currency: "MYR" }, due_date: "2018-12-20" });
  });

  it("does not offer a charge on a draft lease, and a tenant never sees it", async () => {
    mockApi({ ...admin, "GET /v1/leases/L1": lease({ status: "draft" }), "GET /v1/invoices": page([]), "GET /v1/documents": page([]) });
    renderApp("/leases/L1");
    await screen.findByRole("button", { name: "Amend" });
    expect(screen.queryByRole("button", { name: "Add a charge" })).not.toBeInTheDocument();
  });

  it("a tenant sees the ledger but none of the staff actions", async () => {
    mockApi({ "GET /v1/me": ME(["tenant"], { tenant_id: "T1" }), "GET /v1/leases/L1": lease(), "GET /v1/invoices": page([]), "GET /v1/documents": page([]), "GET /v1/leases/L1/ledger": page([]) });
    renderApp("/leases/L1");
    expect(await screen.findByRole("heading", { name: "Ledger" })).toBeInTheDocument();
    for (const name of ["Amend", "Renew", "Add a charge", "End tenancy"]) expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
  });
});

describe("maintenance assignment", () => {
  const req = (over: object = {}) => ({ id: "M1", area: "unit", property_id: "P1", unit_id: "U1", tenant_id: "T1", title: "Leak", description: "d", category: "plumbing",
    priority: "normal", status: "triaged", assigned_to: null, attachment_ids: [], ...stamp, ...over });
  const common = { "GET /v1/maintenance-requests/M1": req(), "GET /v1/maintenance-requests/M1/comments": page([]), "GET /v1/documents": page([]) };

  it("an administrator picks a staff member, and the generic Assign button is gone", async () => {
    const api = mockApi({ ...admin, ...common,
      "GET /v1/users": page([{ user_id: "S1", email: "pm@example.com", name: "Aminah", roles: ["property_manager"], status: "active", created_at: "x" }, { user_id: "S2", email: "off@example.com", roles: ["property_manager"], status: "disabled", created_at: "x" }]),
      "PATCH /v1/maintenance-requests/M1": req({ status: "assigned", assigned_to: "S1" }) });
    renderApp("/maintenance/M1");
    expect(await screen.findByRole("heading", { name: "Leak" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Assign" })).toHaveLength(1); // only the one inside the form
    const select = await screen.findByLabelText("Assign to");
    await waitFor(() => expect(select.querySelectorAll("option").length).toBe(2)); // blank plus the active user, not the disabled one
    await userEvent.selectOptions(select, "S1");
    await userEvent.click(screen.getByRole("button", { name: "Assign" }));
    await waitFor(() => expect(api.count("PATCH /v1/maintenance-requests/M1")).toBe(1));
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toEqual({ assigned_to: "S1" });
  });

  it("a property manager can only assign to themselves, and never asks for the staff list", async () => {
    const api = mockApi({ "GET /v1/me": ME(["property_manager"], { user_id: "PM1" }), ...common, "PATCH /v1/maintenance-requests/M1": req({ status: "assigned", assigned_to: "PM1" }) });
    renderApp("/maintenance/M1");
    await userEvent.click(await screen.findByRole("button", { name: "Assign to me" }));
    await waitFor(() => expect(api.count("PATCH /v1/maintenance-requests/M1")).toBe(1));
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toEqual({ assigned_to: "PM1" });
    expect(api.count("GET /v1/users")).toBe(0); // that endpoint is for administrators and would return 403
  });

  it("offers no assignment before triage", async () => {
    mockApi({ ...admin, ...common, "GET /v1/maintenance-requests/M1": req({ status: "open" }), "GET /v1/users": page([]) });
    renderApp("/maintenance/M1");
    await screen.findByRole("heading", { name: "Leak" });
    expect(screen.queryByLabelText("Assign to")).not.toBeInTheDocument();
  });
});

describe("report filters", () => {
  it("narrows arrears and income to one property and refuses a backwards range", async () => {
    const empty = { as_of: "x", rows: [] };
    const api = mockApi({
      ...admin, "GET /v1/properties": page([{ id: "P1", name: "Menara", ...stamp }]),
      "GET /v1/reports/arrears": { ...empty, total_outstanding: money(0) }, "GET /v1/reports/occupancy": empty, "GET /v1/reports/income": empty,
      "GET /v1/reports/maintenance": empty,
    });
    renderApp("/reports");
    await screen.findByRole("option", { name: "Menara" }); // the property list loads first
    await userEvent.selectOptions(screen.getByLabelText("Property"), "P1");
    await waitFor(() => expect(api.fn.mock.calls.some((c) => String((c[0] as Request).url).includes("reports/arrears?property_id=P1"))).toBe(true));
    expect(api.fn.mock.calls.some((c) => String((c[0] as Request).url).includes("reports/income") && String((c[0] as Request).url).includes("property_id=P1"))).toBe(true);
    const before = api.count("GET /v1/reports/income");
    const from = screen.getByLabelText("From");
    await userEvent.clear(from);
    await userEvent.type(from, "2091-01");
    expect(await screen.findByText("The end must not be before the start.")).toBeInTheDocument();
    expect(api.count("GET /v1/reports/income")).toBe(before); // no request for a range that cannot be right
  });
});
