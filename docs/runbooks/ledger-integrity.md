# Ledger does not add up

**When:** The nightly integrity job found a lease whose balance differs from its ledger, or an invoice whose paid amount differs from its allocations.

## What to do

1. This is serious: do not edit data. The job logs `integrity.mismatch` with the lease or invoice id and both numbers.
2. Freeze the lease: tell the organisation not to record payments on it.
3. Reconstruct from the ledger (append-only, the source of truth) and from payment records. Correct with an `adjustment` ledger entry, never by editing an entry.
4. Find the cause (a failed transaction cannot cause this, a manual data edit can) and write it up.
