"""Demo data for local development (`python -m app.cli seed-demo`).

Goes through the real API in-process, with the mock login, so the data is exactly what a user could have created
(same validation, audit entries and jobs). Safe to run again: it stops if the demo organisation exists.
Local only: it needs AUTH_MODE=mock.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.testclient import TestClient

from app.container import Container
from app.core.config import Settings
from app.main import create_app

ORG_NAME = "Demo Landlord"
PLATFORM_ADMIN = "root@example.com"


def _myr(sen: int) -> dict[str, Any]:
    return {"amount_minor": sen, "currency": "MYR"}


def seed(
    settings: Settings, platform_admin: str = PLATFORM_ADMIN, domain: str = "example.com"
) -> list[str]:
    org_name = ORG_NAME if domain == "example.com" else f"{ORG_NAME} ({domain})"
    handle = "demo-" + "".join(ch if ch.isalnum() else "-" for ch in domain.lower())[:24].strip("-")
    ADMIN, MANAGER = f"boss@{domain}", f"manager@{domain}"  # noqa: N806
    TENANTS = [("Siti Aminah", f"siti@{domain}"), ("Ali Hassan", f"ali@{domain}")]  # noqa: N806
    if settings.auth_mode != "mock":
        raise SystemExit("seed-demo is for local development only (AUTH_MODE=mock)")
    notes: list[str] = []
    with TestClient(create_app(Container.build(settings))) as c:

        def login(email: str) -> dict[str, str]:
            r = c.post("/dev/token", json={"email": email})
            r.raise_for_status()
            return {"Authorization": f"Bearer {r.json()['access_token']}"}

        def ok(r: Any) -> Any:
            if r.status_code >= 300:
                raise SystemExit(
                    f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text}"
                )
            return r.json() if r.content else None

        not_admin = (
            f"{platform_admin} is not a platform admin here, so the demo cannot be created.\n"
            "Run it again with the platform admin this database was started with:\n"
            "  python -m app.cli seed-demo --platform-admin <email>\n"
            "(it is PLATFORM_ADMIN_EMAIL in the compose file, or root@example.com by default)"
        )
        r = c.post("/dev/token", json={"email": platform_admin})
        if r.status_code in (401, 403, 404):
            raise SystemExit(not_admin)
        if r.status_code == 422:
            raise SystemExit(f"{platform_admin} is not a valid email address.")
        if r.status_code >= 300:
            raise SystemExit(f"Could not sign in as {platform_admin}: {r.status_code} {r.text}")
        root = {"Authorization": f"Bearer {r.json()['access_token']}"}
        listed = c.get("/v1/platform/organizations", headers=root, params={"limit": 100})
        if listed.status_code in (401, 403):  # signs in, but as a member of a company
            raise SystemExit(not_admin)
        existing = ok(listed)["items"]
        if any(o["name"] == org_name for o in existing):
            return [f"'{org_name}' already exists, nothing to do"]
        created = c.post(
            "/v1/platform/organizations",
            headers=root,
            json={
                "name": org_name,
                "initial_admin": {"email": ADMIN, "name": "Demo Boss"},
                "reason": "local demo data",
                # its own branded sign-in page (contract v0.11.0): /login/<handle>
                "branding": {
                    "enabled": True,
                    "handle": handle,
                    "display_name": "Demo Landlord",
                    "company_name": "Demo Landlord Sdn Bhd",
                    "company_reg_no": "202301012345 (1234567-X)",
                    "accent_colour": "#7c3aed",
                    "contact_email": f"help@{domain}",
                    "welcome_text": {
                        "en": "Welcome to the Demo Landlord tenant portal.",
                        "ms": "Selamat datang ke portal penyewa Demo Landlord.",
                    },
                },
            },
        )
        if created.status_code == 409:
            return [
                f"{ADMIN} already exists. Use --email-domain <other domain> to seed under new addresses"
            ]
        org_id = ok(created)["id"]
        admin = login(ADMIN)
        manager_id = ok(
            c.post(
                "/v1/users/invite",
                headers=admin,
                json={
                    "email": MANAGER,
                    "name": "Demo Manager",
                    "roles": ["property_manager"],
                    "unit_quota": 6,  # shows "3 / 6" units on their Properties page
                },
            )
        )["user_id"]

        addr = {
            "line1": "Jalan PJU 1/3",
            "city": "Petaling Jaya",
            "state": "Selangor",
            "postcode": "47800",
        }
        pelangi = ok(
            c.post(
                "/v1/properties",
                headers=admin,
                json={"name": "Pelangi Damansara", "type": "condominium", "address": addr},
            )
        )
        menara = ok(
            c.post(
                "/v1/properties",
                headers=admin,
                json={
                    "name": "Menara Demo",
                    "type": "apartment",
                    "address": {
                        **addr,
                        "postcode": "50450",
                        "city": "Kuala Lumpur",
                        "state": "WP Kuala Lumpur",
                    },
                },
            )
        )

        # manager scope: the manager runs Pelangi, the admin keeps Menara (and sees both)
        ok(
            c.patch(
                f"/v1/properties/{pelangi['id']}", headers=admin, json={"manager_id": manager_id}
            )
        )

        def unit(prop: dict[str, Any], label: str, rent: int, **extra: Any) -> dict[str, Any]:
            made: dict[str, Any] = ok(
                c.post(
                    f"/v1/properties/{prop['id']}/units",
                    headers=admin,
                    json={"label": label, "default_rent": _myr(rent), **extra},
                )
            )
            return made

        host = unit(pelangi, "D-5-8", 0, letting_mode="rooms")
        rooms = [
            unit(pelangi, n, r, type="room", parent_unit_id=host["id"])
            for n, r in (("Master", 90000), ("Middle room", 60000), ("Small room", 50000))
        ]
        d11 = unit(pelangi, "D-11-12", 220000, letting_mode="whole")
        a1 = unit(menara, "A-1", 150000, letting_mode="whole")
        unit(menara, "A-2", 160000, letting_mode="whole")
        # parking bays (contract v0.10.0): with a unit let whole they come with it; with a unit let by
        # the room, or as property bays, they are let on their own leases
        bay = unit(
            pelangi, "P1-07", 15000, type="parking", parent_unit_id=host["id"], note="Level P1"
        )
        unit(pelangi, "P1-08", 15000, type="parking", parent_unit_id=d11["id"], note="Level P1")
        unit(menara, "B2-115", 20000, type="parking", parent_unit_id=a1["id"], note="Covered")
        unit(menara, "B2-200", 20000, type="parking", note="Visitor bay, rentable")

        tenants = []
        for name, email in TENANTS:
            t = ok(c.post("/v1/tenants", headers=admin, json={"full_name": name, "email": email}))
            ok(c.post(f"/v1/tenants/{t['id']}/invite", headers=admin))
            tok = login(email)  # the first login links the account to the tenant
            ok(
                c.put(
                    "/v1/notifications/preferences", headers=tok, json={"email": {"enabled": True}}
                )
            )
            ok(
                c.post(
                    f"/v1/tenants/{t['id']}/consents",
                    headers=tok,
                    json={"purpose": "email_messaging", "policy_version": "demo", "granted": True},
                )
            )
            tenants.append((t, tok))

        start = date.today().replace(day=1)
        leases = []
        container = c.app.state.container  # type: ignore[attr-defined]

        def agreement_pdf(lease_id: str, tenant_name: str) -> None:
            """Attach a small demo agreement PDF to the lease, already scanned clean."""
            from fpdf import FPDF

            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Helvetica", size=12)
            pdf.multi_cell(0, 8, f"Tenancy agreement (demo)\n\n{org_name}\nTenant: {tenant_name}")
            body = bytes(pdf.output())
            slot = ok(
                c.post(
                    "/v1/documents/upload-url",
                    headers=admin,
                    json={
                        "owner_type": "lease",
                        "owner_id": lease_id,
                        "type": "lease_agreement",
                        "filename": "tenancy-agreement.pdf",
                        "content_type": "application/pdf",
                        "size_bytes": len(body),
                    },
                )
            )
            doc = container.store.documents(org_id).get(slot["document_id"])
            container.storage.put(doc["s3_key"], body, "application/pdf")
            ok(
                c.post(
                    f"/dev/documents/{slot['document_id']}/scan",
                    json={"org_id": org_id, "verdict": "clean"},
                )
            )

        for (t, tenant_tok), u, rent in (
            (tenants[0], a1, 150000),
            (tenants[1], rooms[0], 90000),
            (tenants[1], bay, 15000),  # Ali also rents a bay for his car
        ):
            is_bay = u["type"] == "parking"
            lease = ok(
                c.post(
                    "/v1/leases",
                    headers=admin,
                    json={
                        "unit_id": u["id"],
                        "tenant_ids": [t["id"]],
                        "start_date": str(start),
                        "monthly_rent": _myr(rent),
                        "due_day": 5,
                        "deposits": [
                            {"type": "access_card", "amount": _myr(5000)}
                            if is_bay
                            else {"type": "security", "amount": _myr(rent * 2)}
                        ],
                        **({"vehicle_plate": "WXY 1234"} if is_bay else {}),
                    },
                )
            )
            # tenancy agreement (contract v0.7.0): Siti signs in the portal, Ali signed on paper
            agreement_pdf(lease["id"], t["full_name"])
            if t["full_name"] == TENANTS[0][0]:
                ok(c.post(f"/v1/leases/{lease['id']}/agreement/send", headers=admin, json={}))
                ok(
                    c.post(
                        f"/v1/leases/{lease['id']}/agreement/sign",
                        headers=tenant_tok,
                        json={"full_name": t["full_name"], "agree": True},
                    )
                )
            else:
                ok(
                    c.post(
                        f"/v1/leases/{lease['id']}/agreement/paper",
                        headers=admin,
                        json={"tenant_ids": [t["id"]], "note": "signed at the management office"},
                    )
                )
            ok(c.post(f"/v1/leases/{lease['id']}/activate", headers=admin))
            leases.append(lease)
        ok(
            c.post(
                "/v1/invoice-runs",
                headers={**admin, "Idempotency-Key": f"demo-seed-{start:%Y%m}-0001"},
                json={"period": f"{start:%Y-%m}"},
            )
        )

        # Siti pays her rent so there is a receipt and a ledger; Ali's stays outstanding.
        invoices = ok(c.get("/v1/invoices", headers=tenants[0][1]))["items"]
        rent_inv = next(i for i in invoices if i["line_items"][0]["type"] == "rent")
        qr = ok(c.post(f"/v1/invoices/{rent_inv['id']}/qr", headers=tenants[0][1]))
        ok(
            c.post(
                "/dev/pay",
                json={
                    "reference": qr["reference"],
                    "amount_minor": rent_inv["total"]["amount_minor"],
                },
            )
        )

        maint = ok(
            c.post(
                "/v1/maintenance-requests",
                headers=tenants[1][1],
                json={
                    "unit_id": rooms[0]["id"],
                    "title": "Air-cond is leaking",
                    "description": "Water drips from the unit above the bed.",
                    "category": "appliance",
                    "priority": "normal",
                },
            )
        )
        notes.append(f"maintenance request {maint['id']}")
    notes = [
        f"created '{org_name}' with 2 properties, 6 units and rooms, 4 parking bays, 2 tenants, 3 active leases (one for a bay), this month's invoices",
        f"log in as {ADMIN} (org admin, sees everything), {MANAGER} (property manager of Pelangi Damansara only), {TENANTS[0][1]} or {TENANTS[1][1]} (tenants), {platform_admin} (platform)",
        "Siti's rent is paid; Ali's is outstanding",
        f"the company's branded sign-in page is /login/{handle}",
    ]
    return notes
