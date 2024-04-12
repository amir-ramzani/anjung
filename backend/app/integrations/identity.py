"""Identity provider port (Cognito) with an in-memory implementation for tests and local dev."""

import uuid
from typing import Any, Protocol

from app.core.errors import AppError


class UserExists(AppError):
    status_code, code, default_message = 409, "conflict", "A user with this email already exists"


class WeakPassword(AppError):
    status_code, code, default_message = (
        422,
        "validation_error",
        "The password does not meet the rules",
    )


class IdentityProvider(Protocol):
    def create_user(self, email: str, name: str | None, send_email: bool = True) -> str:
        """Create a user, return the Cognito `sub`. Raises UserExists. `send_email=False` creates the
        user without Cognito's invitation email (the tenant sets a password through our link instead)."""
        ...

    def set_password(self, sub: str, password: str) -> None:
        """Set a permanent password (the set-password link). Raises WeakPassword."""
        ...

    def delete_user(self, sub: str) -> None: ...
    def resend_invite(self, email: str) -> None: ...
    def add_to_group(self, sub: str, group: str) -> None: ...
    def remove_from_group(self, sub: str, group: str) -> None: ...
    def set_enabled(self, sub: str, enabled: bool) -> None: ...
    def global_sign_out(self, sub: str) -> None: ...


class CognitoIdentityProvider:
    def __init__(self, client: Any, pool_id: str, suppress_message: bool = False) -> None:
        self._c, self._pool, self._suppress = client, pool_id, suppress_message

    def create_user(self, email: str, name: str | None, send_email: bool = True) -> str:
        attrs = [{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}]
        if name:
            attrs.append({"Name": "name", "Value": name})
        kwargs: dict[str, Any] = {}
        if self._suppress or not send_email:
            kwargs["MessageAction"] = "SUPPRESS"
        try:
            r = self._c.admin_create_user(
                UserPoolId=self._pool, Username=email, UserAttributes=attrs, **kwargs
            )
        except self._c.exceptions.UsernameExistsException:
            raise UserExists() from None
        return str(next(a["Value"] for a in r["User"]["Attributes"] if a["Name"] == "sub"))

    def _username(self, sub: str) -> str:
        # Cognito accepts the sub as Username for admin calls
        return sub

    def resend_invite(self, email: str) -> None:
        self._c.admin_create_user(UserPoolId=self._pool, Username=email, MessageAction="RESEND")

    def set_password(self, sub: str, password: str) -> None:
        try:
            self._c.admin_set_user_password(
                UserPoolId=self._pool,
                Username=self._username(sub),
                Password=password,
                Permanent=True,
            )
        except self._c.exceptions.InvalidPasswordException:
            raise WeakPassword(
                details=[{"field": "password", "issue": "does not meet the password rules"}]
            ) from None

    def delete_user(self, sub: str) -> None:
        self._c.admin_delete_user(UserPoolId=self._pool, Username=self._username(sub))

    def add_to_group(self, sub: str, group: str) -> None:
        self._c.admin_add_user_to_group(
            UserPoolId=self._pool, Username=self._username(sub), GroupName=group
        )

    def remove_from_group(self, sub: str, group: str) -> None:
        self._c.admin_remove_user_from_group(
            UserPoolId=self._pool, Username=self._username(sub), GroupName=group
        )

    def set_enabled(self, sub: str, enabled: bool) -> None:
        fn = self._c.admin_enable_user if enabled else self._c.admin_disable_user
        fn(UserPoolId=self._pool, Username=self._username(sub))

    def global_sign_out(self, sub: str) -> None:
        self._c.admin_user_global_sign_out(UserPoolId=self._pool, Username=self._username(sub))


class InMemoryIdentityProvider:
    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}  # sub -> record
        self.fail_next_create = False

    def sub_for(self, email: str) -> str:
        return next(s for s, u in self.users.items() if u["email"] == email.lower())

    def create_user(self, email: str, name: str | None, send_email: bool = True) -> str:
        if any(u["email"] == email.lower() for u in self.users.values()):
            raise UserExists()
        sub = str(uuid.uuid4())
        self.users[sub] = {
            "email": email.lower(),
            "name": name,
            "groups": set(),
            "enabled": True,
            "signed_out": 0,
            "invite_email": send_email,
            "password": None,
        }
        return sub

    def delete_user(self, sub: str) -> None:
        self.users.pop(sub, None)

    def resend_invite(self, email: str) -> None:
        self.resent = [*getattr(self, "resent", []), email]

    def set_password(self, sub: str, password: str) -> None:
        self.users[sub]["password"] = password

    def add_to_group(self, sub: str, group: str) -> None:
        self.users[sub]["groups"].add(group)

    def remove_from_group(self, sub: str, group: str) -> None:
        self.users[sub]["groups"].discard(group)

    def set_enabled(self, sub: str, enabled: bool) -> None:
        self.users[sub]["enabled"] = enabled

    def global_sign_out(self, sub: str) -> None:
        self.users[sub]["signed_out"] += 1
