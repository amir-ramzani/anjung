# Runbooks

Every CloudWatch alarm in `infra/anjung_infra/stacks_observability.py` names one of these files in its description. A test (`infra/tests/test_stacks.py`) fails if an alarm points at a runbook that does not exist.

- [API 5xx rate above 1%](api-errors.md)
- [API p95 latency above 1 s](api-latency.md)
- [A dead letter queue has messages](dead-letter-queue.md)
- [Oldest message is too old](queue-backlog.md)
- [DynamoDB is throttling](dynamodb-throttling.md)
- [Running tasks below desired](task-health.md)
- [SES bounce or complaint rate too high](email-reputation.md)
- [Payment webhook signature failures](webhook-failures.md)
- [Ledger does not add up](ledger-integrity.md)
- [Settlement report does not match](reconciliation.md)
- [Invoice run finished with errors](invoice-run-failure.md)
- [Money received that could not be matched](unmatched-payments.md)
- [Breach response](breach-response.md)
- [Backup and restore](restore.md)
- [Release and rollback](release-and-rollback.md)
- [Go-live checklist](go-live.md)
