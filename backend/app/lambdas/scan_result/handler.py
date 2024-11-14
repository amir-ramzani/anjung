"""Document scan lifecycle (LLD 10.1 step 4).

pending_upload --(S3 ObjectCreated)--> scanning --(scanner verdict)--> clean | infected

`on_object_created` and `on_scan_result` are plain functions so the Lambda wrappers, the tests
and the local dev endpoint share one implementation.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.clock import Clock, iso
from app.integrations.storage import DocumentStorage, parse_key
from app.repositories import keys
from app.repositories.dynamo import DynamoTable, Put, TxCancelled

log = logging.getLogger(__name__)


def _load(table: DynamoTable, key: str) -> dict[str, Any] | None:
    parsed = parse_key(key)
    if parsed is None:
        return None
    org_id, did = parsed
    doc = table.get(keys.org_pk(org_id), keys.doc_sk(did))
    return doc if doc and doc.get("s3_key") == key else None


def _set_status(
    table: DynamoTable, doc: dict[str, Any], status: str, clock: Clock, **extra: Any
) -> None:
    new = {
        **doc,
        **extra,
        "status": status,
        "version": doc["version"] + 1,
        "updated_at": iso(clock.now()),
    }
    table.transact(
        [Put(item=new, condition="#v = :v", names={"#v": "version"}, values={":v": doc["version"]})]
    )


def on_object_created(table: DynamoTable, key: str, clock: Clock) -> str:
    doc = _load(table, key)
    if doc is None:
        log.warning("object without a document record")
        return "ignored"
    if doc["status"] != "pending_upload":
        return str(doc["status"])
    try:
        _set_status(table, doc, "scanning", clock)
    except TxCancelled:
        return "retry"
    return "scanning"


def on_scan_result(
    table: DynamoTable, storage: DocumentStorage, key: str, verdict: str, clock: Clock
) -> str:
    """`verdict` is clean or infected. Anything else marks the document failed."""
    doc = _load(table, key)
    if doc is None or doc["status"] not in ("pending_upload", "scanning"):
        return "ignored"
    if verdict == "clean":
        storage.tag(key, "clean")
        _set_status(table, doc, "clean", clock)
        return "clean"
    if verdict == "infected":
        moved = storage.quarantine(key)  # unreachable through any presigned path afterwards
        _set_status(table, doc, "infected", clock, s3_key=moved)
        log.error(
            "infected upload quarantined", extra={"org_id": doc["org_id"], "entity_id": doc["id"]}
        )
        return "infected"
    _set_status(table, doc, "failed", clock)
    return "failed"
