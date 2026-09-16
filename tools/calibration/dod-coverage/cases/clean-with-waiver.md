---
id: CAL-8
title: Warn when a session claims a rendered artifact it never opened
---
# Claim-side warning
## Plan (PM)
### Problem & goal
A session can claim the board shows something without ever opening the board.
### Design
A Stop hook parses the turn and pairs a claim regex against the turn's tool-use
records, warning when a claim has no matching artifact read. Warn-only: a Stop
hook cannot deny.
### Impact / ripple analysis
- `defaults/README.md` → the hook table describes this hook's behaviour → 1c
### Sub-steps
- 1a — add the claim/evidence pairing to the hook
- 1b — tests for flagged, not-flagged, and prior-turn cases
- 1c — update the hook table
### Test plan
- New unit tests covering all three cases.
- **Manual verification (recorded carve-out, not a passing test):** that the
  warning actually surfaces in the client UI cannot be driven from a test.
  → WAIVED: no hermetic way to drive the harness's message surface.
---
done_evidence:
  - must_run: 'bash defaults/hooks/tests/test_claim_pairing.sh'
    was_red_at: HEAD
    lane: build
  - must_run: 'grep -q claim-pairing defaults/README.md'
    lane: done
