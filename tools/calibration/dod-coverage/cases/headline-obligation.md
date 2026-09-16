---
id: CAL-6
title: Deny a done-move when the ticket's evidence does not hold
---
# The done gate
## Plan (PM)
### Problem & goal
A ticket can be moved to done from intent — nothing asserts the evidence the
ticket itself declared. The gate must DENY the move when that evidence fails.
### Design
A PreToolUse hook matches the `git mv` into `done/`, grades the ticket's declared
evidence, and returns a deny decision when a condition fails.
### Sub-steps
- 1a — add the hook and match the done-move
- 1b — parse the ticket's declared evidence
- 1c — return the deny decision on a failed condition
### Test plan
- Unit tests for the matcher and the parser.
---
done_evidence:
  - must_run: 'python3 -m pytest tools/tests/test_done_matcher.py -q'
    lane: build
  - must_run: 'python3 -m pytest tools/tests/test_evidence_parser.py -q'
    lane: build
