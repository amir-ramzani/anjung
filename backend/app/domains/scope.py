"""Manager scope (contract v0.4.0): what a property manager may see inside their organisation.

- An org admin sees everything in the organisation.
- A property manager (without org_admin) sees the properties they manage (`Property.manager_id`, set to
  the creator and reassignable by an admin) and everything attached to them: units, leases, invoices,
  ledgers, maintenance requests and documents. They see the tenants they created plus every tenant on
  a lease in one of their properties.
- Out-of-scope records behave as if they did not exist (404), like another organisation's.
- Tenants are not affected: their own rules (their leases only) live in each service.

Cost: the first check in a request reads the org's properties once (and, for lease, invoice or tenant
checks, the org's leases once). Fine for the portfolios Anjung targets; a manager index (GSI) is the
next step if an organisation grows to thousands of properties.
"""

from __future__ import annotations

from functools import cached_property
from typing import Any

from app.container import Container
from app.core.context import RequestContext, Role
from app.core.errors import NotFound

Item = dict[str, Any]


class Scope:
    def __init__(self, c: Container, ctx: RequestContext) -> None:
        self._c, self._ctx = c, ctx

    @property
    def restricted(self) -> bool:
        """True for a property manager who is not also an org admin."""
        roles = self._ctx.roles
        return Role.PROPERTY_MANAGER in roles and Role.ORG_ADMIN not in roles

    @property
    def user_id(self) -> str:
        return self._ctx.user_id

    # ---- properties
    @cached_property
    def property_ids(self) -> frozenset[str]:
        repo = self._c.store.properties(self._ctx.org_id)
        out: set[str] = set()
        cursor = None
        while True:
            page, cursor = repo.page(100, cursor, None)
            out |= {p["id"] for p in page if p.get("manager_id") == self._ctx.user_id}
            if cursor is None:
                return frozenset(out)

    def property_ok(self, pid: str | None) -> bool:
        return not self.restricted or (pid is not None and pid in self.property_ids)

    def require_property(self, pid: str | None) -> None:
        if not self.property_ok(pid):
            raise NotFound()

    # ---- leases and what hangs off them
    @cached_property
    def _leases(self) -> list[Item]:
        """Every lease on the manager's properties (any status)."""
        repo = self._c.store.leases(self._ctx.org_id)
        out: list[Item] = []
        cursor = None
        while True:
            page, cursor = repo.page(100, cursor, None)
            out += [x for x in page if x["property_id"] in self.property_ids]
            if cursor is None:
                return out

    @cached_property
    def lease_ids(self) -> frozenset[str]:
        return frozenset(x["id"] for x in self._leases)

    @cached_property
    def tenant_ids_on_leases(self) -> frozenset[str]:
        return frozenset(t for x in self._leases for t in x["tenant_ids"])

    def lease_ok(self, lease: Item) -> bool:
        return self.property_ok(lease["property_id"])

    def require_lease(self, lease: Item) -> None:
        if not self.lease_ok(lease):
            raise NotFound()

    def invoice_ok(self, invoice: Item) -> bool:
        return not self.restricted or invoice["lease_id"] in self.lease_ids

    # ---- tenants
    def tenant_ok(self, tenant: Item) -> bool:
        if not self.restricted:
            return True
        return (
            tenant.get("created_by") == self._ctx.user_id
            or tenant["id"] in self.tenant_ids_on_leases
        )

    def tenant_id_ok(self, tid: str) -> bool:
        if not self.restricted:
            return True
        if tid in self.tenant_ids_on_leases:
            return True
        t = self._c.store.tenants(self._ctx.org_id).get(tid)
        return t is not None and t.get("created_by") == self._ctx.user_id

    def require_tenant(self, tenant: Item) -> None:
        if not self.tenant_ok(tenant):
            raise NotFound()
