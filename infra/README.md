# infra

AWS CDK (Python). Six stacks per environment, named `Anjung-<Env>-<Stack>`, wired with typed props rather than
CloudFormation exports so each can be deployed alone:

| Stack | Contents |
|---|---|
| `Network` | VPC, gateway endpoints (S3, DynamoDB), interface endpoints in staging and prod |
| `Data` | KMS key, `anjung-main` table (generated from `backend/app/repositories/schema.py`), documents bucket, four queues with DLQs, secrets |
| `Auth` | Cognito (Essentials plan), groups, `spa`, `smoke` and (non-prod) `postman` clients, Pre Token Generation V2 and Post Confirmation Lambdas |
| `App` | ECR, ECS cluster, API behind an ALB, four workers, task roles, autoscaling, the eight EventBridge schedules |
| `Edge` | SPA bucket and CloudFront, security headers, regional WAF on the ALB |
| `Observability` | The alarms of LLD 14.4 (each names a runbook), log metric filters, dashboard |

## Use

Prerequisites: Python 3.12, uv, Node 22, `npm i -g aws-cdk`.

```bash
cd infra
uv sync
uv run pytest                         # 24 tests on the synthesised templates, no AWS needed
cdk synth -c env=dev                  # also: staging, prod
cdk bootstrap                         # once per account and region
cdk deploy "Anjung-Dev-*" -c env=dev -c image_tag=<git sha>
```

Per-environment settings (sizes, region, domains, log retention) are in `anjung_infra/config.py`.

## Before the first real deploy

These are deliberate gaps, each needs a decision or an account:

- **Region** (decision D4, decided): Malaysia, `ap-southeast-5`. Before the first deploy confirm every service we use is
  offered there, notably SES (sending and bounce handling) and Cognito; if one is missing, that service alone may need
  another region, which affects the data-residency story.
- **Domain and certificate** (task P0-13): without `certificate_arn` the ALB serves plain HTTP, which is fine for dev
  only. CloudFront also needs the custom domain and certificate for staging and prod.
- **Image**: push an image to the ECR repository first (`backend/Dockerfile`), then deploy `App` with its tag.
- **Secrets**: `anjung/<env>/payment` holds `REPLACE_ME` until the gateway (D2) is chosen.
- **SES**: verify the sending domain, set DKIM, SPF and DMARC, and move out of the sandbox. The sender is
  `ses_from_address` in `config.py` (staging `no-reply@mail.staging.anjung.my`, prod `no-reply@mail.anjung.my`;
  dev has none and records messages in the log). The API and the workers may send, only from `anjung.my` or a
  subdomain of it: the API sends welcome emails with a set-password link at once, the workers everything else.
  Bounces and complaints: the configuration set `anjung-<env>` publishes them to the topic `anjung-<env>-email-feedback`,
  which feeds the `notifications` queue; the worker suppresses the address.
- **Staff MFA**: the pool makes TOTP optional. Requiring it for staff but not tenants needs a rule in the Pre Token
  Lambda or a second pool.
- **Smoke users**: the smoke organisations and TOTP seeds (plan P1-12) are created by a bootstrap step that does not exist yet.

## Where this differs from the LLD

The API task role has `dynamodb:DeleteItem`, which LLD 14.2 reserves for the housekeeping role. The API deletes lock
and idempotency items inside transactions (unit labels, tenant emails, idempotency keys, documents), and a
transaction needs the per-item permission. Neither role can `Scan` or `DeleteTable`, and a test enforces that.
