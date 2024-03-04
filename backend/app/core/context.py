from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    PLATFORM_ADMIN = "platform_admin"
    ORG_ADMIN = "org_admin"
    PROPERTY_MANAGER = "property_manager"
    TENANT = "tenant"
    VENDOR = "vendor"  # reserved, never issued in v1


@dataclass(frozen=True, slots=True)
class RequestContext:
    user_id: str
    org_id: str  # from the validated token only, never from request input
    roles: frozenset[Role]
    tenant_id: str | None
    email: str | None
    request_id: str
    ip: str | None
    locale: str = "en"

    @property
    def actor_role(self) -> str:
        order = [Role.PLATFORM_ADMIN, Role.ORG_ADMIN, Role.PROPERTY_MANAGER, Role.TENANT]
        for r in order:
            if r in self.roles:
                return r.value
        return sorted(r.value for r in self.roles)[0]
