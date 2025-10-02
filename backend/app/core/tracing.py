"""Distributed tracing with OpenTelemetry (plan P5-05).

A trace follows one piece of work across the API, the queue and the worker: an HTTP request becomes a span, the job
it enqueues carries the W3C `traceparent` in its envelope, and the worker continues the same trace. So a payment can be
followed from the webhook to the ledger write, and every log line carries the trace id.

Ids are always generated, even with no collector, so logs are correlated everywhere. Spans leave the process only when
OTEL_EXPORTER_OTLP_ENDPOINT is set (in AWS: the collector sidecar, which forwards to X-Ray).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_provider: TracerProvider | None = None
_propagator = TraceContextTextMapPropagator()


def configure(
    service: str, otlp_endpoint: str | None = None, extra_exporters: Sequence[SpanExporter] = ()
) -> TracerProvider:
    """Set up the global tracer once. Later calls return the same provider (OpenTelemetry allows one)."""
    global _provider
    if _provider is not None:
        return _provider
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces"))
        )
    for exporter in extra_exporters:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _provider = provider
    return provider


def add_processor(processor: SpanProcessor) -> None:
    if _provider is None:
        raise RuntimeError("tracing is not configured")
    _provider.add_span_processor(processor)


def _tracer() -> trace.Tracer:
    return trace.get_tracer("anjung")


@contextmanager
def span(
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Mapping[str, Any] | None = None,
    traceparent: str | None = None,
) -> Iterator[trace.Span]:
    """A span that is the current one inside the block. With `traceparent` it continues someone else's trace."""
    token = None
    if traceparent:
        token = otel_context.attach(_propagator.extract({"traceparent": traceparent}))
    try:
        with (
            _tracer().start_as_current_span(
                name,
                kind=kind,
                attributes=dict(attributes or {}),
                record_exception=False,
                set_status_on_exception=False,  # the SDK would store the message: it may hold personal data
            ) as s
        ):
            try:
                yield s
            except Exception as e:
                s.set_status(
                    Status(StatusCode.ERROR, type(e).__name__)
                )  # the type only: messages may hold personal data
                raise
    finally:
        if token is not None:
            otel_context.detach(token)


def traceparent() -> str:
    """The W3C header for the current span, to put on an outgoing message. Empty when there is none."""
    carrier: dict[str, str] = {}
    _propagator.inject(carrier)
    return carrier.get("traceparent", "")


def current_ids() -> tuple[str, str]:
    """(trace_id, span_id) as hex for logs, or empty strings outside any span."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return "", ""
    return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"
