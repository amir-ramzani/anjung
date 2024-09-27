from __future__ import annotations

from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.concurrency import retry_on_version_conflict
from app.core.context import RequestContext
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.core.ids import new_id
from app.domains.tenants.schemas import NotificationPreferences
from app.repositories.tenants import consent_item

Item = dict[str, Any]


class PreferenceService:
    """A tenant's own channel settings. They apply to the very next message (LLD 9.2)."""

    def __init__(self, c: Container, ctx: RequestContext) -> None:
        self._c, self._ctx = c, ctx
        self._repo = c.store.tenants(ctx.org_id)

    def _tenant(self) -> Item:
        if not self._ctx.tenant_id:
            raise Forbidden()
        t = self._repo.get(self._ctx.tenant_id)
        if t is None:
            raise NotFound()
        return t

    def get(self) -> Item:
        prefs: Item = self._tenant().get("notification_preferences") or {}
        return prefs

    def put(self, body: NotificationPreferences) -> Item:
        wa = body.whatsapp
        if wa and wa.enabled and not wa.number:
            raise ValidationFailed(
                details=[{"field": "whatsapp.number", "issue": "required to enable WhatsApp"}]
            )

        def attempt() -> Item:
            old = self._tenant()
            prev = old.get("notification_preferences") or {}
            now = self._c.clock.now()
            new: Item = {}
            if body.locale:
                new["locale"] = body.locale
            if body.email:
                new["email"] = {
                    "enabled": body.email.enabled,
                    **({"address": body.email.address} if body.email.address else {}),
                }
            if wa:
                was = prev.get("whatsapp") or {}
                same_number = was.get("number") == wa.number
                opted = was.get("opted_in_at") if (was.get("enabled") and same_number) else None
                new["whatsapp"] = {
                    "enabled": wa.enabled,
                    **({"number": wa.number} if wa.number else {}),
                    # opt-in is stamped by the server; switching WhatsApp off revokes it
                    "opted_in_at": (opted or iso(now)) if wa.enabled else None,
                }
            merged = {**prev, **new}
            item = {
                **old,
                "notification_preferences": merged,
                "version": old["version"] + 1,
                "updated_at": iso(now),
            }
            ops: list[Any] = [self._repo.replace_op(item, old["version"])]
            for channel, purpose in (
                ("email", "email_messaging"),
                ("whatsapp", "whatsapp_messaging"),
            ):
                was_on = bool((prev.get(channel) or {}).get("enabled"))
                now_on = bool((merged.get(channel) or {}).get("enabled"))
                if was_on and not now_on:  # opting out withdraws consent in the same transaction
                    ops.append(
                        self._repo.consent_put_op(
                            consent_item(
                                self._ctx.org_id,
                                old["id"],
                                new_id(),
                                {
                                    "purpose": purpose,
                                    "policy_version": "preferences",
                                    "granted": False,
                                    "source": "portal",
                                    "recorded_by": self._ctx.user_id,
                                },
                                iso(now),
                            )
                        )
                    )
            ops.append(
                self._c.store.audit(self._ctx.org_id).put_for(
                    self._ctx,
                    now,
                    "notification.preferences",
                    "tenant",
                    old["id"],
                    {
                        "email": merged.get("email", {}).get("enabled"),
                        "whatsapp": merged.get("whatsapp", {}).get("enabled"),
                    },
                )
            )
            self._c.store.table.transact(ops)
            return merged

        out: Item = retry_on_version_conflict(attempt)
        return out
