from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.repositories import keys
from app.repositories.base import Item, OrgRepository
from app.repositories.dynamo import NOT_EXISTS, Delete, Put

_VER = "#v = :v"
_NAMES = {"#v": "version"}


def tenant_item(org_id: str, tid: str, fields: dict[str, Any], now: str) -> Item:
    return {
        "PK": keys.org_pk(org_id),
        "SK": keys.tenant_sk(tid),
        "GSI5PK": keys.subject_gsi5pk(org_id, tid),  # data-subject index (PDPA)
        "GSI5SK": keys.tenant_sk(tid),
        "entity_type": "tenant",
        "schema_version": 1,
        "org_id": org_id,
        "id": tid,
        "tenant_id": tid,
        "roles": ["tenant"],
        **fields,
        "status": "invited",
        "linked": False,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }


def consent_item(org_id: str, tid: str, cid: str, fields: dict[str, Any], now: str) -> Item:
    return {
        "PK": keys.org_pk(org_id),
        "SK": keys.consent_sk(tid, now, cid),
        "GSI5PK": keys.subject_gsi5pk(org_id, tid),
        "GSI5SK": f"CONSENT#{cid}",
        "entity_type": "consent",
        "schema_version": 1,
        "org_id": org_id,
        "tenant_id": tid,
        "id": cid,
        **fields,
        "recorded_at": now,
    }


class TenantRepository(OrgRepository):
    def get(self, tid: str) -> Item | None:
        return self._t.get(self.pk, keys.tenant_sk(tid))

    def get_many(self, ids: list[str]) -> list[Item]:
        return self._t.batch_get([(self.pk, keys.tenant_sk(i)) for i in ids])

    def page(
        self,
        limit: int,
        cursor: str | None,
        status: str | None,
        email: str | None,
        also: Callable[[Item], bool] | None = None,
    ) -> tuple[list[Item], str | None]:
        # consent items share the TENANT# prefix, so filter on entity_type
        return self._page(
            pk=self.pk,
            sk_prefix="TENANT#",
            limit=limit,
            cursor=cursor,
            accept=lambda it: (
                it["entity_type"] == "tenant"
                and (status is None or it["status"] == status)
                and (email is None or it["email"] == email.lower())
                and (also is None or also(it))
            ),
        )

    def create_op(self, item: Item) -> Put:
        return Put(item=item, condition=NOT_EXISTS)

    def replace_op(self, item: Item, expected_version: int) -> Put:
        return Put(item=item, condition=_VER, names=_NAMES, values={":v": expected_version})

    def email_lock_op(self, email: str, tid: str) -> Put:
        return Put(
            item={
                "PK": self.pk,
                "SK": keys.tenant_email_lock_sk(email),
                "entity_type": "tenant_email_lock",
                "org_id": self.org_id,
                "tenant_id": tid,
            },
            condition=NOT_EXISTS,
        )

    def email_unlock_op(self, email: str) -> Delete:
        return Delete(key=(self.pk, keys.tenant_email_lock_sk(email)))

    def invite_put_op(self, item: Item) -> Put:
        return Put(item=item)

    def consents(self, tid: str, limit: int, cursor: str | None) -> tuple[list[Item], str | None]:
        return self._page(
            pk=self.pk,
            sk_prefix=keys.consent_prefix(tid),
            limit=limit,
            cursor=cursor,
            forward=False,
        )

    def consent_put_op(self, item: Item) -> Put:
        return Put(item=item, condition=NOT_EXISTS)
