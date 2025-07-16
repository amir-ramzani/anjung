import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { ME, mockApi, page, renderApp, signedIn } from "../test/helpers";

class FakeXhr {
  static instances: FakeXhr[] = [];
  static status = 204;
  static body = "";
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  status = FakeXhr.status;
  responseText = FakeXhr.body;
  sent: FormData | null = null;
  url = "";
  constructor() { FakeXhr.instances.push(this); }
  open(_m: string, url: string) { this.url = url; }
  send(body: FormData) {
    this.sent = body;
    queueMicrotask(() => { this.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 1 }); this.onload?.(); });
  }
}

const doc = (over: object = {}) => ({
  id: "D1", owner_type: "tenant", owner_id: "T1", type: "identity", filename: "ic.pdf", content_type: "application/pdf",
  size_bytes: 2048, status: "clean", retention_class: "lease_financial", created_at: "2018-11-20T00:00:00Z", created_by: "u", ...over,
});
const tenant = { id: "T1", full_name: "Siti Aminah", email: "siti@example.com", status: "active", linked: true, created_at: "x", updated_at: "x", phone: null };
const file = (name = "ic.pdf", type = "application/pdf", size = 5) => {
  const f = new File(["x".repeat(Math.min(size, 10))], name, { type });
  Object.defineProperty(f, "size", { value: size });
  return f;
};

beforeEach(() => { signedIn(); FakeXhr.instances = []; FakeXhr.status = 204; FakeXhr.body = ""; vi.stubGlobal("XMLHttpRequest", FakeXhr); });
afterEach(() => vi.unstubAllGlobals());

const staff = () => ({ "GET /v1/me": ME(["org_admin"]), "GET /v1/tenants/T1": tenant });

describe("document list", () => {
  it("lists a tenant's documents with their scan status", async () => {
    mockApi({ ...staff(), "GET /v1/documents": page([doc(), doc({ id: "D2", filename: "virus.pdf", status: "infected" }), doc({ id: "D3", filename: "new.pdf", status: "scanning" })]) });
    renderApp("/tenants/T1");
    const row = (await screen.findByText("ic.pdf")).closest("tr")!;
    expect(within(row).getByText("2 KB")).toBeInTheDocument();
    expect(within(row).getByText("Clean")).toBeInTheDocument();
    expect(within(screen.getByText("virus.pdf").closest("tr")!).getByRole("button", { name: "Download" })).toBeDisabled(); // blocked files cannot be fetched at all
    expect(within(screen.getByText("new.pdf").closest("tr")!).getByText("Scanning")).toBeInTheDocument();
  });

  it("asks for the owner it is showing, nothing broader", async () => {
    const api = mockApi({ ...staff(), "GET /v1/documents": page([]) });
    renderApp("/tenants/T1");
    expect(await screen.findByText("No documents yet.")).toBeInTheDocument();
    const call = api.calls.find((c) => c.path === "/v1/documents")!;
    expect(call).toBeDefined();
    expect(api.fn.mock.calls.map((c) => String((c[0] as Request).url)).find((u) => u.includes("/v1/documents?"))).toMatch(/owner_type=tenant&owner_id=T1/);
  });

  it("opens a clean file through a short-lived link", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    mockApi({ ...staff(), "GET /v1/documents": page([doc()]), "GET /v1/documents/D1/download-url": { url: "https://s3.example/file?sig=1", expires_at: "x" } });
    renderApp("/tenants/T1");
    await userEvent.click(await screen.findByRole("button", { name: "Download" }));
    await waitFor(() => expect(open).toHaveBeenCalledWith("https://s3.example/file?sig=1", "_blank", "noopener"));
  });

  it("explains a file that is still being scanned instead of failing generically", async () => {
    mockApi({ ...staff(), "GET /v1/documents": page([doc({ status: "scanning" })]),
      "GET /v1/documents/D1/download-url": { status: 409, body: { code: "document_not_clean", message: "no", request_id: "r" } } });
    renderApp("/tenants/T1");
    await userEvent.click(await screen.findByRole("button", { name: "Download" }));
    expect(await screen.findByText("This file is still being scanned. Try again in a moment.")).toBeInTheDocument();
  });

  it("says why a financial record cannot be deleted", async () => {
    window.confirm = vi.fn(() => true);
    mockApi({ ...staff(), "GET /v1/documents": page([doc()]),
      "DELETE /v1/documents/D1": { status: 409, body: { code: "invalid_state", message: "retention hold", request_id: "r" } } });
    renderApp("/tenants/T1");
    await userEvent.click(await screen.findByRole("button", { name: "Remove" }));
    expect(await screen.findByText("This document must be kept and cannot be deleted.")).toBeInTheDocument();
  });

  it("a tenant cannot remove documents", async () => {
    mockApi({ "GET /v1/me": ME(["tenant"], { tenant_id: "T1" }), "GET /v1/documents": page([doc()]) });
    renderApp("/documents");
    expect(await screen.findByText("ic.pdf")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "My documents" })).toBeInTheDocument();
  });
});

