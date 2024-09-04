"""Billing job handlers. Each is idempotent: SQS delivers at least once."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.container import Container
from app.core.dates import today_kl
from app.core.money import Currency, Money
from app.domains.billing.jobs import (
    expire_leases,
    integrity_check,
    overdue_sweep,
    reconcile_payments,
    termination_effects,
)
from app.domains.billing.payments import apply_event
from app.domains.billing.receipts import generate_receipt
from app.domains.billing.runs import execute_run, sweep_org
from app.domains.maintenance.jobs import autoclose
from app.domains.organisations import plan
from app.integrations.payments.base import PaymentEvent
from app.repositories.dynamo import TxCancelled
from app.worker.envelope import Envelope, Fatal, Retryable, make_envelope
from app.worker.handlers import notifications
from app.worker.queue import Handler


def payment_event(c: Container) -> Handler:
    def handle(env: Envelope) -> None:
        p = env.payload
        ev = PaymentEvent(
            provider=p["provider"],
            event_id=p["event_id"],
            gateway_txn_id=p["gateway_txn_id"],
            reference=p["reference"],
            amount=Money(p["amount_minor"], Currency(p["currency"])),
            status=p["status"],
            paid_at=datetime.fromisoformat(p["paid_at"]),
        )
        try:
            apply_event(c, ev)
        except TxCancelled as e:
            raise Retryable(str(e)) from None

    return handle


def invoice_run(c: Container) -> Handler:
    def handle(env: Envelope) -> None:
        repo = c.store.billing(env.org_id)
        run = repo.get_run(env.payload["period"], env.payload["run_id"])
        if run is None:
            raise Fatal("invoice run not found")
        execute_run(
            c,
            env.org_id,
            run,
            only_period=env.payload["period"],
            lease_ids=env.payload.get("lease_ids"),
        )

    return handle


def invoice_sweep(c: Container) -> Handler:
    def handle(env: Envelope) -> None:
        sweep_org(c, env.org_id)

    return handle


def generate_receipt_job(c: Container) -> Handler:
    def handle(env: Envelope) -> None:
        generate_receipt(c, env.org_id, env.payload["invoice_id"])

    return handle


def fan_out(c: Container, job_type: str, queue: str = "housekeeping") -> Handler:
    """Scheduler entry: one message per active organisation (LLD 8.4)."""

    def handle(env: Envelope) -> None:
        cursor = None
        while True:
            orgs, cursor = c.store.platform().list_registry(100, cursor, "active")
            for org in orgs:
                # a plan that ended past its grace period: the organisation is closed to everyone, so its
                # jobs stop too (contract v0.12.0). Read-only organisations keep billing their tenants.
                meta = c.store.org(org["org_id"]).get_meta()
                if plan.status(meta, today_kl(c.clock)) == "ended":
                    continue
                c.jobs.enqueue(queue, make_envelope(job_type, org["org_id"], {}, c.clock.now()))
            if cursor is None:
                return

    return handle


def build(c: Container) -> dict[str, Handler]:
    return {
        "payment_event": payment_event(c),
        "invoice_run": invoice_run(c),
        "invoice_sweep": invoice_sweep(c),
        "invoice_sweep_all": fan_out(c, "invoice_sweep", "invoicing"),
        "generate_receipt": generate_receipt_job(c),
        **build_jobs(c),
        **notifications.build(c, fan_out),
    }


def job(c: Container, fn: Any) -> Handler:
    def handle(env: Envelope) -> None:
        fn(c, env.org_id)

    return handle


def termination_job(c: Container) -> Handler:
    def handle(env: Envelope) -> None:
        termination_effects(c, env.org_id, env.payload["lease_id"])

    return handle


def reconcile_job(c: Container) -> Handler:
    def handle(env: Envelope) -> None:
        reconcile_payments(c, today_kl(c.clock) - timedelta(days=1))

    return handle


def build_jobs(c: Container) -> dict[str, Handler]:
    return {
        "overdue_sweep": job(c, overdue_sweep),
        "overdue_sweep_all": fan_out(c, "overdue_sweep"),
        "lease_expiry": job(c, expire_leases),
        "maintenance_autoclose": job(c, autoclose),
        "maintenance_autoclose_all": fan_out(c, "maintenance_autoclose"),
        "lease_expiry_all": fan_out(c, "lease_expiry"),
        "integrity_check": job(c, integrity_check),
        "integrity_check_all": fan_out(c, "integrity_check"),
        "lease_termination_effects": termination_job(c),
        "reconcile_payments": reconcile_job(c),
    }
