# Anjung

Rental management system for Malaysian landlords: properties, tenants, leases, DuitNow QR billing,
maintenance and PDPA-ready privacy tooling.

## Status

The backend and SPA implement **every phase of the plan that can be built and verified without an AWS account or a
payment gateway**, plus the additions A-01 to A-14 (implementation plan section 8.1): manager scope, staff access,
tenancy agreement signing, set-password links, deactivate and delete, parking bays, company branding and company plans.
The real gateway adapter, WhatsApp sending and the production rollout are not done. `docs/status-checklist.md` has the
current state of every task. API contract: **v0.17.0**, 99 operations.

| Phase | Area | State |
|---|---|---|
| P0, P1 | Auth, tenancy, properties, units and rooms, audit | Done, in `backend/` |
| P2 | Tenants, consent, invites, leases (rolling, deposits, rooms), documents, privacy export and requests, anonymisation, worker framework | Done. The malware scanner itself (P2-06) is an external service: the status flow and the quarantine are built |
| P3 | Ledger, invoices, daily sweep, payments, webhook, QR, manual payments, allocation, reversals, late fees, receipts, one-off charges, reconciliation, integrity check | Done against a **fake provider**. The real adapter waits on decision D2 (gateway) |
| P4 | Maintenance (incl. shared areas), notifications (consent, quiet hours, reminders, SES, bounce handling) | Done. **WhatsApp** waits on decision D3. SES is coded and untested against AWS |
| P5 | Reports (arrears, occupancy, income, maintenance; CSV downloads), runbooks, infrastructure as code | Reports read DynamoDB directly (the Athena pipeline is not built). CDK stacks synthesise and pass 24 tests, **nothing has been deployed** |
| Additions | A-01 to A-14: manager scope, staff access, unit limits, agreement signing, set-password links, deactivate and delete with a retention purge (off until D5), parking bays, branding, company plans | Done and tested locally |
| Frontend | React SPA: own branded sign-in, staff, tenant and platform screens, the pay screen. `frontend/` | Done for the built API, 150 unit and page tests and 9 browser tests (6 journeys, 3 accessibility checks); installable on phones |
| Tooling | Postman collection (generated, 9 hand-written flows), local smoke flow, home-network demo bundle (HTTPS), Malay review sheet | Done. Postman flows: 130 assertions; local smoke: 45 checks |
| Not started | Deployed smoke checks 3 to 13, load test, penetration test, DR drill, pilot | Needs people, accounts or a deployed environment |

## Repo map

```
backend/     FastAPI app, worker, Lambdas, 400+ tests            -> backend/README.md
infra/       AWS CDK (Python): 6 stacks, 24 tests                 -> infra/README.md
contracts/   openapi.json v0.17.0 (source of truth), Postman      -> contracts/README.md
smoke/       post-deploy checks 1 and 2, local flow (39 checks)   -> smoke/README.md
docs/        HLD, LLD, plan, runbooks
frontend/    React SPA (Vite, TypeScript), typed client from the contract -> frontend/README.md
```

## Quick start

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/), Docker (optional, for the image).

```bash
cd backend
uv sync
uv run pytest          # 400+ tests, no AWS needed (moto, DynamoDB Local)
uv run ruff check . && uv run mypy
```

Run everything locally with mock auth: `docker compose up --build` (see `backend/README.md`).

Demo data and users (local only): `docker compose exec api python -m app.cli seed-demo` creates the "Demo Landlord"
organisation with `boss@example.com` (org admin), `manager@example.com` (property manager), tenants `siti@example.com` and
`ali@example.com`, two properties (one with a unit let by the room), four parking bays, active leases (one for a bay),
this month's invoices (Siti's is paid) and a maintenance request. The company is branded, with its own sign-in page at
`/login/demo-example-com`. Log in with the email only (no password); the platform admin is `root@example.com`. It does
nothing if the organisation exists; use `--email-domain demo2.example.com` to seed another set.

## Docs

- `docs/anjung-hld.md`: high-level design
- `docs/anjung-lld.md`: low-level design (keys, transactions, state machines)
- `docs/anjung-implementation-plan.md`: tasks, estimates, phases
