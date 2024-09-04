"""Worker entrypoint: `python -m app.worker.main housekeeping` (queue chosen by argument)."""

import json
import sys

import boto3

from app.container import Container
from app.core import tracing
from app.core.config import Settings
from app.core.logging import configure_logging
from app.worker.handlers.housekeeping import build_handlers
from app.worker.runner import Runner


def main() -> None:
    queue = sys.argv[1] if len(sys.argv) > 1 else "housekeeping"
    settings = Settings()
    configure_logging(settings.log_level, settings.env, service=f"worker-{queue}")
    tracing.configure(f"anjung-worker-{queue}", settings.otel_exporter_otlp_endpoint)
    urls = json.loads(settings.queue_urls_json or "{}")
    if queue not in urls or f"{queue}-dlq" not in urls:
        raise SystemExit(f"QUEUE_URLS_JSON needs '{queue}' and '{queue}-dlq'")
    container = Container.build(settings)
    sqs = boto3.client("sqs", region_name=settings.aws_region)
    Runner(sqs, urls[queue], urls[f"{queue}-dlq"], build_handlers(container)).run_forever()


if __name__ == "__main__":
    main()
