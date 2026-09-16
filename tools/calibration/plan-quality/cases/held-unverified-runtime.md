---
id: CALP-10
title: Bill each session's tokens to the ticket it worked on
---
# Token attribution
## Problem (user)
Token cost is pooling under `__unassigned__` instead of landing on tickets, so
the per-ticket cost on the board is blank and I cannot see what anything cost.
## Plan (PM)
### Problem & goal
Bind a session to the ticket it picked up so the Stop hook bills the right one.
### Design
- **Approach:** when a playbook picks up a ticket it runs a small helper that
  writes the ticket id into a per-session marker file keyed on
  `$CLAUDE_SESSION_ID`. The Stop hook reads the marker for its own session and
  writes the turn's tokens to that ticket's ledger row.
- **Why this approach:** the pickup is the natural binding point, and keying on
  the session id means two parallel sessions never collide. It is the smallest
  change that fixes the pooling.
### Sub-steps
- 1a — the marker-writing helper, keyed on `$CLAUDE_SESSION_ID`
- 1b — the Stop hook reads the marker for its session
- 1c — the playbooks call the helper at pickup
### Test plan
- Unit tests: the helper writes the marker; the hook reads it and bills the row.
### Risks
- A session that never picks up a ticket still pools; that is correct.