describe("uploading", () => {
  it("refuses a wrong type, an oversize file and no file without calling the API", async () => {
    const api = mockApi({ ...staff(), "GET /v1/documents": page([]) });
    renderApp("/tenants/T1");
    const input = await screen.findByLabelText("File");
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText("Choose a file first.")).toBeInTheDocument();
    await userEvent.upload(input, file("evil.html", "text/html"), { applyAccept: false });
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText("Only PDF, JPEG, PNG and WebP files are accepted.")).toBeInTheDocument();
    await userEvent.upload(input, file("big.pdf", "application/pdf", 16 * 1024 * 1024));
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText("The file is larger than 15 MB.")).toBeInTheDocument();
    expect(api.count("POST /v1/documents/upload-url")).toBe(0);
    expect(FakeXhr.instances).toHaveLength(0);
  });

  it("asks for a slot, uploads the signed form, waits for the scan and refreshes the list", async () => {
    let listed = false;
    const api = mockApi({
      ...staff(),
      "GET /v1/documents": () => page(listed ? [doc({ filename: "passport.pdf" })] : []),
      "POST /v1/documents/upload-url": () => ({ status: 201, body: { document_id: "D9", upload: { url: "http://s3.local/bucket", method: "POST", fields: { key: "org/o/tenant/T1/D9/passport.pdf", policy: "p" }, expires_at: "x" } } }),
      "POST /dev/documents/D9/scan": () => { listed = true; return { status: "clean" }; },
      "GET /v1/documents/D9": () => doc({ id: "D9", status: "clean" }),
    });
    renderApp("/tenants/T1");
    await userEvent.upload(await screen.findByLabelText("File"), file("passport.pdf", "application/pdf", 5));
    await userEvent.selectOptions(screen.getByLabelText("Type"), "lease_agreement");
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText("Uploaded and checked.")).toBeInTheDocument();
    expect(api.calls.find((c) => c.path === "/v1/documents/upload-url")?.body).toEqual({
      owner_type: "tenant", owner_id: "T1", type: "lease_agreement", filename: "passport.pdf", content_type: "application/pdf", size_bytes: 5 });
    const sent = FakeXhr.instances[0]!;
    expect(sent.url).toBe("http://s3.local/bucket");
    expect([...sent.sent!.keys()]).toEqual(["key", "policy", "file"]);
    expect(api.calls.find((c) => c.path === "/dev/documents/D9/scan")?.body).toEqual({ org_id: "o1", verdict: "clean" });
    expect(await screen.findByText("passport.pdf")).toBeInTheDocument();
  });

  it("tells the user a file was blocked when the scan finds a virus", async () => {
    mockApi({
      ...staff(), "GET /v1/documents": page([]),
      "POST /v1/documents/upload-url": { status: 201, body: { document_id: "D9", upload: { url: "http://s3.local/b", method: "POST", fields: {}, expires_at: "x" } } },
      "POST /dev/documents/D9/scan": { status: "infected" },
      "GET /v1/documents/D9": doc({ id: "D9", status: "infected" }),
    });
    renderApp("/tenants/T1");
    await userEvent.upload(await screen.findByLabelText("File"), file());
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText("This file was blocked: it may contain a virus.")).toBeInTheDocument();
  });

  it("shows S3's own refusals in plain words", async () => {
    FakeXhr.status = 403;
    mockApi({ ...staff(), "GET /v1/documents": page([]),
      "POST /v1/documents/upload-url": { status: 201, body: { document_id: "D9", upload: { url: "http://s3.local/b", method: "POST", fields: {}, expires_at: "x" } } } });
    renderApp("/tenants/T1");
    await userEvent.upload(await screen.findByLabelText("File"), file());
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText("The upload link expired. Please try again.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled(); // can try again
  });

  it("shows the API's refusal (for example a tenant uploading to someone else)", async () => {
    mockApi({ ...staff(), "GET /v1/documents": page([]),
      "POST /v1/documents/upload-url": { status: 403, body: { code: "forbidden", message: "Not permitted", request_id: "r" } } });
    renderApp("/tenants/T1");
    await userEvent.upload(await screen.findByLabelText("File"), file());
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText("Not permitted")).toBeInTheDocument();
    expect(FakeXhr.instances).toHaveLength(0);
  });

  it("a tenant is offered only the types the API would accept", async () => {
    mockApi({ "GET /v1/me": ME(["tenant"], { tenant_id: "T1" }), "GET /v1/documents": page([]) });
    renderApp("/documents");
    const select = await screen.findByLabelText("Type");
    expect([...select.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["Identity document", "Other"]);
  });

  it("takes a file dragged onto the form", async () => {
    const api = mockApi({ ...staff(), "GET /v1/documents": page([]),
      "POST /v1/documents/upload-url": { status: 201, body: { document_id: "D9", upload: { url: "http://s3.local/b", method: "POST", fields: {}, expires_at: "x" } } },
      "POST /dev/documents/D9/scan": { status: "clean" }, "GET /v1/documents/D9": doc({ id: "D9" }) });
    renderApp("/tenants/T1");
    const form = (await screen.findByLabelText("File")).closest("form")!;
    expect(screen.getByText(/Or drag a file here\./)).toBeInTheDocument();
    fireEvent.dragOver(form, { dataTransfer: { files: [] } });
    expect(form).toHaveClass("dragging");
    expect(screen.getByText(/Drop the file to add it\./)).toBeInTheDocument();
    fireEvent.drop(form, { dataTransfer: { files: [file("dropped.pdf"), file("second.pdf")] } });
    expect(form).not.toHaveClass("dragging");
    expect(screen.getByText(/Chosen: dropped\.pdf\./)).toBeInTheDocument();
    expect(screen.getByText("One file at a time: only the first was added.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText("Uploaded and checked.")).toBeInTheDocument();
    expect((api.calls.find((c) => c.path === "/v1/documents/upload-url")?.body as { filename: string }).filename).toBe("dropped.pdf");
  });

  it("a unit has its own documents, opened from its row", async () => {
    const api = mockApi({
      "GET /v1/me": ME(["org_admin"]),
      "GET /v1/properties/P1": { id: "P1", name: "Menara", type: "condominium", status: "active", address: { line1: "1 Jalan A", city: "KL", state: "WP", postcode: "50000" }, created_at: "x", updated_at: "x" },
      "GET /v1/properties/P1/units": page([{ id: "U1", property_id: "P1", label: "A-1", type: "whole_unit", letting_mode: "whole", status: "vacant", default_rent: { amount_minor: 1, currency: "MYR" }, created_at: "x", updated_at: "x" }]),
      "GET /v1/documents": (req: Request) => page(new URL(req.url).searchParams.get("owner_type") === "unit" ? [doc({ owner_type: "unit", owner_id: "U1", filename: "inventory.pdf", type: "inspection_report" })] : []),
      "GET /v1/users": page([]),
    });
    renderApp("/properties/P1");
    const open = await screen.findByRole("button", { name: "Documents", expanded: false });
    await userEvent.click(open);
    expect(await screen.findByRole("heading", { name: "Documents for A-1" })).toBeInTheDocument();
    expect(await screen.findByText("inventory.pdf")).toBeInTheDocument();
    expect(api.calls.some((c) => c.path === "/v1/documents" && c.method === "GET")).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Documents", expanded: true }));
    expect(screen.queryByRole("heading", { name: "Documents for A-1" })).not.toBeInTheDocument();
  });

  it("a tenant can add proof of payment to their invoice", async () => {
    mockApi({ "GET /v1/me": ME(["tenant"], { tenant_id: "T1" }), "GET /v1/documents": page([]),
      "GET /v1/invoices/I1": { id: "I1", number: "INV-1", lease_id: "L1", period: "2018-12", status: "open", issue_date: "2018-11-20", due_date: "2018-12-05",
        line_items: [], total: { amount_minor: 1, currency: "MYR" }, paid: { amount_minor: 0, currency: "MYR" }, outstanding: { amount_minor: 1, currency: "MYR" }, created_at: "x", updated_at: "x", version: 1 } });
    renderApp("/invoices/I1");
    const select = await screen.findByLabelText("Type");
    expect([...select.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["Other"]);
  });
});
