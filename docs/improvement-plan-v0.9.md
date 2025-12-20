# Improvement plan: branding, parking bays, and a standard lifecycle (deactivate and delete)

| | |
|---|---|
| **Status** | Phases C, B and A built (contracts v0.9.0, v0.10.0, v0.11.0). Remaining: the later items (subdomain alias, own domains, B4) |
| **Based on** | contract v0.8.0, HLD/LLD/plan v0.3 |
| **Target contract** | v0.9.0 (lifecycle), v0.10.0 (parking), v0.11.0 (branding) |

Three requests, analysed against the code as built, with a recommended design, the work broken into tasks, estimates and
the decisions needed before starting. Recommended order: **C (lifecycle) → B (parking) → A (branding)**. C matters
for PDPA before any real tenant data goes in, and it is the smallest. A is the largest and has parts that depend on AWS
and on the WhatsApp provider (D3).

---

## A. Branding (white-label) per organisation

> **Built (contract v0.11.0).** As designed below, with these details: the logo is served by the API from the documents bucket (no separate public bucket), PNG, JPEG or WebP only (no SVG); receipts draw PNG and JPEG logos; the SPA's sign-in uses SRP through `amazon-cognito-identity-js` and handles new-password, MFA-code and forgot-password steps; setting up MFA from the app is not built. The seeded demo company has a branded page. See LLD 11.5.

### A.1 What was asked

When a new business account is created, it can optionally set:
- a URL handle;
- a logo;
- a display or company name, and a company registration number (if any);
- a branded login page.

When branding is on, every email, WhatsApp message and page the tenant sees uses that brand.

### A.2 Decision B1: each company is its own organisation (decided)

The businesses that want their own name and registration number are **independent companies**, so each one is a
separate **organisation** and the brand belongs to the organisation. This uses what is already built:
- **Creating a company:** the platform admin creates the organisation and its first org admin
  (`POST /v1/platform/organizations`, the Organisations page). The company's admin then adds its own property managers
  and staff (Users page).
- **Data isolation:** each company's properties, tenants, leases, invoices, documents and audit log are isolated from
  every other organisation, and tests check this route by route.
- **PDPA:** each company is the data user for its own tenants, and Anjung is its processor (D1). Answering that is easier
  than with several brands sharing one organisation.

**Consequences, accepted for this version:**

| Situation | Effect |
|---|---|
| Who creates a company account | The platform admin (Anjung), not an org admin |
| A person working for two companies | Needs a separate email for each, because one login belongs to one organisation |
| A tenant renting from two companies | Same: one email per company. This is rare |
| Seeing across companies | Only the platform admin sees the list of organisations. It does not see their tenants |

One login across several organisations (an organisation switcher) is left for later, about 5 to 8 days, if real demand
appears. It changes the one-organisation-per-identity rule in the Pre Token Lambda and the sign-in.

Brand profiles inside one organisation (several brands per company) are **not** built. If a company later needs
sub-brands, they can be added on top of the organisation's brand.

### A.3 Design

**Brand on the organisation** (new `Organization.branding`, part of the `ORG#o / META` item):
- `enabled` (default off: plain Anjung look)
- `handle` (unique across the platform; lowercase letters, digits, hyphen; 3 to 30 characters; reserved words blocked)
- `display_name` (shown to tenants; defaults to the organisation name), `company_name`, `company_reg_no` (optional,
  e.g. SSM `202301012345 (1234567-X)`)
- `logo` (PNG or SVG, at most 512 KB, scanned, public), optional `accent_colour` (contrast-checked against the theme)
- `contact_email`, `contact_phone` (reply-to and footer)
- `login_page`: `enabled`, `welcome_text` (en and ms)

A handle registry item (`BRANDHANDLE#<handle>`) keeps handles unique and resolves a handle to its organisation without
a sign-in. Changing a handle frees the old one after a grace period (30 days), so links in old messages keep working
during that time.

**Who sets it:**
- The platform admin can fill it in when creating the organisation (the create form gains a branding section).
- The org admin edits it afterwards (Settings → Branding). Managers and tenants only see it.
- Every change is audited, including in the platform organisation when the platform admin makes it.

