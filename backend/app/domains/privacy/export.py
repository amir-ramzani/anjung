"""Data-subject export (LLD 13.1). Registry based: every entity type that carries the data-subject
index (GSI5) must have an entry here. tests/unit/test_privacy_registry.py fails when one is missing."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.domains.billing.service import invoice_to_api, ledger_to_api, payment_to_api
from app.domains.leases.service import to_api as lease_to_api

Item = dict[str, Any]
Ctx = dict[str, Any]


def _profile(item: Item, ctx: Ctx) -> Item:
    doc = item.get("identity_document")
    identity = None
    if doc:
        identity = {"type": doc["type"]}
        if ctx["reveal_identity"]:  # decrypted only for the subject's own request, over TLS
            identity["number"] = ctx["container"].encryptor.decrypt(
                doc["number_enc"],
                {
                    "org_id": item["org_id"],
                    "tenant_id": item["id"],
                    "field": "identity_document.number",
                },
            )
    return {
        "id": item["id"],
        "full_name": item["full_name"],
        "email": item.get("email"),
        "phone": item.get("phone"),
        "status": item["status"],
        "linked": item["linked"],
        "identity_document": identity,
        "notification_preferences": item.get("notification_preferences"),
        "created_at": item["created_at"],
    }


def _consent(item: Item, ctx: Ctx) -> Item:
    return {k: item[k] for k in ("purpose", "policy_version", "granted", "recorded_at", "source")}


def _document(item: Item, ctx: Ctx) -> Item:
    return {
        k: item[k]
        for k in (
            "id",
            "owner_type",
            "owner_id",
            "type",
            "filename",
            "content_type",
            "size_bytes",
            "status",
            "retention_class",
            "created_at",
            "created_by",
        )
    }


def _request(item: Item, ctx: Ctx) -> Item:
    return {
        k: item.get(k) for k in ("id", "type", "status", "received_at", "due_at", "completed_at")
    }


# entity_type -> (section of the export, mapper). `tenant_lease` is resolved to the lease itself.
def _maintenance(item: Item, ctx: Ctx) -> Item:
    return {
        k: item.get(k)
        for k in (
            "id",
            "area",
            "property_id",
            "unit_id",
            "tenant_id",
            "title",
            "description",
            "category",
            "priority",
            "status",
            "assigned_to",
            "attachment_ids",
            "created_at",
            "updated_at",
            "resolved_at",
        )
    }


def _notification(item: Item, ctx: Ctx) -> Item:
    return {
        "id": item["id"],
        "channel": item["channel"],
        "template": item["template"],
        "locale": item["locale"],
        "status": item["status"],
        "created_at": item["created_at"],
    }


REGISTRY: dict[str, tuple[str, Callable[[Item, Ctx], Item | None]]] = {
    "tenant": ("profile", _profile),
    "consent": ("consents", _consent),
    "document": ("documents", _document),
    "privacy_request": ("privacy_requests", _request),
    "maintenance_request": ("maintenance_requests", _maintenance),
    "notification_log": ("notifications", _notification),
    "tenant_lease": ("leases", lambda item, ctx: None),  # handled below: needs the lease record
}


def _own_signatures_only(lease: Item, tenant_id: str) -> Item:
    """One tenant's export never names co-tenants: keep only this tenant's signature."""
    agreement = lease.get("agreement")
    if agreement:
        agreement = {
            **agreement,
            "signatures": [s for s in agreement["signatures"] if s["tenant_id"] == tenant_id],
        }
    return {**lease, "agreement": agreement}


def build_export(c: Container, org_id: str, tenant_id: str, *, reveal_identity: bool) -> Item:
    repo = c.store.privacy(org_id)
    items = repo.subject_items(tenant_id)
    sections: dict[str, list[Item]] = defaultdict(list)
    ctx: Ctx = {"container": c, "reveal_identity": reveal_identity}
    lease_ids: list[str] = []
    for it in items:
        entry = REGISTRY.get(it["entity_type"])
        if entry is None:  # never silently drop personal data: fail loudly instead
            raise RuntimeError(f"entity type {it['entity_type']} is not registered for export")
        section, mapper = entry
        if it["entity_type"] == "tenant_lease":
            lease_ids.append(it["lease_id"])
            continue
        mapped = mapper(it, ctx)
        if mapped is not None:
            sections[section].append(mapped)
    leases = [x for x in c.store.leases(org_id).get_many(lease_ids) if x["status"] != "draft"]
    sections["leases"] = [_own_signatures_only(lease_to_api(x), tenant_id) for x in leases]
    # the evidence kept with their own agreement signatures (the lease API never shows the IP or browser)
    signing = [
        {
            "lease_id": lease["id"],
            "document_id": sig.get("document_id"),
            "method": sig["method"],
            "full_name": sig["full_name"],
            "signed_at": sig["signed_at"],
            "ip": sig.get("ip"),
            "user_agent": sig.get("user_agent"),
        }
        for lease in leases
        for sig in (lease.get("agreement") or {}).get("signatures", [])
        if sig["tenant_id"] == tenant_id
    ]
    # money records hang off the leases the tenant is on (co-tenants share a lease, so they share its ledger)
    billing = c.store.billing(org_id)
    for lease in leases:
        for inv in billing.all_invoices_of_lease(lease["id"]):
            sections["invoices"].append(invoice_to_api(inv))
            sections["payments"] += [
                payment_to_api(p) for p in billing.payments_of_invoice(inv["id"])
            ]
        sections["ledger"] += [ledger_to_api(e) for e in billing.all_ledger(lease["id"])]
    # comments hang off the request, not the subject index: take the ones on the tenant's own requests
    mr = c.store.maintenance(org_id)
    comments: list[Item] = []
    for req in sections["maintenance_requests"]:
        page, cur = mr.comments(req["id"], 100, None, lambda it: not it["internal"])
        while True:
            comments += [
                {
                    "request_id": req["id"],
                    "body": x["body"],
                    "author_role": x["author_role"],
                    "created_at": x["created_at"],
                }
                for x in page
            ]
            if cur is None:
                break
            page, cur = mr.comments(req["id"], 100, cur, lambda it: not it["internal"])
    consents = sorted(sections["consents"], key=lambda x: x["recorded_at"], reverse=True)
    data: Item = {
        # the contract has no section for requests: they travel inside the free-form profile
        "profile": {
            **(sections["profile"] or [{}])[0],
            "privacy_requests": sections["privacy_requests"],
            "maintenance_comments": comments,
            "agreement_signatures": signing,
        },
        "consents": consents,
        "leases": sections["leases"],
        "invoices": sections["invoices"],
        "payments": sections["payments"],
        "ledger": sections["ledger"],
        "maintenance_requests": sections["maintenance_requests"],
        "notifications": sections["notifications"],
        "documents": sorted(sections["documents"], key=lambda x: x["created_at"]),
    }
    return {
        "tenant_id": tenant_id,
        "generated_at": iso(c.clock.now()),
        "format_version": "1",
        "data": data,
    }
