# Oldest message is too old

**When:** Oldest message age exceeded 10 minutes (payment_events) or 60 minutes (others).

## What to do

1. Is the worker service running? See `tasks-<queue>`.
2. Is it busy? Worker scaling follows queue depth up to `worker_max`.
3. Look in the worker log group for repeated `job failed` lines: a stuck message is retried with backoff and keeps the queue head busy only on FIFO queues (`payment_events`).
4. A single poison message on the FIFO queue blocks its message group: see the DLQ runbook.
