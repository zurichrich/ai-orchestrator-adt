---
id: CALP-5
title: Stop the watcher burning the API quota on every tick
---
# Watcher quota runaway
## Problem (user)
The background watcher exhausted the API quota in one session. It must not be
able to do that again.
## Plan (PM)
### Problem & goal
Bound the watcher's API usage per tick.
### Design
- **Approach:** the push path (`cache -> Issues`) gets a rate limiter: a
  token-bucket in `adt_sync.py` that caps writes per tick, plus a backoff when the
  remaining-quota header drops below a floor. The tick logs what it spent.
- **Why this approach:** the runaway was observed on the push path — the
  reconcile loop writing every changed ticket — so that is where the bound goes.
### Sub-steps
- 1a — token bucket around the push writes
- 1b — read the remaining-quota header and back off
- 1c — log spend per tick
### Test plan
- Unit tests: the bucket caps writes; the backoff fires under the floor.
