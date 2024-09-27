from datetime import UTC, datetime, timedelta
from typing import Any

from app.domains.notifications import service as ns
from app.domains.notifications.service import in_quiet_hours
from app.integrations.channels import TransientError
from app.worker.handlers.notifications import reminder_sweep
from fastapi.testclient import TestClient

from tests.conftest import World
from tests.integration.billing_helpers import Setup, at


def opt_in(
    client: TestClient,
    s: Setup,
    *,
    email: bool = True,
    whatsapp: bool = False,
    consent: bool = True,
) -> None:
    prefs: dict[str, Any] = {"locale": "en"}
    if email:
        prefs["email"] = {"enabled": True}
    if whatsapp:
        prefs["whatsapp"] = {"enabled": True, "number": "+60123456789"}
    assert (
        client.put("/v1/notifications/preferences", headers=s.tenant_h, json=prefs).status_code
        == 200
    )
    tid = s.tenant["id"]
    if consent:
        for purpose in (["email_messaging"] if email else []) + (
            ["whatsapp_messaging"] if whatsapp else []
        ):
            r = client.post(
                f"/v1/tenants/{tid}/consents",
                headers=s.tenant_h,
                json={"purpose": purpose, "policy_version": "2018-11", "granted": True},
            )
            assert r.status_code == 201


def sent(world: World, channel: str = "email") -> list[Any]:
    """Messages from the notification pipeline (not the welcome email sent when a tenant is invited)."""
    out = world.c.channels[channel].sent  # type: ignore[attr-defined]
    return [m for m in out if m.template not in ("tenant_invite", "invite_staff")]


def log(client: TestClient, s: Setup, **params: Any) -> list[dict[str, Any]]:
    r = client.get("/v1/notifications/log", headers=s.h, params={"limit": 100, **params})
    assert r.status_code == 200, r.text
    return [
        e for e in r.json()["items"] if e["template"] != "tenant_invite"
    ]  # not the welcome email


def test_nothing_is_sent_without_preferences_and_consent(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[])
    s.run("2018-12")
    assert sent(world) == [] and log(client, s) == []  # default: nobody is messaged
    opt_in(client, s, consent=False)  # enabled but no consent record
    s.run("2019-01")
    assert sent(world) == []
    client.post(
        f"/v1/tenants/{s.tenant['id']}/consents",
        headers=s.tenant_h,
        json={"purpose": "email_messaging", "policy_version": "1", "granted": True},
    )
    s.run("2019-02")
    assert len(sent(world)) == 1


