"""Notification job handlers: fan-out of events, delivery, quiet-hour release, SES feedback and reminders."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.container import Container
from app.core.dates import today_kl
from app.core.money import Money
from app.domains.billing.common import Item
from app.domains.notifications import service as ns
from app.repositories.dynamo import TxCancelled
from app.worker.envelope import Envelope, Fatal, Retryable
from app.worker.queue import Handler


def link(c: Container, path: str) -> str:
    return f"{c.settings.app_base_url.rstrip('/')}{path}"


def invoice_params(c: Container, inv: Item) -> dict[str, str]:
    return {
        "number": inv["number"],
        "amount": Money(max(inv["total_minor"] - inv["paid_minor"], 0)).format(),
        "due_date": inv["due_date"],
        "pay_link": link(c, f"/pay/{inv['id']}"),  # a link, never a QR image
    }


def notify_event(c: Container) -> Handler:
    def handle(env: Envelope) -> None:
        p = env.payload
        tpl, entity = p["template"], p["entity_id"]
        params: dict[str, str] = {k: str(v) for k, v in p.get("params", {}).items()}
        org = env.org_id
        if tpl in ("invoice_issued", "payment_reminder", "invoice_overdue", "payment_received"):
            inv = c.store.billing(org).get_invoice(entity)
            if inv is None:
                raise Fatal("invoice not found")
            params = {**invoice_params(c, inv), **params}
            if tpl == "payment_received":
                params["receipt_link"] = link(c, f"/invoices/{entity}/receipt")
        elif tpl == "maintenance_update":
            params["link"] = link(c, f"/maintenance/{entity}")
        elif tpl in ("lease_activated", "lease_terminated", "lease_agreement_sent"):
            params["link"] = link(c, f"/leases/{entity}")
        elif tpl == "privacy_request_update":
            params["link"] = link(c, f"/privacy/requests/{entity}")
        try:
            ns.notify(c, org, tpl, p["tenant_ids"], entity, params, p.get("offset", ""))
        except TxCancelled as e:
            raise Retryable(str(e)) from None

    return handle


def send_notification(c: Container) -> Handler:
    def handle(env: Envelope) -> None:
        ns.send(c, env.org_id, env.payload["month"], env.payload["sk"])

    return handle


def release_deferred(c: Container) -> Handler:
    def handle(env: Envelope) -> None:
        ns.release_deferred(c, env.org_id)

    return handle


def ses_feedback(c: Container) -> Handler:
    def handle(env: Envelope) -> None:
        p = env.payload
        ns.handle_feedback(
            c, p["provider_message_id"], p["kind"], p.get("permanent", True), p["address"]
        )

    return handle


def reminder_sweep(c: Container, org_id: str) -> int:
    """Reminders at the org's offsets around the due date (negative = before). One per invoice and
    offset, whatever the number of sweeps, and always with a pay link (LLD 9, plan P4-07)."""
    meta = c.store.org(org_id).get_meta() or {}
    offsets = set((meta.get("settings") or {}).get("reminder_offsets_days", [-3, 0, 3]))
    today = today_kl(c.clock)
    sent = 0
    far = date.fromisoformat(str(today)).toordinal() + 31
    for inv in c.store.billing(org_id).unpaid_due_on_or_before(str(date.fromordinal(far))):
        offset = (today - date.fromisoformat(inv["due_date"])).days
        if offset in offsets and inv["tenant_ids"]:
            tpl = "payment_reminder" if offset <= 0 else "invoice_overdue"
            sent += ns.notify(
                c,
                org_id,
                tpl,
                inv["tenant_ids"],
                inv["id"],
                invoice_params(c, inv),
                offset=f"reminder{offset:+d}",
            )
    return sent


def reminder_job(c: Container) -> Handler:
    def handle(env: Envelope) -> None:
        reminder_sweep(c, env.org_id)

    return handle


def build(c: Container, fan_out: Any) -> dict[str, Handler]:
    return {
        "notify": notify_event(c),
        "send_notification": send_notification(c),
        "release_deferred": release_deferred(c),
        "release_deferred_all": fan_out(c, "release_deferred", "notifications"),
        "ses_feedback": ses_feedback(c),
        "reminder_sweep": reminder_job(c),
        "reminder_sweep_all": fan_out(c, "reminder_sweep", "notifications"),
    }
