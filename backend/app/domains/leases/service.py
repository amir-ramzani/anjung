from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.concurrency import retry_on_version_conflict
from app.core.context import RequestContext, Role
from app.core.dates import display_date, parse_date, today_kl
from app.core.errors import Forbidden, InvalidState, LeaseOverlap, NotFound, ValidationFailed
from app.core.ids import new_id
from app.domains.leases.schemas import (
    AgreementPaper,
    AgreementSend,
    AgreementSign,
    Deposit,
    LeaseCreate,
    LeaseRenew,
    LeaseTerminate,
    LeaseUpdate,
    RecurringCharge,
)
from app.domains.lifecycle import Plan, delete_documents, documents_of, remove_files, write_all
from app.domains.places import PlaceNames
from app.domains.scope import Scope
from app.repositories.audit import diff_summary
from app.repositories.dynamo import Delete, TxCancelled
from app.repositories.leases import lease_item, with_active_index, without_active_index

STAFF = {Role.ORG_ADMIN, Role.PROPERTY_MANAGER}
ACTIVATE_WINDOW_DAYS = 31
TERMS = [
    "end_date",
    "notice_days",
    "monthly_rent_minor",
    "due_day",
    "recurring_charges",
    "tenant_ids",
    "deposits",
]

log = logging.getLogger(__name__)
Item = dict[str, Any]
OPEN_END = "9999-12-31"  # a rolling lease holds its dates open-ended in the unit lock


def _end(end_date: str | None) -> str:
    return end_date or OPEN_END


def _money(minor: int) -> dict[str, Any]:
    return {"amount_minor": minor, "currency": "MYR"}


def _deposits_out(items: list[Item]) -> list[Item]:
    return [
        {
            "type": d["type"],
            "amount": _money(d["amount_minor"]),
            "description": d.get("description"),
        }
        for d in items
    ]


def _plate(unit: Item, plate: str | None) -> str | None:
    """A vehicle plate is recorded on parking bay leases only (contract v0.10.0). Personal data: it is in the
    tenant's export and removed when they are erased."""
    if not plate:
        return None
    if unit.get("type") != "parking":
        raise ValidationFailed(
            details=[{"field": "vehicle_plate", "issue": "only for a parking bay lease"}]
        )
    return " ".join(plate.upper().split())


def to_api(item: Item) -> Item:
    return {
        **{
            k: item[k]
            for k in (
                "id",
                "unit_id",
                "property_id",
                "tenant_ids",
                "status",
                "start_date",
                "end_date",
                "notice_days",
                "due_day",
                "terminated_at",
                "created_at",
                "updated_at",
            )
        },
        "monthly_rent": _money(item["monthly_rent_minor"]),
        "deposits": _deposits_out(item["deposits"]),
        "deposits_held": _deposits_out(item.get("deposits_held", [])),
        "balance": _money(item["balance_minor"]),
        "credit": _money(item["credit_minor"]),
        "recurring_charges": [
            {"name": c["name"], "amount": _money(c["amount_minor"]), "active": c["active"]}
            for c in item.get("recurring_charges", [])
        ],
        "agreement": agreement_out(item),
        "vehicle_plate": item.get("vehicle_plate"),
    }


# ---- tenancy agreement (contract v0.7.0): sent to the tenants, signed by each, then activated
NOT_SENT: Item = {"status": "not_sent", "document_id": None, "sent_at": None, "signatures": []}
TERMS_SIGNED = {
    "end_date",
    "notice_days",
    "monthly_rent_minor",
    "due_day",
    "recurring_charges",
    "tenant_ids",
    "deposits",
}


def agreement_of(lease: Item) -> Item:
    return lease.get("agreement") or NOT_SENT


def agreement_out(lease: Item) -> Item:
    a = agreement_of(lease)
    signed = {s["tenant_id"] for s in a["signatures"]}
    return {
        "status": a["status"],
        "document_id": a.get("document_id"),
        "sent_at": a.get("sent_at"),
        "signed_at": a.get("signed_at"),
        "signatures": [
            {k: s[k] for k in ("tenant_id", "full_name", "method", "signed_at")}
            for s in a["signatures"]
        ],
        "pending_tenant_ids": [t for t in lease["tenant_ids"] if t not in signed],
    }


def _norm_name(s: str) -> str:
    return " ".join(s.split()).casefold()  # spacing and case do not matter


