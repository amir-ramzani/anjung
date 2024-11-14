from __future__ import annotations

from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.context import RequestContext, Role
from app.core.errors import (
    AppError,
    DocumentNotClean,
    Forbidden,
    InvalidState,
    NotFound,
    ValidationFailed,
)
from app.core.ids import new_id
from app.domains.documents.schemas import UploadUrlRequest
from app.domains.scope import Scope
from app.integrations.storage import DocumentStorage, object_key, sanitise_filename
from app.repositories.documents import document_item

STAFF = {Role.ORG_ADMIN, Role.PROPERTY_MANAGER}
RETENTION = {
    "lease_agreement": "lease_financial",
    "identity": "lease_financial",
    "receipt": "lease_financial",
    "inspection_report": "operational",
    "maintenance_photo": "operational",
    "other": "operational",
    "logo": "operational",
}
# tenants may attach only these types, only to their own tenant record or lease
TENANT_TYPES = {"identity", "other", "maintenance_photo"}


class StorageNotConfigured(AppError):
    status_code, code, default_message = 503, "internal_error", "Document storage is not configured"


class DocumentService:
    def __init__(self, c: Container, ctx: RequestContext) -> None:
        self._c, self._ctx = c, ctx
        self._docs = c.store.documents(ctx.org_id)
        self._audit = c.store.audit(ctx.org_id)
        self._scope = Scope(c, ctx)

    @property
    def _storage(self) -> DocumentStorage:
        if self._c.storage is None:
            raise StorageNotConfigured()
        return self._c.storage

    @property
    def _is_staff(self) -> bool:
        return bool(self._ctx.roles & STAFF)

    # ---- ownership: tenants reach only their own tenant record and leases
    def _owner_exists(self, owner_type: str, owner_id: str) -> bool:
        """The owner exists and, for a property manager, is within their scope (else it looks missing)."""
        org = self._ctx.org_id
        s = self._c.store
        sc = self._scope
        if owner_type == "tenant":
            t = s.tenants(org).get(owner_id)
            return t is not None and sc.tenant_ok(t)
        if owner_type == "lease":
            lease = s.leases(org).get(owner_id)
            return lease is not None and sc.lease_ok(lease)
        if owner_type == "unit":
            u = s.units(org).get(owner_id)
            return u is not None and sc.property_ok(u["property_id"])
        if owner_type == "property":
            return s.properties(org).get(owner_id) is not None and sc.property_ok(owner_id)
        if owner_type == "maintenance_request":
            req = s.maintenance(org).get(owner_id)
            return req is not None and sc.property_ok(req.get("property_id"))
        if owner_type == "invoice":
            inv = s.billing(org).get_invoice(owner_id)
            return inv is not None and sc.invoice_ok(inv)
        if owner_type == "organization":  # the logo: only the organisation itself, admins upload it
            return owner_id == org and Role.ORG_ADMIN in self._ctx.roles
        raise ValidationFailed(
            details=[
                {
                    "field": "owner_type",
                    "issue": f"{owner_type} documents are not available yet",
                    "code": "owner_type_not_available",
                    "params": {"owner_type": owner_type},
                }
            ]
        )

    def _tenant_owns(self, owner_type: str, owner_id: str) -> bool:
        tid = self._ctx.tenant_id
        if tid is None:
            return False
        if owner_type == "tenant":
            return owner_id == tid
        if owner_type == "lease":
            lease = self._c.store.leases(self._ctx.org_id).get(owner_id)
            visible = lease is not None and (
                lease["status"] != "draft"
                # a draft becomes visible once its agreement is sent for them to read and sign
                or (lease.get("agreement") or {}).get("status", "not_sent") != "not_sent"
            )
            return visible and tid in lease["tenant_ids"]  # type: ignore[index]
        if owner_type == "maintenance_request":
            req = self._c.store.maintenance(self._ctx.org_id).get(owner_id)
            return req is not None and req.get("tenant_id") == tid
        if owner_type == "invoice":
            inv = self._c.store.billing(self._ctx.org_id).get_invoice(owner_id)
            return inv is not None and tid in inv["tenant_ids"]
        return False

    def _can_access(self, doc: dict[str, Any]) -> bool:
        return self._is_staff or self._tenant_owns(doc["owner_type"], doc["owner_id"])

    def get(self, did: str) -> dict[str, Any]:
        doc = self._docs.get(did)
        if doc is None or (
            self._scope.restricted and not self._owner_exists(doc["owner_type"], doc["owner_id"])
        ):
            raise NotFound()
        if not self._can_access(doc):
            raise Forbidden()
        return doc

    # ---- list
    def page(
        self, owner_type: str, owner_id: str, limit: int, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        if not self._owner_exists(owner_type, owner_id):
            raise NotFound()  # also what another organisation's owner looks like
        if not self._is_staff and not self._tenant_owns(owner_type, owner_id):
            raise Forbidden()
        return self._docs.page_for_owner(owner_type, owner_id, limit, cursor)

    # ---- upload
    def create_upload(self, body: UploadUrlRequest) -> tuple[dict[str, Any], dict[str, Any], Any]:
        if not self._owner_exists(body.owner_type, body.owner_id):
            raise NotFound()
        if (body.type == "logo") != (body.owner_type == "organization"):
            raise ValidationFailed(
                details=[
                    {
                        "field": "type",
                        "issue": "a logo belongs to the organisation, and only a logo",
                    }
                ]
            )
        if body.type == "logo" and (
            body.content_type == "application/pdf" or body.size_bytes > 512 * 1024
        ):
            raise ValidationFailed(
                details=[
                    {
                        "field": "content_type",
                        "issue": "a logo is a PNG, JPEG or WebP image of at most 512 KB",
                    }
                ]
            )
        if not self._is_staff and (
            body.type not in TENANT_TYPES or not self._tenant_owns(body.owner_type, body.owner_id)
        ):
            raise Forbidden()
        now = self._c.clock.now()
        did = new_id()
        key = object_key(self._ctx.org_id, body.owner_type, body.owner_id, did, body.filename)
        post, expires_at = self._storage.presign_upload(key, body.content_type, body.size_bytes)
        item = document_item(
            self._ctx.org_id,
            did,
            {
                "owner_type": body.owner_type,
                "owner_id": body.owner_id,
                "type": body.type,
                "filename": sanitise_filename(body.filename),
                "content_type": body.content_type,
                "size_bytes": body.size_bytes,
                "retention_class": RETENTION[body.type],
                "s3_key": key,
                "created_by": self._ctx.user_id,
            },
            iso(now),
        )
        self._c.store.table.transact(
            [
                self._docs.create_op(item),
                self._audit.put_for(
                    self._ctx,
                    now,
                    "document.upload_url",
                    "document",
                    did,
                    {"owner_type": body.owner_type, "type": body.type},
                ),
            ]
        )
        return item, post, expires_at

    # ---- download
    def download(self, did: str) -> dict[str, Any]:
        doc = self.get(did)
        if doc["status"] != "clean":
            raise DocumentNotClean()
        url, expires_at = self._storage.presign_download(
            doc["s3_key"], doc["filename"], doc["content_type"]
        )
        return {
            "url": url,
            "expires_at": expires_at,
            "filename": doc["filename"],
            "content_type": doc["content_type"],
        }

    # ---- delete
    def delete(self, did: str) -> None:
        doc = self.get(did)
        if doc["retention_class"] == "lease_financial":
            # TODO(D5): lift once counsel signs off retention periods; until then financial records are held
            raise InvalidState("This document is under a retention hold")
        now = self._c.clock.now()
        self._c.store.table.transact(
            [
                self._docs.delete_op(did),
                self._audit.put_for(
                    self._ctx,
                    now,
                    "document.delete",
                    "document",
                    did,
                    {"owner_type": doc["owner_type"], "type": doc["type"]},
                ),
            ]
        )
        self._storage.delete(doc["s3_key"])
