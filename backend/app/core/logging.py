"""JSON logging with redaction of personal data keys (LLD section 15.4)."""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_REDACT = re.compile(r"(email|phone|name|identity|token|authorization|secret|password)", re.I)
_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def redact(fields: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in fields.items():
        if _REDACT.search(k):
            out[k] = "[redacted]"
        elif isinstance(v, dict):
            out[k] = redact(v)
        else:
            out[k] = v
    return out


class JsonFormatter(logging.Formatter):
    def __init__(self, env: str, service: str = "api") -> None:
        super().__init__()
        self._base = {"env": env, "service": service}

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "msg": record.getMessage(),
            **self._base,
        }
        from app.core.tracing import current_ids

        trace_id, span_id = current_ids()
        if trace_id:
            doc["trace_id"], doc["span_id"] = trace_id, span_id
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        doc.update(redact(extras))
        if record.exc_info:
            doc["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            doc["stack"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str)


def configure_logging(level: str, env: str, service: str = "api") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(env, service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
