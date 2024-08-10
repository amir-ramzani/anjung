import json
import re
from datetime import timedelta

import boto3
from fastapi.testclient import TestClient

from tests.conftest import REGION, World
from tests.integration.billing_helpers import Setup


def rent(s: Setup) -> dict:  # type: ignore[type-arg]
    s.run("2018-12")
    return s.rent_invoice()


def test_qr_follows_the_provider_rules(world: World, client: TestClient) -> None:
    s = Setup(world)
    inv = rent(s)
    qr = s.qr(inv["id"])
    assert re.fullmatch(r"ANJ[A-Z2-7]{1,20}", qr["reference"]) and len(qr["reference"]) <= 23
    assert qr["amount"]["amount_minor"] == 160000 and qr["provider"] == "fake"
    assert qr["expires_at"].startswith("2018-11-20T03:05:00")  # now + the provider's validity
    assert s.qr(inv["id"])["reference"] == qr["reference"]  # still valid: same QR
    assert (
        client.get(f"/v1/invoices/{inv['id']}", headers=s.h).json()["qr"]["reference"]
        == qr["reference"]
    )
    world.c.clock.set(world.c.clock.now() + timedelta(minutes=6))  # expired: a fresh reference
    assert s.qr(inv["id"])["reference"] != qr["reference"]
    ref = world.c.store.table.get(f"QRREF#{qr['reference']}", "META")
    assert (
        ref is not None
        and ref["org_id"] == s.org.id
        and ref["invoice_id"] == inv["id"]
        and ref["ttl"] > 0
    )


def test_qr_is_refused_for_paid_void_and_foreign_invoices_and_is_rate_limited(
    world: World, client: TestClient
) -> None:
    s = Setup(world)
    inv = rent(s)
    other = Setup(world)
    other_inv = rent(other)
    # another organisation's invoice looks missing, never forbidden
    assert client.post(f"/v1/invoices/{other_inv['id']}/qr", headers=s.tenant_h).status_code == 404
    assert client.post(f"/v1/invoices/{other_inv['id']}/qr", headers=s.h).status_code == 404
    # another tenant of the same organisation is forbidden
    neighbour = s.org.tenant_login(s.org.new_tenant())
    assert client.post(f"/v1/invoices/{inv['id']}/qr", headers=neighbour).status_code == 403
    client.post(f"/v1/invoices/{inv['id']}/void", headers=s.h, json={"reason": "wrong"})
    assert client.post(f"/v1/invoices/{inv['id']}/qr", headers=s.tenant_h).status_code == 409
    world.c.settings.qr_rate_per_minute = 3
    from app.core.ratelimit import RateLimiter

    world.c.qr_limiter = RateLimiter(3)
    dep = [i for i in s.invoices() if i["line_items"][0]["type"] == "deposit"][0]
    codes = []
    for _ in range(5):
        world.c.clock.set(
            world.c.clock.now() + timedelta(minutes=10)
        )  # each QR has expired, so each needs a new one
        codes.append(client.post(f"/v1/invoices/{dep['id']}/qr", headers=s.tenant_h).status_code)
    assert (
        codes[:3] == [201, 201, 201]
        and codes[3] == 429
        and "Retry-After" in client.post(f"/v1/invoices/{dep['id']}/qr", headers=s.tenant_h).headers
    )


def test_webhook_pays_the_invoice_exactly_once(world: World, client: TestClient) -> None:
    s = Setup(world)
    inv = rent(s)
    ref = s.qr(inv["id"])["reference"]
    assert s.pay(ref, 160000, "evt-1").json() == {"received": True}
    got = client.get(f"/v1/invoices/{inv['id']}", headers=s.h).json()
    assert (
        got["status"] == "paid"
        and got["paid"]["amount_minor"] == 160000
        and got["outstanding"]["amount_minor"] == 0
    )
    assert s.balance() == 300000  # the deposit invoice is still owed
    led = s.ledger()
    assert led[-1]["type"] == "payment" and led[-1]["amount"]["amount_minor"] == -160000
    assert led[-1]["balance_after"]["amount_minor"] == 300000 and led[-1]["created_by"] == "gateway"
    # the same event again, and the same gateway transaction under a new event id: nothing changes
    for ev in ("evt-1", "evt-2"):
        assert s.pay(ref, 160000, ev, txn="TXN-evt-1").status_code == 200
    assert (
        client.get(f"/v1/invoices/{inv['id']}", headers=s.h).json()["paid"]["amount_minor"]
        == 160000
    )
    assert s.balance() == 300000 and len(s.ledger()) == len(led)
    items, _ = world.c.store.table.query(pk=f"ORG#{s.org.id}#INV#{inv['id']}", sk_prefix="PAY#")
    assert len(items) == 1
    gsi3, _ = world.c.store.table.query(pk=f"ORG#{s.org.id}#INV#unpaid", index="GSI3")
    assert inv["id"] not in {i["id"] for i in gsi3}  # a paid invoice leaves the sparse index


