# Money received that could not be matched

**When:** A payment arrived for an unknown reference or a missing invoice.

## What to do

1. Org-level unmatched payments appear under `GET /v1/payments/unmatched`; ask the organisation to allocate them.
2. Payments for an unknown reference are parked in the platform organisation queue. Identify the organisation from the gateway record and allocate manually, with a reason.
3. Nothing is credited until it is allocated: the money is held, never dropped.
