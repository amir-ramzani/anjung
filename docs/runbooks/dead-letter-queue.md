# A dead letter queue has messages

**When:** Any message in a DLQ means a job failed its retries or was poison (unknown type or version).

## What to do

1. Identify the queue from the alarm name. `payment_events` is money: treat it as urgent.
2. Read a message in the SQS console (do not delete it yet). `type` and `org_id` tell you what it was. Find the matching error in that worker's log group by `org_id`.
3. Fix the cause, then redrive from the DLQ (SQS console, Start DLQ redrive). Handlers are idempotent, so a redrive is safe.
4. For `payment_events`, afterwards check `payment-unmatched` and run the reconciliation job.