def test_webhook_signature_timestamp_and_provider_checks(world: World, client: TestClient) -> None:
    s = Setup(world)
    ref = s.qr(rent(s)["id"])["reference"]
    prov = world.c.payments["fake"]
    now = world.c.clock.now()
    body, headers = prov.signed_request(reference=ref, amount_minor=100, now=now, event_id="evt-x")  # type: ignore[attr-defined]
    url = "/v1/webhooks/payments/fake"
    assert client.post(url, content=body, headers=headers).status_code == 200
    bad = {**headers, "x-signature": "0" * 64}
    for h, b in (
        (bad, body),
        ({k: v for k, v in headers.items() if k != "x-signature"}, body),
        ({k: v for k, v in headers.items() if k != "x-timestamp"}, body),
        (headers, body + b" "),
    ):
        r = client.post(url, content=b, headers=h)
        assert r.status_code == 401 and r.json()["code"] == "webhook_signature_invalid"
    old, oh = prov.signed_request(
        reference=ref, amount_minor=100, now=now - timedelta(minutes=6), event_id="evt-old"
    )  # type: ignore[attr-defined]
    assert client.post(url, content=old, headers=oh).status_code == 401  # replay of an old capture
    assert (
        client.post("/v1/webhooks/payments/gateway_a", content=body, headers=headers).status_code
        == 404
    )
    other = prov.sign(b"{}", "1")  # type: ignore[attr-defined]
    assert (
        client.post(
            url, content=b"{}", headers={"x-signature": other, "x-timestamp": "1"}
        ).status_code
        == 401
    )
    signed_garbage = b"not json"
    ts = str(int(now.timestamp()))
    r = client.post(
        url,
        content=signed_garbage,
        headers={"x-signature": prov.sign(signed_garbage, ts), "x-timestamp": ts},
    )  # type: ignore[attr-defined]
    assert r.status_code == 401
    assert client.post(url, content=body).status_code == 401
    items, _ = world.c.store.table.query(pk=f"ORG#{s.org.id}#UNMATCHED", sk_prefix="PAY#")
    assert items == []  # nothing from the rejected calls was recorded


def test_fake_provider_is_absent_in_prod(world: World, client: TestClient, settings) -> None:  # type: ignore[no-untyped-def]
    from app.container import Container

    c = Container.build(
        settings.model_copy(
            update={"env": "prod", "payment_provider": "gateway_a", "auth_mode": "cognito"}
        ),
        ddb_client=world.c.store.table._c,  # noqa: SLF001
        identity=world.c.identity,
    )
    assert "fake" not in c.payments


def test_underpayment_overpayment_and_credit(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[])
    inv = rent(s)
    ref = s.qr(inv["id"])["reference"]
    s.pay(ref, 60000, "e1")
    part = client.get(f"/v1/invoices/{inv['id']}", headers=s.h).json()
    assert part["status"] == "partially_paid" and part["outstanding"]["amount_minor"] == 100000
    assert s.balance() == 100000
    s.pay(ref, 110000, "e2")  # 10000 too much
    done = client.get(f"/v1/invoices/{inv['id']}", headers=s.h).json()
    assert done["status"] == "paid" and done["paid"]["amount_minor"] == 160000
    lease = s.lease_now()
    assert lease["balance"]["amount_minor"] == -10000 and lease["credit"]["amount_minor"] == 10000
    s.run("2019-01")  # the credit is applied to the next invoice automatically
    nxt = s.rent_invoice("2019-01")
    assert (
        nxt["paid"]["amount_minor"] == 10000
        and nxt["status"] == "partially_paid"
        or nxt["outstanding"]["amount_minor"] == 150000
    )
    assert s.lease_now()["credit"]["amount_minor"] == 0 and s.balance() == 150000
    allocs, _ = world.c.store.table.query(pk=f"ORG#{s.org.id}#INV#{nxt['id']}", sk_prefix="ALLOC#")
    assert [a["source"] for a in allocs] == ["credit"]


