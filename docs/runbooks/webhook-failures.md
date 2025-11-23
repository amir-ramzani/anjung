# Payment webhook signature failures

**When:** More than 5 webhook calls in 5 minutes failed signature or timestamp checks.

## What to do

1. Someone is probing the endpoint, or the gateway rotated its secret. Rotation first: ask the gateway, update `anjung/<env>/payment`, the API picks it up within 5 minutes.
2. If probing: the WAF already rate limits `/v1/webhooks/` per IP. Add the source to a block rule if it persists.
3. Payments are not lost while webhooks fail: the settlement reconciliation job reports any payment we missed.
