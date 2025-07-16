import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";

import { ME, mockApi, money, page, renderApp, signedIn } from "../test/helpers";

const property = (over: object = {}) => ({
  id: "P1", name: "Menara Demo", type: "condominium", status: "active", unit_count: 1, manager_id: "u1",
  address: { line1: "1 Jalan", city: "KL", state: "WP", postcode: "50000", country: "MY" },
  created_at: "2018-11-20T00:00:00Z", updated_at: "2018-11-20T00:00:00Z", ...over,
});
const propertyPage = (over: object = {}, extra: object = {}) => ({
  "GET /v1/me": ME(["property_manager"]),
  "GET /v1/properties/P1": property(over),
  "GET /v1/properties/P1/units": page([]),
  "GET /v1/documents": page([]),
  ...extra,
});
const tenant = { id: "T1", full_name: "Siti Aminah", email: "siti@example.com", status: "active", linked: true, created_at: "x", updated_at: "x", phone: null };

beforeEach(() => signedIn());
afterEach(() => vi.unstubAllGlobals());

describe("deactivate and reactivate", () => {
  it("deactivates a property, and offers reactivation once it is inactive", async () => {
    const api = mockApi(propertyPage({}, { "POST /v1/properties/P1/deactivate": property({ status: "inactive" }) }));
    renderApp("/properties/P1");
    await userEvent.click(await screen.findByRole("button", { name: "Deactivate" }));
    await waitFor(() => expect(api.count("POST /v1/properties/P1/deactivate")).toBe(1));
  });

  it("shows an inactive property as such, with Reactivate and no editing", async () => {
    mockApi(propertyPage({ status: "inactive" }));
    renderApp("/properties/P1");
    expect(await screen.findByRole("button", { name: "Reactivate" })).toBeInTheDocument();
    expect(screen.getAllByText("Inactive").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Add unit" })).not.toBeInTheDocument();
  });
});

describe("delete", () => {
  it("shows what goes, needs a reason and the typed name, then deletes and leaves the page", async () => {
    let query = "";
    const api = mockApi(propertyPage({}, {
      "GET /v1/properties/P1/deletion": { mode: "remove", blockers: [], affects: { units: 2, rooms: 1, documents: 1 }, note: null },
      "DELETE /v1/properties/P1": (req: Request) => { query = new URL(req.url).search; return { outcome: "removed", privacy_request_id: null }; },
      "GET /v1/properties": { items: [], next_cursor: null, quota: null },
    }));
    renderApp("/properties/P1");
    await userEvent.click(await screen.findByRole("button", { name: "Delete…" }));
    expect(await screen.findByText("This will be deleted completely. This cannot be undone.")).toBeInTheDocument();
    expect(screen.getByText(/2 units, 1 room, 1 document/)).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: "Delete permanently" });
    await userEvent.type(screen.getByLabelText("Reason (kept in the audit log)"), "made by mistake");
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Type “Menara Demo” to confirm"), "menara demo"); // case does not matter
    await userEvent.click(confirm);
    await waitFor(() => expect(api.count("DELETE /v1/properties/P1")).toBe(1));
    expect(new URLSearchParams(query).get("reason")).toBe("made by mistake");
    expect(await screen.findByRole("heading", { name: "Properties" })).toBeInTheDocument();
  });

  it("offers no delete button for a record kept for its history, and says why in its place", async () => {
    mockApi(propertyPage({}, {
      "GET /v1/properties/P1/deletion": { mode: "blocked", blockers: [{ code: "lease_history", message: "A-1 has lease records. Deactivate the property instead." }], affects: {} },
    }));
    renderApp("/properties/P1");
    expect(await screen.findByText("Kept as a record, so it cannot be deleted:")).toBeInTheDocument();
    expect(screen.getByText(/A-1 has lease records/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete…" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deactivate" })).toBeInTheDocument(); // what to do instead
  });

  it("says what to clear first when only that stops the delete", async () => {
    mockApi(propertyPage({}, {
      "GET /v1/properties/P1/deletion": { mode: "blocked", affects: {}, blockers: [{ code: "maintenance_history", message: "x", params: { what: "property", count: 1 } }] },
    }));
    renderApp("/properties/P1");
    expect(await screen.findByText("This cannot be deleted yet:")).toBeInTheDocument();
    expect(screen.getByText(/1 maintenance request/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete…" })).not.toBeInTheDocument();
  });

  it("shows a blocker in the user's language, with its values", async () => {
    mockApi(propertyPage({}, {
      "GET /v1/properties/P1/deletion": { mode: "blocked", affects: {}, blockers: [
        { code: "lease_history", message: "x", params: { what: "property", units: "A-1, A-2", more: 3, count: 8 } },
        { code: "maintenance_history", message: "x", params: { what: "property", count: 2 } },
      ] },
    }));
    renderApp("/properties/P1");
    expect(await screen.findByText(/financial records: A-1, A-2\. Deactivate.* and 3 more/)).toBeInTheDocument();
    expect(screen.getByText("This property has 2 maintenance requests. Delete them first, or deactivate the property instead.")).toBeInTheDocument();
  });

  it("erases a tenant with history and stays on the page", async () => {
    const api = mockApi({
      "GET /v1/me": ME(["org_admin"]), "GET /v1/tenants/T1": tenant, "GET /v1/documents": page([]), "GET /v1/tenants/T1/consents": page([]),
      "GET /v1/tenants/T1/deletion": { mode: "erase", blockers: [], affects: { leases: 1 }, note: "The tenant has lease history." },
      "DELETE /v1/tenants/T1": { outcome: "erasure_started", privacy_request_id: "R1" },
    });
    renderApp("/tenants/T1");
    await userEvent.click(await screen.findByRole("button", { name: "Delete…" }));
    expect(await screen.findByText("Personal data will be erased. This cannot be undone.")).toBeInTheDocument();
    expect(screen.getByText("The tenant has lease history.")).toBeInTheDocument();
    expect(screen.getByText(/1 lease \(kept, without personal data\)/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Reason (kept in the audit log)"), "tenant asked");
    await userEvent.type(screen.getByLabelText("Type “Siti Aminah” to confirm"), "Siti Aminah");
    await userEvent.click(screen.getByRole("button", { name: "Erase personal data" }));
    await waitFor(() => expect(api.count("DELETE /v1/tenants/T1")).toBe(1));
    expect(screen.getByRole("heading", { name: "Siti Aminah" })).toBeInTheDocument();
  });

  it("offers deleting a lease only while it is a draft", async () => {
    const lease = {
      id: "L1", unit_id: "U1", property_id: "P1", tenant_ids: ["T1"], status: "active", start_date: "2018-12-01", end_date: null,
      notice_days: 30, monthly_rent: money(150000), deposits: [], due_day: 5, balance: money(0), credit: money(0),
      created_at: "x", updated_at: "x", unit_label: "A-1",
    };
    mockApi({ "GET /v1/me": ME(["org_admin"]), "GET /v1/leases/L1": lease, "GET /v1/invoices": page([]), "GET /v1/documents": page([]), "GET /v1/leases/L1/ledger": page([]), "GET /v1/org": { id: "o1", name: "x", status: "active", settings: {}, created_at: "x", updated_at: "x" } });
    renderApp("/leases/L1");
    expect(await screen.findByRole("heading", { name: "Lease" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete draft…" })).not.toBeInTheDocument();
  });
});
