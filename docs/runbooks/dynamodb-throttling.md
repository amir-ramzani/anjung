# DynamoDB is throttling

**When:** ThrottledRequests above zero for 5 minutes.

## What to do

1. The table is on-demand, so throttling means a hot partition or a sudden spike above double the previous peak.
2. Find the partition: the `ORG#<id>` partition holds properties, units, tenants and leases. A very large organisation may need sharding (LLD 4.6).
3. A runaway job (invoice run, anonymisation) is more likely: check the housekeeping and invoicing workers and pause the offending schedule in EventBridge Scheduler.
