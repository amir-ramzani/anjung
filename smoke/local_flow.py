"""End to end check of a LOCAL stack (`docker compose up`, mock auth): organisation, tenant, lease, deposit and
rent invoices, QR, payment through the real webhook, receipt, ledger and the tenant's email.

    uv run --with httpx smoke/local_flow.py --base-url http://localhost:8080

It uses the dev-only endpoints (`/dev/token`, `/dev/jobs`, `/dev/pay`, `/dev/outbox`), so it cannot run against
a real environment. The full deployed-environment smoke test needs Cognito users (plan P1-12, P3-15).
"""

import argparse
import sys
import time
import uuid
from datetime import date, timedelta

import httpx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8080")
    ap.add_argument("--platform-admin", default="root@example.com")
    ap.add_argument(
        "--insecure",
        action="store_true",
        help="do not check HTTPS certificates (the NAS demo's own certificate, docs/nas-deploy.md)",
    )
    args = ap.parse_args()
    run = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    c = httpx.Client(base_url=args.base_url, timeout=20, verify=not args.insecure)
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail and not ok else ""))

    def login(email: str) -> dict[str, str]:
        r = c.post("/dev/token", json={"email": email})
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    root = login(args.platform_admin)
    r = c.post("/v1/platform/organizations", headers=root, json={
        "name": f"Smoke {run}", "initial_admin": {"email": f"boss-{run}@example.com"}, "reason": "local flow"})
    check("create organisation", r.status_code == 201, r.text)
    org_id = r.json()["id"]
    admin = login(f"boss-{run}@example.com")
    prop = c.post("/v1/properties", headers=admin, json={"name": "Menara", "type": "condominium", "address": {
        "line1": "1 Jalan A", "city": "KL", "state": "WP", "postcode": "50000"}}).json()
    unit = c.post(f"/v1/properties/{prop['id']}/units", headers=admin, json={
        "label": "A-1", "default_rent": {"amount_minor": 150000, "currency": "MYR"}}).json()
    tenant_email = f"tenant-{run}@example.com"
    tenant = c.post("/v1/tenants", headers=admin, json={"full_name": "Siti Aminah", "email": tenant_email}).json()
    check("invite tenant", c.post(f"/v1/tenants/{tenant['id']}/invite", headers=admin).status_code == 202)
    tok = login(tenant_email)
    check("tenant first login links the account", c.get("/v1/me", headers=tok).json()["tenant_id"] == tenant["id"])
    # consent and email preference, so the tenant gets messages
    c.put("/v1/notifications/preferences", headers=tok, json={"email": {"enabled": True}})
    c.post(f"/v1/tenants/{tenant['id']}/consents", headers=tok,
           json={"purpose": "email_messaging", "policy_version": "local", "granted": True})
    start = date.today() + timedelta(days=3)
    money = {"currency": "MYR"}
    lease = c.post("/v1/leases", headers=admin, json={
        "unit_id": unit["id"], "tenant_ids": [tenant["id"]], "start_date": str(start),
        "monthly_rent": {"amount_minor": 150000, **money}, "due_day": start.day if start.day <= 28 else 28,
        "deposits": [{"type": "security", "amount": {"amount_minor": 300000, **money}}]}).json()
    # tenancy agreement: activation waits until the tenant has signed (contract v0.7.0)
    early = c.post(f"/v1/leases/{lease['id']}/activate", headers=admin)
    check("activation waits for the signed agreement", early.status_code == 409, early.text)
    pdf = b"%PDF-1.4\n% smoke tenancy agreement\n"
    slot = c.post("/v1/documents/upload-url", headers=admin, json={
        "owner_type": "lease", "owner_id": lease["id"], "type": "lease_agreement", "filename": "agreement.pdf",
        "content_type": "application/pdf", "size_bytes": len(pdf)}).json()
    up = httpx.post(slot["upload"]["url"], data=slot["upload"]["fields"], files={"file": ("agreement.pdf", pdf, "application/pdf")}, verify=not args.insecure)
    check("upload the agreement", up.status_code in (200, 201, 204), up.text)
    c.post(f"/dev/documents/{slot['document_id']}/scan", json={"org_id": org_id, "verdict": "clean"})
    sent = c.post(f"/v1/leases/{lease['id']}/agreement/send", headers=admin, json={})
    check("send the agreement", sent.status_code == 200 and sent.json()["agreement"]["status"] == "sent", sent.text)
    check("the tenant sees the draft to sign", c.get(f"/v1/leases/{lease['id']}", headers=tok).status_code == 200)
    signed = c.post(f"/v1/leases/{lease['id']}/agreement/sign", headers=tok, json={"full_name": "siti  aminah", "agree": True})
    check("the tenant signs", signed.status_code == 200 and signed.json()["agreement"]["status"] == "signed", signed.text)
    act = c.post(f"/v1/leases/{lease['id']}/activate", headers=admin)
    check("activate the lease", act.status_code == 200 and act.json()["status"] == "active", act.text)
    inv = c.get("/v1/invoices", headers=admin).json()["items"]
    check("deposit invoice created on activation", [i["line_items"][0]["type"] for i in inv] == ["deposit"])
    run_r = c.post("/v1/invoice-runs", headers={**admin, "Idempotency-Key": f"run-{run}-0001"},
                   json={"period": f"{start.year:04d}-{start.month:02d}"})
    check("invoice run accepted", run_r.status_code == 202, run_r.text)
    invoices = c.get("/v1/invoices", headers=tok).json()["items"]
    rent = next(i for i in invoices if i["line_items"][0]["type"] == "rent")
    qr = c.post(f"/v1/invoices/{rent['id']}/qr", headers=tok)
    check("QR for the rent invoice", qr.status_code == 201 and qr.json()["reference"].startswith("ANJ"), qr.text)
    total = rent["total"]["amount_minor"]
    pay = c.post("/dev/pay", json={"reference": qr.json()["reference"], "amount_minor": total})
    check("gateway webhook accepted", pay.json().get("webhook_status") == 200, pay.text)
    paid = c.get(f"/v1/invoices/{rent['id']}", headers=admin).json()
    check("invoice is paid", paid["status"] == "paid" and paid["outstanding"]["amount_minor"] == 0, str(paid["status"]))
    receipt = c.get(f"/v1/invoices/{rent['id']}/receipt", headers=tok)
    check("receipt downloadable", receipt.status_code == 200, receipt.text)
    ledger = c.get(f"/v1/leases/{lease['id']}/ledger?limit=100", headers=admin).json()["items"]
    check("ledger has the payment", "payment" in [e["type"] for e in ledger])
    box = c.get("/dev/outbox").json()["email"]
    mine = [m for m in box if m["to"] == tenant_email]
    check("tenant was emailed with a pay link", any("/pay/" in m["text"] for m in mine), str([m["template"] for m in mine]))
    check("audit log has entries", len(c.get("/v1/audit-log?limit=100", headers=admin).json()["items"]) > 5)
    rpt = c.get("/v1/reports/income", headers=admin).json()["rows"]
    check("income report shows the collection", bool(rpt) and rpt[0]["collected"]["amount_minor"] == total)
    check("org id is consistent", c.get("/v1/me", headers=admin).json()["org_id"] == org_id)
    # set-password link (contract v0.8.0): staff share it, the tenant sets a password, the link is used up
    second = c.post("/v1/tenants", headers=admin, json={"full_name": "Ali Hassan", "email": f"ali-{run}@example.com"}).json()
    link = c.post(f"/v1/tenants/{second['id']}/invite", headers=admin, json={"channel": "link"}).json()["link"]
    token = link.rsplit("/invite/", 1)[1]
    check("set-password link shows who is invited", c.get(f"/v1/invites/{token}").json().get("full_name") == "Ali Hassan")
    done = c.post(f"/v1/invites/{token}/accept", json={"password": "Sunny-Day-2018"})
    check("tenant sets a password through the link", done.status_code == 204, done.text)
    check("the link works only once", c.get(f"/v1/invites/{token}").status_code == 404)
    # staff welcome email (contract v0.15.0): the same set-password link, sent by the app
    pm_email = f"pm-{run}@example.com"
    pm = c.post("/v1/users/invite", headers=admin, json={"email": pm_email, "name": "Hafiz", "roles": ["property_manager"]})
    check("invite a property manager", pm.status_code == 201, pm.text)
    welcome = [m for m in c.get("/dev/outbox").json()["email"] if m["to"] == pm_email and m["template"] == "invite_staff"]
    check("the new manager gets a welcome email with a link", len(welcome) == 1 and "/invite/" in welcome[0]["text"])
    staff_token = welcome[0]["text"].split("/invite/", 1)[1].split()[0] if welcome else "missing-token-missing-token"
    check("the link is for staff", c.get(f"/v1/invites/{staff_token}").json().get("kind") == "staff")
    check("the manager sets a password through it", c.post(f"/v1/invites/{staff_token}/accept", json={"password": "Sunny-Day-2018"}).status_code == 204)
    # maintenance summary and CSV downloads (contract v0.15.0)
    c.post("/v1/maintenance-requests", headers=admin, json={"unit_id": unit["id"], "title": "Leak", "description": "Kitchen tap", "category": "plumbing", "priority": "urgent"})
    m_rows = c.get("/v1/reports/maintenance", headers=admin).json()["rows"]
    check("maintenance report counts the open request", sum(r["open_urgent"] for r in m_rows) >= 1, str(m_rows))
    csv_r = c.get("/v1/reports/income", headers=admin, params={"format": "csv"})
    check("a report downloads as CSV", csv_r.status_code == 200 and csv_r.headers["content-type"].startswith("text/csv")
          and csv_r.text.lstrip("\ufeff").startswith("Period,Property,Invoiced (MYR)"), csv_r.text[:200])
    # parking bays (contract v0.10.0)
    units_url = f"/v1/properties/{prop['id']}/units"
    bay_rent = {"amount_minor": 15000, **money}
    with_unit = c.post(units_url, headers=admin, json={"type": "parking", "label": "B2-115", "default_rent": bay_rent, "parent_unit_id": unit["id"]}).json()
    check("a bay allocated to a unit let whole comes with its lease",
          c.get(f"/v1/leases/{lease['id']}", headers=admin).json().get("included_bays") == [{"id": with_unit["id"], "label": "B2-115"}])
    bay_lease = {"tenant_ids": [second["id"]], "start_date": str(start), "monthly_rent": bay_rent, "due_day": 5, "deposits": []}
    refused = c.post("/v1/leases", headers=admin, json={**bay_lease, "unit_id": with_unit["id"]})
    check("that bay cannot be let on its own", refused.status_code == 422, refused.text)
    free = c.post(units_url, headers=admin, json={"type": "parking", "label": "V-1", "default_rent": bay_rent}).json()
    own = c.post("/v1/leases", headers=admin, json={**bay_lease, "unit_id": free["id"], "vehicle_plate": "wxy 1234"})
    check("a property bay is let on its own lease, with a plate", own.status_code == 201 and own.json()["vehicle_plate"] == "WXY 1234", own.text)
    # deactivate and delete (contract v0.9.0)
    why = {"reason": "local flow clean-up"}
    gone = c.delete(f"/v1/leases/{own.json()['id']}", headers=admin, params=why)
    check("a draft lease can be deleted", gone.status_code == 200 and gone.json()["outcome"] == "removed", gone.text)
    off = c.post(f"/v1/units/{free['id']}/deactivate", headers=admin).json()
    on = c.post(f"/v1/units/{free['id']}/reactivate", headers=admin).json()
    check("a bay can be deactivated and reactivated", off["status"] == "inactive" and on["status"] == "vacant")
    blocked = c.get(f"/v1/properties/{prop['id']}/deletion", headers=admin).json()
    check("a property with lease history cannot be deleted", blocked["mode"] == "blocked" and blocked["blockers"][0]["code"] == "lease_history")
    check("an unused bay can be deleted", c.delete(f"/v1/units/{free['id']}", headers=admin, params=why).json().get("outcome") == "removed")
    # organisation branding (contract v0.11.0): the company's own sign-in page at /login/<handle>
    handle = f"smoke-{run}"
    branded = c.patch("/v1/org", headers=admin, json={"branding": {
        "enabled": True, "handle": handle, "company_name": "Smoke Sdn Bhd", "company_reg_no": "1234-X"}})
    check("set the company's branding", branded.status_code == 200 and branded.json()["branding"]["handle"] == handle, branded.text)
    public = c.get(f"/v1/brands/{handle}")
    check("the branded sign-in page is public", public.status_code == 200 and public.json()["company_reg_no"] == "1234-X", public.text)
    check("signed-in users see their company's brand", (c.get("/v1/me", headers=tok).json().get("branding") or {}).get("handle") == handle)
    # the company's plan, set by the platform admin (contract v0.12.0)
    plan_url = f"/v1/platform/organizations/{org_id}/plan"
    c.patch(plan_url, headers=root, json={"unit_limit": 1, "reason": "local flow"})  # A-1 is the one unit
    full = c.post(f"/v1/properties/{prop['id']}/units", headers=admin, json={"label": "A-2", "default_rent": {"amount_minor": 150000, **money}})
    check("the plan's unit limit stops a new unit", full.status_code == 409 and full.json()["code"] == "quota_exceeded"
          and full.json().get("params", {}).get("scope") == "plan", full.text)
    shown = c.get(f"/v1/properties/{prop['id']}", headers=admin).json().get("units_active")
    used = c.get("/v1/org", headers=admin).json()["plan"]["units_used"]
    check("the property shows the units the plan counts", shown == used == 1, f"{shown} / {used}")
    empty = c.post("/v1/properties", headers=admin, json={"name": "Empty", "type": "landed", "address": {
        "line1": "1 Jalan B", "city": "KL", "state": "WP", "postcode": "50000"}})
    check("an empty property is free", empty.status_code == 201, empty.text)
    c.patch(plan_url, headers=root, json={"unit_limit": None, "active_until": str(date.today() - timedelta(days=1)), "reason": "local flow"})
    ro = c.post("/v1/properties", headers=admin, json={"name": "Read-only", "type": "landed", "address": {
        "line1": "1 Jalan C", "city": "KL", "state": "WP", "postcode": "50000"}})
    check("after the plan ends, staff are read-only", ro.status_code == 403 and ro.json()["code"] == "plan_read_only", ro.text)
    check("and tenants carry on", c.get("/v1/invoices", headers=tok).status_code == 200)
    c.patch(plan_url, headers=root, json={"active_until": None, "reason": "local flow: renewed"})
    check("renewing the plan lifts it", c.get("/v1/org", headers=admin).json()["plan"]["status"] == "active")
    print(f"{'ok' if not failures else 'FAILED'} in {time.perf_counter() - started:.1f}s")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
