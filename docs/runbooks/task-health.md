# Running tasks below desired

**When:** Fewer tasks than desired for 5 minutes.

## What to do

1. ECS console, service events: failing health checks, image pull errors, out of memory.
2. Image pull errors on a new tag mean the deploy pipeline did not push the image: redeploy the previous tag.
3. `Missing required setting` in the task log means a configuration value is absent: the service refuses to start by design (LLD 14.6).