**Which brand a tenant sees:** their organisation's, everywhere: pages, messages, receipts. No lookup per property or
manager is needed.

**URL handle and login page** (decision **B2, decided:** a path now, subdomains later):
- **Now: a path on the app's own address**, `/login/<handle>`. For example `http://localhost:5173/login/rumahkita`
  locally, `http://<NAS>:8088/login/rumahkita` on the NAS demo, and `https://app.anjung.my/login/rumahkita` once
  deployed. No extra DNS, certificates or hosting are needed, so it works everywhere today.
  - The page calls the public `GET /v1/brands/{handle}` (name, logo, colour, welcome text; nothing private) and shows the
    branded sign-in. An unknown handle, or one whose branding is off, shows the plain Anjung sign-in, never an error
    that reveals whether a company exists.
  - **After sign-in** the brand comes from the person's own organisation (`Me.branding`), not from the address, so the
    whole app stays branded on every page.
  - The browser remembers the last handle, so **signing out** and an **expired session** return to `/login/<handle>`.
    A plain `/login` is always the Anjung page.
  - **The set-password page** (`/invite/<token>`) already knows its organisation from the link, so it is branded with no
    handle in the address.
  - **Links in messages** point to `/login/<handle>` (sign-in) or to the page itself (pay, lease, maintenance), which
    sends a signed-out person to their organisation's branded sign-in.
- **Later: subdomain alias**, `<handle>.anjung.my`, serving the same branded pages. It needs one wildcard certificate,
  one CloudFront distribution and CORS for `*.anjung.my`. `/login/<handle>` keeps working, so old links stay valid.
- Without a handle, or with branding off, people use the plain `/login`.
- **The company's own domain** (e.g. `portal.rumahkita.my`) is a later phase too. It needs a certificate per domain, DNS
  verification by the customer and CloudFront alternate names (decision **B4**).

**A branded login page needs our own sign-in screen** (decision **B3, decided:** each organisation gets its own branded sign-in page and set-password page). Cognito's hosted UI is styled per app client,
not per organisation, and lives on one domain. Recommended: replace the hosted-UI redirect with a sign-in page in the SPA
that talks to Cognito directly (SRP, so the password never reaches our API). It also handles "new password required" and
"forgot password". This change also benefits unbranded sign-in (one consistent page, in Malay too). It must keep MFA
for staff (HLD 5.1) and Cognito's lockout and throttling. The set-password page from v0.8.0 already works this way.

Signing in itself works the same everywhere: an account belongs to one organisation, so someone who opens another
company's page still signs in to their own account. When that happens, the app should send them to their own
organisation's address. Branding is presentation, never a security boundary.

**Messages:**
- **Email:** the template body stays as it is. A brand wrapper adds the logo, the name, and a footer with the company name and registration number and the
  contact details. From: `"<Brand> via Anjung" <no-reply@mail.anjung.my>`, Reply-To: the organisation's contact email.
  Sending from the company's own domain needs SES domain verification per company (later, with B4).
- **WhatsApp:** until the WhatsApp integration (D3) is done, the "Send by WhatsApp" invitation button is hidden; staff use "Share on WhatsApp". Meta templates are approved in advance, so the brand name is a template variable
  (`{{1}} has set up your tenant account…`). Messages come from Anjung's WhatsApp number. A number per company
  needs its own WhatsApp Business account and is out of scope (depends on D3).
- **Receipts and invoices (PDF and pages):** show the brand name, company name, registration number and logo. Receipts still
  carry no tenant name.
- **The staff-shared WhatsApp text** (v0.8.0) and the set-password page use the brand too.

### A.4 Tasks and estimates (ideal days)

