# API p95 latency above 1 s

**When:** Target response time p95 exceeded 1 s for 10 minutes.

## What to do

1. Check CPU on the `api` service: autoscaling targets 60% CPU and 200 requests per target.
2. Check `dynamodb-throttles`. Throttling shows up as latency first.
3. Look for slow routes in the request log (`latency_ms`). Report endpoints are the usual suspects: they read many items and cache for 60 seconds.
4. Raise `api_max` for the environment in `infra/anjung_infra/config.py` if load is real.
