from datetime import date

import boto3
from app.core.money import Money
from app.domains.billing.jobs import (
    expire_leases,
    integrity_check,
    overdue_sweep,
    reconcile_payments,
    termination_effects,
    whole_months,
)
from app.integrations.payments.base import SettlementRecord
from app.repositories.dynamo import Update
from fastapi.testclient import TestClient

from tests.conftest import REGION, World
from tests.integration.billing_helpers import Setup, at


def set_late_fee(client: TestClient, s: Setup, rule: dict) -> None:  # type: ignore[type-arg]
    r = client.patch("/v1/org", headers=s.h, json={"settings": {"late_fee": rule}})
    assert r.status_code == 200, r.text


def test_months_overdue() -> None:
    assert whole_months(date(2018, 12, 5), date(2018, 12, 20)) == 0
    assert whole_months(date(2018, 12, 5), date(2019, 1, 4)) == 0
    assert whole_months(date(2018, 12, 5), date(2019, 1, 5)) == 1
    assert whole_months(date(2018, 12, 5), date(2019, 2, 20)) == 2


def test_overdue_with_grace_and_fixed_late_fee_once(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    set_late_fee(
        client,
        s,
        {
            "type": "fixed",
            "grace_days": 2,
            "fixed_amount": {"amount_minor": 5000, "currency": "MYR"},
        },
    )
    s.run("2018-12")
    inv = s.rent_invoice()
    at(world, 2018, 12, 7)  # due on the 5th plus 2 days of grace: still not overdue today
    assert overdue_sweep(world.c, s.org.id) == {"marked_overdue": 0, "late_fees": 0}
    at(world, 2018, 12, 8)
    assert overdue_sweep(world.c, s.org.id) == {"marked_overdue": 1, "late_fees": 1}
    got = client.get(f"/v1/invoices/{inv['id']}", headers=s.h).json()
    assert got["status"] == "overdue" and got["total"]["amount_minor"] == 155000
    assert got["line_items"][-1]["type"] == "late_fee"
    assert s.balance() == 155000 and s.ledger()[-1]["type"] == "charge_late_fee"
    again = overdue_sweep(world.c, s.org.id)  # idempotent: nothing new on a re-run or the next day
    at(world, 2018, 12, 9)
    again2 = overdue_sweep(world.c, s.org.id)
    assert again == again2 == {"marked_overdue": 0, "late_fees": 0} and s.balance() == 155000


def test_percent_late_fee_per_whole_month(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    set_late_fee(client, s, {"type": "percent_per_month", "grace_days": 0, "percent_bp": 150})
    s.run("2018-12")
    inv = s.rent_invoice()
    at(world, 2018, 12, 10)
    assert overdue_sweep(world.c, s.org.id)["late_fees"] == 0  # not yet a whole month
    at(world, 2019, 1, 10)
    assert overdue_sweep(world.c, s.org.id)["late_fees"] == 1
    at(world, 2019, 2, 10)
    assert (
        overdue_sweep(world.c, s.org.id)["late_fees"] == 1
    )  # month 2, same base: 1.5% of the rent, not compounding
    got = client.get(f"/v1/invoices/{inv['id']}", headers=s.h).json()
    fees = [x["amount"]["amount_minor"] for x in got["line_items"] if x["type"] == "late_fee"]
    assert (
        fees == [2250, 2250] and got["total"]["amount_minor"] == 154500
    )  # 1.5% of 150000, integer basis points


def test_no_late_fee_when_the_rule_is_none_or_invoice_is_paid(
    world: World, client: TestClient
) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    s.run("2018-12")
    at(world, 2018, 12, 20)
    assert overdue_sweep(world.c, s.org.id) == {"marked_overdue": 1, "late_fees": 0}
    s2 = Setup(world, deposits=[], recurring_charges=[])
    set_late_fee(
        client,
        s2,
        {
            "type": "fixed",
            "grace_days": 0,
            "fixed_amount": {"amount_minor": 5000, "currency": "MYR"},
        },
    )
    s2.run("2018-12")
    inv = s2.rent_invoice()
    s2.pay(s2.qr(inv["id"])["reference"], 150000, "paid-in-time")
    assert overdue_sweep(world.c, s2.org.id) == {"marked_overdue": 0, "late_fees": 0}


def test_lease_expiry_releases_the_unit(world: World, client: TestClient) -> None:
    s = Setup(world, start_date="2018-12-01", end_date="2019-02-28", deposits=[])
    at(world, 2019, 2, 28)
    assert expire_leases(world.c, s.org.id) == 0  # the end date itself is still inside the lease
    at(world, 2019, 3, 1)
    assert expire_leases(world.c, s.org.id) == 1
    assert s.lease_now()["status"] == "expired"
    u = client.get(f"/v1/units/{s.unit['id']}", headers=s.h).json()
    assert u["status"] == "vacant" and u["current_lease_id"] is None
    assert expire_leases(world.c, s.org.id) == 0
    items, _ = world.c.store.table.query(pk=f"ORG#{s.org.id}#LEASE#active", index="GSI4")
    assert items == []
    rolling = Setup(world, end_date=None, deposits=[])
    at(world, 2022, 1, 1)
    assert expire_leases(world.c, rolling.org.id) == 0  # rolling leases never expire


def test_deposit_is_held_after_payment_and_settled_on_termination(
    world: World, client: TestClient
) -> None:
    s = Setup(world, recurring_charges=[])
    dep = next(i for i in s.invoices() if i["line_items"][0]["type"] == "deposit")
    s.pay(s.qr(dep["id"])["reference"], 300000, "dep-pay")
    lease = s.lease_now()
    assert lease["deposits_held"] == [
        {
            "type": "security",
            "amount": {"amount_minor": 300000, "currency": "MYR"},
            "description": "Security deposit",
        }
    ]
    led = s.ledger()
    assert [e["type"] for e in led] == ["charge_other", "payment", "deposit_received"]
    assert (
        led[-1]["balance_after"]["amount_minor"] == 0 and s.balance() == 0
    )  # held money is not a balance
    # rent for December is invoiced, and a future month too
    s.run("2018-12")
    s.run("2019-01")
    assert s.balance() == 300000
    r = client.post(
        f"/v1/leases/{s.lease['id']}/terminate",
        headers=s.h,
        json={
            "termination_date": "2018-12-20",
            "reason": "moved out",
            "deposit_deduction": [
                {"type": "security", "amount": {"amount_minor": 50000, "currency": "MYR"}}
            ],
        },
    )
    assert r.status_code == 200
    # the follow-up job ran (inline): January is void, December is prorated 20/31, deposit settled
    jan = s.rent_invoice("2019-01")
    assert jan["status"] == "void"
    dec = s.rent_invoice("2018-12")
    assert (
        dec["total"]["amount_minor"] == 96774
        and "to termination date" in dec["line_items"][0]["description"]
    )
    types = [e["type"] for e in s.ledger()]
    assert types[-4:] == [
        "adjustment",
        "adjustment",
        "adjustment",
        "deposit_returned",
    ]  # prorate, void, kept, returned
    lease = s.lease_now()
    assert lease["deposits_held"] == [] and lease["balance"]["amount_minor"] == 96774 - 50000
    returned = [e for e in s.ledger() if e["type"] == "deposit_returned"]
    assert [e["amount"]["amount_minor"] for e in returned] == [
        -250000
    ]  # the rest goes back to the tenant
    assert termination_effects(world.c, s.org.id, s.lease["id"]) == {
        "voided": 0,
        "credited": 0,
        "deposit_entries": 0,
    }
    assert integrity_check(world.c, s.org.id) == []  # everything still adds up


def test_prorating_the_final_period_after_payment_creates_credit(
    world: World, client: TestClient
) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    s.run("2018-12")
    inv = s.rent_invoice()
    s.pay(s.qr(inv["id"])["reference"], 150000, "full")
    client.post(
        f"/v1/leases/{s.lease['id']}/terminate",
        headers=s.h,
        json={"termination_date": "2018-12-20", "reason": "r"},
    )
    got = client.get(f"/v1/invoices/{inv['id']}", headers=s.h).json()
    assert (
        got["total"]["amount_minor"] == 96774
        and got["paid"]["amount_minor"] == 96774
        and got["status"] == "paid"
    )
    lease = s.lease_now()
    assert lease["credit"]["amount_minor"] == 53226 and lease["balance"]["amount_minor"] == -53226
    assert integrity_check(world.c, s.org.id) == []


def test_integrity_check_detects_tampering(world: World, client: TestClient) -> None:
    s = Setup(world)
    s.run("2018-12")
    assert integrity_check(world.c, s.org.id) == []
    world.c.store.table.transact(
        [
            Update(
                key=(f"ORG#{s.org.id}", f"LEASE#{s.lease['id']}"),
                expression="SET balance_minor = :b",
                values={":b": 1},
            )
        ]
    )
    problems = integrity_check(world.c, s.org.id)
    assert [p["kind"] for p in problems] == ["balance_mismatch"] and problems[0]["balance"] == 1
    inv = s.rent_invoice()
    world.c.store.table.transact(
        [
            Update(
                key=(f"ORG#{s.org.id}#LEASE#{s.lease['id']}", f"INV#2018-12#{inv['id']}"),
                expression="SET paid_minor = :p",
                values={":p": 777},
            )
        ]
    )
    assert {p["kind"] for p in integrity_check(world.c, s.org.id)} == {
        "balance_mismatch",
        "invoice_paid_mismatch",
    }


def test_reconciliation_report(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    s.run("2018-12")
    inv = s.rent_invoice()
    s.pay(s.qr(inv["id"])["reference"], 150000, "recon-1", txn="TXN-OK")
    day = date(2018, 11, 19)
    assert reconcile_payments(world.c, day) == []
    prov = world.c.payments["fake"]
    prov.settlements.append(SettlementRecord("TXN-MISSING", Money(777), "ANJX"))  # type: ignore[attr-defined]
    prov.settlements.append(SettlementRecord("TXN-OK", Money(149999), "ANJY"))  # type: ignore[attr-defined]
    issues = reconcile_payments(world.c, day)
    assert {i["kind"] for i in issues} == {"missing_payment", "amount_mismatch"}
    body = (
        boto3.client("s3", region_name=REGION)
        .get_object(Bucket="anjung-docs-test", Key=f"reports/reconciliation/fake/{day}.json")[
            "Body"
        ]
        .read()
    )
    assert b"TXN-MISSING" in body


def test_fan_out_sweeps_every_active_org_and_skips_suspended_ones(
    world: World, client: TestClient
) -> None:
    from app.worker.envelope import make_envelope

    a, b = (
        Setup(world, deposits=[], recurring_charges=[]),
        Setup(world, deposits=[], recurring_charges=[]),
    )
    client.patch(
        f"/v1/platform/organizations/{b.org.id}",
        headers=world.platform,
        json={"status": "suspended", "reason": "unpaid"},
    )
    at(world, 2018, 11, 29)
    world.c.jobs.enqueue(
        "invoicing",
        make_envelope(
            "invoice_sweep_all", world.c.settings.platform_org_id, {}, world.c.clock.now()
        ),
    )
    assert len([i for i in a.invoices() if i["period"] == "2018-12"]) == 1
    raw, _ = world.c.store.table.query(pk=f"ORG#{b.org.id}#LEASE#{b.lease['id']}", sk_prefix="INV#")
    assert raw == []  # a suspended organisation is skipped by the scheduler
