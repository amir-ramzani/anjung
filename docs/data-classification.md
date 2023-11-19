# Data classification sheet (plan P0-12)

Every personal or sensitive field Anjung stores, why, who may see it, where it lives and what happens to it. Built from the code as of contract v0.16.0 (v0.12.0 to v0.14.0 added no personal data). **Review it whenever a field is added**: `test_export_registry_covers_every_subject_indexed_entity` in `tests/integration/test_privacy.py` already fails if a subject-indexed entity is missing from the export, and this sheet should move with it.

Open human decisions (not settled by code): **D1** controller or processor, the privacy owner / DPO (not yet named), **D5** retention periods. Fields marked *(D5)* have a placeholder behaviour until counsel signs off.

Classes: **Restricted** = identity numbers, credentials, signing material. **Confidential** = personal contact details, financial records tied to a person. **Internal** = business data without personal content. **Public** = nothing in this system.

## People

| Data | Class | Purpose | Who sees it | Stored in | At rest / in logs | End of life |
|---|---|---|---|---|---|---|
| Tenant full name | Confidential | Lease, invoices, contact | Org staff; the tenant | Table `TENANT#` | DynamoDB KMS CMK. Never in logs or span attributes | Replaced by `Anonymised tenant <hash>` on erasure |
| Tenant email | Confidential | Invitation, notices, receipts | Org staff; the tenant | Table `TENANT#`, plus a uniqueness lock item per email | KMS. Not logged | Removed on erasure, and the lock item is deleted so the address can be reused |
| Tenant phone | Confidential | Notices, contact | Org staff; the tenant | Table `TENANT#` | KMS. Not logged | Removed |
| Vehicle plate (parking bay leases, optional) | Confidential | Tell guards and staff which car uses the bay | Staff who manage the property; the tenants on the lease | `vehicle_plate` on the `LEASE#` item | KMS. Not logged; opaque in audit diffs | In the tenant's export (the lease). Set to null on erasure |
| Identity document number (NRIC/passport) | **Restricted** | Tenancy identification | Masked form to staff on every read. The full number is decrypted only inside the data export (own export or staff export of that tenant) | `identity_document.number_enc` (KMS data key) + `masked_number` | App-level encryption on top of table KMS | Removed on erasure |
| Identity document files (scan, photo) | **Restricted** | Tenant screening, legal record | Staff; the owning tenant | S3 documents bucket (SSE-KMS, private) | Never public; short-lived presigned URLs; malware-scanned before use | Held under financial retention *(D5)*, renamed to a neutral key; deleted when the hold ends |
| Cognito user (email, sub, name) | Confidential | Login | Cognito | Cognito user pool | Managed by AWS | Disabled/deleted with the account |
| Set-password link (one-time token for an invited tenant or, since v0.15.0, staff member) | **Restricted** | Lets the person set their first password | Whoever holds it. Staff see a tenant's link only when they choose to share it themselves; sent by the app by email or WhatsApp, nobody else sees it | `INVITETOKEN#<sha256>` item (hash only, with org, tenant or staff `sub`, email, expiry) | The token itself is never stored, logged, queued or written to the notification log | Deleted when used or replaced; expires with the invitation (7 days) and is removed by TTL |
| Staff user record (name, email, roles, status, access dates, property limit) | Confidential | Authorisation, need-to-know scope, audit trail | Org admin; the member sees their own access dates | Table `MEMBER#` | KMS | Deactivation keeps the record, so history and assignments stay. Removed when the member is removed. Their audit entries follow audit retention *(D5)* |
| Organisation branding (display and company name, registration number, contact email and phone, colour, welcome text, logo) | Public | Show the landlord's identity to tenants | Anyone with the handle (`GET /v1/brands/{handle}`); every user of the organisation | `branding` on the organisation item, `BRANDHANDLE#` registry, logo in the documents bucket | KMS. Not logged | Changed or cleared by the org admin; deleted with the organisation. If the landlord is an individual, the contact details are their personal data and they choose to publish them |
| Property manager assignment (`Property.manager_id`), tenant creator (`Tenant.created_by`) | Internal | Decide what each manager may see | Org staff (as names on screen) | `PROP#`, `TENANT#` items | KMS | Kept with the property or tenant. A removed member's id stays until reassigned |
| Notification preferences | Confidential | Honour channels and quiet hours | Tenant; staff read | `TENANT#` item | KMS | Removed on erasure |
| Consents (type, grant/withdraw time, version) | Confidential | PDPA evidence | Tenant; org staff | Append-only consent history | KMS | Retained as evidence of what was agreed *(D5)* |
| IP address of staff and tenant actions | Confidential | Audit trail | Org admin via audit log | Audit entries | KMS | Retention of the audit log *(D5)*. Not removed on erasure |
| Tenancy agreement signature (name as typed, time, document, IP address, browser; paper: the staff member who recorded it and an optional note) | Confidential | Proof the tenant accepted the agreement (contract v0.7.0) | Staff and co-tenants see name, method and time. The IP address and browser are not shown in the app or the API, only in the tenant's own export | `agreement.signatures` on the `LEASE#` item | KMS. Not logged | Kept with the lease as a financial record *(D5)*. On erasure: name replaced, IP address, browser and note removed; the time and document stay as proof |

