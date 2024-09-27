"""Notification channels behind one port (LLD section 9). A channel only delivers: consent, preferences,
templates and the log live in the notification service."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str
    html: str
    template: str
    locale: str
    from_name: str | None = None  # the organisation's brand (contract v0.11.0)
    reply_to: str | None = None


class TransientError(Exception):
    """Try again later (throttled, provider down)."""


class NotificationChannel(Protocol):
    name: str

    def send(self, msg: Message) -> str:
        """Deliver and return the provider's message id. Raises TransientError to be retried."""
        ...


class FakeChannel:
    """Records messages, and logs them so the local stack and the NAS demo can read them in the API's
    log (`docker compose logs api`), set-password links included. Never used in prod."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.sent: list[Message] = []
        self.ids: list[str] = []  # provider message ids, in the order sent
        self.fail_with: Exception | None = None

    def send(self, msg: Message) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(msg)
        log.info("fake %s to %s: %s\n%s", self.name, msg.to, msg.subject, msg.text)
        self.ids.append(f"{self.name}-{uuid.uuid4().hex[:12]}")
        return self.ids[-1]


def sender(address: str, brand: str | None) -> str:
    """`"Rumah Kita via Anjung" <no-reply@…>`: the brand shows, the address stays ours (no per-company domain yet)."""
    if not brand:
        return address
    clean = "".join(ch for ch in brand if ch not in '"\\<>\r\n').strip()[:60]
    return f'"{clean} via Anjung" <{address}>' if clean else address


class SesChannel:
    """Amazon SES v2. Bounces and complaints come back through SNS to the `ses_feedback` job."""

    name = "email"

    def __init__(
        self, client: Any, from_address: str, configuration_set: str | None = None
    ) -> None:
        self._c, self._from, self._cfg = client, from_address, configuration_set

    def send(self, msg: Message) -> str:
        kwargs: dict[str, Any] = {}
        if self._cfg:
            kwargs["ConfigurationSetName"] = self._cfg
        if msg.reply_to:
            kwargs["ReplyToAddresses"] = [msg.reply_to]
        try:
            r = self._c.send_email(
                FromEmailAddress=sender(self._from, msg.from_name),
                Destination={"ToAddresses": [msg.to]},
                Content={
                    "Simple": {
                        "Subject": {"Data": msg.subject, "Charset": "UTF-8"},
                        "Body": {
                            "Text": {"Data": msg.text, "Charset": "UTF-8"},
                            "Html": {"Data": msg.html, "Charset": "UTF-8"},
                        },
                    }
                },
                **kwargs,
            )
        except self._c.exceptions.TooManyRequestsException as e:
            raise TransientError(str(e)) from None
        return str(r["MessageId"])
