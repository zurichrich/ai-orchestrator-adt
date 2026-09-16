---
id: FIXTURE-306
title: dry-run fixture with three DoD authoring defects
track: standard
done_evidence:
  - must_run: test -z "$(echo adt-forensic-investigator | grep -oE '[a-z]+-investigator' | grep -v '^adt-')"
    lane: build
  - must_run: bash tools/tests/fixtures/no_such_test_written_yet.sh
    lane: build
  - must_run: test -f README.md
    was_red_at: f962f15b84e1f066ba7e3809a677368215759533
    lane: build
---

# dry-run fixture

Not a ticket. A fixture for `tools/tests/test_dod_dryrun.py`. Each condition
has a DoD authoring defect.

1. **A prefix check that can never pass.** `grep -oE` prints only the matched
   text, so `[a-z]+-investigator` against `adt-forensic-investigator` prints
   `forensic-investigator`. The `^adt-` filter never sees the prefix, so the
   condition can never go green.

2. **A test that does not exist.** The command exits 127, which `--dry-run`
   flags as EXIT-127.

3. **Always green.** `test -f README.md` was true at its `was_red_at` pin and
   is true now, which `--dry-run` flags as ALREADY-GREEN.

Conditions 1 and 2 work from any directory. Condition 3 needs a git repo to
replay the pin.
