import json
from datetime import UTC, datetime
from typing import Any

from app.core.clock import iso
from app.worker.envelope import Envelope, Fatal, Retryable, make_envelope
from app.worker.queue import InlineJobQueue, SqsJobQueue
from app.worker.runner import Runner, backoff_seconds

NOW = datetime(2018, 11, 20, tzinfo=UTC)
ORG = "01JD8Z3K9Q4M7T2V6X8B5N1C0A"


class FakeSqs:
    """Just enough SQS: receive counts, delete, visibility changes and a DLQ."""

    def __init__(self) -> None:
        self.queues: dict[str, list[dict[str, Any]]] = {"q": [], "dlq": []}
        self.visibility: dict[str, int] = {}
        self.deleted: list[str] = []
        self._n = 0

    def send_message(self, QueueUrl: str, MessageBody: str) -> None:  # noqa: N803
        self._n += 1
        self.queues[QueueUrl].append(
            {
                "Body": MessageBody,
                "ReceiptHandle": f"r{self._n}",
                "Attributes": {"ApproximateReceiveCount": "1"},
            }
        )

    def receive_message(self, QueueUrl: str, **_: Any) -> dict[str, Any]:  # noqa: N803
        return {"Messages": list(self.queues[QueueUrl])}

    def delete_message(self, QueueUrl: str, ReceiptHandle: str) -> None:  # noqa: N803
        self.queues[QueueUrl] = [
            m for m in self.queues[QueueUrl] if m["ReceiptHandle"] != ReceiptHandle
        ]
        self.deleted.append(ReceiptHandle)

    def change_message_visibility(
        self, QueueUrl: str, ReceiptHandle: str, VisibilityTimeout: int
    ) -> None:  # noqa: N803
        self.visibility[ReceiptHandle] = VisibilityTimeout


def env(type_: str = "job", version: int = 1) -> str:
    e = make_envelope(type_, ORG, {"x": 1}, NOW)
    return Envelope(**{**e.__dict__, "version": version}).dumps()


def runner(sqs: FakeSqs, handlers: dict[str, Any]) -> Runner:
    return Runner(sqs, "q", "dlq", handlers, concurrency=2, wait_seconds=0)


def test_envelope_roundtrip() -> None:
    e = make_envelope("payment_event", ORG, {"a": 1}, NOW)
    assert Envelope.loads(e.dumps()) == e
    assert e.occurred_at == iso(NOW) and len(e.message_id) == 26 and e.version == 1


def test_success_deletes_the_message() -> None:
    sqs, seen = FakeSqs(), []
    sqs.send_message("q", env())
    assert runner(sqs, {"job": lambda e: seen.append(e.payload)}).poll_once() == 1
    assert seen == [{"x": 1}] and sqs.queues["q"] == [] and sqs.queues["dlq"] == []


def test_unknown_type_version_and_garbage_go_straight_to_the_dlq() -> None:
    sqs = FakeSqs()
    for body in (env("mystery"), env("job", version=99), "not json", json.dumps({"type": "job"})):
        sqs.send_message("q", body)
    runner(sqs, {"job": lambda e: None}).poll_once()
    assert sqs.queues["q"] == [] and len(sqs.queues["dlq"]) == 4  # poison is never retried


def test_retryable_backs_off_and_stays_on_the_queue() -> None:
    sqs = FakeSqs()
    sqs.send_message("q", env())
    sqs.queues["q"][0]["Attributes"]["ApproximateReceiveCount"] = "3"

    def boom(_: Envelope) -> None:
        raise Retryable("db busy")

    runner(sqs, {"job": boom}).poll_once()
    assert len(sqs.queues["q"]) == 1 and sqs.visibility == {"r1": 40}  # 2^3 * 5 s
    assert [backoff_seconds(n) for n in (1, 2, 5, 10, 20)] == [10, 20, 160, 900, 900]


def test_fatal_is_dead_lettered_and_unexpected_errors_are_left_for_redelivery() -> None:
    sqs = FakeSqs()
    sqs.send_message("q", env("fatal"))
    sqs.send_message("q", env("crash"))

    def fatal(_: Envelope) -> None:
        raise Fatal("bad data")

    def crash(_: Envelope) -> None:
        raise RuntimeError("bug")

    runner(sqs, {"fatal": fatal, "crash": crash}).poll_once()
    assert len(sqs.queues["dlq"]) == 1
    assert [Envelope.loads(m["Body"]).type for m in sqs.queues["q"]] == [
        "crash"
    ]  # SQS will redeliver, then redrive


def test_inline_queue_runs_handlers_and_swallows_failures() -> None:
    seen: list[str] = []

    def bad(_: Envelope) -> None:
        raise RuntimeError("boom")

    q = InlineJobQueue(lambda: {"ok": lambda e: seen.append(e.type), "bad": bad})
    q.enqueue("housekeeping", make_envelope("ok", ORG, {}, NOW))
    q.enqueue("housekeeping", make_envelope("bad", ORG, {}, NOW))  # must not raise into the request
    q.enqueue("housekeeping", make_envelope("nobody", ORG, {}, NOW))
    assert seen == ["ok"] and [e.type for e in q.sent] == ["ok", "bad", "nobody"]


def test_sqs_queue_sends_the_envelope() -> None:
    sqs = FakeSqs()
    SqsJobQueue(sqs, {"housekeeping": "q"}).enqueue(
        "housekeeping", make_envelope("job", ORG, {"k": 1}, NOW)
    )
    assert Envelope.loads(sqs.queues["q"][0]["Body"]).payload == {"k": 1}
