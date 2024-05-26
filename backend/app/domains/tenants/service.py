from __future__ import annotations

import contextlib
import secrets
from datetime import timedelta
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.concurrency import retry_on_version_conflict
from app.core.context import RequestContext, Role
from app.core.crypto import mask
from app.core.errors import Conflict, Forbidden, InvalidState, NotFound, ValidationFailed
from app.core.ids import new_id
from app.domains.lifecycle import Plan, keys_of, partition, remove_files, write_all
from app.domains.organisations import branding
from app.domains.places import PlaceNames
from app.domains.scope import Scope
from app.domains.tenants.schemas import ConsentCreate, TenantCreate, TenantUpdate
from app.integrations.identity import UserExists
from app.repositories import keys
from app.repositories.audit import diff_summary
from app.repositories.dynamo import NOT_EXISTS, Check, Delete, Put, TxCancelled, Update
from app.repositories.tenants import consent_item, tenant_item

STAFF = {Role.ORG_ADMIN, Role.PROPERTY_MANAGER}


class TenantService:
    def __init__(self, c: Container, ctx: RequestContext) -> None:
        self._c, self._ctx = c, ctx
        self._repo = c.store.tenants(ctx.org_id)
        self._audit = c.store.audit(ctx.org_id)
        self._scope = Scope(c, ctx)

    @property
    def _is_staff(self) -> bool:
        return bool(self._ctx.roles & STAFF)

    def _require_self_or_staff(self, tid: str) -> None:
        if not self._is_staff and self._ctx.tenant_id != tid:
            raise Forbidden()  # a tenant may only touch their own record

    def view(self, item: dict[str, Any]) -> dict[str, Any]:
        """API shape. The identity document is masked and shown to staff only."""
        out = {**item}
        if (
            item["status"] == "anonymised"
        ):  # the contract requires an email: show a clearly fake one
            out["email"] = f"removed+{item['id'][-6:].lower()}@anonymised.invalid"
        doc = item.get("identity_document")
        out["identity_document"] = (
            {"type": doc["type"], "masked_number": doc["masked_number"]}
            if doc and self._is_staff
            else None
        )
        return out

    def view_with_rentals(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """view() plus `rentals`: each tenant's active and upcoming (draft) leases with the property and
        unit or room. One link query per tenant, then batched reads. Tenants never see draft leases."""
        leases_repo = self._c.store.leases(self._ctx.org_id)
        lease_ids = {
            t["id"]: [x["lease_id"] for x in leases_repo.all_links(t["id"])] for t in items
        }
        wanted = {"active", "draft"} if self._is_staff else {"active"}
        all_ids = sorted({i for ids in lease_ids.values() for i in ids})
        seen = {  # every lease of theirs the caller may see, any status
            x["id"]: x
            for x in (leases_repo.get_many(all_ids) if all_ids else [])
            if self._scope.lease_ok(x)  # never another manager's lease
            and (self._is_staff or x["status"] != "draft")  # tenants never see drafts
        }
        leases = {k: x for k, x in seen.items() if x["status"] in wanted}
        places = PlaceNames(self._c, self._ctx).places(list(leases.values()))
        out = []
        for t in items:
            mine = sorted(
                (leases[i] for i in lease_ids[t["id"]] if i in leases),
                key=lambda x: (x["status"] != "active", x["start_date"]),
            )
            rentals = [
                {"lease_id": x["id"], "status": x["status"], **places[x["id"]]} for x in mine
            ]
            statuses = {seen[i]["status"] for i in lease_ids[t["id"]] if i in seen}
            out.append({**self.view(t), "rentals": rentals, "tenancy": _tenancy(statuses)})
        return out

    # ---- tenants
    def create(self, body: TenantCreate) -> dict[str, Any]:
        now = self._c.clock.now()
        tid = new_id()
        email = body.email.lower()
        fields: dict[str, Any] = {
            "full_name": body.full_name,
            "email": email,
            "phone": body.phone,
            "created_by": self._ctx.user_id,  # manager scope: a manager sees the tenants they add
            "identity_document": None,
            "notification_preferences": (
                body.notification_preferences.model_dump(mode="json", exclude_none=True)
                if body.notification_preferences
                else {}
            ),
        }
        if body.identity_document:
            enc = self._c.encryptor
            if enc is None:
                raise ValidationFailed(
                    details=[
                        {"field": "identity_document", "issue": "not enabled in this environment"}
                    ]
                )
            number = body.identity_document.number
            fields["identity_document"] = {
                "type": body.identity_document.type,
                "masked_number": mask(number),
                "number_enc": enc.encrypt(
                    number,
                    {
                        "org_id": self._ctx.org_id,
                        "tenant_id": tid,
                        "field": "identity_document.number",
                    },
                ),
            }
        item = tenant_item(self._ctx.org_id, tid, fields, iso(now))
        try:
            self._c.store.table.transact(
                [
                    self._repo.create_op(item),
                    self._repo.email_lock_op(email, tid),
                    self._audit.put_for(
                        self._ctx,
                        now,
                        "tenant.create",
                        "tenant",
                        tid,
                        {"identity_document": bool(body.identity_document)},
                    ),
                ]
            )
        except TxCancelled as e:
            if e.failed(1):
                raise Conflict("A tenant with this email already exists") from None
            raise
        return item

    def get(self, tid: str) -> dict[str, Any]:
        self._require_self_or_staff(tid)
        item = self._repo.get(tid)
        if item is None or not self._scope.tenant_ok(item):
            raise NotFound()
        return item

    def page(
        self, limit: int, cursor: str | None, status: str | None, email: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        also = self._scope.tenant_ok if self._scope.restricted else None
        return self._repo.page(limit, cursor, status, email, also=also)

    def update(self, tid: str, body: TenantUpdate) -> dict[str, Any]:
        changes = body.model_dump(exclude_unset=True)
        if changes.get("email"):
            changes["email"] = changes["email"].lower()
        for k in ("full_name", "email", "status"):
            if k in changes and changes[k] is None:
                del changes[k]

        def attempt() -> dict[str, Any]:
            old = self.get(tid)
            if old["status"] == "anonymised":
                raise InvalidState("Anonymised tenants cannot be edited")
            new = {**old, **changes}
            now = self._c.clock.now()
            new["updated_at"], new["version"] = iso(now), old["version"] + 1
            ops: list[Any] = [self._repo.replace_op(new, old["version"])]
            relabel = "email" in changes and changes["email"] != old["email"]
            if relabel:
                if old["linked"] or old.get("pending_sub"):
                    raise InvalidState("The email of an invited or active tenant cannot be changed")
                ops += [
                    self._repo.email_unlock_op(old["email"]),
                    self._repo.email_lock_op(new["email"], tid),
                ]
            ops.append(
                self._audit.put_for(
                    self._ctx,
                    now,
                    "tenant.update",
                    "tenant",
                    tid,
                    diff_summary(
                        old,
                        new,
                        ["full_name", "email", "phone", "status"],
                        opaque=("full_name", "email", "phone"),
                    ),
                )
            )
            try:
                self._c.store.table.transact(ops)
            except TxCancelled as e:
                if relabel and e.failed(2):
                    raise Conflict("A tenant with this email already exists") from None
                raise
            return new

        old_status = self._repo.get(tid)
        new = retry_on_version_conflict(attempt)
        if new["status"] == "former" and old_status and old_status["status"] != "former":
            sub = new.get("cognito_sub")
            if sub:  # a former tenant must not keep a live session
                self._c.identity.set_enabled(sub, False)
                self._c.identity.global_sign_out(sub)
        return new

    def invite(self, tid: str, channel: str = "email") -> dict[str, Any]:
        """Give an invited tenant a way in (contract v0.8.0, v0.14.0): a one-time set-password link, sent
        by the app as a welcome email or over WhatsApp, or returned for staff to share. Only the newest
        link works; it lasts `invite_ttl_days`. An email address on the bounce/complaint list is refused
        (contract v0.14.1), as every other email to it is skipped."""
        whatsapp_to: str | None = None

        def attempt() -> tuple[str | None, Any]:
            nonlocal whatsapp_to
            old = self.get(tid)
            if old["linked"] or old["status"] in ("active", "former", "anonymised"):
                raise InvalidState("This tenant cannot be invited")
            if channel == "whatsapp":
                whatsapp_to = self._whatsapp_number(old)
            elif channel == "email" and "email" not in self._c.channels:
                raise Conflict(
                    "Email sending is not set up. Use Share on WhatsApp to send the link yourself."
                )
            elif channel == "email" and self._c.store.notifications(self._ctx.org_id).suppressed(
                "email", old["email"]
            ):
                raise Conflict(
                    "Emails to this address bounced or were marked as spam, so none are sent. "
                    "Correct the email address, or use Share on WhatsApp to send the link yourself.",
                    code="address_suppressed",
                    params={"scope": "tenant"},
                )
            now = self._c.clock.now()
            expires = now + timedelta(days=self._c.settings.invite_ttl_days)
            sub, created = old.get("pending_sub"), False
            if not sub:
                try:
                    # never Cognito's own invitation: every channel uses our set-password link
                    sub = self._c.identity.create_user(
                        old["email"], old["full_name"], send_email=False
                    )
                except UserExists:
                    raise Conflict("This email already has an account") from None
                created = True
            token = secrets.token_urlsafe(32)  # a new link replaces the previous one
            new = {
                **old,
                "pending_sub": sub,
                "invited_at": iso(now),
                "version": old["version"] + 1,
                "updated_at": iso(now),
            }
            new["invite_token_pk"] = keys.invite_token_pk(token)
            ops: list[Put | Update | Delete | Check] = [
                self._repo.replace_op(new, old["version"]),
                # the first sign-in (or the set-password link) links the tenant: valid until `expires`
                self._repo.invite_put_op(
                    {
                        "PK": keys.invite_pk(sub),
                        "SK": keys.META,
                        "entity_type": "tenant_invite",
                        "org_id": self._ctx.org_id,
                        "tenant_id": tid,
                        "email": old["email"],
                        "expires_at": iso(expires),
                        "ttl": int(expires.timestamp()),
                    }
                ),
                self._audit.put_for(
                    self._ctx, now, "tenant.invite", "tenant", tid, {"channel": channel}
                ),
            ]
            ops.append(
                Put(
                    item={
                        "PK": new["invite_token_pk"],
                        "SK": keys.META,
                        "entity_type": "tenant_invite_link",
                        "org_id": self._ctx.org_id,
                        "tenant_id": tid,
                        "sub": sub,
                        "email": old["email"],
                        "expires_at": iso(expires),
                        "ttl": int(expires.timestamp()),
                    },
                    condition=NOT_EXISTS,
                )
            )
            if old.get("invite_token_pk"):  # a new link replaces the old one
                ops.append(Delete(key=(old["invite_token_pk"], keys.META)))
            try:
                self._c.store.table.transact(ops)
            except Exception:
                if created:
                    self._c.identity.delete_user(sub)
                raise
            if created:
                with contextlib.suppress(
                    Exception
                ):  # group is cosmetic, tokens come from the table
                    self._c.identity.add_to_group(sub, "tenant")
            return token, expires

        token, expires = retry_on_version_conflict(attempt)
        link: str | None = f"{self._c.settings.app_base_url.rstrip('/')}/invite/{token}"
        if channel == "whatsapp" and whatsapp_to and link:
            self._send_invite(tid, "whatsapp", whatsapp_to, link, expires)
            link = None  # sent by the app: staff never see it
        elif channel == "email" and link:
            self._send_invite(tid, "email", None, link, expires)
            link = None
        return {"channel": channel, "expires_at": expires, "link": link}

    def _whatsapp_number(self, tenant: dict[str, Any]) -> str:
        from app.domains.notifications.service import latest_consents

        if "whatsapp" not in self._c.channels:
            raise Conflict(
                "WhatsApp sending is not set up yet. Use Share on WhatsApp to send the link yourself."
            )
        prefs = (tenant.get("notification_preferences") or {}).get("whatsapp") or {}
        number = prefs.get("number") or tenant.get("phone")
        if not number:
            raise ValidationFailed(
                "Add the tenant's phone number first",
                details=[{"field": "phone", "issue": "required to invite by WhatsApp"}],
            )
        if not latest_consents(self._c, self._ctx.org_id, tenant["id"]).get("whatsapp_messaging"):
            raise ValidationFailed(
                "Record the tenant's consent to WhatsApp messages first",
                details=[{"field": "channel", "issue": "no WhatsApp consent recorded"}],
            )
        return str(number)

    def _send_invite(self, tid: str, channel: str, to: str | None, link: str, expires: Any) -> None:
        """The welcome message with the set-password link, by email or WhatsApp. Sent now rather than
        queued, so the link never sits in a queue or the notification log. Emails carry the company's
        branding when it is on (contract v0.14.0)."""
        from app.core.dates import display_date
        from app.domains.notifications.templates import brand_wrap, render
        from app.integrations.channels import Message
        from app.repositories.notifications import log_item

        org = self._c.store.org(self._ctx.org_id).get_meta() or {}
        tenant = self._repo.get(tid) or {}
        locale = ((tenant.get("notification_preferences") or {}).get("locale")) or (
            org.get("settings") or {}
        ).get("default_locale", "en")
        brand = branding.public(org)  # contract v0.11.0: the company's own name when branding is on
        to = to or str(tenant.get("email") or "")
        params = {
            "name": tenant.get("full_name", ""),
            "org_name": brand["display_name"] if brand else org.get("name", ""),
            "expires": display_date(expires),
        }
        subject, text, html = render("tenant_invite", locale, {**params, "link": link})
        if channel == "email" and brand:
            base = (self._c.settings.public_api_url or self._c.settings.app_base_url).rstrip("/")
            logo = f"{base}{brand['logo_url']}" if brand.get("logo_url") else None
            text, html = brand_wrap(text, html, brand, locale, logo)
        now = self._c.clock.now()
        entry = log_item(
            self._ctx.org_id,
            new_id(),
            iso(now),
            {
                "tenant_id": tid,
                "channel": channel,
                "template": "tenant_invite",
                "locale": locale,
                "params": {**params, "link": "(set-password link, not stored)"},
            },
        )
        repo = self._c.store.notifications(self._ctx.org_id)
        try:
            pid = self._c.channels[channel].send(
                Message(
                    to,
                    subject,
                    text,
                    html,
                    "tenant_invite",
                    locale,
                    from_name=brand["display_name"] if brand and channel == "email" else None,
                    reply_to=brand.get("contact_email") if brand and channel == "email" else None,
                )
            )
        except Exception as e:  # noqa: BLE001
            repo.put({**entry, "status": "failed", "error": type(e).__name__})
            what = "email" if channel == "email" else "WhatsApp message"
            raise Conflict(
                f"The {what} could not be sent. Try again, or share the link yourself."
            ) from None
        repo.put({**entry, "status": "sent", "provider_message_id": pid})
        repo.message_lookup_put(pid, entry["created_at"][:7], entry["SK"])  # bounces find the entry

    # ---- delete (contract v0.9.0): remove a tenant added by mistake, or erase one with history
    def _tenant_deletion(
        self, tid: str
    ) -> tuple[Plan, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        from app.core.money import Money

        tenant = self.get(tid)  # manager scope applies
        repo = self._c.store.leases(self._ctx.org_id)
        leases = repo.get_many([x["lease_id"] for x in repo.all_links(tid)])
        plan = Plan()
        if tenant["status"] == "anonymised":
            plan.block("already_erased", "This tenant's personal data has already been erased.")
        if any(x["status"] in ("draft", "active") for x in leases):
            plan.block(
                "lease_open",
                "The tenant has an active or draft lease. End the lease, or delete the draft, first.",
            )
        ended = [x for x in leases if x["status"] in ("terminated", "expired")]
        owed = sum(max(x.get("balance_minor", 0), 0) for x in ended)
        if owed:
            plan.block(
                "unpaid",
                f"The tenant still owes {Money(owed).format()} on ended leases. Settle it first.",
                amount_minor=owed,
            )
        credit = sum(x.get("credit_minor", 0) for x in ended)
        if credit:
            plan.block(
                "credit",
                f"The tenant has {Money(credit).format()} in credit. Refund it first.",
                amount_minor=credit,
            )
        held = sum(d["amount_minor"] for x in ended for d in x.get("deposits_held", []))
        if held:
            plan.block(
                "deposits_held",
                f"Deposits of {Money(held).format()} are still held. Settle them first.",
                amount_minor=held,
            )
        items = self._c.store.privacy(self._ctx.org_id).subject_items(tid)
        docs = [i for i in items if i["entity_type"] == "document"]
        if plan.mode != "blocked":
            if leases:
                plan.mode = "erase"
                plan.note = (
                    "The tenant has lease history. Their personal data is erased now; leases, invoices "
                    "and receipts are kept without it until the retention period ends. Only an "
                    "administrator can do this."
                )
            else:
                plan.note = (
                    "The tenant never had a lease, so everything about them is removed, including "
                    "their login and their documents."
                )
        plan.count("documents", len(docs))
        plan.count("consents", sum(1 for i in items if i["entity_type"] == "consent"))
        plan.count("messages", sum(1 for i in items if i["entity_type"] == "notification_log"))
        plan.count(
            "maintenance_requests",
            sum(1 for i in items if i["entity_type"] == "maintenance_request"),
        )
        plan.count("leases", len(leases))
        return plan, tenant, items, docs

    def deletion(self, tid: str) -> dict[str, Any]:
        return self._tenant_deletion(tid)[0].view()

    def delete(self, tid: str, reason: str) -> dict[str, Any]:
        plan, tenant, items, docs = self._tenant_deletion(tid)
        plan.require_not_blocked()
        if plan.mode == "erase":
            return self._erase(tid, reason)
        org = self._ctx.org_id
        now = self._c.clock.now()
        ops: list[Any] = [
            self._audit.put_for(
                self._ctx,
                now,
                "tenant.delete",
                "tenant",
                tid,
                {"reason": reason, **plan.affects},  # ids and counts only: no personal data
            ),
            *keys_of(
                items
            ),  # the tenant record and everything indexed under them (PDPA subject index)
            self._repo.email_unlock_op(tenant["email"]),
        ]
        for mr in (i for i in items if i["entity_type"] == "maintenance_request"):
            ops += keys_of(partition(self._c, keys.mr_comment_pk(org, mr["id"])))
        sub = tenant.get("pending_sub") or tenant.get("cognito_sub")
        if sub:
            ops.append(Delete(key=(keys.invite_pk(sub), keys.META)))
        if tenant.get("invite_token_pk"):
            ops.append(Delete(key=(tenant["invite_token_pk"], keys.META)))
        write_all(self._c, ops)
        remove_files(self._c, docs)
        if sub:
            with contextlib.suppress(Exception):  # the login is useless without a tenant record
                self._c.identity.delete_user(sub)
        return {"outcome": "removed", "privacy_request_id": None}

    def _erase(self, tid: str, reason: str) -> dict[str, Any]:
        """Erasure through the privacy request flow, so it is tracked and audited like any other."""
        from app.domains.privacy.schemas import PrivacyRequestCreate, PrivacyRequestUpdate
        from app.domains.privacy.service import PrivacyService

        if Role.ORG_ADMIN not in self._ctx.roles:
            raise Forbidden("Only an administrator can erase a tenant with lease history")
        privacy = PrivacyService(self._c, self._ctx)
        req = privacy.create(
            PrivacyRequestCreate(
                type="deletion", tenant_id=tid, details=f"Deleted by staff: {reason}"[:2000]
            )
        )
        privacy.update(req["id"], PrivacyRequestUpdate(status="completed", decision_note=reason))
        return {"outcome": "erasure_started", "privacy_request_id": req["id"]}

    # ---- consents (PDPA: purpose, version, timestamp, source)
    def consents(
        self, tid: str, limit: int, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        self.get(tid)
        return self._repo.consents(tid, limit, cursor)

    def record_consent(self, tid: str, body: ConsentCreate) -> dict[str, Any]:
        self.get(tid)
        now = self._c.clock.now()
        source = "staff_on_behalf" if self._is_staff else "portal"
        item = consent_item(
            self._ctx.org_id,
            tid,
            new_id(),
            {
                "purpose": body.purpose,
                "policy_version": body.policy_version,
                "granted": body.granted,
                "source": source,
                "recorded_by": self._ctx.user_id,
            },
            iso(now),
        )
        self._c.store.table.transact(
            [
                self._repo.consent_put_op(item),
                self._audit.put_for(
                    self._ctx,
                    now,
                    "consent.record",
                    "tenant",
                    tid,
                    {"purpose": body.purpose, "granted": body.granted, "source": source},
                ),
            ]
        )
        return item


def _tenancy(statuses: set[str]) -> str:
    """Whether someone rents now, separate from their account status (contract v0.6.0).
    A tenant whose leases have all ended stays able to sign in (final invoices, deposit refunds) until
    staff mark them former."""
    if "active" in statuses:
        return "current"
    if "draft" in statuses:
        return "upcoming"
    if statuses & {"terminated", "expired"}:
        return "past"
    return "none"
