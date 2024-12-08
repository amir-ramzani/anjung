"""Cognito Post Confirmation trigger: links a tenant's Cognito sub on first login (LLD 11.2).

The invitation created `INVITE#<sub>`. Linking is a single conditional transaction, so an
invite can be used once, and only when the sign-in email matches the invited email.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.core.clock import iso
from app.repositories import keys
from app.repositories.dynamo import Delete, DynamoTable, TxCancelled, Update

log = logging.getLogger(__name__)


def link_tenant(table: DynamoTable, sub: str, email: str, now: datetime) -> bool:
    invite = table.get(keys.invite_pk(sub), keys.META)
    if invite is None:
        return False  # not a tenant invitation (staff, or already linked)
    if invite["email"] != email.strip().lower() or invite["expires_at"] < iso(now):
        return False
    org_id, tid = invite["org_id"], invite["tenant_id"]
    try:
        table.transact(
            [
                Update(
                    key=(keys.org_pk(org_id), keys.tenant_sk(tid)),
                    expression=(
                        "SET GSI2PK = :g2p, GSI2SK = :g2s, linked = :t, #s = :active, "
                        "cognito_sub = :sub, version = version + :one, updated_at = :now "
                        "REMOVE pending_sub"
                    ),
                    condition="pending_sub = :sub AND linked = :f AND #s = :invited",
                    names={"#s": "status"},
                    values={
                        ":g2p": keys.sub_gsi2pk(sub),
                        ":g2s": f"TENANT#{org_id}#{tid}",
                        ":t": True,
                        ":f": False,
                        ":active": "active",
                        ":invited": "invited",
                        ":sub": sub,
                        ":one": 1,
                        ":now": iso(now),
                    },
                ),
                Delete(key=(keys.invite_pk(sub), keys.META)),
            ]
        )
    except TxCancelled:
        log.info("tenant link refused")
        return False
    return True


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    from app.core.clock import SystemClock
    from app.lambdas.pre_token_generation.handler import _get_table

    attrs = event["request"]["userAttributes"]
    link_tenant(_get_table(), attrs["sub"], attrs.get("email", ""), SystemClock().now())
    return event  # never block confirmation: Pre Token Generation fails closed if unlinked
