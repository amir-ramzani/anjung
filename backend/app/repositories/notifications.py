from __future__ import annotations

from typing import Any

from app.repositories import keys
from app.repositories.base import Item, OrgRepository
from app.repositories.dynamo import NOT_EXISTS, Put

DEDUPE_TTL_DAYS = 90


def log_item(org_id: str, lid: str, ts: str, fields: dict[str, Any]) -> Item:
    month = ts[:7]
    return {
        "PK": keys.notif_pk(org_id, month),
        "SK": f"{ts}#{lid}",
        "GSI5PK": keys.subject_gsi5pk(org_id, fields["tenant_id"]),  # data-subject index
        "GSI5SK": f"NOTIF#{lid}",
        "entity_type": "notification_log",
        "schema_version": 1,
        "org_id": org_id,
        "id": lid,
        "provider_message_id": None,
        "error": None,
        "deliver_after": None,
        **fields,
        "status": fields.get("status", "queued"),
        "created_at": ts,
    }


class NotificationRepository(OrgRepository):
    def dedupe_op(self, dedupe_key: str, now: Any) -> Put:
        return Put(
            item={
                "PK": self.pk,
                "SK": keys.notif_dedupe_sk(dedupe_key),
                "entity_type": "notification_key",
                "org_id": self.org_id,
                "ttl": int(now.timestamp()) + DEDUPE_TTL_DAYS * 86400,
            },
            condition=NOT_EXISTS,
        )

    def create_op(self, item: Item) -> Put:
        return Put(item=item, condition=NOT_EXISTS)

    def get(self, month: str, sk: str) -> Item | None:
        return self._t.get(keys.notif_pk(self.org_id, month), sk)

    def put(self, item: Item) -> None:
        self._t.put(item)

    def page(
        self, month: str, limit: int, cursor: str | None, accept: Any
    ) -> tuple[list[Item], str | None]:
        return self._page(
            pk=keys.notif_pk(self.org_id, month),
            sk_prefix=None,
            limit=limit,
            cursor=cursor,
            forward=False,
            accept=accept,
        )

    def deferred(self, month: str, now_iso: str) -> list[Item]:
        items, _ = self._t.query(pk=keys.notif_pk(self.org_id, month))
        return [
            i
            for i in items
            if i["status"] == "queued" and i["deliver_after"] and i["deliver_after"] <= now_iso
        ]

    def suppressed(self, channel: str, address: str) -> bool:
        return self._t.get(self.pk, keys.suppress_sk(channel, address)) is not None

    def suppress_put(self, channel: str, address: str, reason: str, now: str) -> None:
        self._t.put(
            {
                "PK": self.pk,
                "SK": keys.suppress_sk(channel, address),
                "entity_type": "suppression",
                "org_id": self.org_id,
                "channel": channel,
                "reason": reason,
                "created_at": now,
            }
        )

    def message_lookup_put(self, provider_id: str, month: str, sk: str) -> None:
        self._t.put(
            {
                "PK": f"NOTIFMSG#{provider_id}",
                "SK": keys.META,
                "entity_type": "notification_message",
                "org_id": self.org_id,
                "month": month,
                "log_sk": sk,
            }
        )
