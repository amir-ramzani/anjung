"""Cross-organisation access. Only domains/platform and the Cognito Lambdas may use this
(LLD 3.3 rule 5). Enforced by tests/unit/test_tenancy_rules.py."""

from __future__ import annotations

from app.core.pagination import CursorCodec
from app.repositories import keys
from app.repositories.base import Item, item_key
from app.repositories.dynamo import DynamoTable


class PlatformRepository:
    def __init__(self, table: DynamoTable, cursors: CursorCodec) -> None:
        self._t, self._cursors = table, cursors

    def get_org(self, org_id: str) -> Item | None:
        return self._t.get(keys.org_pk(org_id), keys.META)

    def list_registry(
        self, limit: int, cursor: str | None, status: str | None
    ) -> tuple[list[Item], str | None]:
        pk = keys.REGISTRY_PK
        start = self._cursors.decode(cursor, pk=pk, index=None) if cursor else None
        found: list[Item] = []
        while True:
            items, last = self._t.query(pk=pk, limit=limit + 1, start_key=start)
            for it in items:
                if status is None or it["status"] == status:
                    found.append(it)
                    if len(found) > limit:
                        edge = item_key(found[limit - 1], None)
                        return found[:limit], self._cursors.encode(key=edge, index=None, pk=pk)
            if last is None:
                return found, None
            start = last

    def get_orgs(self, org_ids: list[str]) -> list[Item]:
        return self._t.batch_get([(keys.org_pk(o), keys.META) for o in org_ids])
