"""All PK/SK/GSI key builders: the single source of truth (LLD section 4.2)."""

import hashlib

REGISTRY_PK = "REGISTRY#ORG"
META = "META"


def org_pk(org_id: str) -> str:
    return f"ORG#{org_id}"


def member_sk(sub: str) -> str:
    return f"MEMBER#{sub}"


def prop_sk(property_id: str) -> str:
    return f"PROP#{property_id}"


def unit_sk(property_id: str, unit_id: str) -> str:
    return f"PROP#{property_id}#UNIT#{unit_id}"


def room_sk(property_id: str, parent_unit_id: str, room_id: str) -> str:
    return f"PROP#{property_id}#UNIT#{parent_unit_id}#ROOM#{room_id}"


def room_prefix(property_id: str, parent_unit_id: str) -> str:
    return f"PROP#{property_id}#UNIT#{parent_unit_id}#ROOM#"


def unit_prefix(property_id: str) -> str:
    return f"PROP#{property_id}#UNIT#"


def entity_gsi1pk(org_id: str, kind: str, entity_id: str) -> str:
    """GSI1 partition for lookup by global ID: ORG#o#<KIND>#<id>."""
    return f"ORG#{org_id}#{kind}#{entity_id}"


def unit_label_lock_sk(property_id: str, label: str) -> str:
    digest = hashlib.sha256(label.strip().casefold().encode()).hexdigest()[:32]
    return f"UNITLBL#{property_id}#{digest}"


def audit_pk(org_id: str, month: str) -> str:
    return f"ORG#{org_id}#AUDIT#{month}"


def audit_sk(ts: str, entry_id: str) -> str:
    return f"{ts}#{entry_id}"


def audit_entity_gsi1pk(org_id: str, entity_type: str, entity_id: str) -> str:
    return f"ORG#{org_id}#ENT#{entity_type}#{entity_id}"


def tenant_sk(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"


def consent_sk(tenant_id: str, ts: str, consent_id: str) -> str:
    return f"TENANT#{tenant_id}#CONSENT#{ts}#{consent_id}"


def consent_prefix(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}#CONSENT#"


def subject_gsi5pk(org_id: str, tenant_id: str) -> str:
    return f"ORG#{org_id}#SUBJECT#{tenant_id}"


def tenant_email_lock_sk(email: str) -> str:
    return f"TENEMAIL#{hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]}"


def brand_handle_pk(handle: str) -> str:
    """A branding handle (contract v0.11.0), unique across the platform."""
    return f"BRANDHANDLE#{handle}"


def invite_pk(sub: str) -> str:
    return f"INVITE#{sub}"


def invite_token_pk(token: str) -> str:
    """A set-password link. Only the hash of the token is stored, never the token itself."""
    return f"INVITETOKEN#{hashlib.sha256(token.encode()).hexdigest()}"


def lease_sk(lease_id: str) -> str:
    return f"LEASE#{lease_id}"


def lease_gsi1sk(start_date: str, lease_id: str) -> str:
    return f"LEASE#{start_date}#{lease_id}"


def tenant_lease_sk(tenant_id: str, lease_id: str) -> str:
    return f"TL#{tenant_id}#{lease_id}"


def tenant_lease_prefix(tenant_id: str) -> str:
    return f"TL#{tenant_id}#"


def unit_lock_sk(unit_id: str) -> str:
    return f"UNITLOCK#{unit_id}"


def active_leases_gsi4pk(org_id: str) -> str:
    return f"ORG#{org_id}#LEASE#active"


def doc_sk(document_id: str) -> str:
    return f"DOC#{document_id}"


def doc_owner_gsi1pk(org_id: str, owner_type: str, owner_id: str) -> str:
    return f"ORG#{org_id}#OWNER#{owner_type}#{owner_id}"


def lease_pk(org_id: str, lease_id: str) -> str:
    """Invoices, ledger entries and invoice locks of one lease share this partition."""
    return f"ORG#{org_id}#LEASE#{lease_id}"


def invoice_sk(period: str, invoice_id: str) -> str:
    return f"INV#{period}#{invoice_id}"


def invkey_sk(period: str, kind: str) -> str:
    return f"INVKEY#{period}#{kind}"


def ledger_sk(entry_id: str) -> str:
    return f"LEDGER#{entry_id}"


def invoice_pk(org_id: str, invoice_id: str) -> str:
    """Payments and allocations of one invoice."""
    return f"ORG#{org_id}#INV#{invoice_id}"


def unmatched_pk(org_id: str) -> str:
    return f"ORG#{org_id}#UNMATCHED"


def unpaid_gsi3pk(org_id: str) -> str:
    return f"ORG#{org_id}#INV#unpaid"


def all_invoices_gsi4pk(org_id: str) -> str:
    return f"ORG#{org_id}#INV#all"


def payref_pk(provider: str, ref: str) -> str:
    return f"PAYREF#{provider}#{ref}"


def qrref_pk(reference: str) -> str:
    return f"QRREF#{reference}"


def webhook_event_pk(provider: str, event_id: str) -> str:
    return f"WHEVT#{provider}#{event_id}"


def invoice_counter_sk(year: int) -> str:
    return f"COUNTER#invoice#{year}"


def invoice_run_sk(period: str, run_id: str) -> str:
    return f"INVRUN#{period}#{run_id}"


def mr_sk(request_id: str) -> str:
    return f"MR#{request_id}"


def mr_tenant_gsi1pk(org_id: str, tenant_id: str) -> str:
    return f"ORG#{org_id}#TENANT#{tenant_id}"


def mr_status_gsi4pk(org_id: str, status: str) -> str:
    return f"ORG#{org_id}#MR#{status}"


def mr_comment_pk(org_id: str, request_id: str) -> str:
    return f"ORG#{org_id}#MR#{request_id}"


def notif_pk(org_id: str, month: str) -> str:
    return f"ORG#{org_id}#NOTIF#{month}"


def notif_dedupe_sk(dedupe_key: str) -> str:
    return "NOTIFKEY#" + hashlib.sha256(dedupe_key.encode()).hexdigest()[:40]


def suppress_sk(channel: str, address: str) -> str:
    return f"SUPPRESS#{channel}#{hashlib.sha256(address.strip().lower().encode()).hexdigest()[:32]}"


def privacy_request_sk(request_id: str) -> str:
    return f"PRIVREQ#{request_id}"


def sub_gsi2pk(sub: str) -> str:
    return f"SUB#{sub}"


def member_gsi2sk(org_id: str) -> str:
    return f"MEMBER#{org_id}"
