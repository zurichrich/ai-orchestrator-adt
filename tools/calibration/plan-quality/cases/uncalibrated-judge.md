---
id: CALP-3
title: Counter-check every plan's Definition of Done for coverage
---
# Coverage counter-check
## Problem (user)
The DoD is written by the session that builds against it and grades itself
green. Nothing checks that the conditions actually cover the spec.
## Plan (PM)
### Problem & goal
Add an independent check that the DoD covers the spec.
### Design
- **Approach:** a read-only subagent in a separate context reads the spec and the
  `done_evidence:` block and returns COVERED / GAP / UNKNOWN. `adt_dod.py --gate`
  refuses any ticket whose recorded verdict is not COVERED, so the check cannot
  be skipped. Ship the gate enabled in the same commit as the reviewer.
- **Why this approach:** a separate context window is where the independence
  comes from; anything in-session is the author grading themselves.
### Sub-steps
- 1a — the reviewer agent file
- 1b — the `--gate` refusal on a non-COVERED verdict
- 1c — the plan playbook invokes it and records the block
### Test plan
- Unit tests for the gate: refuses on GAP, refuses on UNKNOWN, passes on COVERED.
