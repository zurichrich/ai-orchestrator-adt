---
id: CAL-4
title: Stop the sync push burning the API rate limit
---
# Rate-limit discipline for the sync
## Plan (PM)
### Problem & goal
`reconcile_all()` re-fetches every Issue on every tick and drains the hourly
rate limit.
### Design
Add content-hash change detection plus a persisted index to the **push** path, so
a converged ticket costs zero calls. The same `reconcile_all()` pass also runs a
**pull** path that lists every Issue with its full body every 60 seconds.
### Sub-steps
- 1a — content-hash change detection on the push path
- 1b — persist the board index so the push path skips unchanged tickets
### Test plan
- A push-path test asserting a converged ticket makes no API call.
---
done_evidence:
  - must_run: 'python3 -m pytest tools/tests/test_push_incremental.py -q'
    lane: build
