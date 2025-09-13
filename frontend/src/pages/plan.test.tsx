import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";

import { ApiError } from "../api/client";
import { errorText } from "../components/ui";
import i18n from "../i18n";
import { unitAllowance } from "./Properties";
import { ME, mockApi, money, page, renderApp, signedIn } from "../test/helpers";

const plan = (over: object = {}) => ({ unit_limit: 10, active_until: "2019-12-31", units_used: 8, status: "active", read_only_until: "2020-01-30", ...over });
const dashboard = { "GET /v1/reports/arrears": { as_of: "x", rows: [], total_outstanding: money(0) }, "GET /v1/reports/occupancy": { as_of: "x", rows: [] } };
const org = (over: object = {}) => ({ id: "O1", name: "Rumah Kita", status: "active", settings: {}, created_at: "x", updated_at: "x", plan: plan(), ...over });

beforeEach(() => signedIn());
afterEach(() => vi.unstubAllGlobals());

describe("a company's plan", () => {
  it("staff see the plan's usage and end date on the dashboard", async () => {
    mockApi({ "GET /v1/me": ME(["org_admin"], { plan: plan() }), ...dashboard });
    renderApp("/");
    expect(await screen.findByText("8 / 10")).toBeInTheDocument();
    expect(screen.getByText("Plan ends")).toBeInTheDocument();
    expect(screen.getByText("31-12-2019")).toBeInTheDocument();
  });

  it("says plainly when the plan has ended and the app is read-only", async () => {
    mockApi({ "GET /v1/me": ME(["property_manager"], { plan: plan({ status: "read_only", active_until: "2018-11-01", read_only_until: "2018-12-01" }) }), ...dashboard });
    renderApp("/");
    expect(await screen.findByText(/Your plan ended on 01-11-2018\. You can view and export until 01-12-2018/)).toBeInTheDocument();
  });

  it("signs everyone out with the reason once the plan has fully ended", async () => {
    mockApi({ "GET /v1/me": { status: 403, body: { code: "plan_ended", message: "This organisation's plan has ended. Contact your landlord or admin.", request_id: "r" } } });
    renderApp("/");
    expect(await screen.findByText(/plan has ended/)).toBeInTheDocument();
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
  });

  it("the platform admin sets a company's plan with a reason", async () => {
    const api = mockApi({
      "GET /v1/me": ME(["platform_admin"]),
      "GET /v1/platform/organizations": page([org()]),
      "PATCH /v1/platform/organizations/O1/plan": org({ plan: plan({ unit_limit: 20 }) }),
    });
    renderApp("/platform/organizations");
    expect(await screen.findByText("8 / 10")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Plan" }));
    const limit = screen.getAllByLabelText("Unit limit for the company (optional)").at(-1)!;
    await userEvent.clear(limit);
    await userEvent.type(limit, "20");
    await userEvent.type(screen.getAllByLabelText("Reason").at(-1)!, "upgraded");
    await userEvent.click(screen.getAllByRole("button", { name: "Save" }).at(-1)!);
    await waitFor(() => expect(api.count("PATCH /v1/platform/organizations/O1/plan")).toBe(1));
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toEqual({ unit_limit: 20, active_until: "2019-12-31", reason: "upgraded" });
  });
});

describe("the platform admin corrects a company's details (v0.17.0)", () => {
  it("changes the name and handle with a reason, keeping the rest of the branding", async () => {
    const branded = org({ name: "Rumah Kitta", branding: { enabled: true, handle: "rumah-kitta", display_name: "Rumah Kita", login_page: true } });
    const api = mockApi({
      "GET /v1/me": ME(["platform_admin"]),
      "GET /v1/platform/organizations": page([branded]),
      "PATCH /v1/platform/organizations/O1/details": org(),
    });
    renderApp("/platform/organizations");
    await userEvent.click(await screen.findByRole("button", { name: "Edit details" }));
    const form = screen.getByRole("form", { name: "Details of Rumah Kitta" });
    const name = screen.getAllByLabelText("Organisation name").at(-1)!;
    await userEvent.clear(name);
    await userEvent.type(name, "Rumah Kita");
    const handle = screen.getByDisplayValue("rumah-kitta");
    await userEvent.clear(handle);
    await userEvent.type(handle, "rumah-kita");
    expect(form).toHaveTextContent("/login/rumah-kita");
    await userEvent.type(screen.getAllByLabelText("Reason").at(-1)!, "typo");
    await userEvent.click(screen.getAllByRole("button", { name: "Save" }).at(-1)!);
    await waitFor(() => expect(api.count("PATCH /v1/platform/organizations/O1/details")).toBe(1));
    const body = api.calls.find((c) => c.method === "PATCH")?.body as Record<string, any>;
    expect(body.name).toBe("Rumah Kita");
    expect(body.reason).toBe("typo");
    expect(body.branding).toMatchObject({ enabled: true, handle: "rumah-kita", display_name: "Rumah Kita", login_page: true });
    expect(body.branding).not.toHaveProperty("logo_document_id"); // the logo is the company admin's
  });

  it("sends no branding for a company that has none when it is left empty", async () => {
    const api = mockApi({
      "GET /v1/me": ME(["platform_admin"]),
      "GET /v1/platform/organizations": page([org({ name: "Acme" })]),
      "PATCH /v1/platform/organizations/O1/details": org(),
    });
    renderApp("/platform/organizations");
    await userEvent.click(await screen.findByRole("button", { name: "Edit details" }));
    const name = screen.getAllByLabelText("Organisation name").at(-1)!;
    await userEvent.clear(name);
    await userEvent.type(name, "Acme Bhd");
    await userEvent.type(screen.getAllByLabelText("Reason").at(-1)!, "renamed");
    await userEvent.click(screen.getAllByRole("button", { name: "Save" }).at(-1)!);
    await waitFor(() => expect(api.count("PATCH /v1/platform/organizations/O1/details")).toBe(1));
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toEqual({ name: "Acme Bhd", reason: "renamed" });
  });

  it("offers no edit for a closed company", async () => {
    mockApi({ "GET /v1/me": ME(["platform_admin"]), "GET /v1/platform/organizations": page([org({ status: "closed" })]) });
    renderApp("/platform/organizations");
    expect(await screen.findByText("Rumah Kita")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit details" })).toBeNull();
  });
});

describe("the units badge shows the tighter limit", () => {
  it("picks whichever limit leaves less room", () => {
    expect(unitAllowance({ used: 2, limit: null }, { unit_limit: 5, units_used: 6 })).toEqual({ kind: "company", used: 6, limit: 5, full: true });
    expect(unitAllowance({ used: 2, limit: 4 }, { unit_limit: 50, units_used: 6 })).toEqual({ kind: "own", used: 2, limit: 4, full: false });
    expect(unitAllowance({ used: 2, limit: null }, { unit_limit: null, units_used: 6 })).toEqual({ kind: "unlimited", used: 2, limit: null, full: false });
    expect(unitAllowance(null, { unit_limit: 10, units_used: 3 })).toEqual({ kind: "company", used: 3, limit: 10, full: false }); // an admin
    expect(unitAllowance(null, { unit_limit: null })).toBeNull(); // an admin, no plan limit: no badge
  });

  it("tells a manager with no limit of their own that the company's plan is full", async () => {
    signedIn();
    mockApi({
      "GET /v1/me": ME(["property_manager"], { plan: plan({ unit_limit: 5, units_used: 6 }) }),
      "GET /v1/properties": { items: [], next_cursor: null, quota: { used: 2, limit: null } },
    });
    renderApp("/properties");
    expect(await screen.findByText("Company plan: 6 / 5 units")).toBeInTheDocument();
    expect(screen.getByText(/Your company's plan is full \(6 of 5 units\)/)).toBeInTheDocument();
  });
});

describe("the properties list counts units the way the plan does", () => {
  it("shows units that are on, and how many are off", async () => {
    mockApi({
      "GET /v1/me": ME(["org_admin"], { plan: plan({ unit_limit: 5, units_used: 3 }) }),
      "GET /v1/users": page([]),
      "GET /v1/properties": { items: [
        { id: "P1", name: "Pelangi", type: "condominium", status: "active", unit_count: 2, units_active: 1, address: {}, created_at: "x", updated_at: "x" },
        { id: "P2", name: "Menara", type: "apartment", status: "active", unit_count: 2, units_active: 2, address: {}, created_at: "x", updated_at: "x" },
      ], next_cursor: null, quota: null },
    });
    renderApp("/properties");
    expect(await screen.findByText("1 (1 inactive)")).toBeInTheDocument();
    expect(screen.getByText("Company plan: 3 / 5 units")).toBeInTheDocument();
  });
});

describe("plan and limit errors in the user's language", () => {
  afterEach(() => { void i18n.changeLanguage("en"); });

  it("uses the error's values with the app's own wording, English or Malay", async () => {
    const e = new ApiError(409, "quota_exceeded", "Your plan allows 5 units and 5 are in use.", [], "r", { scope: "plan", limit: 5, used: 5 });
    expect(errorText(e)).toBe("Your company's plan allows 5 units and 5 are in use. Ask Anjung to raise the limit, or deactivate a unit first.");
    await i18n.changeLanguage("ms");
    expect(errorText(e)).toBe("Pelan syarikat anda membenarkan 5 unit dan 5 sedang digunakan. Minta Anjung menaikkan had, atau nyahaktifkan unit dahulu.");
    const ro = new ApiError(403, "plan_read_only", "x", [], "r", { date: "2018-12-01", until: "2018-12-31" });
    expect(errorText(ro)).toContain("tamat pada 01-12-2018");
    // an error the app has no wording for keeps the API's message
    expect(errorText(new ApiError(409, "invalid_state", "Something specific", [], "r"))).toBe("Something specific");
  });
});
