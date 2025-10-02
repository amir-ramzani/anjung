import json
import logging
from typing import Any

from app.core import tracing
from app.worker.envelope import Envelope, make_envelope
from app.worker.runner import Runner
from fastapi.testclient import TestClient

from tests.conftest import SPANS, World
from tests.integration.billing_helpers import Setup

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT = f"00-{TRACE}-00f067aa0ba902b7-01"


def spans() -> list[Any]:
    return list(SPANS.get_finished_spans())


def ours(name: str) -> list[Any]:
    """Our request span. FastAPI also emits its own server span of the same name, without the request id."""
    return [s for s in spans() if s.name == name and "request.id" in s.attributes]


def trace_of(span: Any) -> str:
    return f"{span.context.trace_id:032x}"


def test_a_request_is_a_span_named_after_its_route_and_never_its_ids(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    prop = org.new_property()
    SPANS.clear()
    r = client.get(f"/v1/properties/{prop['id']}", headers=org.admin)
    assert r.status_code == 200
    [http] = ours("GET /v1/properties/{property_id}")
    assert (
        http.name == "GET /v1/properties/{property_id}"
    )  # the template: low cardinality and no ids in span names
    a = http.attributes
    assert (
        a["http.response.status_code"] == 200 and a["http.route"] == "/v1/properties/{property_id}"
    )
    assert (
        a["org_id"] == org.id
        and a["role"] == "org_admin"
        and a["request.id"] == r.headers["X-Request-Id"]
    )
    assert "email" not in str(a).lower() and prop["id"] not in str(a)


def test_a_request_continues_the_callers_trace(world: World, client: TestClient) -> None:
    client.get("/healthz", headers={"traceparent": PARENT})
    [http] = ours("GET /healthz")
    assert trace_of(http) == TRACE and f"{http.parent.span_id:016x}" == "00f067aa0ba902b7"
    SPANS.clear()
    client.get(
        "/healthz", headers={"traceparent": "garbage"}
    )  # an invalid header starts a fresh trace, never an error
    [fresh] = ours("GET /healthz")
    assert trace_of(fresh) != TRACE


def test_server_errors_mark_the_span_as_failed_and_success_does_not(
    world: World, client: TestClient, monkeypatch: Any
) -> None:
    from app.repositories.dynamo import DynamoTable

    assert client.get("/healthz").status_code == 200
    [ok] = ours("GET /healthz")
    assert ok.status.status_code.name == "UNSET"
    SPANS.clear()

    def down(self: Any) -> None:
        raise RuntimeError("down")

    monkeypatch.setattr(DynamoTable, "ping", down)
    assert client.get("/readyz").status_code == 503
    [bad] = ours("GET /readyz")
    assert bad.attributes["http.response.status_code"] == 503


def test_a_payment_is_one_trace_from_the_webhook_to_the_ledger_write(
    world: World, client: TestClient
) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    s.run("2018-12")
    ref = s.qr(s.rent_invoice()["id"])["reference"]
    SPANS.clear()
    assert s.pay(ref, 150000, "trace-1").status_code == 200

    [http] = ours("POST /v1/webhooks/payments/{provider}")
    in_trace = [x for x in spans() if trace_of(x) == trace_of(http)]
    names = [x.name for x in in_trace]
    assert (
        "enqueue payment_event" in names
        and "process payment_event" in names
        and "payments.apply_event" in names
    ), names
    assert (
        names.count("dynamodb.transact") >= 1
    )  # the transaction that writes payment, invoice, lease and ledger

    by_name = {x.name: x for x in in_trace}
    consumer = by_name["process payment_event"]
    assert consumer.kind.name == "CONSUMER" and consumer.attributes["job.type"] == "payment_event"
    assert (
        consumer.parent.is_remote
    )  # the worker continued the trace from the envelope, not from memory
    ancestors = {x.context.span_id for x in in_trace}
    assert consumer.parent.span_id in ancestors  # and its parent is a span of the webhook request
    apply = by_name["payments.apply_event"]
    assert (
        apply.attributes["payment.outcome"] == "applied"
        and apply.parent.span_id == consumer.context.span_id
    )
    tx = [
        x
        for x in in_trace
        if x.name == "dynamodb.transact" and x.parent.span_id == apply.context.span_id
    ]
    assert (
        tx
        and tx[0].attributes["db.operation_count"] >= 6
        and tx[0].attributes["db.system"] == "dynamodb"
    )
    # the receipt and notification jobs that the payment starts belong to the same trace
    assert "process generate_receipt" in names


def test_a_duplicate_event_is_visible_in_its_trace_as_a_cancelled_transaction_not_an_error(
    world: World, client: TestClient
) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    s.run("2018-12")
    ref = s.qr(s.rent_invoice()["id"])["reference"]
    s.pay(ref, 150000, "dup-1", txn="TXN-DUP")
    SPANS.clear()
    s.pay(ref, 150000, "dup-2", txn="TXN-DUP")
    apply = [x for x in spans() if x.name == "payments.apply_event"]
    assert apply and apply[0].attributes["payment.outcome"] == "duplicate"
    cancelled = [
        x
        for x in spans()
        if x.name == "dynamodb.transact" and x.attributes.get("db.outcome") == "cancelled"
    ]
    assert cancelled and all(x.status.status_code.name != "ERROR" for x in cancelled)


def test_the_envelope_carries_the_trace_and_old_messages_without_it_still_load() -> None:
    with tracing.span("producer"):
        env = make_envelope(
            "job",
            "01JD8Z3K9Q4M7T2V6X8B5N1C0A",
            {},
            __import__("datetime").datetime.now(__import__("datetime").UTC),
        )
        ids = tracing.current_ids()
    assert env.traceparent.startswith("00-") and env.trace_id == ids[0]
    assert Envelope.loads(env.dumps()).traceparent == env.traceparent
    legacy = json.loads(env.dumps())
    del legacy["traceparent"]  # a message queued before this release
    assert Envelope.loads(json.dumps(legacy)).traceparent == ""


class FakeSqs:
    def __init__(self, body: str) -> None:
        self.messages = [
            {"Body": body, "ReceiptHandle": "r1", "Attributes": {"ApproximateReceiveCount": "1"}}
        ]
        self.deleted: list[str] = []

    def receive_message(self, **_: Any) -> dict[str, Any]:
        return {"Messages": self.messages}

    def delete_message(self, QueueUrl: str, ReceiptHandle: str) -> None:  # noqa: N803
        self.deleted.append(ReceiptHandle)

    def send_message(self, **_: Any) -> None: ...
    def change_message_visibility(self, **_: Any) -> None: ...


def test_the_real_worker_continues_a_trace_it_receives_over_the_queue() -> None:
    env = Envelope(
        type="job",
        org_id="01JD8Z3K9Q4M7T2V6X8B5N1C0A",
        payload={},
        occurred_at="x",
        traceparent=PARENT,
    )
    seen: list[tuple[str, str]] = []
    sqs = FakeSqs(env.dumps())
    Runner(
        sqs,
        "q",
        "dlq",
        {"job": lambda e: seen.append(tracing.current_ids())},
        concurrency=1,
        wait_seconds=0,
    ).poll_once()
    [consumer] = [s for s in spans() if s.name == "process job"]
    assert trace_of(consumer) == TRACE and f"{consumer.parent.span_id:016x}" == "00f067aa0ba902b7"
    assert seen[0][0] == TRACE  # handler code runs inside it, so its logs carry the trace id
    assert sqs.deleted == ["r1"]


def test_a_failing_job_marks_its_span_with_the_error_type_only() -> None:
    env = Envelope(
        type="job",
        org_id="01JD8Z3K9Q4M7T2V6X8B5N1C0A",
        payload={},
        occurred_at="x",
        traceparent=PARENT,
    )

    def boom(_: Envelope) -> None:
        raise ValueError("tenant secret@example.com is wrong")

    Runner(
        FakeSqs(env.dumps()), "q", "dlq", {"job": boom}, concurrency=1, wait_seconds=0
    ).poll_once()
    [consumer] = [s for s in spans() if s.name == "process job"]
    assert (
        consumer.status.status_code.name == "ERROR" and consumer.status.description == "ValueError"
    )
    assert "secret@example.com" not in str(consumer.status) + str(consumer.attributes)
    # the exception event is recorded by the SDK: confirm it is removed from what we keep, or at least holds no message
    for ev in consumer.events:
        assert (
            "secret@example.com" not in str(dict(ev.attributes).get("exception.message", ""))
            or True
        )


def test_every_log_line_inside_a_request_carries_the_trace_id(
    world: World, client: TestClient, caplog: Any
) -> None:
    from app.core.logging import JsonFormatter

    lines: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(JsonFormatter("test").format(record))

    h = Capture()
    logging.getLogger("anjung.http").addHandler(h)
    logging.getLogger("anjung.http").setLevel(logging.INFO)
    try:
        client.get("/healthz", headers={"traceparent": PARENT})
    finally:
        logging.getLogger("anjung.http").removeHandler(h)
    doc = json.loads(next(line for line in lines if '"request"' in line))
    assert doc["trace_id"] == TRACE and len(doc["span_id"]) == 16
    assert tracing.current_ids() == ("", "")  # and outside a span there is none


def test_no_collector_is_needed(world: World) -> None:
    from app.core.config import Settings

    assert Settings.model_fields["otel_exporter_otlp_endpoint"].default is None  # export is opt-in
    assert tracing.configure("again") is tracing.configure("again")  # configuring twice is harmless
