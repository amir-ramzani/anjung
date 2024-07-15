"""Receipt PDFs. A receipt shows the invoice and its payments but no tenant names, so it holds no
personal data and survives anonymisation under the financial retention hold."""

from __future__ import annotations

import io
import logging
from typing import Any

from fpdf import FPDF

from app.container import Container
from app.core.clock import iso
from app.core.dates import display_date
from app.core.errors import NotFound
from app.domains.organisations import branding
from app.repositories import keys
from app.repositories.documents import document_item

Item = dict[str, Any]
log = logging.getLogger(__name__)


def _money(minor: int) -> str:
    return f"MYR {minor / 100:,.2f}"  # display only, never used in calculations


def _header(org_name: str, brand: Item | None) -> list[str]:
    """The landlord's name, and with branding on (contract v0.11.0) its company name, registration number and
    contact details."""
    if not brand:
        return [org_name]
    lines = [brand.get("company_name") or brand["display_name"]]
    if brand.get("company_reg_no"):
        lines.append(f"Registration no. {brand['company_reg_no']}")
    contact = "  |  ".join(x for x in (brand.get("contact_phone"), brand.get("contact_email")) if x)
    if contact:
        lines.append(contact)
    return lines


def render_pdf(
    org_name: str,
    unit_label: str,
    inv: Item,
    payments: list[Item],
    brand: Item | None = None,
    logo: tuple[bytes, str] | None = None,
) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    if logo and logo[1] in ("image/png", "image/jpeg"):  # fpdf cannot draw WebP: the name is enough
        try:
            pdf.image(io.BytesIO(logo[0]), x=pdf.w - 50, y=8, h=16)
        except Exception:  # noqa: BLE001
            log.warning("could not draw the organisation logo on a receipt")
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Payment receipt", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    for text in (
        *_header(org_name, brand),
        f"Invoice {inv['number']}  |  Unit {unit_label}",
        f"Issued {display_date(inv['issue_date'])}, due {display_date(inv['due_date'])}",
    ):
        pdf.cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Charges", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    for ln in inv["line_items"]:
        pdf.cell(130, 7, ln["description"])
        pdf.cell(0, 7, _money(ln["amount_minor"]), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(130, 7, "Total", new_x="RIGHT")
    pdf.cell(0, 7, _money(inv["total_minor"]), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Payments received", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    for p in payments:
        pdf.cell(130, 7, f"{display_date(p['received_at'])}  {p['method'].replace('_', ' ')}")
        pdf.cell(0, 7, _money(p["amount_minor"]), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(130, 7, "Outstanding")
    pdf.cell(
        0,
        7,
        _money(max(inv["total_minor"] - inv["paid_minor"], 0)),
        align="R",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    return bytes(pdf.output())


def generate_receipt(c: Container, org_id: str, invoice_id: str) -> Item | None:
    """(Re)generate the receipt of an invoice that has at least one successful payment."""
    repo = c.store.billing(org_id)
    inv = repo.get_invoice(invoice_id)
    if inv is None or c.storage is None:
        return None
    payments = sorted(
        (p for p in repo.payments_of_invoice(invoice_id) if p["status"] == "succeeded"),
        key=lambda p: p["received_at"],
    )
    if not payments:
        return None
    lease = c.store.leases(org_id).get(inv["lease_id"])
    unit = c.store.units(org_id).get(lease["unit_id"]) if lease else None
    org = c.store.org(org_id).get_meta() or {}
    brand = branding.public(org)
    logo = None
    did = (org.get("branding") or {}).get("logo_document_id") if brand else None
    logo_doc = c.store.documents(org_id).get(did) if did else None
    if logo_doc and logo_doc["status"] == "clean" and c.storage is not None:
        try:
            logo = (c.storage.read(logo_doc["s3_key"]), logo_doc["content_type"])
        except Exception:  # noqa: BLE001
            log.warning("could not read the organisation logo")
    pdf = render_pdf(org.get("name", ""), unit["label"] if unit else "", inv, payments, brand, logo)
    key = f"org/{org_id}/invoice/{invoice_id}/{invoice_id}/receipt.pdf"
    c.storage.put(key, pdf, "application/pdf")
    now = iso(c.clock.now())
    doc = document_item(
        org_id,
        invoice_id,  # the document id is the invoice id: one receipt per invoice
        {
            "owner_type": "invoice",
            "owner_id": invoice_id,
            "type": "receipt",
            "filename": "receipt.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(pdf),
            "retention_class": "lease_financial",
            "s3_key": key,
            "created_by": "system",
        },
        now,
    )
    doc["status"] = "clean"  # generated by us, not uploaded: nothing to scan
    c.store.table.put(doc)
    return doc


def receipt_download(c: Container, org_id: str, invoice_id: str) -> dict[str, Any]:
    doc = c.store.table.get(keys.org_pk(org_id), keys.doc_sk(invoice_id))
    if doc is None or c.storage is None:
        raise NotFound()  # no payment yet, so no receipt
    url, expires = c.storage.presign_download(doc["s3_key"], doc["filename"], doc["content_type"])
    return {
        "url": url,
        "expires_at": iso(expires),
        "filename": doc["filename"],
        "content_type": doc["content_type"],
    }
