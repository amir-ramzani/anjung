# Release and rollback

- `main` deploys to dev on merge. Staging deploys release candidates (tags). Production needs a manual approval.
- After every deploy the smoke test runs: full in dev and staging, read-only in prod (`smoke/`). A failing check blocks promotion, and in prod redeploys the previous image tag.
- ECS deploys with a circuit breaker: a task that cannot become healthy is rolled back automatically.

## Roll back by hand
```bash
cd infra
cdk deploy "Anjung-Prod-App" -c env=prod -c image_tag=<previous git sha>
```
Image tags are immutable in prod, so the previous tag is exactly what ran before.

## Data changes
Schema changes are additive and backward compatible for at least one release. Backfills run as one-off ECS tasks with a dry-run mode first.
