# API 5xx rate above 1%

**When:** The ALB reports more than 1% 5xx over 5 minutes.

## What to do

1. Open the dashboard `anjung-<env>` and the `api` log group. Filter `{ $.status >= 500 }`: each line has `request_id`, `route`, `error_code`.
2. One route failing? Look at the stack trace for that `request_id`. Everything failing? Check the `tasks-api` alarm, DynamoDB throttles and recent deploys.
3. If a deploy caused it: redeploy the previous image tag (`cdk deploy Anjung-<Env>-App -c image_tag=<previous>`). The ECS circuit breaker normally rolls a failing deploy back by itself.
4. Never restart tasks to hide a payments error before checking `payment-unmatched` and the DLQs.