## Records about people

| Data | Class | Purpose | Who sees it | End of life |
|---|---|---|---|---|
| Lease (tenant link, dates, rent, deposits, agreement status) | Confidential | Contract | Staff who manage the property; the tenants on it (a draft only once its agreement is sent) | Kept: financial record. Tenant link remains, name is anonymised |
| Tenancy agreement document (PDF, signed scan) | Confidential | The contract itself | Staff who manage the property; the tenants on the lease | Retention hold: cannot be deleted *(D5)*. On erasure the file is moved to a neutral name |
| Ledger, invoices, payments, allocations | Confidential | Accounting and tax | Staff; the tenant sees their own | Kept for the financial retention period *(D5)*; amounts, dates and ids only |
| Payment provider reference, transaction id | Internal | Reconciliation | Staff | Kept with the payment |
| Card or bank details | none | Never stored: the provider hosts them (decision D2) | n/a | n/a |
| Receipts (PDF) | Confidential | Proof of payment | Staff; the tenant | Kept with the ledger. Contain no tenant name by design |
| Maintenance requests and comments | Confidential | Repairs; may contain free-text personal details | Staff; the requesting tenant (not internal comments) | Operational: removed with the property or on request; personal text is part of export |
| Maintenance photos | Confidential | Evidence | Same as the request | Operational |
| Audit log (who did what, when, from which IP) | Confidential | Accountability | Org admin | Retention *(D5)* |
| Notification outbox (recipient, template, status) | Confidential | Delivery proof | Staff via notification log | Dedupe records expire by TTL; log retention *(D5)* |

## Business data without personal content

Organisation, properties, units, rooms, rent amounts and report aggregates are **Internal**. Report aggregates contain no names. Their CSV downloads (contract v0.15.0) add property names, unit labels and, in the arrears report, the tenant's name, so a downloaded arrears file is **Confidential**: it is fetched by staff with the same manager scope as the screen, and leaves the system once saved.

## Where personal data must never appear

- Application logs, metrics, span names and attributes, alarms (request logs and spans carry ids only; the tracing tests assert exception messages are not recorded).
- Error responses and the problem `detail` text.
- Postman collection, fixtures and seed data (synthetic only).
- Receipts (tenant name excluded).
- URLs and query strings (opaque ids and cursors only).
- One tenant's export: co-tenants appear only as ids, never by name or signature (display names and co-tenant signatures are left out).
- The home-network demo (`docs/nas-deploy.md`): demo data only, because it uses a password-free test sign-in.

## Data subject rights, mapped

| Right | How it is done |
|---|---|
| Access / portability | `GET /v1/privacy/export` (own data) and `GET /v1/tenants/{id}/privacy-export` (staff): every personal item found through the GSI5 subject index, plus the tenant's own agreement signatures with their IP address and browser (`profile.agreement_signatures`) |
| Correction | `PATCH /v1/tenants/{id}` |
| Withdraw consent | `/v1/tenants/{id}/consents`; history is kept |
| Erasure | Privacy request, then the anonymisation job: idempotent, dry-run first, financial records kept without personal fields, including the tenant's agreement signatures (co-tenants' signatures untouched) |
| Deletion by staff (v0.9.0) | `DELETE /v1/tenants/{id}` after a preview. A tenant who never had a lease is removed completely: every item under their subject index, their files (identity documents included: no tenancy, so no financial hold), their invitation and their login. A tenant with lease history is erased through a privacy request (org admin only). Blocked while a lease is live or money is owed either way. The reason is audited, never personal data |
| Retention | The purge job deletes ended leases with their invoices, payments, ledger, receipts and agreement files, then anonymised tenants and inactive properties with nothing left, and closed organisations. Built, dry-run capable, and **off until D5** sets the periods |
| Staff access | Inside an organisation a property manager sees only their own properties and those tenants (need to know). An admin can switch a member's access off or limit it to dates, effective on the next request |

## Sign-off

| Role | Name | Date |
|---|---|---|
| Privacy owner / DPO | _to be named_ | |
| Engineering lead | | |
