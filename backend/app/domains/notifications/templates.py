"""Templates live in git next to the code (LLD 9.3): templates/<id>/<locale>.{subject,txt,html}.
Messages carry links, never sensitive details. Values are HTML-escaped in the HTML body."""

from __future__ import annotations

import html
from functools import cache
from pathlib import Path
from string import Template
from typing import Any

ROOT = Path(__file__).parent / "templates"
LOCALES = ("en", "ms")
# params each template needs: a missing one is a bug in the caller, not an empty string in an email
REQUIRED: dict[str, set[str]] = {
    "invoice_issued": {"number", "amount", "due_date", "pay_link"},
    "payment_reminder": {"number", "amount", "due_date", "pay_link"},
    "invoice_overdue": {"number", "amount", "due_date", "pay_link"},
    "payment_received": {"number", "amount", "receipt_link"},
    "maintenance_update": {"status", "link"},
    "lease_activated": {"start_date", "link"},
    "lease_agreement_sent": {"start_date", "link"},
    "lease_terminated": {"end_date", "link"},
    "privacy_request_update": {"status", "link"},
    "tenant_invite": {"name", "org_name", "link", "expires"},
    "invite_staff": {"name", "org_name", "role", "email", "link", "expires"},  # contract v0.15.0
    "plan_ending": {
        "org_name",
        "until",
        "days",
        "grace_days",
    },  # to org admins (contract v0.12.0)  # sent directly, never queued
}


@cache
def _load(template: str, locale: str, part: str) -> Template:
    return Template((ROOT / template / f"{locale}.{part}").read_text())


FOOTER = {
    "en": "Sent by {name} through Anjung.",
    "ms": "Dihantar oleh {name} melalui Anjung.",
}
REG = {"en": "Registration no.", "ms": "No. pendaftaran"}


def brand_wrap(
    text: str, body: str, brand: dict[str, Any] | None, locale: str, logo_url: str | None
) -> tuple[str, str]:
    """With branding on (contract v0.11.0): the logo and name above the message, the company details below.
    The message itself never changes. Values are escaped like template parameters."""
    if not brand:
        return text, body
    loc = locale if locale in LOCALES else "en"
    name = brand.get("company_name") or brand["display_name"]
    lines = [FOOTER[loc].format(name=brand["display_name"])]
    if brand.get("company_reg_no"):
        lines.append(f"{name}, {REG[loc]} {brand['company_reg_no']}")
    contact = " | ".join(x for x in (brand.get("contact_phone"), brand.get("contact_email")) if x)
    if contact:
        lines.append(contact)
    e = html.escape
    colour = brand.get("accent_colour") or "#0f766e"
    head = (
        f'<img src="{e(logo_url)}" alt="{e(brand["display_name"])}" height="40" style="height:40px">'
        if logo_url
        else f'<strong style="font-size:18px;color:{e(colour)}">{e(brand["display_name"])}</strong>'
    )
    out_html = (
        f'<div style="border-bottom:3px solid {e(colour)};padding-bottom:8px;margin-bottom:16px">{head}</div>'
        f"{body}"
        f'<hr style="border:0;border-top:1px solid #ddd;margin-top:24px">'
        f'<p style="color:#666;font-size:12px">{"<br>".join(e(x) for x in lines)}</p>'
    )
    return f"{text.rstrip()}\n\n--\n" + "\n".join(lines) + "\n", out_html


def render(template: str, locale: str, params: dict[str, str]) -> tuple[str, str, str]:
    """(subject, text, html). Unknown locale falls back to English."""
    if template not in REQUIRED:
        raise KeyError(f"unknown template {template}")
    missing = REQUIRED[template] - params.keys()
    if missing:
        raise ValueError(f"template {template} needs {sorted(missing)}")
    loc = locale if locale in LOCALES else "en"
    safe = {k: html.escape(str(v)) for k, v in params.items()}
    return (
        _load(template, loc, "subject").substitute(params).strip(),
        _load(template, loc, "txt").substitute(params),
        _load(template, loc, "html").substitute(safe),
    )