def test_unmatched_failed_and_wrong_currency_money(world: World, client: TestClient) -> None:
    s = Setup(world)
    inv = rent(s)
    ref = s.qr(inv["id"])["reference"]
    assert s.pay("ANJNOSUCHREF", 5000, "u1").status_code == 200
    unmatched = client.get("/v1/payments/unmatched", headers=s.h)
    assert unmatched.status_code == 200
    # an unknown reference cannot be tied to an organisation: it waits in the platform queue
    assert unmatched.json()["items"] == []
    parked, _ = world.c.store.table.query(
        pk=f"ORG#{world.c.settings.platform_org_id}#UNMATCHED", sk_prefix="PAY#"
    )
    assert (
        len(parked) == 1
        and parked[0]["status"] == "unmatched"
        and parked[0]["amount_minor"] == 5000
    )
    s.pay(ref, 100, "f1", status="failed")
    assert s.balance() == 460000  # failed and unknown payments never touch the ledger
    failed, _ = world.c.store.table.query(pk=f"ORG#{s.org.id}#INV#{inv['id']}", sk_prefix="PAY#")
    assert [p["status"] for p in failed] == ["failed"]


def test_expired_reference_still_matches(world: World, client: TestClient) -> None:
    s = Setup(world)
    inv = rent(s)
    ref = s.qr(inv["id"])["reference"]
    world.c.clock.set(world.c.clock.now() + timedelta(days=2))  # long after the QR expired
    s.pay(ref, 160000, "late-1")
    assert client.get(f"/v1/invoices/{inv['id']}", headers=s.h).json()["status"] == "paid"


def test_payment_for_a_vanished_invoice_is_parked_in_the_org_queue(
    world: World, client: TestClient
) -> None:
    from app.repositories.dynamo import Delete

    s = Setup(world)
    inv = rent(s)
    ref = s.qr(inv["id"])["reference"]
    world.c.store.table.transact(
        [
            Delete(
                key=(f"ORG#{s.org.id}#LEASE#{inv['lease_id']}", f"INV#{inv['period']}#{inv['id']}")
            )
        ]
    )  # the QR reference still resolves, the invoice record does not
    s.pay(ref, 160000, "orphan-1")
    page = client.get("/v1/payments/unmatched", headers=s.h).json()["items"]
    assert len(page) == 1 and page[0]["status"] == "unmatched" and page[0]["invoice_id"] is None
    assert (
        page[0]["amount"]["amount_minor"] == 160000 and s.balance() == 460000
    )  # money kept, ledger untouched


def test_allocate_rules(world: World, client: TestClient) -> None:
    from datetime import UTC, datetime

    from app.core.money import Money
    from app.domains.billing.payments import record_unmatched
    from app.integrations.payments.base import PaymentEvent

    s = Setup(world, deposits=[])
    inv = rent(s)
    ev = PaymentEvent(
        "fake",
        "ev-a",
        "txn-a",
        "UNKNOWN1",
        Money(160000),
        "succeeded",
        datetime(2018, 11, 20, tzinfo=UTC),
    )
    pay = record_unmatched(world.c, s.org.id, ev, "unknown_reference")
    path = f"/v1/payments/{pay['id']}/allocate"
    body = {"invoice_id": inv["id"], "reason": "tenant sent the transfer reference by message"}
    assert client.post(path, headers=s.h, json=body).status_code == 422  # key header missing
    assert (
        client.post(
            path,
            headers=s.idem("alloc-key-1"),
            json={**body, "amount": {"amount_minor": 5, "currency": "MYR"}},
        ).status_code
        == 422
    )
    assert (
        client.post(
            path,
            headers=s.idem("alloc-key-2"),
            json={**body, "invoice_id": "01JD8Z3K9Q4M7T2V6X8B5N1C0B"},
        ).status_code
        == 422
    )
    assert (
        client.post(path, headers=s.idem("alloc-key-3"), json={"invoice_id": inv["id"]}).status_code
        == 422
    )  # reason
    ok = client.post(path, headers=s.idem("alloc-key-4"), json=body)
    assert (
        ok.status_code == 200
        and ok.json()["status"] == "succeeded"
        and ok.json()["invoice_id"] == inv["id"]
    )
    assert client.get(f"/v1/invoices/{inv['id']}", headers=s.h).json()["status"] == "paid"
    assert client.get("/v1/payments/unmatched", headers=s.h).json()["items"] == []
    replay = client.post(path, headers=s.idem("alloc-key-4"), json=body)
    assert replay.status_code == 200 and replay.json()["id"] == ok.json()["id"]
    second = client.post(path, headers=s.idem("alloc-key-5"), json=body)
    assert second.status_code == 409  # already allocated
    assert s.balance() == 0
    actions = [
        a["action"] for a in client.get("/v1/audit-log?limit=100", headers=s.h).json()["items"]
    ]
    assert "payment.allocate" in actions and "payment.unmatched" in actions


