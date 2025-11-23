# Settlement report does not match

**When:** The daily reconciliation found a gateway transaction we did not record, or an amount that differs.

## What to do

1. The report is in the documents bucket under `reports/reconciliation/<provider>/<day>.json`.
2. `missing_payment`: the webhook was lost. Query the gateway for the transaction and replay it as a signed event, or record a manual payment with the gateway reference.
3. `amount_mismatch`: compare the gateway report with the payment record and open a case with the gateway.
