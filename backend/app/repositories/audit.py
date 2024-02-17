"""Audit log (LLD 15.3). Entries are written inside the same transaction as the change."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.clock import iso
from app.core.context import RequestContext
from app.core.ids import new_id
from app.repositories import keys
from app.repositories.base import Item, OrgRepository
from app.repositories.dynamo import NOT_EXISTS, Put


class AuditRepository(OrgRepository):
    def put_op(
        self,
        *,
        actor_id: str,
        actor_role: str,
        action: str,
        entity_type: str,
        entity_id: str,
        summary: dict[str, Any],
        now: datetime,
        request_id: str,
        ip: str | None,
    ) -> Put:
        ts, eid = iso(now), new_id()
        item: Item = {
            "PK": keys.audit_pk(self.org_id, ts[:7]),
            "SK": keys.audit_sk(ts, eid),
            "GSI1PK": keys.audit_entity_gsi1pk(self.org_id, entity_type, entity_id),
            "GSI1SK": keys.audit_sk(ts, eid),
            "entity_type": "audit",
            "schema_version": 1,
            "org_id": self.org_id,
            "id": eid,
            "actor_id": actor_id,
            "actor_role": actor_role,
            "action": action,
            "target_type": entity_type,
            "entity_id": entity_id,
            "summary": summary,
            "ip": ip,
            "request_id": request_id,
            "created_at": ts,
        }
        return Put(item=item, condition=NOT_EXISTS)

    def put_for(
        self,
        ctx: RequestContext,
        now: datetime,
        action: str,
        entity_type: str,
        entity_id: str,
        summary: dict[str, Any] | None = None,
    ) -> Put:
        return self.put_op(
            actor_id=ctx.user_id,
            actor_role=ctx.actor_role,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary or {},
            now=now,
            request_id=ctx.request_id,
            ip=ctx.ip,
        )

    def page(
        self,
        *,
        month: str | None,
        entity_type: str | None,
        entity_id: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Item], str | None]:
        if entity_type and entity_id:
            return self._page(
                pk=keys.audit_entity_gsi1pk(self.org_id, entity_type, entity_id),
                sk_prefix=f"{month}" if month else None,
                index="GSI1",
                limit=limit,
                cursor=cursor,
                forward=False,
            )
        if month is None:
            raise ValueError("month is required without an entity filter")
        accept = None
        if entity_type or entity_id:
            accept = lambda it: (  # noqa: E731
                (not entity_type or it["target_type"] == entity_type)
                and (not entity_id or it["entity_id"] == entity_id)
            )
        return self._page(
            pk=keys.audit_pk(self.org_id, month),
            sk_prefix=None,
            limit=limit,
            cursor=cursor,
            forward=False,
            accept=accept,
        )


def diff_summary(
    old: dict[str, Any], new: dict[str, Any], fields: list[str], opaque: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Changed fields only. `opaque` fields list their name but never values."""
    changed: dict[str, Any] = {}
    for f in fields:
        if old.get(f) != new.get(f):
            changed[f] = "changed" if f in opaque else {"from": old.get(f), "to": new.get(f)}
    return {"changed": changed}
