import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";

import * as cognito from "../auth/cognito";
import { config } from "../config";
import { readableOnWhite, rememberedHandle } from "../lib/brand";
import { ME, mockApi, money, renderApp, signedIn } from "../test/helpers";

vi.mock("../auth/cognito", async (orig) => ({
  ...(await orig<typeof import("../auth/cognito")>()),
  signIn: vi.fn(), setNewPassword: vi.fn(), sendMfaCode: vi.fn(), forgotPassword: vi.fn(), confirmNewPassword: vi.fn(),
  refresh: vi.fn(), revoke: vi.fn(),
}));

const brand = {
  handle: "rumah-kita", display_name: "Rumah Kita", company_name: "Rumah Kita Sdn Bhd", company_reg_no: "1234-X",
  accent_colour: "#7c3aed", contact_email: "help@example.com", contact_phone: "+60312345678",
  welcome_text: { en: "Welcome, tenants of Rumah Kita", ms: "Selamat datang" }, login_page: true, logo_url: null,
};
const arrears = { as_of: "x", rows: [], total_outstanding: money(0) };

afterEach(() => { vi.unstubAllGlobals(); config.authMode = "mock"; localStorage.clear(); });

describe("a company's own sign-in page", () => {
  beforeEach(() => sessionStorage.clear());

  it("shows the company's name, welcome, details and colour at /login/<handle>, and remembers it", async () => {
    mockApi({ "GET /v1/brands/rumah-kita": brand });
    renderApp("/login/rumah-kita");
    expect(await screen.findByText("Welcome, tenants of Rumah Kita")).toBeInTheDocument();
    expect(screen.getByText("Rumah Kita")).toBeInTheDocument(); // the name stands in for a logo
    expect(screen.getByText("Rumah Kita Sdn Bhd (1234-X)")).toBeInTheDocument();
    expect(screen.getByText("Powered by Anjung")).toBeInTheDocument();
    await waitFor(() => expect(rememberedHandle()).toBe("rumah-kita"));
    expect(document.documentElement.style.getPropertyValue("--primary")).toBe("#7c3aed");
    expect(document.title).toBe("Rumah Kita");
  });

  it("a plain /login is always the Anjung page, even after visiting a company's page", async () => {
    localStorage.setItem("anjung.brand", "rumah-kita");
    mockApi({});
    renderApp("/login");
    expect(await screen.findByText("Anjung")).toBeInTheDocument();
    expect(screen.queryByText("Welcome, tenants of Rumah Kita")).not.toBeInTheDocument();
  });

  it("signing out goes back to the company's own page", async () => {
    signedIn();
    localStorage.setItem("anjung.brand", "rumah-kita");
    mockApi({
      "GET /v1/me": ME(["org_admin"], { branding: brand }), "GET /v1/reports/arrears": arrears, "GET /v1/reports/occupancy": { as_of: "x", rows: [] },
      "GET /v1/brands/rumah-kita": brand,
    });
    renderApp("/");
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByText("Welcome, tenants of Rumah Kita")).toBeInTheDocument();
  });

  it("an unknown handle shows the plain sign-in page", async () => {
    mockApi({ "GET /v1/brands/nobody": { status: 404, body: { code: "not_found", message: "No such page", request_id: "r" } } });
    renderApp("/login/nobody");
    expect(await screen.findByText("Anjung")).toBeInTheDocument();
    expect(screen.queryByText("No such page")).not.toBeInTheDocument();
  });

  it("refuses colours too light for white text", () => {
    expect(readableOnWhite("#7c3aed")).toBe(true);
    expect(readableOnWhite("#ffee00")).toBe(false);
    expect(readableOnWhite("teal")).toBe(false);
  });
});

describe("signing in with a password (Cognito)", () => {
  beforeEach(() => { sessionStorage.clear(); config.authMode = "cognito"; });

  it("signs in, asking for a new password first when invited by email", async () => {
    vi.mocked(cognito.signIn).mockResolvedValue({ kind: "new_password" });
    vi.mocked(cognito.setNewPassword).mockResolvedValue({ kind: "done", tokens: { accessToken: "tok", refreshToken: "r", expiresAt: Date.now() + 3600_000 } });
    mockApi({ "GET /v1/me": ME(["org_admin"]), "GET /v1/reports/arrears": arrears, "GET /v1/reports/occupancy": { as_of: "x", rows: [] } });
    renderApp("/login");
    await userEvent.type(await screen.findByLabelText("Email"), "boss@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "Temp-Pass-123!");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(vi.mocked(cognito.signIn)).toHaveBeenCalledWith("boss@example.com", "Temp-Pass-123!");
    await userEvent.type(await screen.findByLabelText("New password"), "Sunny-Day-2018");
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  });

  it("says plainly when the password is wrong, without saying whether the email exists", async () => {
    vi.mocked(cognito.signIn).mockRejectedValue(Object.assign(new Error("x"), { code: "NotAuthorizedException" }));
    renderApp("/login");
    await userEvent.type(await screen.findByLabelText("Email"), "x@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("The email or password is not right.")).toBeInTheDocument();
  });

  it("resets a forgotten password with an emailed code", async () => {
    vi.mocked(cognito.forgotPassword).mockResolvedValue();
    vi.mocked(cognito.confirmNewPassword).mockResolvedValue();
    renderApp("/login");
    await userEvent.click(await screen.findByRole("button", { name: "Forgot your password?" }));
    await userEvent.type(screen.getByLabelText("Email"), "x@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Email me a code" }));
    await userEvent.type(await screen.findByLabelText("Code from the email"), "123456");
    await userEvent.type(screen.getByLabelText("New password"), "Sunny-Day-2018");
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));
    expect(await screen.findByText(/Your password is changed/)).toBeInTheDocument();
    expect(vi.mocked(cognito.confirmNewPassword)).toHaveBeenCalledWith("x@example.com", "123456", "Sunny-Day-2018");
  });
});

describe("inside the app", () => {
  beforeEach(() => signedIn());

  it("shows the company's name instead of Anjung, and remembers its sign-in page", async () => {
    mockApi({ "GET /v1/me": ME(["org_admin"], { branding: brand }), "GET /v1/reports/arrears": arrears, "GET /v1/reports/occupancy": { as_of: "x", rows: [] } });
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav).toHaveTextContent("Rumah Kita");
    await waitFor(() => expect(rememberedHandle()).toBe("rumah-kita"));
  });

  it("an admin saves the branding", async () => {
    const api = mockApi({
      "GET /v1/me": ME(["org_admin"]),
      "GET /v1/org": { id: "o1", name: "Acme", status: "active", settings: {}, created_at: "x", updated_at: "x", branding: null },
      "PATCH /v1/org": (_r: Request, b: unknown) => ({ id: "o1", name: "Acme", status: "active", settings: {}, created_at: "x", updated_at: "x", branding: (b as { branding: object }).branding }),
    });
    renderApp("/settings");
    await userEvent.click(await screen.findByLabelText("Use my branding"));
    await userEvent.type(screen.getByLabelText("Handle (your sign-in page address)"), "Rumah-Kita");
    expect(screen.getByText(/\/login\/rumah-kita/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Company registration no. (optional)"), "1234-X");
    await userEvent.click(screen.getByRole("button", { name: "Save branding" }));
    await waitFor(() => expect(api.count("PATCH /v1/org")).toBe(1));
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toMatchObject({
      branding: { enabled: true, handle: "rumah-kita", company_reg_no: "1234-X", display_name: null, welcome_text: { en: null, ms: null } },
    });
  });
});
