---
id: CAL-2
title: Add a metrics reporter that counts follow-on tickets
---
# Add a metrics reporter
## Plan (PM)
### Problem & goal
Nothing counts how many follow-on tickets a delivery spawns, so the follow-on
rate cannot be baselined or shown to fall.
### Design
A new `tools/adt_metrics.py` walks the closed backlog, reads each ticket's
`follow_ons:` frontmatter, and prints the per-ticket count plus the mean.
### Sub-steps
- 1a — write `tools/adt_metrics.py`
- 1b — it must handle a ticket with no `follow_ons:` key (treat as 0)
- 1c — it must print the mean across all closed tickets
### Test plan
- Confirm the file exists and is importable.
---
done_evidence:
  - must_run: 'test -f tools/adt_metrics.py'
    lane: build
  - must_run: 'python3 -c "import sys; sys.path.insert(0,\"tools\"); import adt_metrics"'
    lane: build
