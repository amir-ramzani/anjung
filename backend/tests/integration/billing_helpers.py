from datetime import UTC, datetime
from typing import Any

from app.domains.billing.runs import sweep_org
from fastapi.testclient import TestClient

from tests.conftest import Org, World

REC = [{"name": "Parking", "amount": {"amount_minor": 10000, "currency": "MYR"}}]
SEC = [{"type": "security", "amount": {"amount_minor": 300000, "currency": "MYR"}}]


class Setup:
    def __init__(self, world: World, **lease_over: Any) -> None:
        self.world, self.client = world, world.client
        self.org: Org = world.make_org()
        self.unit = self.org.new_unit(self.org.new_property()["id"])
        self.tenant = self.org.new_tenant()
        over = {"recurring_charges": REC, "deposits": SEC, **lease_over}
        self.lease = self.org.new_lease(self.unit["id"], [self.tenant["id"]], **over)
        r = self.client.post(f"/v1/leases/{self.lease['id']}/activate", headers=self.org.admin)
        assert r.status_code == 200, r.text
        self.tenant_h = self.org.tenant_login(self.tenant)

    @property
    def h(self) -> dict[str, str]:
        return self.org.admin

    def idem(self, key: str) -> dict[str, str]:
        return {**self.h, "Idempotency-Key": key}

    def run(self, period: str, key: str | None = None) -> dict[str, Any]:
        r = self.client.post(
            "/v1/invoice-runs",
            headers=self.idem(key or f"run-{period}-{id(self)}"),
            json={"period": period},
        )
        assert r.status_code == 202, r.text
        return dict(r.json())

    def invoices(self, **params: Any) -> list[dict[str, Any]]:
        r = self.client.get("/v1/invoices", headers=self.h, params={"limit": 100, **params})
        assert r.status_code == 200, r.text
        return list(r.json()["items"])

    def rent_invoice(self, period: str = "2018-12") -> dict[str, Any]:
        return next(i for i in self.invoices(period=period) if i["line_items"][0]["type"] == "rent")

    def lease_now(self) -> dict[str, Any]:
        return dict(self.client.get(f"/v1/leases/{self.lease['id']}", headers=self.h).json())

    def ledger(self) -> list[dict[str, Any]]:
        return list(
            self.client.get(
                f"/v1/leases/{self.lease['id']}/ledger?limit=100", headers=self.h
            ).json()["items"]
        )

    def pay(
        self,
        reference: str,
        amount: int,
        event: str,
        *,
        txn: str | None = None,
        status: str = "succeeded",
        currency: str = "MYR",
        now: datetime | None = None,
    ) -> Any:
        prov = self.world.c.payments["fake"]
        body, headers = prov.signed_request(  # type: ignore[attr-defined]
            reference=reference,
            amount_minor=amount,
            now=now or self.world.c.clock.now(),
            event_id=event,
            txn_id=txn,
            status=status,
            currency=currency,
        )
        return self.client.post("/v1/webhooks/payments/fake", content=body, headers=headers)

    def qr(self, invoice_id: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        r = self.client.post(f"/v1/invoices/{invoice_id}/qr", headers=headers or self.tenant_h)
        assert r.status_code == 201, r.text
        return dict(r.json())

    def balance(self) -> int:
        return int(self.lease_now()["balance"]["amount_minor"])


def sweep(world: World, org: Org) -> dict[str, Any]:
    return sweep_org(world.c, org.id)


def at(world: World, y: int, m: int, d: int) -> None:
    world.c.clock.set(datetime(y, m, d, 4, 0, tzinfo=UTC))  # type: ignore[attr-defined]


_ = TestClient
