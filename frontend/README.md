# frontend

React single page app for staff, tenants and the platform admin. Vite, TypeScript (strict), React Router, TanStack
Query, i18next (English and Bahasa Melayu), and a typed API client generated from `contracts/openapi.json`.

## Run it

Needs Node 22 and the API running (`docker compose up --build` from the repository root, mock auth).

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173, proxies /v1 and /dev to the API on :8080
```

Sign in with any existing user's email, no password (mock auth is refused unless the API runs with `ENV=local`).
The compose stack creates `root@example.com` (platform admin). It can create an organisation, whose
administrator can create staff, properties and tenants. Tenants sign in the same way after being invited.
For ready-made users and data run `docker compose exec api python -m app.cli seed-demo` from the repository root, then
sign in as `boss@example.com` (org admin), `manager@example.com`, `siti@example.com` or `ali@example.com` (tenants).

## Commands

| Command | What it does |
|---|---|
| `npm run gen:api` | Regenerates `src/api/schema.d.ts` from the contract. CI fails if the committed file is stale |
| `npm run typecheck` | `tsc --noEmit`, strict, no unused code |
| `npm test` | 150 unit and page tests (Vitest, Testing Library, happy-dom) |
| `npm run test:e2e` | Six journeys and three accessibility checks in a real Chromium against the running stack (`npx playwright install chromium` once). Another stack, such as the NAS demo: `E2E_BASE_URL`, `E2E_API_URL`, `PLATFORM_ADMIN_EMAIL`, and `E2E_INSECURE=1` for its own certificate |
| `npm run build` | Typecheck, then a production bundle in `dist/` |

## Configuration (build time, `VITE_*`)

| Variable | Meaning |
|---|---|
| `VITE_AUTH_MODE` | `mock` (default, local) or `cognito` |
| `VITE_COGNITO_POOL_ID`, `VITE_COGNITO_CLIENT_ID` | The user pool and the `spa` client from the Auth stack outputs |
| `VITE_WHATSAPP_SENDING` | `true` only once the WhatsApp integration (D3) is live: shows "Send by WhatsApp" |
| `VITE_API_BASE_URL` | API origin. Empty means same origin (the dev proxy) |

## What is in it

- **Sign-in:** our own page, branded per company at `/login/<handle>` (contract v0.11.0). With Cognito it uses SRP
  through `amazon-cognito-identity-js` (the password never reaches our API), handles the new-password step after an
  email invitation, an MFA code, and forgot password; refresh and sign-out use `InitiateAuth` and `RevokeToken`.
  A mock mode signs in by email for local work. Tokens live in `sessionStorage`, so they go when the tab closes.
  A plain `/login` is always the Anjung page; the browser remembers the last company page, so sign-out and an
  expired session return to `/login/<handle>`.
- **Branding:** the company's name or logo and accent colour in the app (from `/v1/me`), Settings → Branding for the
  org admin (handle, names, registration number, colour, contacts, welcome text, logo upload), and a branded
  set-password page.
- **Staff:** properties with units and rooms (letting mode switch), tenants and invites, a lease wizard (fixed or
  rolling, deposits by type) with activate and terminate, invoices with manual payment and void, unmatched payments,
  maintenance triage with comments, reports, staff users, organisation settings (late fee entered as a percentage,
  sent as basis points), audit log, privacy requests.
- **Deactivate or delete:** one section on every record (`components/Lifecycle.tsx`). It reads the delete preview when
  it shows and offers Delete only when it would work; otherwise the reasons appear in its place, next to Deactivate.
- **Documents:** one component (`components/Documents.tsx`) used on leases, properties, tenants (new detail page), maintenance requests and a tenant's own "My documents" page. Uploads check type and size in the browser first, then go straight to S3 with progress, then the screen waits for the virus scan. Downloads use a five minute link and explain a file that is still being scanned or was blocked. Staff can delete, except financial records under retention hold.
- **Tenants:** dashboard (balance, next payment, open requests), invoices, the pay screen, maintenance requests,
  notification preferences, data download and privacy requests.
- **Platform admin:** organisations (create, suspend, reactivate, resend the first admin's invitation, set the plan,
  and correct the name and branding with "Edit details", contract v0.17.0; the logo stays with the company's admin).

### The pay screen

The provider's QR can be valid for about a minute, so the screen creates it when it opens (never in a message),
counts down, and swaps in a fresh one the moment it expires, without a reload. It polls the invoice every four
seconds and flips to the receipt when the webhook lands. "Save QR image" is there because a phone cannot scan its own
screen. If the API rate limits QR creation, the screen stops asking and offers a retry button.

### Money

Amounts are integers of sen everywhere. Input is parsed from text without floating point (`19.99` becomes `1999`),
anything with more than two decimals is refused rather than rounded, and every request sends `amount_minor`.
Requests that create money records send an `Idempotency-Key` that is fixed per form, so a double click cannot book
two payments.

## Not done

- A listening test with a real screen reader (NVDA, VoiceOver, TalkBack): the automated checks and keyboard tests below
  cover what can be checked by script.
- CloudFront deployment: the `Edge` stack serves a build from `dist/`, the pipeline step that uploads it is not written.

## Accessibility

`e2e/a11y.spec.ts` signs in as an org admin, a tenant and the platform admin, opens every main screen and scans it with
axe-core against WCAG 2.1 A and AA (24 screens, then six again in dark mode at phone width in Malay, with the folded menu
open). It also checks with the keyboard: Tab reaches "Skip to content" first and it moves focus to the content; focus
rings are visible; after each navigation focus moves to the new page's heading and the tab title names the page (so
screen readers announce it); `<html lang>` follows the chosen language from the first page. Fields with an error are
marked `aria-invalid`; a form-level issue is shown in the error box. No violations were found.

## Installing on a phone (PWA)

`public/manifest.webmanifest` with 192 and 512 px icons (and a maskable one for Android), an Apple touch icon, and a
small service worker (`public/sw.js`, registered by production builds on secure pages only). Pages always come from the
network, with `offline.html` when there is none; the hashed `/assets/` files are cached after first use; the API
(`/v1/`), documents (`/anjung-docs/`) and dev endpoints are never cached, so no personal data is kept by the worker.
Chrome, Edge and Android show an "Install the app" button (the app's own, from `beforeinstallprompt`); Safari on iPhone
shows how to add it from the Share menu. It needs HTTPS (or localhost): on the NAS demo, trust its certificate first
(`docs/nas-deploy.md`). Checked in Chromium against the production image: installable, no manifest errors, offline page
served. The installed app is "Anjung": a company's own branding shows inside it, not on the icon.

## Documents

Every place that holds documents shows the same uploader and list: tenants (identity, agreement), leases, properties,
units (a "Documents" button on each unit's row opens its own list), invoices (staff add receipts or letters; a tenant
can add proof of a bank transfer), maintenance requests (photos) and the tenant's own "My documents". A file can be
chosen or dragged onto the upload form (one at a time). Each file is checked in the browser, uploaded straight to S3
with a signed form, then malware-scanned before anyone can download it.

## Local documents

In mock mode there is no S3 event or malware scanner, so after a successful upload the screen calls the dev-only
`/dev/documents/{id}/scan` endpoint, which marks the file clean. The S3 stand-in is the `s3` container on port 9000.
