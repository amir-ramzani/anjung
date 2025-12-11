"""SES bounces and complaints reach the app through SNS and the notifications queue (LLD 9.2)."""

import json
from typing import Any

from app.worker import ses_events
from app.worker.handlers.housekeeping import build_handlers
from app.worker.runner import Runner
from fastapi.testclient import TestClient

from tests.conftest import World
from tests.unit.test_worker import FakeSqs


def bounce(pid: str, address: str, kind: str = "Permanent") -> dict[str, Any]:
    """An SES event as the configuration set publishes it (raw delivery: no SNS wrapper)."""
    return {
        "eventType": "Bounce",
        "mail": {"messageId": pid, "timestamp": "2018-11-26T01:00:00.000Z"},
        "bounce": {
            "bounceType": kind,
            "bounceSubType": "General",
            "bouncedRecipients": [{"emailAddress": address}],
            "timestamp": "2018-11-26T01:00:05.000Z",
        },
    }


def complaint(pid: str, address: str) -> dict[str, Any]:
    return {
        "eventType": "Complaint",
        "mail": {"messageId": pid},
        "complaint": {"complainedRecipients": [{"emailAddress": address}]},
    }


def deliver(world: World, *events: dict[str, Any]) -> FakeSqs:
    sqs = FakeSqs()
    for e in events:
        sqs.send_message("q", json.dumps(e))
    Runner(sqs, "q", "dlq", build_handlers(world.c), concurrency=1, wait_seconds=0).poll_once()
    return sqs


def last_id(world: World) -> str:
    return str(world.c.channels["email"].ids[-1])  # type: ignore[attr-defined]


def test_events_become_one_job_per_recipient() -> None:
    e = bounce("m1", "A@Example.com")
    e["bounce"]["bouncedRecipients"].append({"emailAddress": "b@example.com"})
    jobs = ses_events.envelopes(e)
    assert [j.type for j in jobs] == ["ses_feedback", "ses_feedback"]
    assert jobs[0].payload == {
        "provider_message_id": "m1",
        "kind": "Bounce",
        "permanent": True,
        "address": "a@example.com",
    }
    assert (
        ses_events.envelopes(bounce("m1", "a@example.com", "Transient"))[0].payload["permanent"]
        is False
    )
    assert ses_events.envelopes(complaint("m2", "c@example.com"))[0].payload["permanent"] is True
    # without raw delivery SNS wraps the event; identity notifications say notificationType
    wrapped = {"Type": "Notification", "Message": json.dumps(complaint("m2", "c@example.com"))}
    assert ses_events.is_ses_event(wrapped) and len(ses_events.envelopes(wrapped)) == 1
    legacy = {**bounce("m3", "d@example.com"), "notificationType": "Bounce"}
    del legacy["eventType"]
    assert ses_events.is_ses_event(legacy)
    assert not ses_events.is_ses_event({"eventType": "Delivery", "mail": {}})
    assert not ses_events.is_ses_event({"type": "job", "org_id": "x"})


def test_a_bounced_tenant_welcome_email_suppresses_the_address(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    t = org.new_tenant("gone@example.com")
    r = client.post(f"/v1/tenants/{t['id']}/invite", headers=org.admin, json={"channel": "email"})
    assert r.status_code == 202
    sqs = deliver(world, bounce(last_id(world), "gone@example.com"))
    assert sqs.queues["q"] == [] and sqs.queues["dlq"] == []
    log = client.get("/v1/notifications/log", headers=org.admin).json()["items"]
    entry = next(e for e in log if e["template"] == "tenant_invite")
    assert entry["status"] == "failed" and entry["error"] == "bounce"
    again = client.post(
        f"/v1/tenants/{t['id']}/invite", headers=org.admin, json={"channel": "email"}
    )
    assert again.status_code == 409 and again.json()["code"] == "address_suppressed"


def test_a_soft_bounce_changes_nothing(world: World, client: TestClient) -> None:
    org = world.make_org()
    t = org.new_tenant("full@example.com")
    client.post(f"/v1/tenants/{t['id']}/invite", headers=org.admin, json={"channel": "email"})
    deliver(world, bounce(last_id(world), "full@example.com", "Transient"))
    assert not world.c.store.notifications(org.id).suppressed("email", "full@example.com")


def test_a_complaint_about_a_staff_welcome_email_suppresses_the_address(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    r = client.post(
        "/v1/users/invite",
        headers=org.admin,
        json={"email": "spam@example.com", "name": "Hafiz", "roles": ["property_manager"]},
    )
    uid = r.json()["user_id"]
    deliver(world, complaint(last_id(world), "spam@example.com"))
    assert world.c.store.notifications(org.id).suppressed("email", "spam@example.com")
    resend = client.post(f"/v1/users/{uid}/invite", headers=org.admin)
    assert resend.status_code == 409 and resend.json()["code"] == "address_suppressed"


def test_unknown_messages_are_dropped_quietly(world: World) -> None:
    sqs = deliver(world, bounce("never-sent", "x@example.com"))
    assert sqs.queues["q"] == [] and sqs.queues["dlq"] == []  # not poison: nothing to retry
