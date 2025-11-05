/** Hand-written request chains (LLD 16.3). Each flow is standalone: it creates what it needs and asserts as it goes. */
const JSON_HEADERS = [{ key: "Content-Type", value: "application/json" }];
const url = (path, query) => ({
  raw: `{{base_url}}${path}${query ? "?" + query.map((q) => `${q.key}=${q.value}`).join("&") : ""}`,
  host: ["{{base_url}}"],
  path: path.split("/").filter(Boolean),
  ...(query ? { query } : {}),
});
const script = (lines) => ({ listen: "test", script: { type: "text/javascript", exec: lines } });
const pre = (lines) => ({ listen: "prerequest", script: { type: "text/javascript", exec: lines } });
const expectStatus = (code) => `pm.test("status is ${code}", () => pm.response.to.have.status(${code}));`;

function step(name, method, path, { body, expect = 200, save = [], query, prerequest = [], headers = [], tests = [] } = {}) {
  const exec = [expectStatus(expect), "const j = (() => { try { return pm.response.json(); } catch (e) { return null; } })();"];
  for (const [variable, expr] of save) exec.push(`pm.collectionVariables.set("${variable}", ${expr});`);
  exec.push(...tests);
  return {
    name,
    event: [...(prerequest.length ? [pre(prerequest)] : []), script(exec)],
    request: {
      method,
      header: [...(body ? JSON_HEADERS : []), ...headers],
      ...(body ? { body: { mode: "raw", raw: typeof body === "string" ? body : JSON.stringify(body, null, 2) } } : {}),
      url: url(path, query),
    },
  };
}

const idem = { key: "Idempotency-Key", value: "{{$guid}}" };
const newEmail = [
  'const stamp = Date.now() + "-" + Math.floor(Math.random() * 1e6);',
  'pm.collectionVariables.set("flow_email", "flow-" + stamp + "@postman-corp.com");',
  'pm.collectionVariables.set("flow_label", "F-" + stamp);',
];
const nextMonth = [
  // the first of next month: a full month's rent, activation is allowed up to 31 days ahead
  "const d = new Date(); const s = new Date(d.getFullYear(), d.getMonth() + 1, 1, 12);",
  "const p = (n) => String(n).padStart(2, '0');",
  'pm.collectionVariables.set("flow_start", s.getFullYear() + "-" + p(s.getMonth() + 1) + "-01");',
  'pm.collectionVariables.set("flow_period", s.getFullYear() + "-" + p(s.getMonth() + 1));',
];
const property = step("Create property", "POST", "/v1/properties", {
  expect: 201, save: [["flow_property_id", "j.id"]],
  body: { name: "Flow property", type: "condominium", address: { line1: "1 Jalan Flow", city: "Kuala Lumpur", state: "WP Kuala Lumpur", postcode: "50450" } },
  prerequest: newEmail,
});
const unit = step("Create unit", "POST", "/v1/properties/{{flow_property_id}}/units", {
  expect: 201, save: [["flow_unit_id", "j.id"]], body: { label: "{{flow_label}}", default_rent: { amount_minor: 150000, currency: "MYR" } },
});