| ID | Task | Est. |
|---|---|---|
| A-B1 | `Organization.branding`, handle registry (unique, reserved words, grace period), org admin edit, branding in the platform create form, audit, contract v0.11.0 | 2 |
| A-B2 | Logo upload into a public assets bucket and CDN path (type and size limits, malware scan, re-encoding to strip metadata) | 1.5 |
| A-B3 | Public `GET /v1/brands/{handle}`; `Me.branding`; per-organisation base URL for links in messages | 1 |
| A-B4 | SPA theming: logo and name in the layout, accent colour, branded tenant portal; Settings → Branding; branding section in the Organisations form | 2.5 |
| A-B5 | Own sign-in page with Cognito SRP (sign-in, new-password challenge, forgot password, MFA for staff), replacing the hosted-UI redirect; send people to their own organisation's address | 6 |
| A-B6 | Path routing `/login/<handle>`: branded sign-in from the handle, remembered handle for sign-out and expiry, links in messages | 1 |
| A-B7 | Email brand wrapper (header, footer, From name, Reply-To), brand-aware receipts and invoice pages | 2.5 |
| A-B8 | WhatsApp templates with the brand variable (after D3), brand in the shared invitation text | 1 |
| A-B9 | Tests (handle isolation between organisations, no private data on the public endpoint, branding off by default), docs, data classification | 1.5 |
| | **Total** | **≈ 19** |
| *later* | Subdomain alias `<handle>.anjung.my` (wildcard DNS and certificate, CloudFront, CORS for `*.anjung.my`, handle from the host) | *≈ 2.5* |
| *later* | Company's own domain (certificate, DNS check, CloudFront alternate names, SES domain per company) | *≈ 5* |
| *later* | One login across several organisations (organisation switcher), only if needed | *≈ 5 to 8* |

Until there is an AWS deployment (Phase 0), the real Cognito sign-in (A-B5) can only be tested against a dev user pool.
The mock sign-in gets the same branded page, so `/login/<handle>` can be shown locally and on the NAS demo as it is.

---

## B. Renting parking bays

> **Built (contract v0.10.0).** As designed below. Details: a bay is stored at property level with its allocation as an attribute; bay labels have their own namespace per property; a let bay cannot be re-allocated; deleting a unit keeps its bays as property bays; the plate is not a lease term. The add-on to a room lease is not built. See LLD 5.7.

### B.1 What was asked

A property manager can rent out parking bays the same way as rooms. With a whole unit the bays normally come with it,
but room tenants may rent a bay separately.

### B.2 Design (follows the room pattern)

- A new unit type, **`parking`** (beside `whole_unit` and `room`), with a label (bay number, e.g. `B2-115`) and an optional
  note (level, covered, EV charger).
- **Where a bay belongs:**
  - **Allocated to a unit** (`parent_unit_id` = the unit, like a room). When that unit is let whole, the bay is **included**
    automatically, appears on the lease as an included bay, and cannot be let to anyone else. When the unit is let by
    rooms, the bay becomes separately lettable, typically to one of the room tenants.
  - **Property bay** (no parent): a pool or visitor bay, separately lettable at any time.
- **Letting a bay** (decision **P1, decided:** each bay on its own lease):
  - **Recommended first: its own lease.** Unit = the bay, with its own rent, dates, deposit (e.g. an access-card deposit)
    and agreement. This reuses everything: overlap checks, invoices, reminders, payments, deposits, signing,
    termination and the privacy export. It also works for someone who does not live there.
  - **Later, if wanted: an add-on to a room lease.** One combined monthly invoice, ending with the room lease. It needs
    lease line items for extra spaces, so it is a bigger change. It can be added once there is real demand.
- **Optional vehicle plate number** on a bay lease (useful for guards). It counts as personal data, so it is included in the
  export and erased on deletion (decision **P2, decided:** yes).
- **Occupancy report:** bays are counted separately from living spaces, so room occupancy is not diluted.
- **Tenant portal:** a tenant sees their bay next to their room ("Room Master · Bay B2-115").

### B.3 Rules (enforced in the API, tested like rooms)

- A bay allocated to a unit that is **let whole** cannot have its own lease (409). Switching that unit to room letting is
  allowed only if its whole-unit lease has ended, which is the same guard as rooms.
- A bay cannot have rooms or bays under it. A bay's parent must be a whole unit, not a room.
- Deactivating a unit deactivates its rooms and bays together, as with rooms today.

### B.4 Tasks and estimates

