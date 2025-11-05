# smoke

Post-deploy checks (LLD 16.2). Currently checks 1 and 2 (health, unauthenticated rejection).

```bash
uv run --with httpx smoke/smoke_test.py --base-url https://api.dev.anjung.my
```

Prints one line per check, writes JUnit XML to `smoke/results/junit.xml`, exits non-zero on failure.
Sign-in, read and write checks (3 to 13) need the Cognito `smoke` client and seeded smoke orgs (P1-12, P3-15).
To add a check, write a function that asserts and append it to `CHECKS`.

## Local end-to-end flow

`smoke/local_flow.py` drives a local stack (`docker compose up`, mock auth) through the whole money path: organisation,
tenant invite and login, lease, agreement send and sign, activation, deposit and rent invoices, QR, payment through the
real webhook, receipt, ledger, the tenant's email and the income report. Then the newer features: a set-password link
used once (v0.8.0), a new manager's welcome email and set-password link, the maintenance report and a CSV download
(v0.15.0), parking bays included with a unit or let on their own with a plate (v0.10.0), and deactivate and
delete (v0.9.0), the company's branding (v0.11.0), and its plan (v0.12.0). 45 checks, about 6 seconds.

```bash
uv run --with httpx smoke/local_flow.py --base-url http://localhost:8080
# the NAS demo (docs/nas-deploy.md): its platform admin, and accept its own HTTPS certificate for the upload
uv run --with httpx smoke/local_flow.py --base-url http://<nas>:8088 --platform-admin root@anjung-corp.com --insecure
```

It uses the dev-only endpoints, so it cannot run against a real environment.
