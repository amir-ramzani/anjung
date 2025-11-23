# SES bounce or complaint rate too high

**When:** Bounce rate above 5% or complaint rate above 0.1%.

## What to do

1. Bounces and complaints suppress the address automatically. A high rate means bad addresses are being entered, or a template is being flagged.
2. Pause the `reminder-sweep` schedule while investigating: SES can pause the account at 10% bounces.
3. Look at the notification log (`GET /v1/notifications/log?status=failed`) to see which organisation is affected.
