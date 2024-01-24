"""One AppError hierarchy mapped to the contract's Error envelope (LLD section 15.1)."""

import re
from typing import Any


def issue_code(issue: str) -> str:
    """A stable, machine-readable code for a fixed English field issue (contract v0.16.0), so clients can
    show it in the user's language: "must not be before from" -> "must_not_be_before_from"."""
    return re.sub(r"[^a-z0-9]+", "_", issue.lower()).strip("_")[:80] or "invalid"


class AppError(Exception):
    status_code = 500
    code = "internal_error"
    default_message = "Internal error"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str | int] | None = None,
    ) -> None:
        self.message = message or self.default_message
        # the values in the message, so the SPA can show it in the user's language (contract v0.13.1)
        self.params = params
        if code:
            self.code = code
        self.details = details
        self.headers = headers
        super().__init__(self.message)

    def to_body(self, request_id: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "request_id": request_id,
        }
        if self.details:
            body["details"] = self.details
        if self.params:
            body["params"] = self.params
        return body


class Unauthorized(AppError):
    status_code, code, default_message = 401, "unauthorized", "Authentication required"


class Forbidden(AppError):
    status_code, code, default_message = 403, "forbidden", "Not permitted"


class AccountInactive(Forbidden):
    """A staff member who is disabled, or outside their access dates (app/core/access.py)."""

    code, default_message = "account_inactive", "Your access is not active"


class PlanReadOnly(Forbidden):
    """The organisation's plan has ended: staff can read but not change anything (contract v0.12.0)."""

    code, default_message = "plan_read_only", "Your organisation's plan has ended"


class PlanEnded(Forbidden):
    """The plan ended more than the grace period ago: nobody in the organisation can use the app."""

    code, default_message = "plan_ended", "This organisation's plan has ended"


class SignInDenied(Forbidden):
    """Local mock sign-in refused. `details[0].issue` carries the reason code for the login page to translate."""

    code, default_message = "sign_in_denied", "You cannot sign in with this email"


class NotFound(AppError):
    status_code, code, default_message = 404, "not_found", "Resource not found"


class BadRequest(AppError):
    status_code, code, default_message = 400, "bad_request", "Malformed request"


class ValidationFailed(AppError):
    """Each detail carries `field`, the English `issue`, and a `code` (contract v0.16.0): given, or made
    from a fixed issue text. An issue with values in it must pass its own `code` and `params`."""

    status_code, code, default_message = 422, "validation_error", "Validation failed"

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        if self.details:
            self.details = [
                {**d, "code": d.get("code") or issue_code(str(d.get("issue", "")))}
                for d in self.details
            ]


class InvalidState(AppError):
    status_code, code, default_message = 409, "invalid_state", "Invalid state for this action"


class Conflict(AppError):
    status_code, code, default_message = 409, "conflict", "Conflict"


class QuotaExceeded(Conflict):
    """A property manager is at the property limit an admin set (contract v0.6.0)."""

    code, default_message = "quota_exceeded", "The property limit has been reached"


class LeaseOverlap(Conflict):
    code, default_message = (
        "lease_overlap",
        "The dates overlap another active or future lease on this unit",
    )


class DocumentNotClean(Conflict):
    code, default_message = "document_not_clean", "The document has not passed the malware scan"


class IdempotencyConflict(AppError):
    status_code, code, default_message = 409, "idempotency_conflict", "Idempotency key reused"


class PayloadTooLarge(AppError):
    status_code, code, default_message = 413, "payload_too_large", "Payload too large"


class RateLimited(AppError):
    status_code, code, default_message = 429, "rate_limited", "Too many requests"
