"""Publishing a notification event. Never raises: a messaging problem must not fail a money or lease action."""

from __future__ import annotations

import logging
from typing import Any

from app.container import Container
from app.worker.envelope import make_envelope

log = logging.getLogger(__name__)


def publish(
    c: Container,
    org_id: str,
    template: str,
    *,
    tenant_ids: list[str],
    entity_id: str,
    params: dict[str, Any] | None = None,
    offset: str = "",
) -> None:
    try:
        c.jobs.enqueue(
            "notifications",
            make_envelope(
                "notify",
                org_id,
                {
                    "template": template,
                    "tenant_ids": tenant_ids,
                    "entity_id": entity_id,
                    "params": params or {},
                    "offset": offset,
                },
                c.clock.now(),
            ),
        )
    except Exception:
        log.exception(
            "could not publish notification", extra={"org_id": org_id, "error_code": template}
        )
