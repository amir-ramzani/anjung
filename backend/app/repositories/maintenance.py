from __future__ import annotations

from typing import Any

from app.repositories import keys
from app.repositories.base import Item, OrgRepository
from app.repositories.dynamo import NOT_EXISTS, Put

OPEN_STATES = ("open", "triaged", "assigned", "in_progress", "resolved")


def request_item(org_id: str, rid: str, fields: dict[str, Any], now: str) -> Item:
    item: Item = {
        "PK": keys.org_pk(org_id),
        "SK": keys.mr_sk(rid),
        "GSI4PK": keys.mr_status_gsi4pk(org_id, "open"),
        "GSI4SK": f"{now}#{rid}",
        "entity_type": "maintenance_request",
        "schema_version": 1,
        "org_id": org_id,
        "id": rid,
        "assigned_to": None,
        "resolved_at": None,
        **fields,
        "status": "open",
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    if fields.get("tenant_id"):
        item["GSI1PK"] = keys.mr_tenant_gsi1pk(org_id, fields["tenant_id"])
        item["GSI1SK"] = f"MR#{now}#{rid}"
        item["GSI5PK"] = keys.subject_gsi5pk(org_id, fields["tenant_id"])  # data-subject index
        item["GSI5SK"] = f"MR#{rid}"
    return item


def with_status_index(item: Item, org_id: str) -> Item:
    """GSI4 holds the request under its current status, rewritten on every status change (LLD 4.5)."""
    return {
        **item,
        "GSI4PK": keys.mr_status_gsi4pk(org_id, item["status"]),
        "GSI4SK": f"{item['created_at']}#{item['id']}",
    }


def comment_item(org_id: str, request_id: str, cid: str, fields: dict[str, Any], now: str) -> Item:
    return {
        "PK": keys.mr_comment_pk(org_id, request_id),
        "SK": f"CMT#{cid}",
        "entity_type": "maintenance_comment",
        "schema_version": 1,
        "org_id": org_id,
        "id": cid,
        "request_id": request_id,
        **fields,
        "created_at": now,
    }


class MaintenanceRepository(OrgRepository):
    def get(self, rid: str) -> Item | None:
        return self._t.get(self.pk, keys.mr_sk(rid))

    def page_all(
        self, limit: int, cursor: str | None, accept: Any
    ) -> tuple[list[Item], str | None]:
        return self._page(
            pk=self.pk, sk_prefix="MR#", limit=limit, cursor=cursor, forward=False, accept=accept
        )

    def page_by_status(
        self, status: str, limit: int, cursor: str | None, accept: Any
    ) -> tuple[list[Item], str | None]:
        return self._page(
            pk=keys.mr_status_gsi4pk(self.org_id, status),
            sk_prefix=None,
            index="GSI4",
            limit=limit,
            cursor=cursor,
            forward=False,
            accept=accept,
        )

    def page_for_tenant(
        self, tid: str, limit: int, cursor: str | None, accept: Any
    ) -> tuple[list[Item], str | None]:
        return self._page(
            pk=keys.mr_tenant_gsi1pk(self.org_id, tid),
            sk_prefix="MR#",
            index="GSI1",
            limit=limit,
            cursor=cursor,
            forward=False,
            accept=accept,
        )

    def comments(
        self, rid: str, limit: int, cursor: str | None, accept: Any
    ) -> tuple[list[Item], str | None]:
        return self._page(
            pk=keys.mr_comment_pk(self.org_id, rid),
            sk_prefix="CMT#",
            limit=limit,
            cursor=cursor,
            accept=accept,
        )

    def create_op(self, item: Item) -> Put:
        return Put(item=item, condition=NOT_EXISTS)

    def replace_op(self, item: Item, expected_version: int) -> Put:
        return Put(
            item=item, condition="#v = :v", names={"#v": "version"}, values={":v": expected_version}
        )
