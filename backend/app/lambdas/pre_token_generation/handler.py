"""Cognito Pre Token Generation trigger, version V2_0 (LLD 11.1).

Fails closed: any problem raises, which makes Cognito deny the sign-in. An unscoped token is
never issued. One organisation per identity in v1.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

import boto3

from app.core.access import blocked_reason
from app.core.dates import KL
from app.repositories import keys
from app.repositories.dynamo import DynamoTable

log = logging.getLogger(__name__)
PLAN_GRACE_DAYS = 30  # keep in step with app/domains/organisations/plan.py
_table: DynamoTable | None = None


def _get_table() -> DynamoTable:
    global _table
    if _table is None:
        client = boto3.client("dynamodb", endpoint_url=os.environ.get("DYNAMODB_ENDPOINT_URL"))
        _table = DynamoTable(client, os.environ["TABLE_NAME"])
    return _table


def resolve_claims(table: DynamoTable, sub: str, today: date | None = None) -> dict[str, str]:
    items, _ = table.query(pk=keys.sub_gsi2pk(sub), index="GSI2")
    people = [i for i in items if i.get("entity_type") in ("membership", "tenant")]
    if len(people) != 1:
        raise RuntimeError("no_unique_membership")
    person = people[0]
    if person["entity_type"] == "membership":
        # staff: disabled, or outside the access dates an admin set (app/core/access.py).
        # GSI2 projects only some attributes: read the access dates from the item itself.
        full = table.get(keys.org_pk(person["org_id"]), keys.member_sk(sub)) or person
        reason = blocked_reason(full, today or datetime.now(KL).date())
        if reason:
            raise RuntimeError(f"user_inactive: {reason}")
    elif person.get("status") in ("disabled", "former", "anonymised"):
        raise RuntimeError("user_disabled")  # former and anonymised tenants
    org = table.get(keys.org_pk(person["org_id"]), keys.META)
    if not org or org.get("status") != "active":
        raise RuntimeError("org_not_active")
    # the plan ended more than its grace period ago (contract v0.12.0); read-only is enforced by the API
    until = (org.get("plan") or {}).get("active_until")
    if until and (today or datetime.now(KL).date()) > date.fromisoformat(until) + timedelta(
        days=PLAN_GRACE_DAYS
    ):
        raise RuntimeError("plan_ended")
    claims = {"org_id": person["org_id"], "roles": json.dumps(person["roles"])}
    if person["entity_type"] == "tenant":
        claims["tenant_id"] = person["tenant_id"]
    if person.get("email"):
        claims["email"] = person["email"]
    return claims


def _mark_active(table: DynamoTable, org_id: str, sub: str) -> None:
    """Best effort: first successful sign-in flips an invited member to active."""
    from app.repositories.dynamo import Update

    try:
        table.transact(
            [
                Update(
                    key=(keys.org_pk(org_id), keys.member_sk(sub)),
                    expression="SET #s = :a",
                    condition="#s = :i",
                    names={"#s": "status"},
                    values={":a": "active", ":i": "invited"},
                )
            ]
        )
    except Exception:  # noqa: BLE001 - never block sign-in on this
        log.debug("could not mark member active", exc_info=True)


def handle(event: dict[str, Any], table: DynamoTable) -> dict[str, Any]:
    sub = event["request"]["userAttributes"]["sub"]
    claims = resolve_claims(table, sub)
    if "tenant_id" not in claims:
        _mark_active(table, claims["org_id"], sub)
    event["response"]["claimsAndScopeOverrideDetails"] = {
        "idTokenGeneration": {"claimsToAddOrOverride": claims},
        "accessTokenGeneration": {"claimsToAddOrOverride": claims},
    }
    return event


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return handle(event, _get_table())
