from typing import Any

from app.repositories import keys
from app.repositories.base import Item, OrgRepository
from app.repositories.dynamo import NOT_EXISTS, Put


def default_settings() -> dict[str, Any]:
    return {
        "default_due_day": 1,
        "invoice_lead_days": 7,
        "reminder_offsets_days": [-3, 0, 3],
        "late_fee": {"type": "none", "grace_days": 0},
        "default_locale": "en",
    }


def org_item(org_id: str, name: str, settings: dict[str, Any], now: str) -> Item:
    return {
        "PK": keys.org_pk(org_id),
        "SK": keys.META,
        "entity_type": "organisation",
        "schema_version": 1,
        "org_id": org_id,
        "id": org_id,
        "name": name,
        "status": "active",
        "settings": settings,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }


def registry_item(org: Item) -> Item:
    return {
        "PK": keys.REGISTRY_PK,
        "SK": org["id"],
        "entity_type": "org_registry",
        "org_id": org["id"],
        "name": org["name"],
        "status": org["status"],
        "created_at": org["created_at"],
    }


class OrganisationRepository(OrgRepository):
    def get_meta(self) -> Item | None:
        return self._t.get(self.pk, keys.META)

    def replace_op(self, item: Item, expected_version: int) -> Put:
        return Put(
            item=item,
            condition="#v = :v",
            names={"#v": "version"},
            values={":v": expected_version},
        )


__all__ = ["OrganisationRepository", "default_settings", "org_item", "registry_item", "NOT_EXISTS"]
