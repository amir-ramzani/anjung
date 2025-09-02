import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";

import { ME, mockApi, money, page, renderApp, signedIn } from "../test/helpers";

// contract v0.15.0: staff welcome emails, the bounce check, the maintenance report and CSV downloads

const user = (over: object = {}) => ({ user_id: "pm1", email: "pm1@example.com", name: "Hafiz", roles: ["property_manager"], status: "invited",
  access: "invited", active_from: null, active_until: null, created_at: "x", units_managed: 0, ...over });
const org = { id: "O1", name: "Rumah Kita", status: "active", settings: {}, created_at: "x", updated_at: "x" };
const empty = { as_of: "x", rows: [] };

afterEach(() => vi.unstubAllGlobals());

describe("staff welcome emails", () => {
  beforeEach(() => signedIn());

  it("resends the welcome email to staff who have not signed in yet", async () => {
    const api = mockApi({
      "GET /v1/me": ME(["org_admin"]),
      "GET /v1/users": page([user(), user({ user_id: "pm2", email: "on@example.com", status: "active", access: "active" })]),
      "POST /v1/users/pm1/invite": { status: 202, body: user() },
    });
    renderApp("/users");
    expect(await screen.findByText(/a link to set their password/)).toBeInTheDocument();
    const buttons = await screen.findAllByRole("button", { name: "Resend invitation" });
    expect(buttons).toHaveLength(1); // only the one still invited
    await userEvent.click(buttons[0]!);
    expect(await screen.findByText("A new welcome email was sent to pm1@example.com. The earlier link no longer works.")).toBeInTheDocument();
    expect(api.count("POST /v1/users/pm1/invite")).toBe(1);
  });

  it("explains a bounced address in the user's language", async () => {
    mockApi({
      "GET /v1/me": ME(["org_admin"]),
      "GET /v1/users": page([user()]),
      "POST /v1/users/pm1/invite": { status: 409, body: { code: "address_suppressed", message: "x", request_id: "r", params: { scope: "staff" } } },
    });
    renderApp("/users");
    await userEvent.click(await screen.findByRole("button", { name: "Resend invitation" }));
    expect(await screen.findByText(/bounced or were marked as spam.*Remove this user/)).toBeInTheDocument();
  });

  it("lets the platform admin resend a company's first admin invitation with a reason", async () => {
    const api = mockApi({
      "GET /v1/me": ME(["platform_admin"]),
      "GET /v1/platform/organizations": page([org]),
      "POST /v1/platform/organizations/O1/admin-invite": { status: 202, body: { sent: 1 } },
    });
    vi.stubGlobal("prompt", vi.fn(() => "lost the email"));
    renderApp("/platform/organizations");
    await userEvent.click(await screen.findByRole("button", { name: "Resend admin invitation" }));
    expect(await screen.findByText("A new welcome email was sent to 1 admin.")).toBeInTheDocument();
    expect(api.calls.find((c) => c.path.endsWith("/admin-invite"))?.body).toEqual({ reason: "lost the email" });
  });
});

describe("the set-password page for staff", () => {
  beforeEach(() => sessionStorage.clear());

  it("greets staff as staff", async () => {
    mockApi({ "GET /v1/invites/tok-123456789012345678901": { kind: "staff", full_name: "Hafiz", email: "pm1@example.com", org_name: "Rumah Kita", expires_at: "2018-11-30T03:00:00Z" } });
    renderApp("/invite/tok-123456789012345678901");
    expect(await screen.findByText("Hi Hafiz, Rumah Kita has set up your staff account.")).toBeInTheDocument();
  });
});

describe("reports", () => {
  beforeEach(() => signedIn());

  const routes = (extra: object = {}) => ({
    "GET /v1/me": ME(["org_admin"]),
    "GET /v1/properties": page([{ id: "P1", name: "Menara", type: "condominium", status: "active", created_at: "x", updated_at: "x" }]),
    "GET /v1/reports/arrears": { ...empty, total_outstanding: money(0) }, "GET /v1/reports/occupancy": empty, "GET /v1/reports/income": empty,
    "GET /v1/reports/maintenance": { ...empty, rows: [{ property_id: "P1", open: 3, open_urgent: 1, oldest_open_days: 12, resolved: 4, avg_days_to_resolve: 2.5, cancelled: 0 }] },
    ...extra,
  });

  it("shows the maintenance summary by property", async () => {
    mockApi(routes());
    renderApp("/reports");
    expect(await screen.findByRole("heading", { name: "Maintenance" })).toBeInTheDocument();
    expect(await screen.findByText("12 days")).toBeInTheDocument();
    expect(screen.getByText("2.5 days")).toBeInTheDocument();
    expect(screen.getAllByText("Menara").length).toBeGreaterThan(1); // in the filter and the table
  });

  it("downloads a report as CSV with the user's token", async () => {
    const csv = new Response("﻿Property,Open\r\nMenara,3\r\n", {
      headers: { "Content-Type": "text/csv; charset=utf-8", "Content-Disposition": 'attachment; filename="anjung-maintenance-2018-11-25.csv"' },
    });
    const api = mockApi(routes({ "GET /v1/reports/occupancy": (req: Request) => (new URL(req.url).searchParams.get("format") === "csv" ? csv : empty) }));
    const created = vi.fn(() => "blob:x");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: created, revokeObjectURL: vi.fn() }));
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { clicked.push(this.download); });
    renderApp("/reports");
    await screen.findByRole("heading", { name: "Occupancy" });
    const buttons = screen.getAllByRole("button", { name: "Download CSV" });
    expect(buttons).toHaveLength(4);
    await userEvent.click(buttons[1]!); // occupancy
    await waitFor(() => expect(clicked).toEqual(["anjung-maintenance-2018-11-25.csv"]));
    const call = api.calls.find((c) => c.path === "/v1/reports/occupancy" && c.headers.get("Authorization"));
    expect(call?.headers.get("Authorization")).toBe("Bearer tok");
    expect(created).toHaveBeenCalledTimes(1);
  });
});
