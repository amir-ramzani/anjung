"""Retention purge (contract v0.9.0, plan C L-05): delete records once their retention period has passed.

PDPA's retention principle says personal data must not be kept longer than its purpose needs. Financial records
(leases, invoices, payments, the ledger, receipts and agreements) are kept for a period counsel has not set yet
(decision D5), so the periods are configuration and **both default to off**:

* `RETENTION_FINANCIAL_DAYS`: an ended lease whose end date is older than this, with nothing owed either way, is
  deleted with all its billing records and files. Then anonymised tenants with no lease left, and inactive
  properties with no lease left, are deleted too.
* `RETENTION_CLOSED_ORG_DAYS`: a closed organisation older than this is deleted entirely (platform job).

Every run can be a dry run, which reports what would go and changes nothing. A run is safe to repeat.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.dates import today_kl
from app.domains.lifecycle import (
    INACTIVE,
    documents_of,
    keys_of,
    partition,
    remove_files,
    unit_lock_keys,
    write_all,
)
from app.repositories import keys
from app.repositories.dynamo import Delete
from app.repositories.properties import label_scope

log = logging.getLogger(__name__)
Item = dict[str, Any]
SYSTEM = "system:retention"


def _all(fetch: Any) -> list[Item]:
    out: list[Item] = []
    cursor = None
    while True:
        page, cursor = fetch(cursor)
        out += page
        if cursor is None:
            return out


def _ended_on(lease: Item) -> str | None:
    return lease.get("terminated_at") or lease.get("end_date")


def _lease_records(c: Container, org_id: str, lease: Item) -> tuple[list[Any], list[Item]]:
    """Every table item and file that belongs to one lease and its billing."""
    lid = lease["id"]
    items = partition(c, keys.lease_pk(org_id, lid))  # invoices, invoice keys, ledger
    ops: list[Any] = keys_of(items)
    docs = documents_of(c, org_id, "lease", lid)
    for inv in (i for i in items if i["entity_type"] == "invoice"):
        under = partition(c, keys.invoice_pk(org_id, inv["id"]))  # payments, allocations
        ops += keys_of(under)
        ops += [
            Delete(key=(keys.payref_pk(p["provider"], p["gateway_reference"]), keys.META))
            for p in under
            if p["entity_type"] == "payment" and p.get("provider") and p.get("gateway_reference")
        ]
        docs += documents_of(c, org_id, "invoice", inv["id"])  # receipts
    ops += [Delete(key=(lease["PK"], lease["SK"]))]
    ops += [c.store.leases(org_id).link_delete_op(t, lid) for t in lease["tenant_ids"]]
    ops += keys_of(docs)
    return ops, docs


def purge_org(c: Container, org_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    days = c.settings.retention_financial_days
    report: dict[str, Any] = {
        "enabled": days is not None,
        "dry_run": dry_run,
        "leases": [],
        "tenants": [],
        "properties": [],
        "kept_owing": [],
    }
    if days is None:
        return report
    cutoff = str(today_kl(c.clock) - timedelta(days=days))
    leases_repo = c.store.leases(org_id)
    leases = _all(lambda cur: leases_repo.page(100, cur, None))
    ops: list[Any] = []
    files: list[Item] = []
    gone: set[str] = set()
    for lease in leases:
        ended = _ended_on(lease)
        if lease["status"] not in ("terminated", "expired") or not ended or ended >= cutoff:
            continue
        if lease.get("balance_minor", 0) or lease.get("credit_minor", 0):
            report["kept_owing"].append(lease["id"])  # money still open: a person decides first
            continue
        lease_ops, docs = _lease_records(c, org_id, lease)
        ops += lease_ops
        files += docs
        gone.add(lease["id"])
        report["leases"].append(lease["id"])

    remaining = [x for x in leases if x["id"] not in gone]
    # anonymised tenants with no lease left: nothing to keep them for
    still_linked = {t for x in remaining for t in x["tenant_ids"]}
    tenants = c.store.tenants(org_id)
    privacy = c.store.privacy(org_id)
    for t in _all(lambda cur: tenants.page(100, cur, "anonymised", None)):  # noqa: B023
        if t["id"] in still_linked:
            continue
        items = privacy.subject_items(t["id"])
        ops += keys_of(items)
        files += [i for i in items if i["entity_type"] == "document"]
        report["tenants"].append(t["id"])

    # inactive properties whose units have no lease left and no maintenance history
    in_use = {x["unit_id"] for x in remaining}
    props = c.store.properties(org_id)
    units_repo = c.store.units(org_id)
    requests = _all(lambda cur: c.store.maintenance(org_id).page_all(100, cur, None))
    for p in _all(lambda cur: props.page(100, cur, INACTIVE)):
        units = units_repo.all_for_property(p["id"])
        ids = {u["id"] for u in units}
        if ids & in_use or any(
            r.get("unit_id") in ids or r.get("property_id") == p["id"] for r in requests
        ):
            continue
        docs = documents_of(c, org_id, "property", p["id"])
        for u in units:
            docs += documents_of(c, org_id, "unit", u["id"])
        ops += [Delete(key=(p["PK"], p["SK"])), *keys_of(units), *keys_of(docs)]
        ops += [units_repo.label_unlock_op(label_scope(u), u["label"]) for u in units]
        ops += unit_lock_keys(org_id, ids)
        files += docs
        report["properties"].append(p["id"])

    report["deleted_items"] = len(ops)
    report["deleted_files"] = len(files)
    if dry_run or not ops:
        return report
    audit = c.store.audit(org_id)
    now = c.clock.now()
    write_all(
        c,
        [
            audit.put_op(
                actor_id=SYSTEM,
                actor_role="system",
                action="retention.purge",
                entity_type="organisation",
                entity_id=org_id,
                summary={
                    "leases": len(report["leases"]),
                    "tenants": len(report["tenants"]),
                    "properties": len(report["properties"]),
                    "retention_days": days,
                },
                now=now,
                request_id="",
                ip=None,
            ),
            *ops,
        ],
    )
    remove_files(c, files)
    log.info(
        "retention purge",
        extra={"org_id": org_id, "error_code": f"leases={len(report['leases'])}"},
    )
    return report


def purge_closed_orgs(c: Container, *, dry_run: bool = False) -> dict[str, Any]:
    """Delete organisations closed for longer than RETENTION_CLOSED_ORG_DAYS: every item they own, their files
    and their users' logins. Run by the platform; nobody inside a closed organisation can sign in anyway."""
    days = c.settings.retention_closed_org_days
    report: dict[str, Any] = {"enabled": days is not None, "dry_run": dry_run, "organisations": []}
    if days is None:
        return report
    cutoff = iso(c.clock.now() - timedelta(days=days))
    platform = c.store.platform()
    for reg in _all(lambda cur: platform.list_registry(100, cur, "closed")):
        org_id = reg["org_id"]
        meta = c.store.org(org_id).get_meta() or {}
        if org_id == c.settings.platform_org_id or meta.get("updated_at", "") >= cutoff:
            continue
        items = c.store.table.scan_org(org_id)
        subs = [
            i.get("user_id")
            if i["entity_type"] == "membership"
            else (i.get("cognito_sub") or i.get("pending_sub"))
            for i in items
            if i["entity_type"] in ("membership", "tenant")
        ]
        files = [i for i in items if i["entity_type"] == "document"]
        report["organisations"].append(
            {"org_id": org_id, "items": len(items), "files": len(files), "logins": len(subs)}
        )
        if dry_run:
            continue
        write_all(
            c, [*keys_of(items), Delete(key=(reg["PK"], reg["SK"]))]
        )  # write_all drops repeats
        remove_files(c, files)
        for sub in filter(None, subs):
            try:
                c.identity.delete_user(sub)
            except Exception:  # noqa: BLE001
                log.warning("could not delete a login of a closed organisation")
    return report