export const flows = {
  name: "Flows",
  description: "Request chains that assert as they go. Run the folder with the Collection Runner. They create their own data and use the signed-in staff user. The gateway step needs the fake provider (dev only) and the webhook secret in your local `fake_webhook_secret` current value.",
  item: [
    {
      name: "Onboard tenant",
      item: [
        step("Create tenant", "POST", "/v1/tenants", {
          expect: 201, save: [["flow_tenant_id", "j.id"]], prerequest: newEmail,
          body: { full_name: "Flow Tenant", email: "{{flow_email}}", phone: "+60123456789" },
          tests: ['pm.test("starts invited and unlinked", () => { pm.expect(j.status).to.eql("invited"); pm.expect(j.linked).to.eql(false); });'],
        }),
        step("Invite", "POST", "/v1/tenants/{{flow_tenant_id}}/invite", { expect: 202 }),
        step("Record consent", "POST", "/v1/tenants/{{flow_tenant_id}}/consents", {
          expect: 201, body: { purpose: "email_messaging", policy_version: "2018-11", granted: true },
          tests: ['pm.test("recorded on behalf of the tenant", () => pm.expect(pm.response.json().source).to.eql("staff_on_behalf"));'],
        }),
        step("The same email is refused", "POST", "/v1/tenants", { expect: 409, body: { full_name: "Again", email: "{{flow_email}}" } }),
        step("Make a set-password link to share", "POST", "/v1/tenants/{{flow_tenant_id}}/invite", {
          expect: 202, body: { channel: "link" }, save: [["flow_invite_token", 'j.link.split("/invite/")[1]']],
          tests: ['pm.test("returns the link", () => pm.expect(pm.response.json().link).to.include("/invite/"));'],
        }),
        step("The link shows who is invited (public)", "GET", "/v1/invites/{{flow_invite_token}}", {
          tests: ['pm.test("names the tenant", () => pm.expect(pm.response.json().full_name).to.eql("Flow Tenant"));'],
        }),
        step("A weak password is refused, the link stays valid", "POST", "/v1/invites/{{flow_invite_token}}/accept", { expect: 422, body: { password: "weak" } }),
        step("Preview deleting the tenant", "GET", "/v1/tenants/{{flow_tenant_id}}/deletion", {
          tests: ['pm.test("never had a lease: removed completely", () => pm.expect(pm.response.json().mode).to.eql("remove"));'],
        }),
        step("Delete the tenant (made for this flow)", "DELETE", "/v1/tenants/{{flow_tenant_id}}", {
          query: [{ key: "reason", value: "Postman flow clean-up" }], tests: ['pm.test("removed", () => pm.expect(pm.response.json().outcome).to.eql("removed"));'],
        }),
        step("The link no longer works", "GET", "/v1/invites/{{flow_invite_token}}", { expect: 404 }),
      ],
    },
    {
      name: "Lease to receipt",
      item: [
        property,
        unit,
        step("Create tenant", "POST", "/v1/tenants", { expect: 201, save: [["flow_tenant_id", "j.id"]], body: { full_name: "Flow Tenant", email: "{{flow_email}}" } }),
        step("Create lease", "POST", "/v1/leases", {
          expect: 201, save: [["flow_lease_id", "j.id"]], prerequest: nextMonth,
          body: { unit_id: "{{flow_unit_id}}", tenant_ids: ["{{flow_tenant_id}}"], start_date: "{{flow_start}}", monthly_rent: { amount_minor: 150000, currency: "MYR" }, due_day: 1, deposits: [] },
          tests: ['pm.test("a rolling lease has no end date", () => pm.expect(j.end_date).to.eql(null));'],
        }),
        step("Skip agreement signing in this flow's organisation", "PATCH", "/v1/org", {
          body: { settings: { require_signed_agreement: false } },  // signing has its own tests; this flow is about money
        }),
        step("Activate", "POST", "/v1/leases/{{flow_lease_id}}/activate", { expect: 200 }),
        step("Run the invoices for the period", "POST", "/v1/invoice-runs", {
          expect: 202, headers: [idem], body: { period: "{{flow_period}}" },
        }),
        step("Find the rent invoice", "GET", "/v1/invoices", {
          query: [{ key: "lease_id", value: "{{flow_lease_id}}" }],
          save: [["flow_invoice_id", "j.items[0].id"]],
          tests: ['pm.test("one invoice of RM 1,500.00", () => { pm.expect(j.items).to.have.length(1); pm.expect(j.items[0].total.amount_minor).to.eql(150000); });'],
        }),
        step("Create the QR", "POST", "/v1/invoices/{{flow_invoice_id}}/qr", {
          expect: 201, save: [["flow_reference", "j.reference"]],
          tests: ['pm.test("reference starts with ANJ", () => pm.expect(j.reference).to.match(/^ANJ/));'],
        }),
        {
          name: "Simulate the gateway paying it (fake provider, dev only)",
          event: [
            pre([
              "// the same signature the real gateway adapter checks: HMAC-SHA256 over '<timestamp>.<raw body>'",
              "const ts = String(Math.floor(Date.now() / 1000));",
              'const body = JSON.stringify({ event_id: "evt-" + Date.now(), gateway_txn_id: "TXN-" + Date.now(), reference: pm.collectionVariables.get("flow_reference"), amount_minor: 150000, currency: "MYR", status: "succeeded", paid_at: new Date().toISOString() });',
              'const secret = pm.variables.get("fake_webhook_secret");',
              'if (!secret) throw new Error("Set fake_webhook_secret in the local (current) value of your environment");',
              'pm.request.headers.upsert({ key: "X-Timestamp", value: ts });',
              'pm.request.headers.upsert({ key: "X-Signature", value: CryptoJS.HmacSHA256(ts + "." + body, secret).toString() });',
              "pm.request.body.update(body);",
            ]),
            script([expectStatus(200), 'pm.test("acknowledged", () => pm.expect(pm.response.json().received).to.eql(true));']),
          ],
          request: {
            method: "POST", header: JSON_HEADERS, auth: { type: "noauth" },
            body: { mode: "raw", raw: "{}" }, url: url("/v1/webhooks/payments/fake"),
          },
        },
        step("The invoice is paid", "GET", "/v1/invoices/{{flow_invoice_id}}", {
          tests: ['pm.test("paid and nothing outstanding", () => { pm.expect(j.status).to.eql("paid"); pm.expect(j.outstanding.amount_minor).to.eql(0); });'],
          save: [],
        }),
        step("The receipt can be downloaded", "GET", "/v1/invoices/{{flow_invoice_id}}/receipt", {
          tests: ['pm.test("a link is returned", () => pm.expect(pm.response.json().url).to.be.a("string"));'],
        }),
        step("The ledger balances to zero", "GET", "/v1/leases/{{flow_lease_id}}/ledger", {
          tests: ['pm.test("ends at zero", () => { const e = pm.response.json().items; pm.expect(e[e.length - 1].balance_after.amount_minor).to.eql(0); });'],
        }),
      ],
    },
    {
      name: "Maintenance",
      item: [
        property,
        unit,
        step("Raise a request", "POST", "/v1/maintenance-requests", {
          expect: 201, save: [["flow_request_id", "j.id"]],
          body: { title: "Leaking tap", description: "The kitchen tap drips.", category: "plumbing", unit_id: "{{flow_unit_id}}" },
        }),
        step("Triage", "PATCH", "/v1/maintenance-requests/{{flow_request_id}}", { body: { status: "triaged" } }),
        step("Who am I", "GET", "/v1/me", { save: [["flow_me", "j.user_id"]] }),
        step("Assign to me", "PATCH", "/v1/maintenance-requests/{{flow_request_id}}", {
          body: { assigned_to: "{{flow_me}}" },
          tests: ['pm.test("moves to assigned", () => pm.expect(pm.response.json().status).to.eql("assigned"));'],
        }),
        step("Start work", "PATCH", "/v1/maintenance-requests/{{flow_request_id}}", { body: { status: "in_progress" } }),
        step("Resolve", "PATCH", "/v1/maintenance-requests/{{flow_request_id}}", {
          body: { status: "resolved" }, tests: ['pm.test("has a resolved time", () => pm.expect(pm.response.json().resolved_at).to.be.a("string"));'],
        }),
        step("Close", "PATCH", "/v1/maintenance-requests/{{flow_request_id}}", { body: { status: "closed" } }),
        step("A closed request cannot be reopened", "PATCH", "/v1/maintenance-requests/{{flow_request_id}}", { expect: 409, body: { status: "in_progress" } }),
        step("A closed request cannot be edited", "PATCH", "/v1/maintenance-requests/{{flow_request_id}}", { expect: 409, body: { priority: "low" } }),
        step("Delete the closed request (org admin)", "DELETE", "/v1/maintenance-requests/{{flow_request_id}}", {
          query: [{ key: "reason", value: "Postman flow clean-up" }], tests: ['pm.test("removed", () => pm.expect(pm.response.json().outcome).to.eql("removed"));'],
        }),
      ],
    },
    {
      name: "Deactivate and delete",
      item: [
        property,
        unit,
        step("Deactivate the property", "POST", "/v1/properties/{{flow_property_id}}/deactivate", {
          tests: ['pm.test("is inactive", () => pm.expect(pm.response.json().status).to.eql("inactive"));'],
        }),
        step("Its unit went with it", "GET", "/v1/units/{{flow_unit_id}}", {
          tests: ['pm.test("unit inactive", () => pm.expect(pm.response.json().status).to.eql("inactive"));'],
        }),
        step("Reactivate the property", "POST", "/v1/properties/{{flow_property_id}}/reactivate", {
          tests: ['pm.test("is active", () => pm.expect(pm.response.json().status).to.eql("active"));'],
        }),
        step("The unit came back", "GET", "/v1/units/{{flow_unit_id}}", {
          tests: ['pm.test("unit vacant", () => pm.expect(pm.response.json().status).to.eql("vacant"));'],
        }),
        step("Preview deleting the property", "GET", "/v1/properties/{{flow_property_id}}/deletion", {
          tests: [
            'pm.test("never had a lease: removed completely", () => pm.expect(pm.response.json().mode).to.eql("remove"));',
            'pm.test("takes its unit", () => pm.expect(pm.response.json().affects.units).to.eql(1));',
          ],
        }),
        step("Delete needs a reason", "DELETE", "/v1/properties/{{flow_property_id}}", { expect: 422 }),
        step("Delete the property", "DELETE", "/v1/properties/{{flow_property_id}}", {
          query: [{ key: "reason", value: "Postman flow clean-up" }], tests: ['pm.test("removed", () => pm.expect(pm.response.json().outcome).to.eql("removed"));'],
        }),
        step("It is gone", "GET", "/v1/properties/{{flow_property_id}}", { expect: 404 }),
      ],
    },
    {
      name: "Parking bays",
      item: [
        property,
        unit,
        step("Add a bay that comes with the unit", "POST", "/v1/properties/{{flow_property_id}}/units", {
          expect: 201, save: [["flow_bay_with_unit", "j.id"]],
          body: { type: "parking", label: "B2-115", parent_unit_id: "{{flow_unit_id}}", default_rent: { amount_minor: 20000, currency: "MYR" }, note: "Covered" },
        }),
        step("Add a property bay", "POST", "/v1/properties/{{flow_property_id}}/units", {
          expect: 201, save: [["flow_bay", "j.id"]],
          body: { type: "parking", label: "V-1", default_rent: { amount_minor: 15000, currency: "MYR" } },
        }),
        step("Bay labels are unique per property", "POST", "/v1/properties/{{flow_property_id}}/units", {
          expect: 409, body: { type: "parking", label: "V-1", default_rent: { amount_minor: 15000, currency: "MYR" } },
        }),
        step("Create tenant", "POST", "/v1/tenants", { expect: 201, save: [["flow_tenant_id", "j.id"]], body: { full_name: "Flow Driver", email: "driver-{{flow_email}}" } }),
        step("The unit's bay cannot be let on its own", "POST", "/v1/leases", {
          expect: 422,
          body: { unit_id: "{{flow_bay_with_unit}}", tenant_ids: ["{{flow_tenant_id}}"], start_date: "2019-01-01", monthly_rent: { amount_minor: 20000, currency: "MYR" }, deposits: [], due_day: 1 },
        }),
        step("Let the property bay, with a plate", "POST", "/v1/leases", {
          expect: 201, save: [["flow_bay_lease", "j.id"]],
          body: { unit_id: "{{flow_bay}}", tenant_ids: ["{{flow_tenant_id}}"], start_date: "2019-01-01", monthly_rent: { amount_minor: 15000, currency: "MYR" }, deposits: [], due_day: 1, vehicle_plate: "wxy 1234" },
          tests: [
            'pm.test("plate stored upper case", () => pm.expect(pm.response.json().vehicle_plate).to.eql("WXY 1234"));',
            'pm.test("shown as a bay", () => pm.expect(pm.response.json().unit_type).to.eql("parking"));',
          ],
        }),
        step("Bays are listed by type", "GET", "/v1/properties/{{flow_property_id}}/units", {
          query: [{ key: "type", value: "parking" }],
          tests: ['pm.test("two bays", () => pm.expect(pm.response.json().items.length).to.eql(2));'],
        }),
        step("Delete the draft bay lease", "DELETE", "/v1/leases/{{flow_bay_lease}}", { query: [{ key: "reason", value: "Postman flow clean-up" }] }),
        step("Delete the tenant", "DELETE", "/v1/tenants/{{flow_tenant_id}}", { query: [{ key: "reason", value: "Postman flow clean-up" }] }),
        step("Delete the property and its bays", "DELETE", "/v1/properties/{{flow_property_id}}", {
          query: [{ key: "reason", value: "Postman flow clean-up" }], tests: ['pm.test("removed", () => pm.expect(pm.response.json().outcome).to.eql("removed"));'],
        }),
      ],
    },
    {
      name: "Company plan",
      description: "What the organisation sees of the plan its platform admin sets (PATCH /v1/platform/organizations/{org_id}/plan needs the platform admin, see the Platform folder).",
      item: [
        step("The organisation's plan", "GET", "/v1/org", {
          tests: [
            'pm.test("has a plan", () => pm.expect(pm.response.json().plan).to.be.an("object"));',
            'pm.test("active", () => pm.expect(pm.response.json().plan.status).to.eql("active"));',
            'pm.test("counts its units", () => pm.expect(pm.response.json().plan.units_used).to.be.a("number"));',
          ],
        }),
        step("Staff see it in /v1/me", "GET", "/v1/me", {
          tests: ['pm.test("me.plan", () => pm.expect(pm.response.json().plan.status).to.eql("active"));'],
        }),
        step("An org admin cannot change it", "PATCH", "/v1/org", { expect: 422, body: { plan: { unit_limit: 999 } } }),
        property,
        unit,
        step("Deactivate the unit", "POST", "/v1/units/{{flow_unit_id}}/deactivate"),
        step("The property shows the units the plan counts", "GET", "/v1/properties/{{flow_property_id}}", {
          tests: [
            'pm.test("one unit in all", () => pm.expect(pm.response.json().unit_count).to.eql(1));',
            'pm.test("none of them on", () => pm.expect(pm.response.json().units_active).to.eql(0));',
          ],
        }),
        step("Delete the property", "DELETE", "/v1/properties/{{flow_property_id}}", {
          query: [{ key: "reason", value: "Postman flow clean-up" }], tests: ['pm.test("removed", () => pm.expect(pm.response.json().outcome).to.eql("removed"));'],
        }),
      ],
    },
    {
      name: "Company branding",
      item: [
        step("Set the company's branding", "PATCH", "/v1/org", {
          prerequest: ['pm.collectionVariables.set("flow_handle", "flow-" + Date.now());'],
          body: { branding: { enabled: true, handle: "{{flow_handle}}", display_name: "Flow Homes", company_name: "Flow Homes Sdn Bhd", company_reg_no: "1234-X", accent_colour: "#7c3aed", welcome_text: { en: "Welcome to Flow Homes" } } },
          tests: ['pm.test("branding on", () => pm.expect(pm.response.json().branding.enabled).to.eql(true));'],
        }),
        step("Its sign-in page is public", "GET", "/v1/brands/{{flow_handle}}", {
          tests: [
            'pm.test("shows the company", () => pm.expect(pm.response.json().company_reg_no).to.eql("1234-X"));',
            'pm.test("nothing private", () => pm.expect(pm.response.json()).to.not.have.property("logo_document_id"));',
          ],
        }),
        step("Signed-in users see it", "GET", "/v1/me", {
          tests: ['pm.test("me.branding", () => pm.expect(pm.response.json().branding.display_name).to.eql("Flow Homes"));'],
        }),
        step("A reserved handle is refused", "PATCH", "/v1/org", { expect: 422, body: { branding: { handle: "admin" } } }),
        step("Switch branding off", "PATCH", "/v1/org", { body: { branding: { enabled: false } } }),
        step("The page is gone", "GET", "/v1/brands/{{flow_handle}}", { expect: 404 }),
      ],
    },
    {
      name: "Staff welcome email",
      description: "Contract v0.15.0: a new staff member gets the app's welcome email with a one-time set-password link. Reads the email from /dev/outbox (local only).",
      item: [
        step("Invite a property manager", "POST", "/v1/users/invite", {
          prerequest: newEmail, expect: 201, save: [["flow_user_id", "j.user_id"]],
          body: { email: "pm-{{flow_email}}", name: "Flow Manager", roles: ["property_manager"] },
          tests: ['pm.test("invited", () => pm.expect(j.status).to.eql("invited"));'],
        }),
        step("Resend the welcome email", "POST", "/v1/users/{{flow_user_id}}/invite", { expect: 202 }),
        step("Read the welcome email (dev outbox)", "GET", "/dev/outbox", {
          tests: [
            'const mail = j.email.filter((m) => m.template === "invite_staff" && m.to === "pm-" + pm.collectionVariables.get("flow_email")).pop();',
            'pm.test("a welcome email was sent", () => pm.expect(mail).to.be.an("object"));',
            'pm.collectionVariables.set("flow_staff_token", mail.text.split("/invite/")[1].split(/\\s/)[0]);',
          ],
        }),
        step("The link is for staff", "GET", "/v1/invites/{{flow_staff_token}}", {
          tests: ['pm.test("kind staff", () => pm.expect(j.kind).to.eql("staff"));', 'pm.test("name", () => pm.expect(j.full_name).to.eql("Flow Manager"));'],
        }),
        step("Set a password", "POST", "/v1/invites/{{flow_staff_token}}/accept", { expect: 204, body: { password: "Sunny-Day-2018" } }),
        step("The link works once", "GET", "/v1/invites/{{flow_staff_token}}", { expect: 404 }),
        step("Remove the user", "DELETE", "/v1/users/{{flow_user_id}}", { expect: 204 }),
      ],
    },
    {
      name: "Reports and CSV",
      description: "Contract v0.15.0: the maintenance summary, and format=csv on every report.",
      item: [
        property,
        unit,
        step("Report an urgent repair", "POST", "/v1/maintenance-requests", {
          expect: 201, body: { unit_id: "{{flow_unit_id}}", title: "Leak", description: "Kitchen tap", category: "plumbing", priority: "urgent" },
        }),
        step("Maintenance summary", "GET", "/v1/reports/maintenance", {
          query: [{ key: "property_id", value: "{{flow_property_id}}" }],
          tests: ['pm.test("one urgent open request", () => { pm.expect(j.rows[0].open).to.eql(1); pm.expect(j.rows[0].open_urgent).to.eql(1); });'],
        }),
        step("Occupancy as CSV", "GET", "/v1/reports/occupancy", {
          query: [{ key: "format", value: "csv" }],
          tests: [
            'pm.test("a CSV file", () => pm.expect(pm.response.headers.get("Content-Type")).to.include("text/csv"));',
            'pm.test("a download", () => pm.expect(pm.response.headers.get("Content-Disposition")).to.include("anjung-occupancy-"));',
            'pm.test("header row", () => pm.expect(pm.response.text().replace(/^\\uFEFF/, "")).to.match(/^Property,Units,Occupied/));',
          ],
        }),
        step("An unknown format is refused", "GET", "/v1/reports/arrears", { expect: 422, query: [{ key: "format", value: "xlsx" }] }),
      ],
    },
  ],
};
