from __future__ import annotations

from app.repositories import keys
from app.repositories.base import Item, OrgRepository
from app.repositories.dynamo import NOT_EXISTS, Delete, Put


def member_item(
    org_id: str, sub: str, email: str, name: str | None, roles: list[str], now: str
) -> Item:
    return {
        "PK": keys.org_pk(org_id),
        "SK": keys.member_sk(sub),
        "GSI2PK": keys.sub_gsi2pk(sub),
        "GSI2SK": keys.member_gsi2sk(org_id),
        "entity_type": "membership",
        "schema_version": 1,
        "org_id": org_id,
        "user_id": sub,
        "email": email,
        "name": name,
        "roles": roles,
        "status": "invited",
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }


class MemberRepository(OrgRepository):
    def get(self, sub: str) -> Item | None:
        return self._t.get(self.pk, keys.member_sk(sub))

    def page(self, limit: int, cursor: str | None) -> tuple[list[Item], str | None]:
        return self._page(pk=self.pk, sk_prefix="MEMBER#", limit=limit, cursor=cursor)

    def put_new_op(self, item: Item) -> Put:
        return Put(item=item, condition=NOT_EXISTS)

    def replace_op(self, item: Item, expected_version: int) -> Put:
        return Put(
            item=item,
            condition="#v = :v",
            names={"#v": "version"},
            values={":v": expected_version},
        )

    def delete_op(self, sub: str, expected_version: int) -> Delete:
        return Delete(
            key=(self.pk, keys.member_sk(sub)),
            condition="#v = :v",
            names={"#v": "version"},
            values={":v": expected_version},
        )
