"""Who gets what, on which channel (LLD 9.1, 9.2)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.dates import KL
from app.core.ids import new_id
from app.domains.notifications.templates import brand_wrap, render
from app.domains.organisations import branding
from app.integrations.channels import Message, TransientError
from app.repositories.dynamo import TxCancelled
from app.worker.envelope import Retryable, make_envelope

log = logging.getLogger(__name__)
Item = dict[str, Any]
PURPOSE = {"email": "email_messaging", "whatsapp": "whatsapp_messaging"}


def latest_consents(c: Container, org_id: str, tenant_id: str) -> dict[str, bool]:
    """The most recent record per purpose wins (an opt-out after an opt-in withdraws consent)."""
    seen: dict[str, bool] = {}
    cursor = None
    repo = c.store.tenants(org_id)
    while True:
        page, cursor = repo.consents(tenant_id, 100, cursor)  # newest first
        for rec in page:
            seen.setdefault(rec["purpose"], rec["granted"])
        if cursor is None:
            return seen


def address_for(tenant: Item, channel: str) -> str | None:
    prefs = tenant.get("notification_preferences") or {}
    ch = prefs.get(channel) or {}
    if not ch.get("enabled"):
        return None
    if channel == "email":
        return ch.get("address") or tenant.get("email")
    return ch.get("number") if ch.get("opted_in_at") else None  # WhatsApp needs an explicit opt-in


def channels_for(c: Container, org_id: str, tenant: Item) -> list[str]:
    consent = latest_consents(c, org_id, tenant["id"])
    return [
        ch
        for ch in ("email", "whatsapp")
        if address_for(tenant, ch) and consent.get(PURPOSE[ch]) and ch in c.channels
    ]


def in_quiet_hours(settings: Item, now: datetime) -> datetime | None:
    """If `now` falls in the org's quiet hours (Malaysian time), the moment they end, else None."""
    qh = settings.get("quiet_hours") or {}
    if not qh.get("start") or not qh.get("end"):
        return None
    local = now.astimezone(KL)
    cur = local.strftime("%H:%M")
    start, end = qh["start"], qh["end"]
    inside = start <= cur < end if start <= end else (cur >= start or cur < end)
    if not inside:
        return None
    h, m = (int(x) for x in end.split(":"))
    until = local.replace(hour=h, minute=m, second=0, microsecond=0)
    if until <= local:
        until += timedelta(days=1)
    return until.astimezone(now.tzinfo)


def notify(
    c: Container,
    org_id: str,
    template: str,
    tenant_ids: list[str],
    entity_id: str,
    params: dict[str, str],
    offset: str = "",
) -> int:
    """Create one log entry per (tenant, channel) and queue the sends. Returns how many were queued.
    The dedupe key makes a repeated event (retry, double sweep) a no-op."""
    repo = c.store.notifications(org_id)
    org = c.store.org(org_id).get_meta() or {}
    settings = org.get("settings") or {}
    queued = 0
    for tid in tenant_ids:
        tenant = c.store.tenants(org_id).get(tid)
        if tenant is None or tenant["status"] in ("former", "anonymised"):
            continue
        prefs = tenant.get("notification_preferences") or {}
        locale = prefs.get("locale") or settings.get("default_locale", "en")
        for channel in channels_for(c, org_id, tenant):
            now = c.clock.now()
            ts = iso(now)
            lid = new_id()
            defer = in_quiet_hours(settings, now)
            item = repo_log(org_id, lid, ts, tid, channel, template, locale, params, defer)
            try:
                c.store.table.transact(
                    [
                        repo.dedupe_op(f"{template}#{entity_id}#{channel}#{offset}#{tid}", now),
                        repo.create_op(item),
                    ]
                )
            except TxCancelled:
                continue  # already sent for this event
            if defer is None:
                _enqueue_send(c, org_id, item)
            queued += 1
    return queued


def repo_log(
    org_id: str,
    lid: str,
    ts: str,
    tid: str,
    channel: str,
    template: str,
    locale: str,
    params: dict[str, str],
    defer: datetime | None,
) -> Item:
    from app.repositories.notifications import log_item

    return log_item(
        org_id,
        lid,
        ts,
        {
            "tenant_id": tid,
            "channel": channel,
            "template": template,
            "locale": locale,
            "params": params,
            "deliver_after": iso(defer) if defer else None,
        },
    )


