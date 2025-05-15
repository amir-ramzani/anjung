# backend

FastAPI service for Anjung. Layering is `router -> service -> repository` (LLD section 2.2).
Routers parse and authorise, services hold rules, repositories know only DynamoDB.

## Setup

```bash
uv sync                      # Python 3.12 is selected through .python-version
```

## Tests and checks

Parallel-transaction tests (lease activation races, letting-mode flip, unit counts) only run on DynamoDB Local,
because moto does not isolate concurrent transactions. CI runs the suite both ways:

```bash
docker run -d -p 8000:8000 amazon/dynamodb-local
ANJUNG_TEST_DDB_ENDPOINT=http://localhost:8000 uv run pytest     # all tests, real transaction semantics
```

```bash
uv run pytest                          # everything
uv run pytest tests/cross_org          # tenancy suite
uv run pytest tests/contract           # code versus contracts/openapi.json
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Tests use `moto` for DynamoDB and KMS, an in-memory identity provider instead of Cognito, and
locally signed RS256 tokens. No AWS account is needed.

**Adding an endpoint:** add it to `contracts/openapi.json` first, then the code, then register it in
`tests/cross_org/test_cross_org.py` (`test_every_route_is_registered` fails otherwise). Role checks
must use `require_roles(...)` and match `x-required-roles`; `tests/contract` enforces both.

## Running the API locally (Docker + mock auth, no AWS)

From the repo root, one command starts DynamoDB Local (persistent volume) and the API with mock auth:

```bash
docker compose up --build        # add -d to run in the background
```

On start the API creates the table, the documents bucket and the platform admin (both idempotent), then serves on
`http://localhost:8080` (Swagger at `/docs`). The platform admin email defaults to `root@example.com`;
override it with `PLATFORM_ADMIN_EMAIL=you@example.com docker compose up`.

```bash
login() { curl -s localhost:8080/dev/token -H 'content-type: application/json' \
          -d "{\"email\":\"$1\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }

ROOT=$(login root@example.com)
curl -s localhost:8080/v1/platform/organizations -H "Authorization: Bearer $ROOT" \
  -H 'content-type: application/json' \
  -d '{"name":"Acme","initial_admin":{"email":"acme-boss@example.com"},"reason":"local"}'

BOSS=$(login acme-boss@example.com)          # the org admin you just created
curl -s localhost:8080/v1/properties -H "Authorization: Bearer $BOSS"
```

**Demo users and data** (instead of creating them by hand): `docker compose exec api python -m app.cli seed-demo`.
It creates "Demo Landlord" with `boss@example.com` (org admin), `manager@example.com` (property manager), tenants
`siti@example.com` and `ali@example.com`, two properties (one with a unit let by the room), active leases, this month's
invoices (Siti's is paid, Ali's is outstanding) and a maintenance request. It goes through the real API, does nothing
if the organisation already exists, and refuses to run unless `AUTH_MODE=mock`. If `example.com` addresses are taken, add
`--email-domain demo2.example.com` to create a separate set (`boss@demo2.example.com` and so on).

- `docker compose down` stops it and keeps the data. `docker compose down -v` wipes the data.
- Code changes need `docker compose up --build`.
- `AUTH_MODE=mock` is refused unless `ENV` is `local` or `test`. Sign-in uses the same claim logic as the
  real Pre Token Lambda (suspended orgs and disabled users get 403 from `/dev/token`). Tokens are signed with a
  key generated at startup, so restarting the API invalidates them: request a new one.
- Use real-looking email domains (`.test` and `.example` fail validation).
- **Tenants** sign in locally too: create one, `POST /v1/tenants/{id}/invite`, then `POST /dev/token` with their email.
  The first token request links the account, like Cognito's Post Confirmation trigger does on first login.
- **Documents:** local S3 is a moto container (`s3`, port 9000). It does not enforce the upload size limit or
  policy conditions the way real S3 does. After uploading to the presigned URL, mark the scan result with
  `POST /dev/documents/{id}/scan {"org_id": "...", "verdict": "clean"}` (stands in for the S3 event + scanner).

### Money, jobs and messages locally

In mock mode there is no SQS: jobs run inline, right after the request that caused them. Everything below works
through the real code paths.

```bash
# 1. create an org, tenant, unit, lease and activate it (see the endpoints in /docs), then run the daily invoice sweep
curl -s localhost:8080/dev/jobs/invoice_sweep -H 'content-type: application/json' -d '{"org_id":"<org id>"}'
# 2. the tenant opens the pay screen: POST /v1/invoices/{id}/qr returns a reference. Pay it like the gateway would:
curl -s localhost:8080/dev/pay -H 'content-type: application/json' -d '{"reference":"ANJ...","amount_minor":160000}'
# 3. see the invoice paid, the ledger, the receipt, and the "email" the tenant would have received
curl -s localhost:8080/dev/outbox
# other jobs: overdue_sweep, reminder_sweep, lease_expiry, integrity_check, maintenance_autoclose, release_deferred
```

Messages only go out to tenants who set preferences and gave consent (`PUT /v1/notifications/preferences` and
`POST /v1/tenants/{id}/consents`). The same jobs run from the command line:
`uv run python -m app.cli run-job integrity_check --org <org id>`.

### Without Docker for the API

Run only DynamoDB in Docker and the API from source (faster edit cycle):

