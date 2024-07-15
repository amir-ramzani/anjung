"""Invoice runs: the daily sweep and manual runs share one code path (LLD 6.2, 8.4)."""

from __future__ import annotations

import logging
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.dates import today_kl
from app.core.ids import new_id
from app.domains.billing.common import Item, period_of
from app.domains.billing.invoicing import generate_for_lease
from app.repositories import keys

log = logging.getLogger(__name__)


def start_run(c: Container, org_id: str, period: str) -> Item:
    run: Item = {
        "PK": keys.org_pk(org_id),
        "SK": "",
        "entity_type": "invoice_run",
        "org_id": org_id,
        "id": new_id(),
        "period": period,
        "status": "running",
        "leases_considered": 0,
        "invoices_created": 0,
        "invoices_skipped": 0,
        "errors": [],
        "started_at": iso(c.clock.now()),
        "finished_at": None,
    }
    run["SK"] = keys.invoice_run_sk(period, str(run["id"]))
    c.store.billing(org_id).run_put(run)
    return run


def execute_run(
    c: Container, org_id: str, run: Item, *, only_period: str | None, lease_ids: list[str] | None
) -> Item:
    """Generate invoices for the org's active leases. A failing lease is recorded and skipped, the rest
    continue. Re-running is safe: INVKEY makes every (lease, period, kind) happen once."""
    repo = c.store.billing(org_id)
    meta = c.store.org(org_id).get_meta() or {}
    lead = (meta.get("settings") or {}).get("invoice_lead_days", 7)
    today = today_kl(c.clock)
    run = {**run}
    leases = [x for x in repo.active_leases() if not lease_ids or x["id"] in lease_ids]
    run["leases_considered"] = len(leases)
    for (
        lease
    ) in leases:  # sequential within an org: the invoice counter serialises numbering anyway
        try:
            created, skipped = generate_for_lease(c, org_id, lease, today, lead, only_period)
            run["invoices_created"] += created
            run["invoices_skipped"] += skipped
        except Exception as e:  # noqa: BLE001 - one bad lease must not stop the run
            log.exception(
                "invoice generation failed", extra={"org_id": org_id, "entity_id": lease["id"]}
            )
            run["errors"].append({"lease_id": lease["id"], "message": type(e).__name__})
    run["status"] = "completed_with_errors" if run["errors"] else "completed"
    run["finished_at"] = iso(c.clock.now())
    repo.run_put(run)
    if run[
        "errors"
    ]:  # the "invoice-run-errors" alarm filters on this message (infra, observability stack)
        log.error(
            "invoice run completed with errors", extra={"org_id": org_id, "entity_id": run["id"]}
        )
    return run


def sweep_org(c: Container, org_id: str) -> Item:
    """The daily 02:00 job for one org."""
    run = start_run(c, org_id, period_of(today_kl(c.clock)))
    return execute_run(c, org_id, run, only_period=None, lease_ids=None)


def run_to_api(item: Item) -> dict[str, Any]:
    return {
        k: item[k]
        for k in (
            "id",
            "period",
            "status",
            "leases_considered",
            "invoices_created",
            "invoices_skipped",
            "errors",
            "started_at",
            "finished_at",
        )
    }