| ID | Task | Est. |
|---|---|---|
| P-01 | `Unit.type = parking`, parent rules, letting guard (included with a whole-unit lease), contract v0.10.0 | 2 |
| P-02 | Leases on bays: included bays shown on whole-unit leases, own bay leases, optional vehicle plate (export and erasure) | 2 |
| P-03 | Occupancy and arrears reports split by space type; display names ("Bay B2-115") | 1 |
| P-04 | SPA: add bays under a property or unit, bay badge, lease form for bays, tenant view | 2.5 |
| P-05 | Tests (double-letting guard, included bays, reports, export), docs, seed demo bays | 1.5 |
| | **Total** | **≈ 9** |
| *later* | Bay as an add-on on a room lease (one invoice) | *≈ 4* |

---

## C. Deactivate and delete, the same way everywhere

> **Built (contract v0.9.0).** As designed below, with these details: deactivate and reactivate are separate endpoints for properties and units, and units switched off with their property or unit come back with it. Deletes take `?reason=`; blocked deletes answer 409 with the reasons. The purge job also deletes closed organisations. Users kept their own delete; documents kept theirs with the retention hold. See LLD 3.11 and 13.3.

### C.1 Why "archive" today

Properties and units are **archived, not deleted**, because invoices, the ledger, receipts, deposits and the audit log
point at them. Deleting a property with history would break those records, and financial records must be kept for the
retention period (decision D5, still with counsel). Tenants become **former**, and erasure is a privacy request that
anonymises them. That is the PDPA path, but it is hidden behind the Privacy page.

The behaviour is mostly right, but it is **inconsistent and incomplete**:

