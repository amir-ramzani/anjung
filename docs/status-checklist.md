# Status checklist against the implementation plan

Counted from the 94 task IDs in `anjung-implementation-plan.md` v0.3, plus the 14 additions in its section 8.1
(contract v0.17.0).
**Done** means built and tested on a developer machine. **Nothing has been deployed to AWS**, so every task whose
acceptance criterion says "deployed", "in dev" or "in staging" is at best Partial. The home-network demo (A-09) runs
on a NAS with the mock sign-in. It is not a deployment and does not count towards any deploy criterion.

| | Count |
|---|---|
| Done | 57 |
| Partial | 23 |
| Not started | 4 |
| Blocked (decision, people or account) | 9 |
| Deferred by design (P4-02 vendor role) | 1 |
| **Incomplete (Partial + Not started + Blocked)** | **36** |
| Additions after the plan (A-01 to A-14), all Done | 14 |

## Not done, in full

### Partial (23)

| ID | What is missing |
|---|---|
| P0-01 | CODEOWNERS and PR template written (team handles are placeholders). No commits, remote or branch protection |
| P0-07 | Region decided: Malaysia (`ap-southeast-5`) and set as the CDK default. Service availability there (SES, Cognito) not yet confirmed against an account |
| P0-04 | Stacks synthesise and pass 24 tests, never deployed to dev |
| P0-05 | Network stack: synth only |
| P0-06 | App stack: synth only, no HTTPS (no certificate), image not pushed |
| P0-08 | Auth stack: synth only, no test user has logged in |
| P0-09 | PR checks exist in `ci.yml`. No main pipeline (build, push, deploy, rollback), no image or secrets scan |
| P0-10 | Logging and alarms are coded. Nothing is searchable until deployed |
| P0-12 | `docs/data-classification.md` written from the code. No privacy owner named, no sign-off |
| P0-11 | Key schema is in the LLD and implemented. No team review recorded |
| P0-15 | Checks 1 and 2 exist. Not wired to run after a deploy |
| P1-01 | Table definition and tests done, not deployed |
| P1-F1 | SPA and edge stack done. Not served from CloudFront, no upload step for `dist/` |
| P1-F2 | Since v0.11.0 the SPA signs in on its own page with SRP (new password, MFA code, forgot password, refresh, sign-out), replacing the hosted UI and PKCE. Tested with the Cognito library mocked, never against a real user pool |
| P2-02 | First-login linking done. Since v0.14.0 tenants, and since v0.15.0 staff and a company's first admin, get the app's own branded welcome email (`tenant_invite`, `invite_staff`) with a one-time set-password link instead of Cognito's invite; addresses on the bounce list are refused. Not yet sent through real SES (P4-04) |
| P2-06 | Status flow and quarantine built. The scanner itself is not chosen or built |
| P3-15 | `smoke/local_flow.py` (45 checks, now including agreement signing, set-password links for tenants and staff, parking bays, deletes, branding, the company plan, the maintenance report and a CSV download) and the Postman Flows cover the money path locally. No deployed smoke checks 8 to 13 |
| P4-04 | SES adapter, templates and bounce handler coded; the infrastructure routes bounces and complaints to the worker (configuration set, SNS topic, notifications queue), and the API may send the welcome emails. Domain verification, DKIM/SPF/DMARC and a real send not done |
| P4-F4 | Done: every label translated and checked by a test, and since v0.16.0 field errors and delete blockers too (by code); phone layout (A-07); accessibility audit with axe-core on every main screen in both themes and at phone width, plus keyboard and focus checks, with its findings fixed (`frontend/README.md`); installable as an app (manifest, icons, service worker, offline page). Missing: the native Malay review (sheet ready in `docs/translation/`, 816 texts) and a listening test with a real screen reader |
| P5-05 | Spans across API, queue and worker, trace ids in logs, collector sidecar and X-Ray permissions in CDK. Not deployed, so nothing seen in X-Ray |
| P5-04 | Alarms in CDK, not deployed, no on-call routing |
| P5-09 | Runbook and notice templates (regulator, individuals in English and Malay) written, counsel to review. No tabletop exercise |
| P5-14 | Rollback plan written. The go-live checklist is unsigned |

### Not started (4)

| ID | What |
|---|---|
| P5-01 | Reporting pipeline (Streams, Firehose, Parquet, Athena) |
| P5-06 | Load test |
| P5-07 | Security review and penetration test |
| P5-08 | Backup and DR drill (the runbook exists, the drill has not been done) |

### Blocked (9)

| ID | Waiting on |
|---|---|
| P0-13 | You: name, trademark, domain, GitHub org |
| P1-12 | A deployed Cognito pool for the smoke users |
| P2-11 | You: gateway shortlist, sandbox accounts, written answers |
| P3-06 | Decision D2: payment gateway. Only the fake provider exists |
| P4-05 | Decision D3: WhatsApp route |
| P5-10 | Counsel: PDPA review, and now also decision D9 (agreement signing evidence, stamp duty). Briefing pack ready: `docs/counsel-review-brief.md` |
| P5-11 | Decision D5: retention periods from counsel. The purge job itself is built and off (A-11); only the periods and the review of its dry-run reports remain |
| P5-12 | AWS accounts, domain, certificates |
| P5-13 | A pilot organisation |

## Done (57)

