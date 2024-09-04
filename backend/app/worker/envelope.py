"""Message envelope (LLD 8.2) and the job-queue port."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.core.clock import iso
from app.core.ids import new_id

SUPPORTED_VERSIONS = {1}


@dataclass(frozen=True)
class Envelope:
    type: str
    org_id: str
    payload: dict[str, Any]
    occurred_at: str
    version: int = 1
    message_id: str = field(default_factory=new_id)
    trace_id: str = ""
    traceparent: str = (
        ""  # W3C trace context of whoever enqueued this: the worker continues that trace
    )

    def dumps(self) -> str:
        return json.dumps(self.__dict__, separators=(",", ":"))

    @classmethod
    def loads(cls, raw: str) -> Envelope:
        d = json.loads(raw)
        return cls(
            type=d["type"],
            org_id=d["org_id"],
            payload=d["payload"],
            occurred_at=d["occurred_at"],
            version=d["version"],
            message_id=d["message_id"],
            trace_id=d.get("trace_id", ""),
            traceparent=d.get("traceparent", ""),
        )


def make_envelope(type_: str, org_id: str, payload: dict[str, Any], now: datetime) -> Envelope:
    from app.core import tracing

    tp = tracing.traceparent()
    return Envelope(
        type=type_,
        org_id=org_id,
        payload=payload,
        occurred_at=iso(now),
        trace_id=tp.split("-")[1] if tp else "",
        traceparent=tp,
    )


class JobQueue(Protocol):
    def enqueue(
        self, queue: str, env: Envelope, *, group_id: str | None = None, dedup_id: str | None = None
    ) -> None: ...


def consumer_span(env: Envelope) -> Any:
    """The span a job runs in: a child of the enqueuing span when the envelope carries one."""
    from opentelemetry.trace import SpanKind

    from app.core import tracing

    return tracing.span(
        f"process {env.type}",
        kind=SpanKind.CONSUMER,
        traceparent=env.traceparent or None,
        attributes={
            "messaging.message.id": env.message_id,
            "job.type": env.type,
            "org_id": env.org_id,
        },
    )


class Retryable(Exception):
    """Transient failure: leave the message for redelivery with backoff."""


class Fatal(Exception):
    """Poison: log, count and drop (it will not succeed on retry)."""
