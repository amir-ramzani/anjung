"""Repository base: org-scoped by construction (LLD section 3.3)."""

from collections.abc import Callable
from typing import Any

from app.core.ids import is_id
from app.core.pagination import CursorCodec
from app.repositories import keys
from app.repositories.dynamo import DynamoTable

Item = dict[str, Any]


def item_key(item: Item, index: str | None) -> dict[str, Any]:
    names = ["PK", "SK"] + ([f"{index}PK", f"{index}SK"] if index else [])
    return {n: item[n] for n in names}


class OrgRepository:
    """Every repository needs an org_id. A missing one is a TypeError, not a data leak."""

    def __init__(self, table: DynamoTable, cursors: CursorCodec, org_id: str) -> None:
        if not is_id(org_id):
            raise ValueError("invalid org_id")
        self._t, self._cursors, self.org_id = table, cursors, org_id

    @property
    def pk(self) -> str:
        return keys.org_pk(self.org_id)

    def _page(
        self,
        *,
        pk: str,
        sk_prefix: str | None,
        limit: int,
        cursor: str | None,
        index: str | None = None,
        forward: bool = True,
        accept: Callable[[Item], bool] | None = None,
    ) -> tuple[list[Item], str | None]:
        """Page through a query. The cursor is the key of the last item returned, signed and
        bound to this partition, so filtering never skips or repeats items."""
        scope = f"{pk}|{sk_prefix or ''}"  # binds the cursor to this exact query, not just the org
        start = self._cursors.decode(cursor, pk=scope, index=index) if cursor else None
        found: list[Item] = []
        while True:
            items, last = self._t.query(
                pk=pk,
                sk_prefix=sk_prefix,
                index=index,
                forward=forward,
                limit=limit + 1,
                start_key=start,
            )
            for it in items:
                if accept is None or accept(it):
                    found.append(it)
                    if len(found) > limit:
                        edge = item_key(found[limit - 1], index)
                        return found[:limit], self._cursors.encode(key=edge, index=index, pk=scope)
            if last is None:
                return found, None
            start = last
