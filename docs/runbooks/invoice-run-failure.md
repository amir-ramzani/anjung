# Invoice run finished with errors

**When:** A sweep or manual run recorded one or more failing leases.

## What to do

1. `GET /v1/invoice-runs` lists the run with `errors` (lease id and error type).
2. Fix the lease data or the bug, then re-run the period: runs are idempotent per (lease, period, kind), so already invoiced leases are skipped.
