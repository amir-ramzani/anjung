"""Display names for leases: property name, unit or room label, tenant names.

Resolved when read (never stored on the lease), so a renamed property, unit or tenant shows at once.
Cost per call: one batch get for the properties, one query per distinct property for its units
(a room's parent sits in the same property), and one batch get for the tenants.
"""

from __future__ import annotations

from typing import Any

from app.container import Container
from app.core.context import RequestContext

Item = dict[str, Any]


class PlaceNames:
    def __init__(self, c: Container, ctx: RequestContext) -> None:
        # always the caller's org from the token (tests/unit/test_tenancy_rules.py)
        self._props = c.store.properties(ctx.org_id)
        self._units = c.store.units(ctx.org_id)
        self._tenants = c.store.tenants(ctx.org_id)
        self._members = c.store.members(ctx.org_id)

    def places(self, leases: list[Item]) -> dict[str, Item]:
        """id -> {property_name, unit_label, parent_unit_label} for leases or maintenance requests
        (anything with `id`, `property_id` and `unit_id`; None where not found or not set)."""
        pids = sorted({x["property_id"] for x in leases})
        if not pids:
            return {}
        names = {p["id"]: p["name"] for p in self._props.get_many(pids)}
        units: dict[str, Item] = {}
        for pid in pids:
            units.update({u["id"]: u for u in self._units.all_for_property(pid)})
        bays: dict[str, list[Item]] = {}  # whole unit id -> bays allocated to it
        for u in units.values():
            if u["type"] == "parking" and u.get("parent_unit_id") and u["status"] != "inactive":
                bays.setdefault(u["parent_unit_id"], []).append(u)
        out: dict[str, Item] = {}
        for lease in leases:
            unit = units.get(lease["unit_id"]) if lease.get("unit_id") else None
            is_room = bool(unit and unit["type"] == "room")
            parent = units.get(unit["parent_unit_id"]) if is_room and unit else None
            included = (
                [{"id": b["id"], "label": b["label"]} for b in bays.get(unit["id"], [])]
                if unit and unit["type"] == "whole_unit" and unit.get("letting_mode") == "whole"
                else None
            )
            out[lease["id"]] = {
                "property_name": names.get(lease["property_id"]),
                "unit_label": unit["label"] if unit else None,
                "parent_unit_label": parent["label"] if parent else None,  # rooms only
                "unit_type": unit["type"] if unit else None,
                "included_bays": included or None,
            }
        return out

    def tenant_names(self, leases: list[Item]) -> dict[str, str]:
        """tenant id -> full name, for every tenant on these leases."""
        ids = sorted({t for x in leases for t in x["tenant_ids"]})
        return {t["id"]: t["full_name"] for t in self._tenants.get_many(ids)} if ids else {}

    def people(self, tenant_ids: set[str], user_ids: set[str]) -> dict[str, str]:
        """id -> display name for tenants (full name) and staff (name, else email)."""
        out: dict[str, str] = {}
        if tenant_ids:
            out.update(
                {t["id"]: t["full_name"] for t in self._tenants.get_many(sorted(tenant_ids))}
            )
        for uid in sorted(user_ids):
            m = self._members.get(uid)
            if m is not None:
                out[uid] = m.get("name") or m["email"]
        return out
