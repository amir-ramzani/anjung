"""SES feedback arriving on the notifications queue (LLD 9.2).

SES publishes bounces and complaints from the configuration set to an SNS topic, which delivers them to the
notifications queue as they are (raw delivery). They are not Anjung envelopes, so the runner turns each one into
one `ses_feedback` job per recipient here.
"""

from __future__ import annotations

import json
from typing import Any

from app.worker.envelope import Envelope

KINDS = ("Bounce", "Complaint")


def _unwrap(body: dict[str, Any]) -> dict[str, Any]:
    """Without raw delivery, SNS wraps the event: {"Type": "Notification", "Message": "<json>"}."""
    if body.get("Type") == "Notification" and isinstance(body.get("Message"), str):
        inner = json.loads(body["Message"])
        return inner if isinstance(inner, dict) else {}
    return body


def is_ses_event(body: dict[str, Any]) -> bool:
    body = _unwrap(body)
    return (body.get("eventType") or body.get("notificationType")) in KINDS and "mail" in body


def envelopes(body: dict[str, Any]) -> list[Envelope]:
    """One `ses_feedback` job per bounced or complaining recipient. Event publishing (configuration set)
    uses `eventType`, identity notifications `notificationType`; both carry the same fields."""
    body = _unwrap(body)
    kind = body.get("eventType") or body.get("notificationType")
    pid = str(body["mail"]["messageId"])
    if kind == "Bounce":
        b = body.get("bounce") or {}
        permanent = b.get("bounceType") == "Permanent"  # Transient and Undetermined: keep sending
        recipients = b.get("bouncedRecipients") or []
        when = b.get("timestamp") or body["mail"].get("timestamp", "")
    else:
        b = body.get("complaint") or {}
        permanent = True
        recipients = b.get("complainedRecipients") or []
        when = b.get("timestamp") or body["mail"].get("timestamp", "")
    return [
        Envelope(
            type="ses_feedback",
            org_id="",  # the message lookup finds the organisation
            payload={
                "provider_message_id": pid,
                "kind": kind,
                "permanent": permanent,
                "address": str(r["emailAddress"]).lower(),
            },
            occurred_at=str(when),
        )
        for r in recipients
        if r.get("emailAddress")
    ]
