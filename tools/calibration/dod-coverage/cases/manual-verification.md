---
id: CAL-3
title: Gate the done-move on the rendering checkout being current
---
# Deploy-freshness gate
## Plan (PM)
### Problem & goal
A ticket can reach done while the checkout that renders the board runs stale
code, so the human sees a board built by pre-merge tooling.
### Design
On the done-move, compare the rendering checkout's HEAD against origin/main and
DENY when it is behind.
### Sub-steps
- 1a — add the freshness comparison to the done-guard hook
- 1b — fail open with a warning when no watcher is installed
### Test plan
- Verified manually against the live watcher on the prod host: the gate did not
  deny on a current checkout.
### Risks
- The comparison must actually update the remote-tracking ref before reading it.
---
done_evidence:
  - must_run: 'bash -n defaults/hooks/done-guard.sh'
    lane: build
