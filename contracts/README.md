# contracts

`openapi.json` (v0.17.0, OpenAPI 3.1) is the source of truth for the API.

- Change the contract first, then the code. `backend/tests/contract` fails when routes, operation IDs, status
  codes, `x-required-roles` or request/response fields drift from it.
- Planned: SPA client generation (P1-F3) and the Postman collection with a CI drift check (P1-13) in `postman/`.
- Production URLs in `servers` are placeholders until the domain is registered (P0-13).

## Changes after the design documents

The HLD, LLD and implementation plan (v0.3, in `docs/`) include all of these.

- **v0.17.0:** The platform admin can correct a company's details: new `PATCH /v1/platform/organizations/{org_id}/details`
  (`updateOrganizationDetails`, body `OrganizationDetailsUpdate`: optional `name` and `branding`, required `reason`).
  The same rules as the org admin's `updateOrganization` (409 for a handle another company uses, 422 for a reserved one),
  audited in both organisations as `org.update`. Not for the platform organisation (404) or a closed one (409). The logo
  and the settings stay with the org admin (`branding.logo_document_id` is 422 here).

- **v0.16.0:** Errors in the user's language, for the rest: each `Error.details` item has a `code` (the validator's
  error type, such as `missing` or `string_too_short`, or a code made from the fixed English issue, such as
  `must_not_be_before_from`) and `params` with the values in it; `Blocker.params` carries the values in a delete
  blocker (`amount_minor`, `count`, `what`, `units`, `more`). `issue` and `message` stay the English fallback.

- **v0.15.0:** Staff welcome emails: `inviteUser` and `createOrganization` (its first admin) send the app's own
  welcome email with a one-time set-password link (the same `/invite/<token>` page and endpoints as tenants;
  `InviteInfo.kind` is `tenant` or `staff`) instead of Cognito's invitation with a temporary password, which is still
  used when email sending is not set up. New `POST /v1/users/{user_id}/invite` (`resendUserInvite`, org admin) and
  `POST /v1/platform/organizations/{org_id}/admin-invite` (`resendOrganizationAdminInvite`, with a reason) send a
  fresh link to staff who have not signed in. Welcome emails are not sent to an address on the bounce/complaint list:
  `409 address_suppressed` (new code, `params.scope` `tenant` or `staff`). Reports: new `GET /v1/reports/maintenance`
  (`getMaintenanceReport`), and `format=csv` on every report returns the rows as a CSV download (`text/csv`).

- **v0.14.0:** `POST /v1/tenants/{id}/invite` with `channel: email` (the default) sends the app's own welcome email
  with a one-time set-password link, branded like the other emails, instead of Cognito's invitation email with a
  temporary password. Same request and response; `link` stays null. 409 when email sending is not set up or fails.

- **v0.13.2:** `Property.units_active`: the whole units that are on, which is what the plan and manager limits
  count (`unit_count` also includes units that are off). Sent by `createProperty`, `getProperty` and `listProperties`; the Properties
  page shows "1 (1 inactive)" when they differ.

- **v0.13.1:** `Error.params`: the values in the message (for `quota_exceeded`: `scope` `plan`, `own` or `manager`,
  `limit`, `used`, `name`; for `plan_read_only`: `date`, `until`), so clients can show it in the user's language.
  `message` stays the English fallback.

- **v0.13.0 (breaking for limits):** limits count units, not properties: every whole unit that is on, in a property
  that is on; rooms, parking bays and empty properties are free. `OrgPlan.property_limit` / `properties_used` become
  `unit_limit` / `units_used`; `User.property_quota` / `properties_managed` become `unit_quota` / `units_managed`
  (also in `UserInvite`, `UserUpdate`, `PlanIn`, `PlanUpdate`); `PropertyPage.quota` counts units. Creating an empty
  property is never refused. Values stored under the old names are read as the new ones.

- **v0.12.0:** a company's plan, set by the platform admin: `Organization.plan`, `OrganizationCreate.plan`, and
  `PATCH /v1/platform/organizations/{org_id}/plan` (property limit for the whole organisation, end date, reason).
  The org admin's manager limits must fit within it (422) and staff end dates must not pass it (422). After the end
  date staff writes answer `403 plan_read_only` for 30 days (money received and privacy requests still work, tenants
  unaffected), then everyone gets `403 plan_ended`. `Me.plan` for staff. New error codes `plan_read_only`, `plan_ended`.

- **v0.11.0:** organisation branding. `Organization.branding` / `OrganizationUpdate.branding` (partial; org admin) and
  `OrganizationCreate.branding` (platform). Handles are unique, some reserved, and a changed handle keeps working for
  30 days. New public `GET /v1/brands/{handle}` (`PublicBrand`) and `GET /v1/brands/{handle}/logo`. `Me.branding`,
  `InviteInfo.brand`. Documents: owner type `organization`, type `logo` (PNG, JPEG or WebP, at most 512 KB). Branded
  emails and receipts follow from the same settings. The SPA uses its own sign-in page (SRP) at `/login/<handle>`.