| Module | Today | Gaps |
|---|---|---|
| Organisation | active, suspended, closed (platform admin) | No deletion after closing |
| Users (staff) | deactivate, reactivate, access dates; DELETE removes the membership | Fine. Wording differs from other modules |
| Property | "archive" (the API's DELETE archives), units archived with it | **No reactivate.** No way to delete one created by mistake |
| Unit or room | archived through PATCH | No reactivate; no delete |
| Tenant | invited, active, former, anonymised; erasure only through a privacy request | **No delete button.** A tenant added by mistake cannot be removed |
| Lease | draft, active, terminated, expired | **A draft lease cannot be deleted** |
| Maintenance request | statuses only | Cannot be deleted (personal text is handled by erasure) |
| Document | DELETE, refused under the retention hold | Fine |
| Nothing | — | **No purge after the retention period** (PDPA retention principle); D5 blocks it |

### C.2 One standard for every module

Three actions, the same names and look in every module:

1. **Deactivate / Reactivate** (reversible). The record is hidden from default lists and pickers, and no new activity
   can start (no new leases on a deactivated property, no invitations for a deactivated tenant). Nothing is lost.
   "Archived" becomes **"Deactivated"** in the app.
2. **Delete.** The API decides which kind, and the app explains it before you confirm:
   - **Remove completely** when nothing depends on the record: a property or unit that never had a lease, a tenant with
     no lease, invoice or payment, a draft lease never activated. It is hard-deleted, and so are its
     documents and its login (Cognito user) if any. The audit log keeps a note with no personal data
     (`tenant.delete`, id only).
   - **Erase personal data, keep the financial record** when there is history: the tenant's personal fields are
     anonymised at once, using the existing anonymisation job, now started from a Delete button with a reason, not only
     from the Privacy page. Leases, invoices and receipts are kept without personal data until the retention period ends.
   - **Refused, with the reason**, when it would break something live: an active lease, an unpaid balance, or deposits
     still held.
3. **Purge after retention** (new scheduled job). Once the retention period for a record has passed since its last
   financial activity, the anonymised records are hard-deleted: leases, invoices, ledger entries, documents, receipts,
   then empty properties and closed organisations. The periods are configuration. **The job is built but stays off until
   counsel sets D5**, because until then financial records are held.

**A preview before every delete:** `GET /v1/{things}/{id}/deletion` returns which kind of delete will happen, what it will
affect (counts), and any blockers. The app shows that in a "Delete" dialog. The user types the name to confirm and gives a
reason, which is audited.

**Who may delete:** org admins, everything in their organisation. Managers may deactivate and reactivate what they
manage, and delete only records that can be removed completely (made by mistake). Erasing a tenant with history stays
admin only. Platform admins delete closed organisations after retention.

**API vocabulary (decision L1, decided: yes):** use `status: active | inactive` for properties and units, keeping `archived`
as an accepted alias for one version. Add `POST /…/deactivate`, `POST /…/reactivate` and a real `DELETE`. Today
`DELETE /v1/properties/{id}` archives, which surprises API users. Tenant statuses stay as they are (`former` means "their
tenancy ended", which is still useful), with Delete added.

### C.3 Tasks and estimates

| ID | Task | Est. |
|---|---|---|
| L-01 | Lifecycle module: deletion preview, delete kinds (remove, erase, refuse), reasons, audit; shared by every service | 2 |
| L-02 | Property and unit: reactivate, real delete when unused, `inactive` status with the `archived` alias, contract v0.9.0 | 1.5 |
| L-03 | Tenant: delete (remove when unused, including the Cognito user and invite link; otherwise start erasure), deactivate | 1.5 |
| L-04 | Draft lease delete (clears agreement signatures and its uploaded documents); maintenance request delete when closed (admin) | 1 |
| L-05 | Retention purge job (configurable periods, dry run, report, off until D5), and deletion of a closed organisation | 3 |
| L-06 | SPA: one "Deactivate / Reactivate / Delete" card on every detail page, preview dialog with typed confirmation, "Deactivated" wording, filters | 3 |
| L-07 | Tests (every module × every kind, blockers, managers' limits, purge dry run), docs, data classification, counsel brief | 2 |
| | **Total** | **≈ 14** |

---

## Decisions

| # | Decision | Outcome or recommendation |
|---|---|---|
| ~~B1~~ | ~~Brand per organisation or per manager~~ | **Decided:** each company is its own organisation, and the brand is set on the organisation (section A.2) |
| ~~B2~~ | ~~URL style~~ | **Decided:** path `/login/<handle>` now; subdomain alias `<handle>.anjung.my` later |
| ~~B3~~ | ~~Own sign-in page~~ | **Decided:** our own sign-in and set-password pages, branded per organisation |
| **B4** | The customer's own domain and email domain | Later phase |
| ~~P1~~ | ~~Bay lease or add-on~~ | **Decided:** each bay on its own lease (add-on later only if needed) |
| ~~P2~~ | ~~Vehicle plates~~ | **Decided:** optional field, treated as personal data |
| ~~L1~~ | ~~`archived` → `inactive`~~ | **Decided:** rename, keeping `archived` as an alias for one version |
| **D5** | Retention periods (with counsel) | Needed only to switch on the purge job |

## Effort and order

| Phase | Contract | Ideal days | Needs |
|---|---|---|---|
| C. Lifecycle | v0.9.0 | ≈ 14 | Ready; purge stays off until D5 |
| B. Parking bays | v0.10.0 | ≈ 9 | Ready |
| A. Branding | v0.11.0 | ≈ 19 | Ready; A-B5 needs AWS (Phase 0) to go live; A-B8 needs D3 |
| | | **≈ 42** | plus 20 to 30% for review and rework |

These come on top of the original plan's estimates, like A-01 to A-10. Each phase updates the contract first, then the code,
the HLD and LLD, the status checklist, the data classification sheet and the counsel brief. The counsel brief gets new
questions for branding (company details shown to tenants, the processor agreement per company), vehicle plates and
the deletion policy.

## D. Company plan (added later, built, contract v0.12.0)

Decided with the user: the platform admin sets each company's property limit and end date; the company's admin still divides it up between managers and staff (both levels). After the end date, staff are read-only for 30 days and tenants carry on; then the company is closed until renewed. Org admins get reminder emails 30, 7 and 1 day before. See LLD 3.12.

**Change (contract v0.13.0).** Both limits count **units**, not properties, so one property with many units cannot get round them: every whole unit that is on, in a property that is on. Rooms, parking bays and empty properties are free. Manager limits count units too, and must add up to at most the company's.