def _same_name(a: str, b: str) -> bool:
    return _norm_name(a) == _norm_name(b)


def _charges(raw: list[RecurringCharge] | None) -> list[Item]:
    return [
        {"name": c.name, "amount_minor": c.amount.amount_minor, "active": c.active}
        for c in raw or []
    ]


def _deposit_items(raw: list[Deposit] | None) -> list[Item]:
    return [
        {"type": d.type, "amount_minor": d.amount.amount_minor, "description": d.description}
        for d in raw or []
    ]


def _overlaps(periods: list[Item], start: str, end: str | None, ignore: str | None = None) -> bool:
    end = _end(end)
    return any(
        p["lease_id"] != ignore and p["start_date"] <= end and start <= p["end_date"]
        for p in periods
    )


class LeaseService:
    def __init__(self, c: Container, ctx: RequestContext) -> None:
        self._c, self._ctx = c, ctx
        self._leases = c.store.leases(ctx.org_id)
        self._units = c.store.units(ctx.org_id)
        self._tenants = c.store.tenants(ctx.org_id)
        self._audit = c.store.audit(ctx.org_id)
        self._scope = Scope(c, ctx)

    @property
    def _is_staff(self) -> bool:
        return bool(self._ctx.roles & STAFF)

    # ---- output
    def present(self, items: list[Item]) -> list[Item]:
        """API shape plus display names (property, unit or room, tenants), resolved in a few batched reads."""
        names = PlaceNames(self._c, self._ctx)
        places, people = names.places(items), names.tenant_names(items)
        return [
            {
                **to_api(x),
                **places.get(x["id"], {}),
                "tenants": [
                    {"id": t, "full_name": people[t]} for t in x["tenant_ids"] if t in people
                ],
            }
            for x in items
        ]

    def present_one(self, item: Item) -> Item:
        return self.present([item])[0]

    # ---- reads
    def get(self, lid: str) -> Item:
        item = self._leases.get(lid)
        if item is None or not self._scope.lease_ok(item):
            raise NotFound()
        if not self._is_staff:
            if item["status"] == "draft" and agreement_of(item)["status"] == "not_sent":
                raise NotFound()  # drafts are internal until the agreement is sent for signing
            if self._ctx.tenant_id not in item["tenant_ids"]:
                raise Forbidden()
        return item

    def page(
        self,
        limit: int,
        cursor: str | None,
        status: str | None,
        unit_id: str | None,
        tenant_id: str | None,
    ) -> tuple[list[Item], str | None]:
        if not self._is_staff:
            tenant_id = self._ctx.tenant_id  # tenants only ever see their own leases
            if tenant_id is None:
                raise Forbidden()
        if tenant_id:
            links, nxt = self._leases.page_links(tenant_id, limit, cursor)
            leases = self._leases.get_many([link["lease_id"] for link in links])
            by_id = {x["id"]: x for x in leases}
            out = [by_id[link["lease_id"]] for link in links if link["lease_id"] in by_id]
            out = [
                x
                for x in out
                if (status is None or x["status"] == status)
                and (unit_id is None or x["unit_id"] == unit_id)
                and (
                    self._is_staff
                    or x["status"] != "draft"
                    or agreement_of(x)["status"] != "not_sent"  # waiting for their signature
                )
                and self._scope.lease_ok(x)
            ]
            return out, nxt
        if unit_id:
            unit = self._units.get(unit_id)
            if unit is None or not self._scope.property_ok(unit["property_id"]):
                return [], None  # an unknown or out-of-scope unit has no leases to show
            return self._leases.page_for_unit(unit_id, limit, cursor, status)
        also = self._scope.lease_ok if self._scope.restricted else None
        return self._leases.page(limit, cursor, status, also=also)

    # ---- helpers
    def _validate_tenants(self, ids: list[str]) -> None:
        found = {t["id"]: t for t in self._tenants.get_many(ids)}
        bad = [
            i
            for i in ids
            if i not in found
            or found[i]["status"] in ("former", "anonymised")
            or not self._scope.tenant_ok(found[i])  # a manager leases only to tenants they can see
        ]
        if bad:
            raise ValidationFailed(
                details=[{"field": "tenant_ids", "issue": "unknown or inactive tenant"}]
            )

    def _lock_periods(self, unit_id: str) -> tuple[Item | None, list[Item]]:
        lock = self._leases.get_lock(unit_id)
        return lock, (lock["periods"] if lock else [])

    # ---- create / amend
    def _new_draft(self, fields: Item, action: str, extra_audit: Item | None = None) -> Item:
        now = self._c.clock.now()
        lid = new_id()
        item = lease_item(self._ctx.org_id, lid, fields, iso(now))
        ops: list[Any] = [self._leases.create_op(item)]
        ops += [self._leases.link_put_op(t, lid) for t in item["tenant_ids"]]
        ops.append(
            self._audit.put_for(
                self._ctx,
                now,
                action,
                "lease",
                lid,
                {"unit_id": item["unit_id"], **(extra_audit or {})},
            )
        )
        self._c.store.table.transact(ops)
        return item

    def _lettable(self, unit: Item, *, strict: bool) -> Item | None:
        """Letting-mode rule (LLD 5.5). Returns the parent whole unit for a room, else None.
        `strict` raises 409 invalid_state (activation), otherwise 422 on unit_id (create)."""

        def fail(msg: str) -> Exception:
            if strict:
                return InvalidState(msg)
            return ValidationFailed(details=[{"field": "unit_id", "issue": msg}])

        if unit["type"] == "whole_unit":
            if unit["letting_mode"] != "whole":
                raise fail("This unit is let by the room, create the lease on a room")
            return None
        if unit["type"] == "parking":  # contract v0.10.0
            if unit.get("parent_unit_id"):
                owner = self._units.get(unit["parent_unit_id"])
                if owner is None or owner["status"] == "inactive":
                    raise fail("The bay's unit is not available")
                if owner["letting_mode"] == "whole":
                    raise fail(
                        f"This bay comes with {owner['label']}, which is let as a whole. Let "
                        f"{owner['label']} by the room, or move the bay to the property, to rent it separately"
                    )
            return None
        parent = self._units.get(unit["parent_unit_id"])
        if parent is None or parent["status"] == "inactive":
            raise fail("The room's unit is not available")
        if parent["letting_mode"] != "rooms":
            raise fail("The room's unit is let as a whole")
        return parent

    def create(self, body: LeaseCreate) -> Item:
        unit = self._units.get(body.unit_id)
        if (
            unit is None
            or unit["status"] == "inactive"
            or not self._scope.property_ok(unit["property_id"])
        ):
            raise ValidationFailed(
                details=[{"field": "unit_id", "issue": "unknown or inactive unit"}]
            )
        self._lettable(unit, strict=False)
        self._validate_tenants(body.tenant_ids)
        _, periods = self._lock_periods(unit["id"])
        end = str(body.end_date) if body.end_date else None
        if _overlaps(periods, str(body.start_date), end):
            raise LeaseOverlap()
        return self._new_draft(
            {
                "unit_id": unit["id"],
                "property_id": unit["property_id"],
                "tenant_ids": list(body.tenant_ids),
                "start_date": str(body.start_date),
                "end_date": end,
                "notice_days": body.notice_days,
                "monthly_rent_minor": body.monthly_rent.amount_minor,
                "deposits": _deposit_items(body.deposits),
                "due_day": body.due_day,
                "recurring_charges": _charges(body.recurring_charges),
                "vehicle_plate": _plate(unit, body.vehicle_plate),
            },
            "lease.create",
            {"rolling": end is None},
        )

    def update(self, lid: str, body: LeaseUpdate) -> Item:
        fields = body.model_fields_set - {"effective_from"}

        def attempt() -> Item:
            old = self.get(lid)
            if old["status"] not in ("draft", "active"):
                raise InvalidState("Only draft and active leases can be amended")
            if "effective_from" in body.model_fields_set and old["status"] == "draft":
                raise ValidationFailed(
                    details=[{"field": "effective_from", "issue": "only applies to active leases"}]
                )
            new = {**old}
            if "end_date" in fields:
                new["end_date"] = str(body.end_date) if body.end_date else None  # null: rolling
            if body.notice_days is not None:
                new["notice_days"] = body.notice_days
            if body.monthly_rent is not None:
                new["monthly_rent_minor"] = body.monthly_rent.amount_minor
            if body.due_day is not None:
                new["due_day"] = body.due_day
            if body.recurring_charges is not None:
                new["recurring_charges"] = _charges(body.recurring_charges)
            if body.tenant_ids is not None:
                self._validate_tenants(body.tenant_ids)
                new["tenant_ids"] = list(body.tenant_ids)
            if body.deposits is not None:
                if old["status"] != "draft":  # the deposit invoice went out on activation
                    raise ValidationFailed(
                        details=[
                            {"field": "deposits", "issue": "can only be changed before activation"}
                        ]
                    )
                new["deposits"] = _deposit_items(body.deposits)
            if "vehicle_plate" in fields:  # not a term: changing it never clears signatures
                unit = self._units.get(old["unit_id"]) or {"type": "unknown"}
                new["vehicle_plate"] = _plate(unit, body.vehicle_plate)
            if new["end_date"] is not None and new["end_date"] <= new["start_date"]:
                raise ValidationFailed(
                    details=[{"field": "end_date", "issue": "must be after start_date"}]
                )
            if (
                old["status"] == "draft"
                and agreement_of(old)["status"] != "not_sent"
                and any(new.get(k) != old.get(k) for k in TERMS_SIGNED)
            ):
                new["agreement"] = NOT_SENT  # nobody is bound by terms they did not see: send again
            now = self._c.clock.now()
            today = today_kl(self._c.clock)
            new["updated_at"], new["version"] = iso(now), old["version"] + 1
            ops: list[Any] = []
            if old["status"] == "active" and fields - {"vehicle_plate"}:
                if new["end_date"] is not None and new["end_date"] < str(today):
                    raise InvalidState(
                        "An active lease cannot end in the past, terminate it instead"
                    )
                eff = body.effective_from or _first_of_next_month(today)
                initial = {  # billing finds the terms in force for a period from these snapshots
                    "effective_from": old["start_date"],
                    "recorded_at": old.get("activated_at", old["updated_at"]),
                    "fields": [],
                    "monthly_rent_minor": old["monthly_rent_minor"],
                    "due_day": old["due_day"],
                    "notice_days": old["notice_days"],
                    "recurring_charges": old["recurring_charges"],
                }
                new["amendments"] = [
                    *(old.get("amendments") or [initial]),
                    {
                        "effective_from": str(eff),
                        "recorded_at": iso(now),
                        "fields": sorted(fields),
                        "monthly_rent_minor": new["monthly_rent_minor"],
                        "due_day": new["due_day"],
                        "notice_days": new["notice_days"],
                        "recurring_charges": new["recurring_charges"],
                    },
                ]
                new = with_active_index(new, self._ctx.org_id)
            ops.append(self._leases.replace_op(new, old["version"]))
            if old["status"] == "active" and _end(new["end_date"]) != _end(old["end_date"]):
                lock, periods = self._lock_periods(old["unit_id"])
                if _overlaps(periods, old["start_date"], new["end_date"], ignore=lid):
                    raise LeaseOverlap()
                periods = [
                    {**p, "end_date": _end(new["end_date"])} if p["lease_id"] == lid else p
                    for p in periods
                ]
                ops.append(self._leases.lock_op(old["unit_id"], periods, lock))
            for t in set(new["tenant_ids"]) - set(old["tenant_ids"]):
                ops.append(self._leases.link_put_op(t, lid))
            for t in set(old["tenant_ids"]) - set(new["tenant_ids"]):
                ops.append(self._leases.link_delete_op(t, lid))
            ops.append(
                self._audit.put_for(
                    self._ctx,
                    now,
                    "lease.update",
                    "lease",
                    lid,
                    diff_summary(
                        old,
                        new,
                        [*TERMS, "vehicle_plate"],
                        opaque=("tenant_ids", "recurring_charges", "deposits", "vehicle_plate"),
                    ),
                )
            )
            self._c.store.table.transact(ops)
            return new

        return retry_on_version_conflict(attempt, attempts=4, index=(0, 1))

    # ---- state transitions
    # ---- tenancy agreement
    def _agreement_required(self) -> bool:
        meta = self._c.store.org(self._ctx.org_id).get_meta() or {}
        return bool((meta.get("settings") or {}).get("require_signed_agreement", True))

    def _agreement_doc(self, lid: str, document_id: str | None) -> Item:
        """The agreement to sign: a clean `lease_agreement` document on this lease (newest by default)."""
        docs = self._c.store.documents(self._ctx.org_id)
        found: list[Item] = []
        cursor = None
        while True:
            page, cursor = docs.page_for_owner("lease", lid, 100, cursor)
            found += [d for d in page if d["type"] == "lease_agreement" and d["status"] == "clean"]
            if cursor is None:
                break
        if document_id:
            found = [d for d in found if d["id"] == document_id]
        if not found:
            raise InvalidState(
                "Upload the tenancy agreement as a 'Lease agreement' document and wait for its "
                "check to finish first"
            )
        return max(found, key=lambda d: d["created_at"])

    def _save_agreement(self, old: Item, agreement: Item, action: str, detail: Item) -> Item:
        now = self._c.clock.now()
        new = {**old, "agreement": agreement, "version": old["version"] + 1, "updated_at": iso(now)}
        self._c.store.table.transact(
            [
                self._leases.replace_op(new, old["version"]),
                self._audit.put_for(self._ctx, now, action, "lease", old["id"], detail),
            ]
        )
        return new

    @staticmethod
    def _with_signatures(agreement: Item, lease: Item, added: list[Item], now: str) -> Item:
        sigs = [*agreement["signatures"], *added]
        done = {s["tenant_id"] for s in sigs} >= set(lease["tenant_ids"])
        return {
            **agreement,
            "signatures": sigs,
            "status": "signed" if done else "sent",
            **({"signed_at": now} if done else {}),
        }

    def send_agreement(self, lid: str, body: AgreementSend) -> Item:
        def attempt() -> Item:
            old = self.get(lid)
            if old["status"] != "draft":
                raise InvalidState("Only a draft lease's agreement can be sent for signing")
            doc = self._agreement_doc(lid, body.document_id)
            agreement = {
                "status": "sent",
                "document_id": doc["id"],
                "sent_at": iso(self._c.clock.now()),
                "sent_by": self._ctx.user_id,
                "signatures": [],
            }  # sending again starts over
            return self._save_agreement(
                old, agreement, "lease.agreement_send", {"document_id": doc["id"]}
            )

        lease = retry_on_version_conflict(attempt)
        from app.domains.notifications.events import publish

        publish(
            self._c,
            self._ctx.org_id,
            "lease_agreement_sent",
            tenant_ids=lease["tenant_ids"],
            entity_id=lid,
            params={"start_date": display_date(lease["start_date"])},
            offset=lease["agreement"]["sent_at"],  # a second sending is a second message
        )
        return lease

    def sign_agreement(self, lid: str, body: AgreementSign, user_agent: str | None) -> Item:
        """A tenant signs in the portal: their typed full name, the moment, the device."""
        tid = self._ctx.tenant_id
        if tid is None:
            raise Forbidden()

        def attempt() -> Item:
            old = self.get(lid)  # tenants reach only their own leases
            a = agreement_of(old)
            if old["status"] != "draft" or a["status"] == "not_sent":
                raise InvalidState("There is no agreement waiting for your signature")
            if any(s["tenant_id"] == tid for s in a["signatures"]):
                raise InvalidState("You have already signed this agreement")
            doc = self._c.store.documents(self._ctx.org_id).get(a["document_id"])
            if doc is None or doc["status"] != "clean":
                raise InvalidState(
                    "The agreement is no longer available, ask your landlord to send it again"
                )
            me = self._tenants.get(tid)
            if me is None or not _same_name(body.full_name, me["full_name"]):
                raise ValidationFailed(
                    details=[
                        {
                            "field": "full_name",
                            "issue": "type your full name exactly as it appears on the lease",
                        }
                    ]
                )
            now = iso(self._c.clock.now())
            sig = {
                "tenant_id": tid,
                "full_name": " ".join(body.full_name.split()),
                "method": "portal",
                "signed_at": now,
                "document_id": a["document_id"],
                "ip": self._ctx.ip,
                "user_agent": (user_agent or "")[:300],
            }
            agreement = self._with_signatures(a, old, [sig], now)
            return self._save_agreement(
                old,
                agreement,
                "lease.agreement_sign",
                {"document_id": a["document_id"], "method": "portal"},
            )

        return retry_on_version_conflict(attempt)

    def paper_agreement(self, lid: str, body: AgreementPaper) -> Item:
        """Staff record that tenants signed on paper; the signed scan must be uploaded first."""

        def attempt() -> Item:
            old = self.get(lid)
            if old["status"] != "draft":
                raise InvalidState("Only a draft lease's agreement can be signed")
            a = agreement_of(old)
            if any(t not in old["tenant_ids"] for t in body.tenant_ids):
                raise ValidationFailed(
                    details=[{"field": "tenant_ids", "issue": "not a tenant on this lease"}]
                )
            signed = {s["tenant_id"] for s in a["signatures"]}
            todo = [t for t in dict.fromkeys(body.tenant_ids) if t not in signed]
            if not todo:
                raise InvalidState("These tenants have already signed")
            doc = self._agreement_doc(lid, None)
            if a["status"] == "not_sent":
                a = {
                    **a,
                    "status": "sent",
                    "document_id": doc["id"],
                    "sent_at": iso(self._c.clock.now()),
                }
            names = {t["id"]: t["full_name"] for t in self._tenants.get_many(todo)}
            now = iso(self._c.clock.now())
            added = [
                {
                    "tenant_id": t,
                    "full_name": names.get(t, ""),
                    "method": "paper",
                    "signed_at": now,
                    "document_id": doc["id"],
                    "recorded_by": self._ctx.user_id,
                    "note": body.note,
                }
                for t in todo
            ]
            agreement = self._with_signatures(a, old, added, now)
            return self._save_agreement(
                old,
                agreement,
                "lease.agreement_paper",
                {"document_id": doc["id"], "tenants": len(todo)},
            )

        return retry_on_version_conflict(attempt)

    def activate(self, lid: str) -> Item:
        def attempt() -> Item:
            old = self.get(lid)
            if old["status"] != "draft":
                raise InvalidState("Only a draft lease can be activated")
            if self._agreement_required() and agreement_of(old)["status"] != "signed":
                raise InvalidState(
                    "Every tenant must sign the tenancy agreement before the lease is activated"
                )
            today = today_kl(self._c.clock)
            if parse_date(old["start_date"]) > today + timedelta(days=ACTIVATE_WINDOW_DAYS):
                raise InvalidState(
                    f"A lease can be activated at most {ACTIVATE_WINDOW_DAYS} days before it starts"
                )
            self._validate_tenants(old["tenant_ids"])
            unit = self._units.get(old["unit_id"])
            if unit is None or unit["status"] in ("inactive", "maintenance"):
                raise InvalidState("The unit is not available for a lease")
            parent = self._lettable(unit, strict=True)
            if parent is not None and parent["status"] == "maintenance":
                raise InvalidState("The room's unit is not available for a lease")
            lock, periods = self._lock_periods(unit["id"])
            if _overlaps(periods, old["start_date"], old["end_date"]):
                raise LeaseOverlap()
            now = self._c.clock.now()
            new = with_active_index(
                {
                    **old,
                    "status": "active",
                    "version": old["version"] + 1,
                    "updated_at": iso(now),
                    "activated_at": iso(now),
                },
                self._ctx.org_id,
            )
            periods = [
                *periods,
                {
                    "lease_id": lid,
                    "start_date": old["start_date"],
                    "end_date": _end(old["end_date"]),
                },
            ]
            current = unit["current_lease_id"] or lid
            ops: list[Any] = [
                self._leases.lock_op(unit["id"], periods, lock),  # the overlap guard
                self._leases.replace_op(new, old["version"]),
                self._leases.unit_update_op(
                    unit, status="occupied", current_lease_id=current, now=iso(now)
                ),
            ]
            if parent is not None:  # a room lease counts against its whole unit (mutual exclusion)
                ops.append(self._leases.parent_rooms_op(parent, +1, iso(now)))
            ops.append(
                self._audit.put_for(
                    self._ctx,
                    now,
                    "lease.activate",
                    "lease",
                    lid,
                    {"unit_id": unit["id"], "rolling": old["end_date"] is None},
                )
            )
            self._c.store.table.transact(ops)
            return new

        activated = retry_on_version_conflict(attempt, attempts=4, index=(0, 1, 2, 3))
        self._deposit_invoice(activated)
        from app.domains.notifications.events import publish

        publish(
            self._c,
            self._ctx.org_id,
            "lease_activated",
            tenant_ids=activated["tenant_ids"],
            entity_id=lid,
            params={"start_date": display_date(activated["start_date"])},
        )
        return self._leases.get(lid) or activated

    def _deposit_invoice(self, lease: Item) -> None:
        """One deposit invoice with a line per deposit type (LLD 6.10). The nightly sweep retries it,
        so a failure here never leaves a lease without one."""
        from app.domains.billing.common import Actor
        from app.domains.billing.invoicing import create_deposit_invoice

        try:
            create_deposit_invoice(self._c, self._ctx.org_id, lease, Actor.of(self._ctx))
        except Exception:
            log.exception(
                "deposit invoice failed",
                extra={"org_id": self._ctx.org_id, "entity_id": lease["id"]},
            )

    def _settle_deposits(self, lease: Item, deduction: list[Deposit] | None) -> Item:
        """Per type: kept amount is deducted, the rest is returned. Capped by what is held (LLD 6.10)."""
        held = {d["type"]: d["amount_minor"] for d in lease.get("deposits_held", [])}
        deducted: dict[str, int] = {}
        for d in deduction or []:
            if d.type not in held or d.amount.amount_minor > held[d.type]:
                raise ValidationFailed(
                    details=[
                        {
                            "field": "deposit_deduction",
                            "issue": f"{d.type} deduction exceeds the amount held",
                            "code": "deduction_exceeds_held",
                            "params": {"type": d.type},
                        }
                    ]
                )
            deducted[d.type] = d.amount.amount_minor
        return {
            "deducted": [{"type": t, "amount_minor": a} for t, a in deducted.items() if a],
            "returned": [
                {"type": t, "amount_minor": a - deducted.get(t, 0)}
                for t, a in held.items()
                if a - deducted.get(t, 0)
            ],
        }

    def terminate(self, lid: str, body: LeaseTerminate) -> Item:
        def attempt() -> Item:
            old = self.get(lid)
            if old["status"] != "active":
                raise InvalidState("Only an active lease can be terminated")
            td = str(body.termination_date)
            today = today_kl(self._c.clock)
            rolling = old["end_date"] is None
            if td < old["start_date"] or (not rolling and td > old["end_date"]):
                raise ValidationFailed(
                    details=[
                        {"field": "termination_date", "issue": "must be within the lease dates"}
                    ]
                )
            waived = False
            if rolling:
                earliest = today + timedelta(days=old["notice_days"])
                if body.termination_date < earliest:
                    if not body.waive_notice:
                        raise ValidationFailed(
                            details=[
                                {
                                    "field": "termination_date",
                                    "issue": f"needs {old['notice_days']} days notice, earliest is {display_date(earliest)}, "
                                    "or set waive_notice",
                                    "code": "notice_too_short",
                                    "params": {
                                        "days": old["notice_days"],
                                        "date": earliest.isoformat(),
                                    },
                                }
                            ]
                        )
                    waived = True
            settlement = self._settle_deposits(old, body.deposit_deduction)
            now = self._c.clock.now()
            new = without_active_index(
                {
                    **old,
                    "status": "terminated",
                    "terminated_at": td,
                    "termination_reason": body.reason,
                    "notice_waived": waived,
                    "deposit_settlement": settlement,
                    "deposits_held": [],
                    "version": old["version"] + 1,
                    "updated_at": iso(now),
                }
            )
            lock, periods = self._lock_periods(old["unit_id"])
            remaining = [p for p in periods if p["lease_id"] != lid]
            ops: list[Any] = [
                self._leases.replace_op(new, old["version"]),
                self._leases.lock_op(old["unit_id"], remaining, lock),
            ]
            unit = self._units.get(old["unit_id"])
            if unit is not None and unit["current_lease_id"] == lid:
                nxt = (
                    min(remaining, key=lambda p: p["start_date"])["lease_id"] if remaining else None
                )
                ops.append(
                    self._leases.unit_update_op(
                        unit,
                        status="occupied" if nxt else "vacant",
                        current_lease_id=nxt,
                        now=iso(now),
                    )
                )
            if unit is not None and unit["type"] == "room":
                parent = self._units.get(unit["parent_unit_id"])
                if parent is not None:
                    ops.append(self._leases.parent_rooms_op(parent, -1, iso(now)))
            ops.append(
                self._audit.put_for(
                    self._ctx,
                    now,
                    "lease.terminate",
                    "lease",
                    lid,
                    {
                        "termination_date": td,
                        "notice_waived": waived,  # an early termination is a deliberate, audited choice
                        "deducted_minor": sum(d["amount_minor"] for d in settlement["deducted"]),
                    },
                )
            )
            self._c.store.table.transact(ops)
            return new

        terminated = retry_on_version_conflict(attempt, attempts=4, index=(0, 1, 2, 3))
        # money follow-ups (void unbilled periods, prorate the last, settle deposits) run as an idempotent job
        from app.worker.envelope import make_envelope

        self._c.jobs.enqueue(
            "housekeeping",
            make_envelope(
                "lease_termination_effects",
                self._ctx.org_id,
                {"lease_id": lid},
                self._c.clock.now(),
            ),
        )
        from app.domains.notifications.events import publish

        publish(
            self._c,
            self._ctx.org_id,
            "lease_terminated",
            tenant_ids=terminated["tenant_ids"],
            entity_id=lid,
            params={"end_date": terminated["terminated_at"]},
        )
        return self._leases.get(lid) or terminated

    # ---- delete a draft (contract v0.9.0): a lease that never started is not a financial record
    def _draft_deletion(self, lid: str) -> tuple[Plan, Item, list[Item]]:
        if not self._is_staff:
            raise Forbidden()
        lease = self.get(lid)
        plan = Plan()
        if lease["status"] != "draft":
            plan.block(
                "not_draft",
                "Only a draft lease can be deleted. An active lease is ended instead, and ended leases "
                "are kept as financial records until the retention period ends.",
            )
        docs = documents_of(self._c, self._ctx.org_id, "lease", lid)
        plan.count("documents", len(docs))
        plan.count("signatures", len(agreement_of(lease)["signatures"]))
        return plan, lease, docs

    def deletion(self, lid: str) -> dict[str, Any]:
        return self._draft_deletion(lid)[0].view()

    def delete(self, lid: str, reason: str) -> dict[str, Any]:
        plan, lease, docs = self._draft_deletion(lid)
        plan.require_not_blocked()
        now = self._c.clock.now()
        ops: list[Any] = [
            self._audit.put_for(
                self._ctx,
                now,
                "lease.delete",
                "lease",
                lid,
                {"reason": reason, "unit_id": lease["unit_id"], **plan.affects},
            ),
            Delete(
                key=(lease["PK"], lease["SK"]),
                condition="#v = :v AND #s = :draft",
                names={"#v": "version", "#s": "status"},
                values={":v": lease["version"], ":draft": "draft"},
            ),
            *[self._leases.link_delete_op(t, lid) for t in lease["tenant_ids"]],
            *delete_documents(self._c, docs),
        ]
        try:
            write_all(self._c, ops)
        except TxCancelled:
            raise InvalidState(
                "The lease changed meanwhile (perhaps it was activated). Try again"
            ) from None
        remove_files(self._c, docs)
        return {"outcome": "removed", "privacy_request_id": None}

    def renew(self, lid: str, body: LeaseRenew) -> Item:
        old = self.get(lid)
        if old["status"] != "active":
            raise InvalidState("Only an active lease can be renewed")
        if old["end_date"] is None:
            raise InvalidState("A rolling lease has no end date to renew from")
        start = parse_date(old["end_date"]) + timedelta(days=1)
        if body.new_end_date <= start:
            raise ValidationFailed(
                details=[{"field": "new_end_date", "issue": "must be after the new start date"}]
            )
        _, periods = self._lock_periods(old["unit_id"])
        if _overlaps(periods, str(start), str(body.new_end_date)):
            raise LeaseOverlap()
        self._validate_tenants(old["tenant_ids"])
        return self._new_draft(
            {
                "unit_id": old["unit_id"],
                "property_id": old["property_id"],
                "tenant_ids": list(old["tenant_ids"]),
                "start_date": str(start),
                "end_date": str(body.new_end_date),
                "notice_days": old["notice_days"],
                "monthly_rent_minor": body.monthly_rent.amount_minor
                if body.monthly_rent
                else old["monthly_rent_minor"],
                "deposits": [],  # the deposits already held carry over, none is charged again
                "due_day": old["due_day"],
                "recurring_charges": _charges(body.recurring_charges)
                if body.recurring_charges is not None
                else old["recurring_charges"],
                "renewed_from": lid,
            },
            "lease.renew",
            {"renewed_from": lid},
        )


def _first_of_next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)
