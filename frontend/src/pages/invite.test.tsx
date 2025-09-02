import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";

import { config } from "../config";
import { passwordProblems } from "./Invite";
import { ME, mockApi, page, renderApp, signedIn } from "../test/helpers";

const tenant = { id: "T1", full_name: "Siti Aminah", email: "siti@example.com", status: "invited", linked: false, created_at: "x", updated_at: "x", phone: "+60123456789" };
const staff = (extra: object = {}) => ({
  "GET /v1/me": ME(["org_admin"]), "GET /v1/tenants/T1": tenant, "GET /v1/documents": page([]), "GET /v1/tenants/T1/consents": page([]), ...extra,
});
const LINK = "http://app.test/invite/abcdefghijklmnopqrstuvwxyz0123456789";

afterEach(() => { vi.unstubAllGlobals(); config.whatsappSending = false; });

describe("inviting a tenant", () => {
  beforeEach(() => signedIn());

  it("makes a link to share from the staff member's own WhatsApp", async () => {
    const api = mockApi(staff({ "POST /v1/tenants/T1/invite": { status: 202, body: { channel: "link", expires_at: "2018-11-30T03:00:00Z", link: LINK } } }));
    renderApp("/tenants/T1");
    await userEvent.click(await screen.findByRole("button", { name: "Share on WhatsApp" }));
    expect(await screen.findByDisplayValue(LINK)).toBeInTheDocument();
    expect(api.calls.find((c) => c.path === "/v1/tenants/T1/invite")?.body).toEqual({ channel: "link" });
    const wa = screen.getByRole("link", { name: "Open WhatsApp" }).getAttribute("href")!;
    expect(wa.startsWith("https://wa.me/60123456789?text=")).toBe(true); // digits only, no +
    const text = decodeURIComponent(wa.split("text=")[1]!);
    expect(text).toContain("Hi Siti Aminah");
    expect(text).toContain(LINK);
    expect(text).toContain("30-11-2018");
    expect(screen.getByText(/Anyone with this link can set the password/)).toBeInTheDocument();
  });

  it("copies the link even on a plain-HTTP address, and says so when it can't", async () => {
    mockApi(staff({ "POST /v1/tenants/T1/invite": { status: 202, body: { channel: "link", expires_at: "2018-11-30T03:00:00Z", link: LINK } } }));
    Object.defineProperty(window, "isSecureContext", { value: false, configurable: true }); // like http://192.168.0.50
    let copied = "";
    const exec = vi.fn(() => { copied = (document.activeElement as HTMLTextAreaElement).value; return true; });
    Object.defineProperty(document, "execCommand", { value: exec, configurable: true });
    try {
      renderApp("/tenants/T1");
      await userEvent.click(await screen.findByRole("button", { name: "Share on WhatsApp" }));
      await userEvent.click(await screen.findByRole("button", { name: "Copy link" }));
      expect(copied).toBe(LINK);
      expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
      exec.mockImplementation(() => false);
      await userEvent.click(screen.getByRole("button", { name: "Copied" }));
      expect(await screen.findByText(/Couldn't copy automatically/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Copy link" })).toBeInTheDocument();
    } finally {
      Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
    }
  });

  it("hides Send by WhatsApp until the WhatsApp integration is switched on", async () => {
    mockApi(staff());
    renderApp("/tenants/T1");
    expect(await screen.findByRole("button", { name: "Share on WhatsApp" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send email" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send by WhatsApp" })).not.toBeInTheDocument();
  });

  it("lets the app send it once switched on, or sends the email, and says so", async () => {
    config.whatsappSending = true;
    const api = mockApi(staff({
      "POST /v1/tenants/T1/invite": (_r: Request, b: { channel: string }) => ({ status: 202, body: { channel: b.channel, expires_at: "2018-11-30T03:00:00Z", link: null } }),
    }));
    renderApp("/tenants/T1");
    await userEvent.click(await screen.findByRole("button", { name: "Send by WhatsApp" }));
    expect(await screen.findByText(/Demo mode: WhatsApp is not connected, so nothing was delivered/)).toBeInTheDocument(); // tests run in mock mode
    await userEvent.click(screen.getByRole("button", { name: "Send email" }));
    expect(await screen.findByText("Welcome email sent to siti@example.com.")).toBeInTheDocument();
    expect(api.calls.filter((c) => c.path === "/v1/tenants/T1/invite").map((c) => c.body)).toEqual([{ channel: "whatsapp" }, { channel: "email" }]);
    expect(screen.queryByRole("link", { name: "Open WhatsApp" })).not.toBeInTheDocument(); // staff never see a sent link
  });

  it("shows why WhatsApp could not be used", async () => {
    config.whatsappSending = true;
    mockApi(staff({ "POST /v1/tenants/T1/invite": { status: 422, body: { code: "validation_error", message: "Record the tenant's consent to WhatsApp messages first", request_id: "r" } } }));
    renderApp("/tenants/T1");
    await userEvent.click(await screen.findByRole("button", { name: "Send by WhatsApp" }));
    expect(await screen.findByText(/consent to WhatsApp messages/)).toBeInTheDocument();
  });

  it("is not offered once the tenant has an account", async () => {
    mockApi(staff({ "GET /v1/tenants/T1": { ...tenant, status: "active", linked: true } }));
    renderApp("/tenants/T1");
    expect(await screen.findByRole("heading", { name: "Siti Aminah" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Share on WhatsApp" })).not.toBeInTheDocument();
  });
});

describe("the set-password page", () => {
  beforeEach(() => sessionStorage.clear()); // opened from WhatsApp, not signed in

  const info = { full_name: "Siti Aminah", email: "siti@example.com", org_name: "Rumah Kita", expires_at: "2018-11-30T03:00:00Z" };

  it("sets a password that meets the rules, then points to sign-in", async () => {
    const api = mockApi({ "GET /v1/invites/tok-123456789012345678901": info, "POST /v1/invites/tok-123456789012345678901/accept": { status: 204, body: null } });
    renderApp("/invite/tok-123456789012345678901");
    expect(await screen.findByText("Hi Siti Aminah, Rumah Kita has set up your tenant account.")).toBeInTheDocument();
    expect(screen.getByText(/You will sign in with siti@example.com/)).toBeInTheDocument();
    const set = screen.getByRole("button", { name: "Set password" });
    await userEvent.type(screen.getByLabelText("New password"), "weakpassword");
    await userEvent.type(screen.getByLabelText(/^Type the password again/), "weakpassword");
    expect(set).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("New password"));
    await userEvent.type(screen.getByLabelText("New password"), "Sunny-Day-2018");
    await userEvent.clear(screen.getByLabelText(/^Type the password again/));
    await userEvent.type(screen.getByLabelText(/^Type the password again/), "Sunny-Day-2018");
    await userEvent.click(set);
    expect(await screen.findByText(/Your password is set/)).toBeInTheDocument();
    expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({ password: "Sunny-Day-2018" });
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("says plainly when the link is no longer valid", async () => {
    mockApi({ "GET /v1/invites/tok-123456789012345678901": { status: 404, body: { code: "not_found", message: "gone", request_id: "r" } } });
    renderApp("/invite/tok-123456789012345678901");
    expect(await screen.findByText(/This link has expired or has already been used/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByLabelText("New password")).not.toBeInTheDocument());
  });

  it("checks the password rules like the user pool", () => {
    expect(passwordProblems("Sunny-Day-2018")).toEqual([]);
    expect(passwordProblems("short")).toEqual(["length", "case", "number", "symbol"]);
    expect(passwordProblems("sunny-day-2018")).toEqual(["case"]);
    expect(passwordProblems("Sunny Day 2018x")).toEqual(["symbol"]); // a space is not a symbol
  });
});