def test_gateway_reversal_reopens_the_invoice(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[])
    inv = rent(s)
    ref = s.qr(inv["id"])["reference"]
    s.pay(ref, 160000, "p1", txn="TXN-R")
    assert s.balance() == 0
    s.pay(ref, 160000, "p1-rev", txn="TXN-R", status="reversed")
    got = client.get(f"/v1/invoices/{inv['id']}", headers=s.h).json()
    assert got["status"] == "open" and got["paid"]["amount_minor"] == 0 and s.balance() == 160000
    assert s.ledger()[-1]["type"] == "payment_reversal"
    s.pay(
        ref, 160000, "p1-rev2", txn="TXN-R", status="reversed"
    )  # a second report of the same reversal
    assert (
        s.balance() == 160000
        and len([e for e in s.ledger() if e["type"] == "payment_reversal"]) == 1
    )
    assert (
        s.pay(ref, 1, "rev-unknown", txn="TXN-NOPE", status="reversed").status_code == 200
    )  # unknown: logged only
    gsi3, _ = world.c.store.table.query(pk=f"ORG#{s.org.id}#INV#unpaid", index="GSI3")
    assert inv["id"] in {i["id"] for i in gsi3}  # back in the unpaid index


def test_manual_payment(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[])
    inv = rent(s)
    path = f"/v1/invoices/{inv['id']}/payments/manual"
    body = {
        "method": "cash",
        "amount": {"amount_minor": 60000, "currency": "MYR"},
        "received_at": "2018-12-04T09:00:00Z",
        "reference": "RCPT-77",
        "reason": "tenant paid cash at the office",
    }
    r = client.post(path, headers=s.idem("manual-key-1"), json=body)
    assert r.status_code == 201
    pay = r.json()
    assert (
        pay["method"] == "cash"
        and pay["status"] == "succeeded"
        and pay["recorded_by"] == s.org.admin_sub
    )
    assert pay["gateway_reference"] == "RCPT-77" and pay["provider"] is None
    replay = client.post(path, headers=s.idem("manual-key-1"), json=body)
    assert replay.status_code == 201 and replay.json()["id"] == pay["id"]
    assert s.balance() == 100000  # applied once
    for bad in (
        {**body, "amount": {"amount_minor": 0, "currency": "MYR"}},
        {**body, "method": "duitnow_qr"},
        {**body, "reason": ""},
        {k: v for k, v in body.items() if k != "reason"},
    ):
        assert client.post(path, headers=s.idem("manual-bad-1"), json=bad).status_code == 422
    rest = client.post(
        path,
        headers=s.idem("manual-key-2"),
        json={**body, "amount": {"amount_minor": 100000, "currency": "MYR"}},
    )
    assert rest.status_code == 201
    assert client.get(f"/v1/invoices/{inv['id']}", headers=s.h).json()["status"] == "paid"
    assert (
        client.post(path, headers=s.idem("manual-key-3"), json=body).status_code == 409
    )  # nothing left to pay
    assert (
        client.post(
            path, headers={**s.tenant_h, "Idempotency-Key": "manual-key-4"}, json=body
        ).status_code
        == 403
    )
    log = client.get("/v1/audit-log?limit=100", headers=s.h).json()["items"]
    entry = next(a for a in log if a["action"] == "payment.manual_record")
    assert entry["summary"]["reason"] == "tenant paid cash at the office"


def test_void_rules(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[])
    inv = rent(s)
    assert s.balance() == 160000
    r = client.post(
        f"/v1/invoices/{inv['id']}/void", headers=s.h, json={"reason": "issued by mistake"}
    )
    assert (
        r.status_code == 200
        and r.json()["status"] == "void"
        and r.json()["outstanding"]["amount_minor"] == 0
    )
    assert (
        s.balance() == 0
        and s.ledger()[-1]["type"] == "adjustment"
        and s.ledger()[-1]["amount"]["amount_minor"] == -160000
    )
    assert (
        client.post(
            f"/v1/invoices/{inv['id']}/void", headers=s.h, json={"reason": "again"}
        ).status_code
        == 409
    )
    assert client.post(f"/v1/invoices/{inv['id']}/void", headers=s.h, json={}).status_code == 422
    gsi3, _ = world.c.store.table.query(pk=f"ORG#{s.org.id}#INV#unpaid", index="GSI3")
    assert inv["id"] not in {i["id"] for i in gsi3}
    s.run("2019-01")
    paying = s.rent_invoice("2019-01")
    client.post(
        f"/v1/invoices/{paying['id']}/payments/manual",
        headers=s.idem("void-pay-1"),
        json={
            "method": "cash",
            "amount": {"amount_minor": 1000, "currency": "MYR"},
            "received_at": "2019-01-02T00:00:00Z",
            "reason": "x",
        },
    )
    assert (
        client.post(
            f"/v1/invoices/{paying['id']}/void", headers=s.h, json={"reason": "no"}
        ).status_code
        == 409
    )  # has payments


