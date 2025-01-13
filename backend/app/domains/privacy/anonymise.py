"""Tenant anonymisation (LLD 13.2). Idempotent, resumable and dry-run capable.

Personal fields are overwritten. Financial records (leases, ledger, invoices) keep amounts, dates and
IDs. Documents under a financial retention hold are kept (renamed) until the retention period ends.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.errors import InvalidState, NotFound
from app.repositories import keys
from app.repositories.dynamo import Delete, Put

log = logging.getLogger(__name__)
Item = dict[str, Any]
REMOVED = "[removed]"


def anonymous_name(tenant_id: str) -> str:
    return f"Anonymised tenant {hashlib.sha256(tenant_id.encode()).hexdigest()[:6]}"


def anonymise_tenant(
    c: Container, org_id: str, tenant_id: str, *, dry_run: bool = False
) -> dict[str, Any]:
    repo = c.store.privacy(org_id)
    tenants = c.store.tenants(org_id)
    tenant = tenants.get(tenant_id)
    if tenant is None:
        raise NotFound()
    items = repo.subject_items(tenant_id)
    leases = c.store.leases(org_id).get_many(
        [i["lease_id"] for i in items if i["entity_type"] == "tenant_lease"]
    )
    if any(x["status"] in ("draft", "active") for x in leases):
        raise InvalidState("End the tenant's active or draft leases before anonymising")

    now = iso(c.clock.now())
    report: dict[str, Any] = {
        "anonymised": [],
        "held": [],
        "deleted_objects": 0,
        "dry_run": dry_run,
    }
    ops: list[Any] = []
    s3_deletes: list[str] = []
    renames: list[tuple[str, str]] = []

    # 1. the tenant record
    if tenant["status"] != "anonymised":
        new = {
            k: v
            for k, v in tenant.items()
            if k
            not in (
                "email",
                "phone",
                "identity_document",
                "notification_preferences",
                "pending_sub",
                "cognito_sub",
                "GSI2PK",
                "GSI2SK",
                "invited_at",
                "invite_token_pk",
            )
        }
        new.update(
            full_name=anonymous_name(tenant_id),
            status="anonymised",
            linked=False,
            anonymised_at=now,
            version=tenant["version"] + 1,
            updated_at=now,
        )
        ops.append(tenants.replace_op(new, tenant["version"]))
        ops.append(tenants.email_unlock_op(tenant["email"]))  # the address can be used again
        report["anonymised"].append("tenant")
    # 2. things around it
    for it in items:
        et = it["entity_type"]
        if et == "document":
            if it["retention_class"] == "lease_financial":
                if it["filename"] != REMOVED:
                    # the object key carries the filename too: move the file to a neutral key
                    neutral = it["s3_key"].rsplit("/", 1)[0] + "/removed"
                    renames.append((it["s3_key"], neutral))
                    ops.append(
                        Put(
                            item={
                                **it,
                                "filename": REMOVED,
                                "s3_key": neutral,
                                "version": it["version"] + 1,
                                "updated_at": now,
                            }
                        )
                    )
                report["held"].append(it["id"])
            else:
                ops.append(Delete(key=(it["PK"], it["SK"])))
                s3_deletes.append(it["s3_key"])
        elif et == "notification_log":
            ops.append(Delete(key=(it["PK"], it["SK"])))  # the log is deleted outright (LLD 13.2)
        elif et == "maintenance_request":
            if it["title"] != REMOVED:
                ops.append(
                    Put(
                        item={
                            **it,
                            "title": REMOVED,
                            "description": REMOVED,
                            "version": it["version"] + 1,
                            "updated_at": now,
                        }
                    )
                )
            ops += _scrub_comments(c, org_id, it["id"], now)
        elif et == "privacy_request" and it.get("details") not in (None, REMOVED):
            ops.append(Put(item={**it, "details": REMOVED, "version": it["version"] + 1}))
    for lease in leases:
        changes: dict[str, Any] = {}
        if lease.get("termination_reason") not in (None, REMOVED):
            changes["termination_reason"] = REMOVED
        if lease.get("vehicle_plate"):  # parking bay leases (contract v0.10.0)
            changes["vehicle_plate"] = None
        agreement = lease.get("agreement")
        if agreement and any(  # anything still to scrub (safe to repeat)
            s["tenant_id"] == tenant_id and _scrub_signature(s, tenant_id) != s
            for s in agreement.get("signatures", [])
        ):
            # the signature stays as proof the lease was accepted; who typed it, from where, does not
            changes["agreement"] = {
                **agreement,
                "signatures": [
                    _scrub_signature(s, tenant_id) if s["tenant_id"] == tenant_id else s
                    for s in agreement["signatures"]
                ],
            }
        if changes:
            ops.append(
                Put(
                    item={
                        **lease,
                        **changes,
                        "version": lease["version"] + 1,
                        "updated_at": now,
                    }
                )
            )
        if "agreement" in changes:
            report["anonymised"].append(f"agreement_signature:{lease['id']}")
    if tenant.get("pending_sub"):
        ops.append(Delete(key=(keys.invite_pk(tenant["pending_sub"]), keys.META)))
    if tenant.get("invite_token_pk"):  # an unused set-password link: it holds the email
        ops.append(Delete(key=(tenant["invite_token_pk"], keys.META)))
    report["deleted_objects"] = len(s3_deletes)

    if dry_run:
        report["operations"] = len(ops)
        return report
    for old_key, new_key in renames:
        if c.storage:
            c.storage.rename(old_key, new_key)  # before the record points at it; safe to repeat
    ops.append(
        c.store.audit(org_id).put_op(
            actor_id="system",
            actor_role="system",
            action="privacy.anonymise",
            entity_type="tenant",
            entity_id=tenant_id,
            summary={"held_documents": len(report["held"])},
            now=c.clock.now(),
            request_id="job",
            ip=None,
        )
    )
    for i in range(0, len(ops), 90):  # transaction limit; every operation is safe to repeat
        c.store.table.transact(ops[i : i + 90])
    for key in s3_deletes:
        if c.storage:
            c.storage.delete(key)
    sub = tenant.get("cognito_sub")
    if sub:
        c.identity.set_enabled(sub, False)
        c.identity.global_sign_out(sub)
        c.identity.delete_user(sub)
    log.info("tenant anonymised", extra={"org_id": org_id, "entity_id": tenant_id})
    return report


def _scrub_signature(sig: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    kept = {k: v for k, v in sig.items() if k not in ("ip", "user_agent", "note")}
    return {**kept, "full_name": anonymous_name(tenant_id)}


def _scrub_comments(c: Container, org_id: str, request_id: str, now: str) -> list[Any]:
    """Every comment on the tenant's requests may quote them, so all bodies go (LLD 13.2)."""
    repo = c.store.maintenance(org_id)
    out: list[Any] = []
    cursor = None
    while True:
        page, cursor = repo.comments(request_id, 100, cursor, lambda it: it["body"] != REMOVED)
        out += [Put(item={**x, "body": REMOVED}) for x in page]
        if cursor is None:
            return out