def _enqueue_send(c: Container, org_id: str, item: Item) -> None:
    c.jobs.enqueue(
        "notifications",
        make_envelope(
            "send_notification",
            org_id,
            {"month": item["created_at"][:7], "sk": item["SK"]},
            c.clock.now(),
        ),
    )


def release_deferred(c: Container, org_id: str) -> int:
    """Sends held back by quiet hours once they have ended (run every 15 minutes)."""
    repo = c.store.notifications(org_id)
    now = c.clock.now()
    n = 0
    for month in {iso(now)[:7], iso(now - timedelta(days=2))[:7]}:
        for it in repo.deferred(month, iso(now)):
            repo.put({**it, "deliver_after": None})
            _enqueue_send(c, org_id, {**it, "deliver_after": None})
            n += 1
    return n


def send(c: Container, org_id: str, month: str, sk: str) -> str:
    """Deliver one logged message. Idempotent: only a `queued` entry is ever sent."""
    repo = c.store.notifications(org_id)
    entry = repo.get(month, sk)
    if entry is None or entry["status"] != "queued":
        return "skipped"
    tenant = c.store.tenants(org_id).get(entry["tenant_id"])
    channel = c.channels.get(entry["channel"])
    if tenant is None or channel is None:
        repo.put({**entry, "status": "failed", "error": "channel_unavailable"})
        return "failed"
    # consent and opt-outs are checked again now, so an opt-out is honoured immediately
    address = address_for(tenant, entry["channel"])
    if not address or not latest_consents(c, org_id, tenant["id"]).get(PURPOSE[entry["channel"]]):
        repo.put({**entry, "status": "suppressed", "error": "opted_out"})
        return "suppressed"
    if entry["channel"] == "email" and repo.suppressed("email", address):
        repo.put({**entry, "status": "suppressed", "error": "address_suppressed"})
        return "suppressed"
    subject, text, html = render(entry["template"], entry["locale"], entry["params"])
    brand = branding.public(c.store.org(org_id).get_meta() or {})
    if brand and entry["channel"] == "email":
        base = (c.settings.public_api_url or c.settings.app_base_url).rstrip("/")
        logo = f"{base}{brand['logo_url']}" if brand.get("logo_url") else None
        text, html = brand_wrap(text, html, brand, entry["locale"], logo)
    try:
        pid = channel.send(
            Message(
                address,
                subject,
                text,
                html,
                entry["template"],
                entry["locale"],
                from_name=brand["display_name"] if brand else None,
                reply_to=brand.get("contact_email") if brand else None,
            )
        )
    except TransientError as e:
        raise Retryable(str(e)) from None
    except Exception as e:  # noqa: BLE001
        log.exception("send failed", extra={"org_id": org_id, "entity_id": entry["id"]})
        repo.put({**entry, "status": "failed", "error": type(e).__name__})
        return "failed"
    repo.put({**entry, "status": "sent", "provider_message_id": pid})
    repo.message_lookup_put(pid, month, sk)
    return "sent"


def handle_feedback(
    c: Container, provider_message_id: str, kind: str, permanent: bool, address: str
) -> str:
    """SES bounce or complaint: suppress the address for that org and mark the log (LLD 9.2)."""
    ref = c.store.table.get(f"NOTIFMSG#{provider_message_id}", "META")
    if ref is None:
        return "unknown_message"
    org_id = ref["org_id"]
    repo = c.store.notifications(org_id)
    # staff emails (welcome, plan ending) have a lookup but no tenant log entry
    entry = repo.get(ref["month"], ref["log_sk"]) if ref.get("log_sk") else None
    if kind == "Complaint" or permanent:
        repo.suppress_put("email", address, kind.lower(), iso(c.clock.now()))
    if entry is not None:
        repo.put(
            {
                **entry,
                "status": "failed" if kind == "Bounce" else "suppressed",
                "error": kind.lower(),
            }
        )
    return "suppressed" if kind == "Complaint" or permanent else "noted"