def test_one_off_charges(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[])
    path = f"/v1/leases/{s.lease['id']}/charges"
    body = {
        "description": "Electricity share, Oct 2018",
        "category": "utility",
        "amount": {"amount_minor": 4250, "currency": "MYR"},
        "due_date": "2018-12-20",
    }
    r = client.post(path, headers=s.idem("charge-key-1"), json=body)
    assert r.status_code == 201
    inv = r.json()
    assert inv["line_items"] == [
        {
            "description": body["description"],
            "type": "one_off",
            "amount": {"amount_minor": 4250, "currency": "MYR"},
            "period_start": None,
            "period_end": None,
            "category": "utility",
        }
    ]
    assert inv["status"] == "open" and inv["due_date"] == "2018-12-20" and s.balance() == 4250
    assert s.ledger()[-1]["type"] == "charge_other"
    replay = client.post(path, headers=s.idem("charge-key-1"), json=body)
    assert (
        replay.json()["id"] == inv["id"] and s.balance() == 4250
    )  # same key: same invoice, billed once
    assert (
        client.post(path, headers=s.idem("charge-key-2"), json=body).json()["id"] != inv["id"]
    )  # a new key is a new charge
    assert s.balance() == 8500
    for bad in (
        {**body, "amount": {"amount_minor": 0, "currency": "MYR"}},
        {**body, "category": "fun"},
        {**body, "description": ""},
        {**body, "due_date": "tomorrow"},
    ):
        assert client.post(path, headers=s.idem("charge-bad-1"), json=bad).status_code == 422
    # payable through the usual QR and allowed after move-out
    ref = s.qr(inv["id"])["reference"]
    s.pay(ref, 4250, "charge-pay")
    assert client.get(f"/v1/invoices/{inv['id']}", headers=s.h).json()["status"] == "paid"
    client.post(
        f"/v1/leases/{s.lease['id']}/terminate",
        headers=s.h,
        json={"termination_date": "2018-12-20", "reason": "r", "waive_notice": True},
    )
    assert client.post(path, headers=s.idem("charge-key-3"), json=body).status_code == 201
    draft = s.org.new_lease(s.org.new_unit(s.org.new_property("Q")["id"])["id"], [s.tenant["id"]])
    assert (
        client.post(
            f"/v1/leases/{draft['id']}/charges", headers=s.idem("charge-key-4"), json=body
        ).status_code
        == 409
    )
    assert (
        client.post(
            path, headers={**s.tenant_h, "Idempotency-Key": "charge-key-5"}, json=body
        ).status_code
        == 403
    )


def test_receipt_is_generated_after_payment_and_holds_no_tenant_name(
    world: World, client: TestClient
) -> None:
    s = Setup(world, deposits=[])
    inv = rent(s)
    assert (
        client.get(f"/v1/invoices/{inv['id']}/receipt", headers=s.h).status_code == 404
    )  # nothing paid yet
    s.pay(s.qr(inv["id"])["reference"], 160000, "rcpt-1")
    r = client.get(f"/v1/invoices/{inv['id']}/receipt", headers=s.tenant_h)
    assert (
        r.status_code == 200
        and r.json()["filename"] == "receipt.pdf"
        and r.json()["url"].startswith("http")
    )
    doc = world.c.store.documents(s.org.id).get(inv["id"])
    assert (
        doc is not None
        and doc["type"] == "receipt"
        and doc["status"] == "clean"
        and doc["owner_type"] == "invoice"
    )
    obj = (
        boto3.client("s3", region_name=REGION)
        .get_object(Bucket="anjung-docs-test", Key=doc["s3_key"])["Body"]
        .read()
    )
    assert (
        obj.startswith(b"%PDF") and b"Siti" not in obj and s.tenant["full_name"].encode() not in obj
    )
    from app.domains.billing.receipts import render_pdf

    other = Setup(world, deposits=[])
    assert client.get(f"/v1/invoices/{inv['id']}/receipt", headers=other.tenant_h).status_code in (
        403,
        404,
    )
    assert callable(render_pdf)


def test_amounts_are_always_integers(world: World, client: TestClient) -> None:
    s = Setup(world)
    s.run("2018-12")
    raw = json.dumps(client.get("/v1/invoices", headers=s.h).json()) + json.dumps(s.ledger())
    assert not re.search(r'"amount_minor": \d+\.\d', raw)
