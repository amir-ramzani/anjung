"""One-off setup: the reserved platform organisation and its first platform admin (LLD 11.4)."""

from app.core.clock import Clock, iso
from app.integrations.identity import IdentityProvider
from app.repositories.dynamo import NOT_EXISTS, DynamoTable, Put
from app.repositories.members import member_item
from app.repositories.organisations import default_settings, org_item


def bootstrap_platform(
    table: DynamoTable,
    identity: IdentityProvider,
    clock: Clock,
    *,
    platform_org_id: str,
    admin_email: str,
    admin_name: str | None = None,
) -> str:
    """Create the platform org (not in REGISTRY#ORG, so no jobs run for it) and its admin."""
    now = iso(clock.now())
    sub = identity.create_user(admin_email, admin_name)
    org = org_item(platform_org_id, "Anjung Platform", default_settings(), now)
    member = member_item(
        platform_org_id, sub, admin_email.lower(), admin_name, ["platform_admin"], now
    )
    try:
        table.transact(
            [Put(item=org, condition=NOT_EXISTS), Put(item=member, condition=NOT_EXISTS)]
        )
    except Exception:
        identity.delete_user(sub)
        raise
    identity.add_to_group(sub, "platform_admin")
    return sub
