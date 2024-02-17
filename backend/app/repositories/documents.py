from __future__ import annotations

from typing import Any

from app.repositories import keys
from app.repositories.base import Item, OrgRepository
from app.repositories.dynamo import NOT_EXISTS, Delete, Put

_VER = "#v = :v"
_NAMES = {"#v": "version"}


def document_item(org_id: str, did: str, fields: dict[str, Any], now: str) -> Item:
    item: Item = {
        "PK": keys.org_pk(org_id),
        "SK": keys.doc_sk(did),
        "GSI1PK": keys.doc_owner_gsi1pk(org_id, fields["owner_type"], fields["owner_id"]),
        "GSI1SK": keys.doc_sk(did),
        "entity_type": "document",
        "schema_version": 1,
        "org_id": org_id,
        "id": did,
        **fields,
        "status": "pending_upload",
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    if fields["owner_type"] == "tenant":  # tenant-owned: part of the data-subject index
        item["GSI5PK"] = keys.subject_gsi5pk(org_id, fields["owner_id"])
        item["GSI5SK"] = keys.doc_sk(did)
    return item


class DocumentRepository(OrgRepository):
    def get(self, did: str) -> Item | None:
        return self._t.get(self.pk, keys.doc_sk(did))

    def page_for_owner(
        self, owner_type: str, owner_id: str, limit: int, cursor: str | None
    ) -> tuple[list[Item], str | None]:
        """Newest first. The owner partition is the GSI1 key written when the document was created."""
        return self._page(
            pk=keys.doc_owner_gsi1pk(self.org_id, owner_type, owner_id),
            sk_prefix="DOC#",
            index="GSI1",
            limit=limit,
            cursor=cursor,
            forward=False,
        )

    def create_op(self, item: Item) -> Put:
        return Put(item=item, condition=NOT_EXISTS)

    def replace_op(self, item: Item, expected_version: int) -> Put:
        return Put(item=item, condition=_VER, names=_NAMES, values={":v": expected_version})

    def delete_op(self, did: str) -> Delete:
        return Delete(key=(self.pk, keys.doc_sk(did)))
