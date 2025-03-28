"""Deactivate, reactivate and delete, the same way in every module (contract v0.9.0, plan section C).

Delete has three outcomes, worked out by each service and shown first by a preview:
* `remove`: nothing depends on the record (made by mistake), so it is deleted completely;
* `erase`: a tenant with history; personal data is anonymised now and the financial records are kept until the
  retention period ends (the retention purge job removes them then);
* `blocked`: deleting would break something live; the blockers say what to do first.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from fastapi import Query

from app.container import Container
from app.core.errors import InvalidState
from app.domains.common import Response
from app.repositories import keys
from app.repositories.dynamo import Delete, Put, Update

log = logging.getLogger(__name__)
Item = dict[str, Any]

ReasonQ = Annotated[str, Query(min_length=3, max_length=500)]
Mode = Literal["remove", "erase", "blocked"]

INACTIVE = "inactive"
LEGACY_INACTIVE = (
    "archived"  # the name before contract v0.9.0, still accepted as input for one version
)


def normalise_status(item: Item | None) -> Item | None:
    """Properties and units written before v0.9.0 say `archived`: read them as `inactive`."""
    if item is not None and item.get("status") == LEGACY_INACTIVE:
        return {**item, "status": INACTIVE}
    return item


def status_in(value: str | None) -> str | None:
    """A status from the request: `archived` is accepted as an alias of `inactive`."""
    return INACTIVE if value == LEGACY_INACTIVE else value


class Blocker(Response):
    code: str
    message: str
    # the values in `message`, so clients can show it in the user's language (contract v0.16.0)
    params: dict[str, str | int] | None = None


class DeletionPreview(Response):
    mode: Mode
    blockers: list[Blocker] = []
    affects: dict[
        str, int
    ] = {}  # what a delete would take with it, e.g. {"units": 3, "documents": 2}
    note: str | None = None


class DeletionResult(Response):
    outcome: Literal["removed", "erasure_started"]
    privacy_request_id: str | None = None


@dataclass
class Plan:
    mode: Mode = "remove"
    blockers: list[dict[str, Any]] = field(default_factory=list)
    affects: dict[str, int] = field(default_factory=dict)
    note: str | None = None

    def block(self, code: str, message: str, **params: str | int) -> None:
        self.mode = "blocked"
        self.blockers.append(
            {"code": code, "message": message, **({"params": params} if params else {})}
        )

    def count(self, what: str, n: int) -> None:
        if n:
            self.affects[what] = self.affects.get(what, 0) + n

    def view(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "blockers": self.blockers,
            "affects": self.affects,
            "note": self.note,
        }

    def require_not_blocked(self) -> None:
        if self.mode == "blocked":
            raise InvalidState(" ".join(b["message"] for b in self.blockers))


def write_all(c: Container, ops: Iterable[Put | Update | Delete]) -> int:
    """Write in transactions of up to 100 operations. Used for deletes, which are safe to repeat:
    if a later chunk fails, running the delete again finishes the job."""
    batch: list[Any] = []
    n = 0
    seen: set[tuple[str, str]] = set()
    for op in ops:
        key = op.key if isinstance(op, (Delete, Update)) else (op.item["PK"], op.item["SK"])
        if key in seen:  # one item may be reached twice (e.g. a document by owner and by subject)
            continue
        seen.add(key)
        batch.append(op)
        if len(batch) == 100:
            c.store.table.transact(batch)
            n += len(batch)
            batch = []
    if batch:
        c.store.table.transact(batch)
        n += len(batch)
    return n


def documents_of(c: Container, org_id: str, owner_type: str, owner_id: str) -> list[Item]:
    repo = c.store.documents(org_id)
    out: list[Item] = []
    cursor = None
    while True:
        page, cursor = repo.page_for_owner(owner_type, owner_id, 100, cursor)
        out += page
        if cursor is None:
            return out


def delete_documents(c: Container, docs: list[Item]) -> list[Delete]:
    """The table deletes for these documents. Their files are removed by `remove_files` after the commit."""
    return [Delete(key=(d["PK"], d["SK"])) for d in docs]


def remove_files(c: Container, docs: list[Item]) -> None:
    """Best effort: a file left behind is unreachable (no document points at it) and is reported, not fatal."""
    if c.storage is None:
        return
    for d in docs:
        if d.get("s3_key"):
            try:
                c.storage.delete(d["s3_key"])
            except Exception:  # noqa: BLE001
                log.warning("could not delete a document file", extra={"entity_id": d.get("id")})


def keys_of(items: Iterable[Item]) -> list[Delete]:
    return [Delete(key=(i["PK"], i["SK"])) for i in items]


def partition(c: Container, pk: str) -> list[Item]:
    out: list[Item] = []
    start = None
    while True:
        items, start = c.store.table.query(pk=pk, start_key=start)
        out += items
        if start is None:
            return out


def lease_ever_on(c: Container, org_id: str, unit_id: str) -> bool:
    page, _ = c.store.leases(org_id).page_for_unit(unit_id, 1, None, None)
    return bool(page)


def maintenance_on(c: Container, org_id: str, *, property_id: str, unit_ids: set[str]) -> int:
    """Maintenance requests raised for this property (shared areas) or any of these units."""
    repo = c.store.maintenance(org_id)
    n = 0
    cursor = None
    while True:
        page, cursor = repo.page_all(100, cursor, None)
        n += sum(
            1
            for r in page
            if r.get("unit_id") in unit_ids
            or (r.get("property_id") == property_id and not r.get("unit_id"))
        )
        if cursor is None:
            return n


def unit_lock_keys(org_id: str, unit_ids: Iterable[str]) -> list[Delete]:
    return [Delete(key=(keys.org_pk(org_id), keys.unit_lock_sk(u))) for u in unit_ids]
