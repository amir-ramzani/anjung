import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";

import { ME, mockApi, money, page, renderApp, signedIn } from "../test/helpers";

const unit = (over: object) => ({
  id: "U", property_id: "P1", type: "whole_unit", parent_unit_id: null, letting_mode: "whole", label: "A-1",
  default_rent: money(150000), status: "vacant", created_at: "x", updated_at: "x", ...over,
});
const units = [
  unit({ id: "A1", label: "A-1" }),
  unit({ id: "H", label: "H", letting_mode: "rooms" }),
  unit({ id: "R1", label: "Master", type: "room", parent_unit_id: "H", letting_mode: null }),
  unit({ id: "B1", label: "B2-115", type: "parking", parent_unit_id: "A1", letting_mode: null, default_rent: money(20000), note: "Covered" }),
  unit({ id: "B2", label: "P1-07", type: "parking", parent_unit_id: "H", letting_mode: null, default_rent: money(15000) }),
  unit({ id: "B3", label: "V-1", type: "parking", parent_unit_id: null, letting_mode: null, default_rent: money(15000) }),
];
const property = {
  id: "P1", name: "Menara Demo", type: "condominium", status: "active", unit_count: 2, manager_id: "u1",
  address: { line1: "1 Jalan", city: "KL", state: "WP", postcode: "50000", country: "MY" }, created_at: "x", updated_at: "x",
};

beforeEach(() => signedIn());
afterEach(() => vi.unstubAllGlobals());

describe("parking bays", () => {
  it("lists bays apart from units, with what they come with", async () => {
    mockApi({ "GET /v1/me": ME(["property_manager"]), "GET /v1/properties/P1": property, "GET /v1/properties/P1/units": page(units), "GET /v1/documents": page([]) });
    renderApp("/properties/P1");
    const section = (await screen.findByRole("heading", { name: "Parking bays" })).closest("section")!;
    expect(within(section).getByText("B2-115")).toBeInTheDocument();
    expect(within(section).getByText("Comes with A-1")).toBeInTheDocument();
    expect(within(section).getByText("Property bay (let on its own)")).toBeInTheDocument();
    expect(within(section).getByText("Covered")).toBeInTheDocument();
    // the units table does not repeat the bays
    const unitsTable = screen.getAllByRole("table")[0]!;
    expect(within(unitsTable).queryByText("B2-115")).not.toBeInTheDocument();
  });

  it("adds a bay allocated to a unit", async () => {
    const api = mockApi({
      "GET /v1/me": ME(["property_manager"]), "GET /v1/properties/P1": property, "GET /v1/properties/P1/units": page(units), "GET /v1/documents": page([]),
      "POST /v1/properties/P1/units": unit({ id: "B9", type: "parking", label: "B2-300" }),
    });
    renderApp("/properties/P1");
    await userEvent.click(await screen.findByRole("button", { name: "Add parking bay" }));
    await userEvent.type(screen.getByLabelText("Bay"), "B2-300");
    await userEvent.type(screen.getByLabelText("Default rent"), "200");
    await userEvent.selectOptions(screen.getByLabelText("Allocated to"), "A1");
    await userEvent.type(screen.getByLabelText("Note"), "Level B2");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.count("POST /v1/properties/P1/units")).toBe(1));
    expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({
      label: "B2-300", default_rent: money(20000), type: "parking", parent_unit_id: "A1", note: "Level B2",
    });
  });

  it("offers only bays that can be let on their own, and asks for the plate", async () => {
    const api = mockApi({
      "GET /v1/me": ME(["org_admin"]),
      "GET /v1/properties": { items: [property], next_cursor: null, quota: null },
      "GET /v1/properties/P1/units": page(units),
      "GET /v1/tenants": page([{ id: "T1", full_name: "Ali", email: "a@example.com", status: "active", linked: true, created_at: "x", updated_at: "x" }]),
      "POST /v1/leases": { id: "L9" },
      "GET /v1/leases/L9": { status: 404, body: { code: "not_found", message: "x", request_id: "r" } },
    });
    renderApp("/leases/new");
    await screen.findByRole("option", { name: "Menara Demo" });
    await userEvent.selectOptions(screen.getByLabelText("Property"), "P1");
    const unitSelect = await screen.findByLabelText("Unit or room");
    await waitFor(() => expect(within(unitSelect).getByRole("option", { name: "Bay P1-07" })).toBeInTheDocument());
    expect(within(unitSelect).getByRole("option", { name: "Bay V-1" })).toBeInTheDocument();
    expect(within(unitSelect).queryByRole("option", { name: "Bay B2-115" })).not.toBeInTheDocument(); // comes with A-1
    expect(screen.queryByLabelText("Vehicle plate")).not.toBeInTheDocument();
    await userEvent.selectOptions(unitSelect, "B2");
    await userEvent.type(screen.getByLabelText("Vehicle plate"), "WXY 1234");
    await userEvent.click(screen.getByLabelText("Ali"));
    await userEvent.type(screen.getByLabelText("Start date"), "2018-12-01");
    await userEvent.click(screen.getByLabelText(/Rolling/));
    await userEvent.type(screen.getByLabelText("Monthly rent"), "150");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.count("POST /v1/leases")).toBe(1));
    expect(api.calls.find((c) => c.method === "POST")?.body).toMatchObject({ unit_id: "B2", vehicle_plate: "WXY 1234" });
    await waitFor(() => expect(api.count("GET /v1/leases/L9")).toBeGreaterThan(0)); // went to the new lease
  });
});
