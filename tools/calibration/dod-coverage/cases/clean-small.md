---
id: CAL-7
title: Add a --version flag to the metrics reporter
---
# --version flag
## Plan (PM)
### Problem & goal
Operators cannot tell which build of `adt_metrics.py` produced a report.
### Design
Add a `--version` flag printing the module's `__version__` and exiting 0.
### Impact / ripple analysis
- none beyond the core change — verified by grepping for `adt_metrics` (only the
  CLI entrypoint and its test reference it; no docs name its flags).
### Sub-steps
- 1a — add `--version` to the argument parser and a `__version__` constant
### Test plan
- New unit test: invoking `--version` exits 0 and prints a semver string.
---
done_evidence:
  - must_run: 'python3 -m pytest tools/tests/test_adt_metrics_version.py -q'
    was_red_at: HEAD
    lane: build