- **v0.10.0:** parking bays. `Unit.type` gains `parking`; a bay may be allocated to a whole unit (`parent_unit_id`)
  and then comes with it while it is let as a whole (`Lease.included_bays`), otherwise it is let on its own lease.
  `UnitUpdate.parent_unit_id` re-allocates a bay. `Unit.note`. `Lease.vehicle_plate` (bay leases only, personal
  data: exported and erased). Display `Lease.unit_type`, `TenantRental.unit_type`. Occupancy gains `bays_total` and
  `bays_occupied`. No new operations. The `archived` alias stays until v0.11.

- **v0.9.0 (breaking for property archiving):** one lifecycle in every module. Properties and units say `inactive`
  instead of `archived` (`archived` is still accepted as input until v0.11, and old items read as `inactive`). New
  `POST /v1/properties/{id}/deactivate` and `/reactivate`, and the same for units. **`DELETE /v1/properties/{id}` now
  deletes** (it used to archive) and needs `?reason=`. Every delete has a preview, `GET …/deletion` →
  `DeletionPreview` (`remove`, `erase` or `blocked`), and returns `DeletionResult`. New deletes: units and rooms,
  tenants (removed if they never had a lease; otherwise erased through a privacy request, org admin only), draft
  leases, and closed or cancelled maintenance requests (org admin). The Postman flows cover it.

- **v0.8.0:** set-password links for tenants. `POST /v1/tenants/{id}/invite` takes an optional `{channel}`:
  `email` (default, unchanged: Cognito's invitation email with a temporary password), `whatsapp` (the app sends a
  one-time link over WhatsApp; needs the tenant's number and recorded WhatsApp consent, `409` until a WhatsApp
  route exists, decision D3) or `link` (returned for staff to share, for example from their own WhatsApp). It now
  returns `TenantInviteResult`. New public `GET /v1/invites/{token}` and `POST /v1/invites/{token}/accept` (sets
  the password and links the account). Only a hash of the token is stored; a link works once, only the newest
  works, and it expires with the invitation (7 days). The link never enters the queue or the notification log.

- **v0.7.1:** `LeaseUpdate.deposits` lets staff change a draft lease's deposits before activation (the deposit
  invoice is issued on activation, so an active lease answers `422`). Like the other terms, a change clears the
  agreement signatures. Privacy (no shape change): a tenant's export keeps only their own agreement signature
  and adds their signing evidence (IP address, browser) under `profile.agreement_signatures`; erasure scrubs the
  tenant's signatures and leaves co-tenants' alone.

- **v0.7.0:** tenancy agreement signing. Staff upload the agreement (`lease_agreement` document) and send it
  (`POST /v1/leases/{id}/agreement/send`); each tenant then sees the draft lease and signs in the portal by typing their
  full name (`/agreement/sign`, recorded with time, document and device), or staff record a paper signature
  (`/agreement/paper`). With `OrgSettings.require_signed_agreement` (default on), `activate` refuses until all have
  signed. Changing a draft's terms after sending clears the signatures. Human-readable dates in messages and receipts
  are DD-MM-YYYY; API fields stay ISO 8601.

- **v0.6.0:** property limit per property manager (`User.property_quota`, null = no limit, and `User.properties_managed`).
  Active properties count, inactive ones do not. A manager creating, or an admin assigning, a property beyond the limit
  gets `409 quota_exceeded`. `PropertyPage.quota` (`used`, `limit`) lets the Properties page show "1 / 5". Lowering a
  limit never takes properties away; it only blocks new ones. Org admins are never limited. Also: `Me.active_from` /
  `active_until` (staff see their own access dates) and maintenance display names (`property_name`, `unit_label`,
  `parent_unit_label`, `reported_by_name`, `assigned_to_name`), and `Tenant.tenancy` (current, upcoming, past, none),
  which is separate from the account `status`: a past tenant keeps their login until staff mark them former.

- **v0.5.0:** staff access window. An org admin can disable a staff member or limit them to dates (`User.active_from`,
  `active_until`, inclusive Malaysian dates, on invite or `PATCH /v1/users/{id}`); `User.access` says what that means
  today. Disabled, not-yet-started and expired staff get `403 account_inactive` at sign-in and on every request
  (`backend/app/core/access.py`), so it takes effect at once. Their properties stay assigned to them (an admin can
  reassign them) and come back when they are reactivated.

- **v0.4.0 (breaking for property managers):** manager scope. A property manager sees only the properties they
  manage (`Property.manager_id`, the creator; an org admin reassigns it with `PATCH /v1/properties/{id}`) and what is
  attached to them (units, leases, invoices, ledgers, maintenance, documents, reports, notification log), plus the
  tenants they created and every tenant on a lease in their properties. Everything else is a 404. Unmatched payments,
  their allocation and invoice runs are org-wide, so they are now org admin only. Implemented in
  `backend/app/domains/scope.py`.

- **v0.3.2:** optional, read-only display fields so lists can say who rents what. `Lease` gains `property_name`,
  `unit_label`, `parent_unit_label` (rooms) and `tenants` (id and full name), filled by the lease endpoints only (the
  privacy export leaves them null, so one tenant's export never lists co-tenants). `Tenant` gains `rentals`: active and
  draft leases with their property and unit or room. Names are resolved when read, so renames show at once.

- **v0.3.1:** added `GET /v1/documents?owner_type&owner_id` (list the documents of one owner, newest first) and the `DocumentPage` schema. Staff could not otherwise find a document again after uploading it.
