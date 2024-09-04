"""Job handlers. Each is idempotent: SQS delivers at least once."""

from __future__ import annotations

from app.container import Container
from app.core.clock import iso
from app.core.errors import InvalidState, NotFound
from app.domains.organisations.plan import send_reminders
from app.domains.privacy.anonymise import anonymise_tenant
from app.domains.privacy.retention import purge_closed_orgs, purge_org
from app.repositories.dynamo import TxCancelled
from app.worker.envelope import Envelope, Fatal, Retryable
from app.worker.queue import Handler


def anonymise_tenant_job(c: Container) -> Handler:
    def handle(env: Envelope) -> None:
        tid, rid = env.payload["tenant_id"], env.payload.get("privacy_request_id")
        try:
            anonymise_tenant(c, env.org_id, tid)
        except (NotFound, InvalidState) as e:
            raise Fatal(str(e)) from None  # will not succeed on retry
        except TxCancelled as e:
            raise Retryable(str(e)) from None
        if rid:
            repo = c.store.privacy(env.org_id)
            req = repo.get(rid)
            if req and req["status"] != "completed":
                done = {
                    **req,
                    "status": "completed",
                    "completed_at": iso(c.clock.now()),
                    "version": req["version"] + 1,
                }
                c.store.table.transact([repo.replace_op(done, req["version"])])

    return handle


def retention_purge_job(c: Container) -> Handler:
    """Per organisation; off until RETENTION_FINANCIAL_DAYS is set (decision D5). `dry_run` reports only."""

    def handle(env: Envelope) -> None:
        purge_org(c, env.org_id, dry_run=bool(env.payload.get("dry_run")))

    return handle


def closed_org_purge_job(c: Container) -> Handler:
    def handle(env: Envelope) -> None:
        purge_closed_orgs(c, dry_run=bool(env.payload.get("dry_run")))

    return handle


def build_handlers(c: Container) -> dict[str, Handler]:
    from app.worker.handlers import billing

    return {
        "anonymise_tenant": anonymise_tenant_job(c),
        "retention_purge": retention_purge_job(c),
        "retention_purge_all": billing.fan_out(c, "retention_purge"),
        "closed_org_purge": closed_org_purge_job(c),
        "plan_reminders": billing.job(c, send_reminders),
        "plan_reminders_all": billing.fan_out(c, "plan_reminders"),
        **billing.build(c),
    }
