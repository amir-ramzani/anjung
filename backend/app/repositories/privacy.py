from __future__ import annotations

from typing import Any

from app.repositories import keys
from app.repositories.base import Item, OrgRepository
from app.repositories.dynamo import NOT_EXISTS, Put


def privacy_request_item(org_id: str, rid: str, fields: dict[str, Any], now: str) -> Item:
    return {
        "PK": keys.org_pk(org_id),
        "SK": keys.privacy_request_sk(rid),
        "GSI5PK": keys.subject_gsi5pk(org_id, fields["tenant_id"]),
        "GSI5SK": f"PRIVREQ#{rid}",
        "entity_type": "privacy_request",
        "schema_version": 1,
        "org_id": org_id,
        "id": rid,
        **fields,
        "status": "received",
        "decision_note": None,
        "completed_at": None,
        "version": 1,
        "received_at": now,
    }


class PrivacyRepository(OrgRepository):
    # ---- requests
    def get(self, rid: str) -> Item | None:
        return self._t.get(self.pk, keys.privacy_request_sk(rid))

    def page(
        self, limit: int, cursor: str | None, tenant_id: str | None
    ) -> tuple[list[Item], str | None]:
        return self._page(
            pk=self.pk,
            sk_prefix="PRIVREQ#",
            limit=limit,
            cursor=cursor,
            forward=False,  # newest first
            accept=lambda it: tenant_id is None or it["tenant_id"] == tenant_id,
        )

    def create_op(self, item: Item) -> Put:
        return Put(item=item, condition=NOT_EXISTS)

    def replace_op(self, item: Item, expected_version: int) -> Put:
        return Put(
            item=item,
            condition="#v = :v",
            names={"#v": "version"},
            values={":v": expected_version},
        )

    # ---- data-subject index (GSI5): every item that holds a tenant's personal data
    def subject_keys(self, tenant_id: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        start = None
        pk = keys.subject_gsi5pk(self.org_id, tenant_id)
        while True:
            items, start = self._t.query(pk=pk, index="GSI5", start_key=start)
            out += [(i["PK"], i["SK"]) for i in items]
            if start is None:
                return out

    def subject_items(self, tenant_id: str) -> list[Item]:
        return self._t.batch_get(self.subject_keys(tenant_id))
