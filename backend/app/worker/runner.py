"""SQS consumer: long polling, bounded concurrency, backoff, DLQ, graceful shutdown (LLD 8.3)."""

from __future__ import annotations

import json
import logging
import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.worker import ses_events
from app.worker.envelope import SUPPORTED_VERSIONS, Envelope, Fatal, Retryable, consumer_span
from app.worker.queue import Handler

log = logging.getLogger(__name__)


def backoff_seconds(receive_count: int) -> int:
    """min(2^n * 5 s, 15 min), the LLD's retry curve."""
    return int(min(2 ** max(receive_count, 1) * 5, 900))


class Runner:
    def __init__(
        self,
        sqs: Any,
        queue_url: str,
        dlq_url: str,
        handlers: dict[str, Handler],
        concurrency: int = 10,
        wait_seconds: int = 20,
    ) -> None:
        self._sqs, self._url, self._dlq = sqs, queue_url, dlq_url
        self._handlers, self._wait = handlers, wait_seconds
        self._pool = ThreadPoolExecutor(max_workers=concurrency)
        self._stop = threading.Event()

    def stop(self, *_: object) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        while not self._stop.is_set():
            self.poll_once()
        self._pool.shutdown(wait=True)  # finish in-flight work before exiting (ECS stopTimeout)
        log.info("worker stopped")

    def poll_once(self) -> int:
        r = self._sqs.receive_message(
            QueueUrl=self._url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=self._wait,
            AttributeNames=["ApproximateReceiveCount"],
        )
        messages = r.get("Messages", [])
        list(self._pool.map(self._process, messages))
        return len(messages)

    def _dead_letter(self, msg: dict[str, Any], reason: str) -> None:
        self._sqs.send_message(QueueUrl=self._dlq, MessageBody=msg["Body"])
        self._sqs.delete_message(QueueUrl=self._url, ReceiptHandle=msg["ReceiptHandle"])
        log.error("message dead-lettered", extra={"reason": reason})

    def _process(self, msg: dict[str, Any]) -> None:
        try:
            body = json.loads(msg["Body"])
            if isinstance(body, dict) and ses_events.is_ses_event(body):
                envs = ses_events.envelopes(body)  # SES feedback through SNS, not an envelope
            else:
                envs = [Envelope.loads(msg["Body"])]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return self._dead_letter(msg, "unparseable")
        for env in envs:
            if self._handlers.get(env.type) is None or env.version not in SUPPORTED_VERSIONS:
                return self._dead_letter(
                    msg, "unknown type or version"
                )  # poison, straight to the DLQ
        count = int(msg.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
        try:
            for env in envs:  # each handler is idempotent, so a retry may repeat the earlier ones
                with consumer_span(env):
                    self._handlers[env.type](env)
        except Retryable:
            self._sqs.change_message_visibility(
                QueueUrl=self._url,
                ReceiptHandle=msg["ReceiptHandle"],
                VisibilityTimeout=backoff_seconds(count),
            )
            return None
        except Fatal:
            log.exception("fatal job error", extra={"job_type": env.type})
            return self._dead_letter(msg, "fatal")
        except Exception:
            log.exception(
                "job failed", extra={"job_type": env.type}
            )  # SQS redelivers, then redrives
            return None
        self._sqs.delete_message(QueueUrl=self._url, ReceiptHandle=msg["ReceiptHandle"])
        return None
