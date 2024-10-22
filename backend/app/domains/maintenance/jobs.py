"""Maintenance jobs (LLD 8.4): resolved requests close themselves after 7 days."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.concurrency import retry_on_version_conflict
from app.domains.notifications.events import publish
from app.repositories.maintenance import with_status_index

AUTOCLOSE_DAYS = 7


def autoclose(c: Container, org_id: str) -> int:
    repo = c.store.maintenance(org_id)
    cutoff = iso(c.clock.now() - timedelta(days=AUTOCLOSE_DAYS))
    closed = 0
    cursor = None
    while True:
        page, cursor = repo.page_by_status(
            "resolved",
            100,
            cursor,
            lambda it: bool(it["resolved_at"]) and it["resolved_at"] <= cutoff,
        )
        for req in page:
            done: dict[str, Any] = {}

            def attempt(rid: str = req["id"], out: dict[str, Any] = done) -> None:
                cur = repo.get(rid)
                if cur is None or cur["status"] != "resolved":
                    return
                now = c.clock.now()
                new = with_status_index(
                    {
                        **cur,
                        "status": "closed",
                        "closed_by": "system",
                        "version": cur["version"] + 1,
                        "updated_at": iso(now),
                    },
                    org_id,
                )
                c.store.table.transact(
                    [
                        repo.replace_op(new, cur["version"]),
                        c.store.audit(org_id).put_op(
                            actor_id="system",
                            actor_role="system",
                            action="maintenance.autoclose",
                            entity_type="maintenance_request",
                            entity_id=rid,
                            summary={"after_days": AUTOCLOSE_DAYS},
                            now=now,
                            request_id="job",
                            ip=None,
                        ),
                    ]
                )
                out.update(new)

            retry_on_version_conflict(attempt)
            if done:
                closed += 1
                if done.get("tenant_id"):
                    publish(
                        c,
                        org_id,
                        "maintenance_update",
                        tenant_ids=[done["tenant_id"]],
                        entity_id=done["id"],
                        params={"status": "closed"},
                        offset="closed",
                    )
        if cursor is None:
            return closed
