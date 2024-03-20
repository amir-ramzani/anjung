"""Local-development stand-in for Cognito (AUTH_MODE=mock, ENV local or test only).

* `MockIdentityProvider`: users live only as DynamoDB memberships. The sub is derived from the
  email, so it survives restarts and needs no user store.
* `DevTokenIssuer`: signs RS256 access tokens with an in-process key. Claims come from the same
  `resolve_claims` the real Pre Token Generation Lambda uses, so org status, disabled users and
  one-org-per-identity behave like production.
"""

import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.clock import Clock
from app.core.dates import today_kl
from app.core.errors import AccountInactive, SignInDenied
from app.integrations.identity import UserExists
from app.lambdas.post_confirmation.handler import link_tenant
from app.lambdas.pre_token_generation.handler import _mark_active, resolve_claims
from app.repositories import keys
from app.repositories.dynamo import DynamoTable

_NS = uuid.UUID("6f1c3a52-0000-4000-8000-000000000001")


def sub_for_email(email: str) -> str:
    return str(uuid.uuid5(_NS, email.strip().lower()))


class MockIdentityProvider:
    def __init__(self, table: DynamoTable) -> None:
        self._t = table

    def create_user(self, email: str, name: str | None, send_email: bool = True) -> str:
        sub = sub_for_email(email)
        items, _ = self._t.query(pk=keys.sub_gsi2pk(sub), index="GSI2")
        if items or self._t.get(keys.invite_pk(sub), keys.META):
            raise UserExists()  # linked, or already invited by someone else
        return sub

    def delete_user(self, sub: str) -> None: ...
    def resend_invite(self, email: str) -> None: ...
    def set_password(self, sub: str, password: str) -> None: ...  # no passwords in mock sign-in
    def add_to_group(self, sub: str, group: str) -> None: ...
    def remove_from_group(self, sub: str, group: str) -> None: ...
    def set_enabled(self, sub: str, enabled: bool) -> None: ...
    def global_sign_out(self, sub: str) -> None: ...


SIGN_IN_DENIED = {  # plain words for the login page (the frontend translates by the reason code)
    "no_unique_membership": "No account uses this email. Check the spelling, or ask your landlord or admin to invite you.",
    "user_disabled": "This account is no longer active. Contact your landlord or admin.",
    "org_not_active": "This organisation is not active. Contact your landlord or admin.",
    "plan_ended": "This organisation's plan has ended. Contact your landlord or admin.",
}


class DevTokenIssuer:
    def __init__(self, table: DynamoTable, issuer: str, client_id: str, clock: Clock) -> None:
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._t, self._issuer, self._client_id, self._clock = table, issuer, client_id, clock

    def key_resolver(self, _token: str) -> Any:
        return self.private_key.public_key()

    def issue(self, email: str, ttl: int = 8 * 3600) -> str:
        sub = sub_for_email(email)
        try:
            try:
                claims = resolve_claims(self._t, sub, today_kl(self._clock))
            except RuntimeError as e:
                if str(e) != "no_unique_membership":
                    raise
                # first sign-in of an invited tenant: Cognito's Post Confirmation trigger links the sub
                if not link_tenant(self._t, sub, email, self._clock.now()):
                    raise
                claims = resolve_claims(self._t, sub, today_kl(self._clock))
        except RuntimeError as e:  # same fail-closed outcomes as the Lambda
            msg = str(e)
            if msg.startswith("user_inactive: "):  # staff access ended: show the reason as is
                raise AccountInactive(msg.removeprefix("user_inactive: ")) from None
            raise SignInDenied(
                SIGN_IN_DENIED.get(msg, SignInDenied.default_message),
                details=[{"field": "email", "issue": msg}],
            ) from None
        if (
            "tenant_id" not in claims
        ):  # like the Lambda: the first sign-in makes an invited member active
            _mark_active(self._t, claims["org_id"], sub)
        now = int(time.time())
        payload = {
            "sub": sub,
            "iss": self._issuer,
            "client_id": self._client_id,
            "token_use": "access",
            "iat": now,
            "exp": now + ttl,
            **claims,
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256", headers={"kid": "mock"})
