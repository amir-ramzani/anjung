from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.core import tracing
from app.worker.envelope import Envelope, consumer_span

log = logging.getLogger(__name__)
Handler = Callable[[Envelope], None]


class SqsJobQueue:
    """Production queue. `urls` maps logical queue names to SQS URLs."""

    def __init__(self, sqs: Any, urls: dict[str, str]) -> None:
        self._sqs, self._urls = sqs, urls

    def enqueue(
        self, queue: str, env: Envelope, *, group_id: str | None = None, dedup_id: str | None = None
    ) -> None:
        extra: dict[str, str] = {}
        if (
            group_id
        ):  # FIFO queues (payment events): ordered per reference, de-duplicated by event id
            extra = {
                "MessageGroupId": group_id,
                "MessageDeduplicationId": dedup_id or env.message_id,
            }
        with tracing.span(f"enqueue {env.type}", attributes={"messaging.destination.name": queue}):
            # the envelope was built before this span, so it carries the caller's context: that is the parent we want
            self._sqs.send_message(QueueUrl=self._urls[queue], MessageBody=env.dumps(), **extra)


class InlineJobQueue:
    """Local and test queue: runs the handler straight away, after the caller's commit.
    Failures are logged and swallowed like an async worker would, so request code never
    depends on job success."""

    def __init__(self, handlers: Callable[[], dict[str, Handler]]) -> None:
        self._handlers = handlers
        self.sent: list[Envelope] = []

    def enqueue(
        self, queue: str, env: Envelope, *, group_id: str | None = None, dedup_id: str | None = None
    ) -> None:
        self.sent.append(env)
        handler = self._handlers().get(env.type)
        if handler is None:
            log.error("no handler for job", extra={"job_type": env.type})
            return
        with tracing.span(f"enqueue {env.type}", attributes={"messaging.destination.name": queue}):
            try:
                with consumer_span(env):
                    handler(env)
            except Exception:
                log.exception("inline job failed", extra={"job_type": env.type})