def test_invoice_issued_carries_a_pay_link_and_is_logged(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    opt_in(client, s)
    s.run("2018-12")
    inv = s.rent_invoice()
    [msg] = sent(world)
    assert msg.template == "invoice_issued" and msg.to == s.tenant["email"] and msg.locale == "en"
    assert (
        inv["number"] in msg.subject
        and f"/pay/{inv['id']}" in msg.text
        and "MYR 1,500.00" in msg.text
    )
    text = msg.text.lower()  # ids are random and may contain the letters "qr": look for the words
    assert (
        "qr code" not in text and "qr_payload" not in text and "<img" not in msg.html
    )  # a link, never a QR image
    [entry] = log(client, s)
    assert (
        entry["status"] == "sent"
        and entry["channel"] == "email"
        and entry["template"] == "invoice_issued"
    )
    assert entry["recipient_tenant_id"] == s.tenant["id"] and entry[
        "provider_message_id"
    ].startswith("email-")
    assert log(client, s, status="failed") == [] and len(log(client, s, channel="email")) == 1
    assert log(client, s, tenant_id=s.tenant["id"]) == log(client, s)
    assert client.get("/v1/notifications/log", headers=s.tenant_h).status_code == 403


def test_one_message_per_event_even_when_the_sweep_runs_twice(
    world: World, client: TestClient
) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    opt_in(client, s)
    s.run("2018-12")
    s.run("2018-12", key="again-key-123")
    assert len(sent(world)) == 1
    # a replayed event is deduplicated by its key, whoever publishes it
    assert ns.notify(
        world.c,
        s.org.id,
        "lease_activated",
        [s.tenant["id"]],
        s.lease["id"],
        {"start_date": "x", "link": "y"},
    ) in (0, 1)
    again = ns.notify(
        world.c,
        s.org.id,
        "lease_activated",
        [s.tenant["id"]],
        s.lease["id"],
        {"start_date": "x", "link": "y"},
    )
    assert again == 0


def test_malay_locale_and_whatsapp_needs_an_explicit_opt_in(
    world: World, client: TestClient
) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    client.put(
        "/v1/notifications/preferences",
        headers=s.tenant_h,
        json={
            "locale": "ms",
            "email": {"enabled": True},
            "whatsapp": {"enabled": True, "number": "+60123456789"},
        },
    )
    for purpose in ("email_messaging", "whatsapp_messaging"):
        client.post(
            f"/v1/tenants/{s.tenant['id']}/consents",
            headers=s.tenant_h,
            json={"purpose": purpose, "policy_version": "1", "granted": True},
        )
    s.run("2018-12")
    assert "Bayar di sini" in sent(world)[0].text and "Invois" in sent(world)[0].subject
    assert len(sent(world, "whatsapp")) == 1 and sent(world, "whatsapp")[0].to == "+60123456789"
    prefs = client.get("/v1/notifications/preferences", headers=s.tenant_h).json()
    assert prefs["whatsapp"]["opted_in_at"] and prefs["whatsapp"]["number"] == "+60123456789"
    assert {e["channel"] for e in log(client, s)} == {"email", "whatsapp"}


def test_opting_out_applies_to_the_very_next_message_and_withdraws_consent(
    world: World, client: TestClient
) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    opt_in(client, s, whatsapp=True)
    s.run("2018-12")
    assert len(sent(world)) == 1 and len(sent(world, "whatsapp")) == 1
    r = client.put(
        "/v1/notifications/preferences",
        headers=s.tenant_h,
        json={"whatsapp": {"enabled": False, "number": "+60123456789"}},
    )
    assert r.status_code == 200 and r.json()["whatsapp"]["enabled"] is False
    assert r.json()["whatsapp"].get("opted_in_at") is None  # the opt-in is revoked with it
    consents = client.get(f"/v1/tenants/{s.tenant['id']}/consents", headers=s.tenant_h).json()[
        "items"
    ]
    assert consents[0]["purpose"] == "whatsapp_messaging" and consents[0]["granted"] is False
    s.run("2019-01")
    assert (
        len(sent(world, "whatsapp")) == 1 and len(sent(world)) == 2
    )  # email carries on, WhatsApp stopped
    # a message already queued when the opt-out happened is not sent either
    from app.domains.notifications.service import repo_log

    entry = repo_log(
        s.org.id,
        "01JD8Z3K9Q4M7T2V6X8B5N1C0Z",
        "2019-01-05T00:00:00.000000Z",
        s.tenant["id"],
        "whatsapp",
        "maintenance_update",
        "en",
        {"status": "open", "link": "x"},
        None,
    )
    world.c.store.notifications(s.org.id).put(entry)
    assert ns.send(world.c, s.org.id, "2019-01", entry["SK"]) == "suppressed"
    assert (
        client.put(
            "/v1/notifications/preferences",
            headers=s.tenant_h,
            json={"whatsapp": {"enabled": True}},
        ).status_code
        == 422
    )  # a number is required


def test_failures_retries_and_suppression(world: World, client: TestClient) -> None:
    import pytest
    from app.worker.envelope import Retryable

    s = Setup(world, deposits=[], recurring_charges=[])
    opt_in(client, s)
    ch = world.c.channels["email"]
    ch.fail_with = RuntimeError("provider rejected")  # type: ignore[attr-defined]
    s.run("2018-12")
    [entry] = log(client, s)
    assert entry["status"] == "failed" and entry["error"] == "RuntimeError"
    ch.fail_with = TransientError("throttled")  # type: ignore[attr-defined]
    items, _ = world.c.store.table.query(pk=f"ORG#{s.org.id}#NOTIF#2018-11")
    fresh = {
        **items[0],
        "id": "01JD8Z3K9Q4M7T2V6X8B5N1C0Y",
        "SK": "2018-11-20T03:00:00.000001Z#x",
        "status": "queued",
        "error": None,
    }
    world.c.store.notifications(s.org.id).put(fresh)
    with pytest.raises(Retryable):
        ns.send(
            world.c, s.org.id, "2018-11", fresh["SK"]
        )  # left queued: SQS redelivers with backoff
    assert world.c.store.notifications(s.org.id).get("2018-11", fresh["SK"])["status"] == "queued"  # type: ignore[index]
    ch.fail_with = None  # type: ignore[attr-defined]
    assert ns.send(world.c, s.org.id, "2018-11", fresh["SK"]) == "sent"
    assert ns.send(world.c, s.org.id, "2018-11", fresh["SK"]) == "skipped"  # never sent twice


def test_bounce_and_complaint_suppress_the_address(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    opt_in(client, s)
    s.run("2018-12")
    [entry] = log(client, s)
    pid = entry["provider_message_id"]
    assert (
        ns.handle_feedback(world.c, "unknown-id", "Bounce", True, s.tenant["email"])
        == "unknown_message"
    )
    assert (
        ns.handle_feedback(world.c, pid, "Bounce", False, s.tenant["email"]) == "noted"
    )  # soft bounce: keep sending
    assert ns.handle_feedback(world.c, pid, "Bounce", True, s.tenant["email"]) == "suppressed"
    s.run("2019-01")
    entries = log(client, s)
    assert entries[0]["status"] == "suppressed" and entries[0]["error"] == "address_suppressed"
    assert len(sent(world)) == 1  # the second message never left


def test_quiet_hours_defer_and_release(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    opt_in(client, s)
    client.patch(
        "/v1/org",
        headers=s.h,
        json={"settings": {"quiet_hours": {"start": "22:00", "end": "08:00"}}},
    )
    world.c.clock.set(datetime(2018, 11, 20, 15, 30, tzinfo=UTC))  # 23:30 in Kuala Lumpur
    s.run("2018-12")
    [entry] = log(client, s)
    assert entry["status"] == "queued" and sent(world) == []  # held until the morning
    world.c.clock.set(datetime(2018, 11, 20, 23, 59, tzinfo=UTC))  # 07:59 local: still quiet
    assert ns.release_deferred(world.c, s.org.id) == 0
    world.c.clock.set(datetime(2018, 11, 21, 0, 5, tzinfo=UTC))  # 08:05 local
    assert ns.release_deferred(world.c, s.org.id) == 1
    assert len(sent(world)) == 1 and log(client, s)[0]["status"] == "sent"
    assert ns.release_deferred(world.c, s.org.id) == 0


def test_quiet_hours_arithmetic() -> None:
    qh = {"quiet_hours": {"start": "22:00", "end": "08:00"}}
    assert in_quiet_hours(qh, datetime(2018, 11, 20, 4, 0, tzinfo=UTC)) is None  # 12:00 local
    until = in_quiet_hours(qh, datetime(2018, 11, 20, 15, 0, tzinfo=UTC))  # 23:00 local
    assert until == datetime(2018, 11, 21, 0, 0, tzinfo=UTC)
    same_day = {"quiet_hours": {"start": "13:00", "end": "15:00"}}
    assert in_quiet_hours(same_day, datetime(2018, 11, 20, 6, 0, tzinfo=UTC)) == datetime(
        2018, 11, 20, 7, 0, tzinfo=UTC
    )
    assert in_quiet_hours({}, datetime(2018, 11, 20, tzinfo=UTC)) is None


def test_reminders_once_per_invoice_and_offset_with_a_pay_link(
    world: World, client: TestClient
) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    opt_in(client, s)
    s.run("2018-12")  # due 2018-12-05, so offsets -3, 0, +3 land on 12-02, 12-05, 12-08
    world.c.channels["email"].sent.clear()  # type: ignore[attr-defined]
    for day, expected in ((1, 0), (2, 1), (2, 0), (5, 1), (6, 0), (8, 1), (9, 0)):
        at(world, 2018, 12, day)
        assert reminder_sweep(world.c, s.org.id) == expected, day
    templates = [m.template for m in sent(world)]
    assert templates == ["payment_reminder", "payment_reminder", "invoice_overdue"]
    assert all("/pay/" in m.text for m in sent(world))
    s.pay(s.qr(s.rent_invoice()["id"])["reference"], 150000, "reminder-pay")
    at(world, 2018, 12, 11)
    assert reminder_sweep(world.c, s.org.id) == 0  # paid invoices are not chased


def test_event_notifications_for_payment_lease_overdue_maintenance_privacy(
    world: World, client: TestClient
) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    opt_in(client, s)
    templates = lambda: [m.template for m in sent(world)]  # noqa: E731
    assert (
        templates() == ["lease_activated"] or templates() == []
    )  # the tenant opted in after activation
    s.run("2018-12")
    inv = s.rent_invoice()
    s.pay(s.qr(inv["id"])["reference"], 150000, "evt-pay")
    assert templates()[-1] == "payment_received" and "/receipt" in sent(world)[-1].text
    req = client.post(
        "/v1/maintenance-requests",
        headers=s.tenant_h,
        json={"title": "t", "description": "d", "category": "plumbing", "unit_id": s.unit["id"]},
    ).json()
    client.patch(f"/v1/maintenance-requests/{req['id']}", headers=s.h, json={"status": "triaged"})
    assert templates()[-1] == "maintenance_update" and "triaged" in sent(world)[-1].text
    client.post(
        f"/v1/maintenance-requests/{req['id']}/comments",
        headers=s.h,
        json={"body": "internal", "internal": True},
    )
    before = len(sent(world))
    client.post(
        f"/v1/maintenance-requests/{req['id']}/comments", headers=s.h, json={"body": "public reply"}
    )
    assert len(sent(world)) == before + 1  # an internal note tells nobody, a public reply does
    pr = client.post("/v1/privacy/requests", headers=s.tenant_h, json={"type": "access"}).json()
    client.patch(f"/v1/privacy/requests/{pr['id']}", headers=s.h, json={"status": "in_progress"})
    assert templates()[-1] == "privacy_request_update"
    client.post(
        f"/v1/leases/{s.lease['id']}/terminate",
        headers=s.h,
        json={"termination_date": "2018-12-20", "reason": "r"},
    )
    assert templates()[-1] == "lease_terminated"
    at(world, 2019, 1, 1)
    from app.domains.billing.jobs import overdue_sweep

    s2 = Setup(world, deposits=[], recurring_charges=[])
    opt_in(client, s2)
    s2.run("2018-12")
    at(world, 2019, 1, 10)
    overdue_sweep(world.c, s2.org.id)
    assert sent(world)[-1].template == "invoice_overdue"


def test_notifications_are_deleted_on_anonymisation_and_exported(
    world: World, client: TestClient
) -> None:
    from app.domains.privacy.anonymise import anonymise_tenant

    s = Setup(world, deposits=[], recurring_charges=[])
    opt_in(client, s)
    s.run("2018-12")
    export = client.get("/v1/privacy/export", headers=s.tenant_h).json()["data"]
    assert [n["template"] for n in export["notifications"]] and "provider_message_id" not in export[
        "notifications"
    ][0]
    client.post(
        f"/v1/leases/{s.lease['id']}/terminate",
        headers=s.h,
        json={"termination_date": "2018-12-20", "reason": "r"},
    )
    anonymise_tenant(world.c, s.org.id, s.tenant["id"])
    assert log(client, s) == []  # the log is deleted outright


def test_preferences_validation_and_isolation(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    other = Setup(world, deposits=[], recurring_charges=[])
    h = s.tenant_h
    assert client.get("/v1/notifications/preferences", headers=h).json() == {}
    for bad in (
        {"email": {}},
        {"email": {"enabled": True, "address": "nope"}},
        {"locale": "fr"},
        {"whatsapp": {"enabled": True, "number": "0123"}},
        {"sms": {"enabled": True}},
    ):
        assert (
            client.put("/v1/notifications/preferences", headers=h, json=bad).status_code == 422
        ), bad
    client.put(
        "/v1/notifications/preferences",
        headers=h,
        json={"locale": "ms", "email": {"enabled": True, "address": "me@example.com"}},
    )
    assert client.get("/v1/notifications/preferences", headers=other.tenant_h).json() == {}
    assert client.get("/v1/notifications/preferences", headers=s.h).status_code == 403
    assert client.put("/v1/notifications/preferences", headers=s.h, json={}).status_code == 403
    again = client.put(
        "/v1/notifications/preferences", headers=h, json={"email": {"enabled": False}}
    ).json()
    assert (
        again["locale"] == "ms" and again["email"]["enabled"] is False
    )  # untouched parts are kept


_ = timedelta