```bash
docker compose up -d dynamodb
cp .env.example .env && set -a && source .env && set +a
export DYNAMODB_ENDPOINT_URL=http://localhost:8000 AWS_ACCESS_KEY_ID=x AWS_SECRET_ACCESS_KEY=x
cd backend
uv run python -m app.cli create-table
uv run python -m app.cli bootstrap-platform --email root@example.com
uv run uvicorn app.main:app_factory --factory --reload --port 8080
```

The in-memory `moto_server` also works (`DYNAMODB_ENDPOINT_URL=http://localhost:5555`) but loses data on exit.

## Settings (LLD 14.6)

Validated at startup, the service refuses to start on missing or malformed values.

| Variable | Meaning |
|---|---|
| `AUTH_MODE` | `cognito` (default) or `mock` (local only, adds `/dev/token`) |
| `ENV` | `local`, `dev`, `staging`, `prod` (docs are disabled in `prod`) |
| `AWS_REGION`, `TABLE_NAME`, `DYNAMODB_ENDPOINT_URL` | DynamoDB (`anjung-main`), endpoint override for local |
| `COGNITO_POOL_ID`, `COGNITO_CLIENT_IDS` | Token issuer and allowed app clients (comma separated) |
| `COGNITO_SUPPRESS_INVITE_EMAIL` | `true` once the SES invite template exists (phase 4). Default `false`: Cognito sends the invite |
| `PLATFORM_ORG_ID` | ULID of the reserved platform organisation |
| `CURSOR_HMAC_KEYS` | Pagination signing keys, first signs, all verify (rotation). 32+ chars |
| `ORG_STATUS_CACHE_TTL_SECONDS` | How fast a suspension blocks existing tokens (default 60) |
| `KMS_KEY_ID` | Enables field encryption (identity numbers, phase 2) |
| `CORS_ALLOW_ORIGINS` | SPA origin(s) |
| `APP_BASE_URL` | The SPA's address; links in messages (pay, set-password) point here |
| `PUBLIC_API_URL` | Optional: where mail clients fetch logo images from; defaults to `APP_BASE_URL` (which serves `/v1` locally and on the NAS) |
| `INVITE_TTL_DAYS` | How long a tenant invitation and its set-password link last (default 7) |
| `RETENTION_FINANCIAL_DAYS`, `RETENTION_CLOSED_ORG_DAYS` | Retention purge periods (decision D5). Unset = the purge does nothing |

## Design notes and deviations from the LLD

- **Sync boto3, not aioboto3.** Endpoints are plain `def`, so FastAPI runs them in a thread pool. This keeps
  moto-based tests simple and JWKS fetches off the event loop. Revisit if profiling says otherwise.
- **Cursors** are bound to the partition *and* the sort-key prefix, so a cursor from one list cannot drive
  another list (found by the cross-org suite).
- **GSI2** projects `org_id, roles, entity_type, status, tenant_id, email, name`, a superset of the LLD, so the
  Pre Token Lambda needs a single query.
- **Unit labels** are unique per property through `UNITLBL#` lock items written in the same transaction.
- **Tenant access to `GET /v1/units/{id}`** is allowed only for the unit of the tenant's own non-draft lease.
- **Unit lock:** `UNITLOCK#<unit>` holds the periods of all activated leases. Activation writes the lock, the
  lease and the unit in one transaction with a version check, so two leases can never both claim the dates
  (a parallel activation test proves exactly one wins).
- **Amending an active lease** applies the new terms immediately and appends to `amendments` with `effective_from`
  (default: first day of next month). Billing (phase 3) must read that history for earlier periods.
- **Rooms and letting mode (contract v0.3.0, LLD 5.5):** a whole unit is let `whole` or by `rooms`. A room is a unit
  (`type=room`, `parent_unit_id`) stored next to its parent. Activating a room lease updates the parent's
  `active_room_leases` in the same transaction, and a letting-mode change needs that counter and the unit's own
  lock to be empty, so a unit and its rooms can never be let together. Deactivating a whole unit deactivates its rooms.
- **Rolling leases:** `end_date: null`. The unit lock holds them open-ended (`9999-12-31`). Termination needs
  `notice_days` from today unless `waive_notice` (audited). Renewal is for fixed-term leases only.
- **Deposits** are a list by type. They are *held* only after their invoice is paid (phase 3), so a
  `deposit_deduction` is validated against `deposits_held`.
- **Existing local data from before v0.3.0** used a single `deposit` field: wipe it (`docker compose down -v`).
- **Deposit and termination ledger effects** (deposit charge, void unbilled periods, deposit return) are
  left for phase 3 with the ledger. Renewals carry a zero deposit.
- **Documents:** financial documents (`lease_agreement`, `identity`, `receipt`) are under a retention hold and cannot be
  deleted on their own until counsel sets retention periods (decision D5). The organisation's `logo` is a document too.
- **Later additions (contracts v0.4.0 to v0.14.0)** are described in the LLD: manager scope and unit limits (3.8), staff
  access (3.9), deactivate and delete (3.11), company plans (3.12), parking bays (5.7), agreement signing (5.6),
  set-password links (11.2), branding and the SPA sign-in (11.5), and the retention purge (13.3).
- **Rate limiting** is per task (QR creation, and the public set-password and brand endpoints per client IP). The WAF is
  the real flood control.
- **Reports** read DynamoDB directly behind `ReportSource`; the Athena pipeline is not built.
- **Not built:** the real payment gateway adapter (decision D2), WhatsApp sending (D3; a recording channel stands in
  locally), the malware scanner service. The retention purge is built but stays off until the periods are set (D5). Cursor keys come from an environment variable (the ECS task injects it from Secrets Manager).