- **P0 (3):** P0-02, P0-03, P0-14
- **P1 (14):** P1-02 to P1-11, P1-13, P1-F3, P1-F4, P1-F5
- **P2 (12):** P2-01, P2-03, P2-04, P2-05, P2-07, P2-08, P2-09, P2-10, P2-F1, P2-F2, P2-F3, P2-F4
- **P3 (18):** P3-01 to P3-05, P3-07 to P3-14, P3-16, P3-F1, P3-F2, P3-F3, P3-F4
- **P4 (8):** P4-01, P4-03, P4-06, P4-07, P4-08, P4-F1, P4-F2, P4-F3
- **P5 (2):** P5-02 (arrears, occupancy, income and maintenance reports with CSV downloads, checked against the ledger in tests), P5-03 (filters, tables, charts and a Download CSV button per report)

## Additions after the plan (14, all Done)

Built and tested locally (backend suite of 441 tests (437 pass; 4 skipped, they need DynamoDB Local), ruff and mypy clean, 150 frontend tests, 9 browser tests (6 journeys and 3 accessibility checks, no WCAG 2.1 AA violations), 24 infrastructure tests, the local smoke flow (45 checks), Postman flows (130 assertions)).
Details are in plan section 8.1 and the contract notes in `contracts/README.md`.

| ID | Addition | Contract |
|---|---|---|
| A-01 | Manager scope: a manager sees only their properties and those tenants, admins see all | v0.4.0 |
| A-02 | Staff access window: deactivate, reactivate, access dates, checked on every request. "Users" page | v0.5.0 |
| A-03 | Limit per manager ("1 / 5"), counted in units since v0.13.0 | v0.6.0, v0.13.0 |
| A-04 | Tenancy agreement signing before activation (portal or paper), org setting to switch it off | v0.7.0 |
| A-05 | Draft lease deposits editable until activation | v0.7.1 |
| A-06 | Document listing, who rents what, maintenance place and people, tenant tenancy status | v0.3.1 to v0.6.0 |
| A-07 | SPA usability: clickable rows, rooms indented, phone layout, translated statuses, DD-MM-YYYY dates | none |
| A-08 | Local Docker stack, mock sign-in with plain messages, `seed-demo`, `smoke/local_flow.py` | none |
| A-09 | Home-network demo on a NAS (`docker-compose.nas.yml`, `docs/nas-deploy.md`); HTTPS through Caddy with its own local certificate (port 8443), uploads kept in a SeaweedFS volume, messages written to the API log | none |
| A-10 | Set-password links: for tenants, shared by staff on their own WhatsApp, sent by the app over WhatsApp (needs D3 in production) or in the welcome email; since v0.15.0 also for staff and a company's first admin, with a resend; no welcome email to a bounced address | v0.8.0, v0.14.0, v0.15.0 |
| A-11 | Deactivate, reactivate and delete in every module, previewed and audited; tenants removed or erased; retention purge built and off until D5 (improvement plan phase C) | v0.9.0 |
| A-12 | Parking bays: included with a unit let whole, otherwise let on their own lease, with an optional vehicle plate (improvement plan phase B) | v0.10.0 |
| A-13 | Organisation branding and the SPA's own sign-in page (branded `/login/<handle>`, emails, receipts); Cognito sign-in needs an AWS dev pool to try for real (improvement plan phase A). Since v0.17.0 the platform admin can also correct a company's name and branding, with a reason | v0.11.0, v0.17.0 |
| A-14 | Company plan set by the platform admin (unit limit, end date), divided up by the org admin in units; read-only for staff after it, then closed | v0.12.0, v0.13.0 |

Known limits of what is built: the local stack's S3 stand-in (moto) keeps files in memory, so it loses uploads when it restarts; the NAS demo uses SeaweedFS with a volume, so its uploads survive. The
manager scope reads the organisation's properties and leases once per request, which suits small portfolios (LLD open
item 14). Nothing is committed to git yet (P0-01).

## Direction

1. **Commit and push** the repository (P0-01). Nothing is under version control yet.
2. **Decisions**: D1 operating model, D2 gateway (with the P2-11 spike), D6 schedule, and D9 agreement signing with counsel
   alongside the PDPA review (P5-10).
3. **Deploy to dev** (P0-04 to P0-10, P1-01, P1-F1, P1-F2): needs an AWS account and confirmation of SES and Cognito in
   `ap-southeast-5`. This unblocks P1-12 and moves most Partial items to Done.
4. **Then** the gateway adapter (P3-06), SES verification (P4-04), the WhatsApp route (D3, P4-05; until then staff share set-password links from their own WhatsApp, A-10), and the reporting
   pipeline (P5-01).
5. **Until then**, the home-network demo is for showing the product only. Keep real tenant data out of it.

## Go-live checklist (plan section 13): 0 of 17

None of the 17 items is checked. Two were added in plan v0.3: agreement signing confirmed by counsel (D9), and mock
sign-in absent outside local. They need a deployed environment, the production gateway, SES, WhatsApp, a domain, counsel
sign-off, the DPO and a pilot.

## Open decisions (plan section 1): 8 of 9 open

D1 operating model, D2 gateway, D3 WhatsApp route, D4 region (decided: Malaysia), D5 retention and breach timeline, D6 schedule or scope,
D7 phone payments (the UI offers "Save QR image" and no redirect option), D8 merchant model, D9 agreement signing evidence
and stamp duty (new: the typed-name method is built, counsel to confirm).
