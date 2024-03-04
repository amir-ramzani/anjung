"""Idempotency-Key handling (LLD 3.6). A client convenience on top of business-level idempotency
(INVKEY, PAYREF, WHEVT), never the only protection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from app.container import Container
from app.core.clock import iso
from app.core.context import RequestContext
from app.core.errors import Conflict, IdempotencyConflict
from app.repositories import keys
from app.repositories.dynamo import ConditionFailed

TTL = timedelta(hours=24)


def request_hash(method: str, path: str, body: BaseModel | None) -> str:
    raw = body.model_dump_json() if body is not None else ""
    return hashlib.sha256(f"{method} {path} {raw}".encode()).hexdigest()


def run_idempotent(
    c: Container,
    ctx: RequestContext,
    key: str,
    method: str,
    path: str,
    body: BaseModel | None,
    fn: Callable[[], Any],
) -> Any:
    """First call runs `fn` and stores its response. The same key and body replays that response,
    a different body is `409 idempotency_conflict`, a call still running is `409 conflict`."""
    pk = f"IDEMP#{ctx.org_id}#{key}"
    digest = request_hash(method, path, body)
    now = c.clock.now()
    record = {
        "PK": pk,
        "SK": keys.META,
        "entity_type": "idempotency",
        "request_hash": digest,
        "response": None,
        "created_at": iso(now),
        "ttl": int((now + TTL).timestamp()),
    }
    try:
        c.store.table.put(record, condition="attribute_not_exists(PK)")
    except ConditionFailed:
        existing = c.store.table.get(pk, keys.META)
        if existing is None:  # expired between the two calls: just try again
            return run_idempotent(c, ctx, key, method, path, body, fn)
        if existing["request_hash"] != digest:
            raise IdempotencyConflict() from None
        if existing["response"] is None:
            raise Conflict(
                "A request with this Idempotency-Key is still running", headers={"Retry-After": "1"}
            ) from None
        return json.loads(existing["response"])
    try:
        result = fn()
    except BaseException:
        c.store.table.transact([_delete(pk)])  # let the client retry after a failure
        raise
    encoded = jsonable_encoder(result)
    c.store.table.put({**record, "response": json.dumps(encoded)})
    return encoded


def _delete(pk: str) -> Any:
    from app.repositories.dynamo import Delete

    return Delete(key=(pk, keys.META))
