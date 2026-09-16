---
name: adt-qa-run
description: QA the ticket in qa/ — grade the DoD, check the diff against the plan, and pass or fail it
adt-budget: 150
---

# /adt-qa-run

## Pre-flight

1. Take the oldest of the highest-priority items in
   `~/.adt/<project>/cache/qa/`.
   **Bind the session to the ticket, then check the binding. If the check fails,
   stop.** Run `.claude/hooks/adt-mark-tix.sh <ID>`, then immediately
   `.claude/hooks/adt-verify-bind.sh <ID>`. If verify exits non-zero, **STOP**,
   and bind and verify again before running QA.
2. Read the backlog file (plan, build log, reviewer sections) and the open PR.
3. **Read `track:`. It sets how deep QA goes.**
   - `fast` never reaches QA. If one lands here it was given the wrong tier: stop
     and flag it instead of running a full pass.
   - `standard` — run the per-sub-step loop and the DoD grade. Targeted
     regression is a judgement call: run it where the diff could plausibly reach
     code that imports the change, and say so when you skip it.
   - `full` — no step below may be SKIPPED. That does NOT mean every step reads
     everything. **The tier decides which checks run; the change decides how
     much each one covers.** A step whose inputs have not changed is satisfied by
     citing the pass that covered them, the same way step 2 treats the suite. If
     you read the whole ticket on every entry, a two-file fix gets a
     twenty-eight-sub-step audit.
   - **Hard floor:** a diff touching a guarded path is `full` here too, and the
     path-triggered reviewers must have signed off.

4. **Is this the first pass, or a return after a FAIL?** A ticket with a
   `## QA report` saying `**Result:** FAIL` has been through this lane before.
   Find the SHA that report graded, and treat **the diff since that SHA** as the
   change under test. Everything the earlier report verified that the new commits
   don't touch keeps its verdict. Checking it again reads the same unchanged code
   and reaches the same answer, and costs the same hour on every return. Say in
   the new report which pass this is and what the baseline SHA is. Without that,
   a reader can't tell it from a report that re-checked nothing.

## What this lane is for

`/adt-build`'s self-review already read the whole diff, ran the tests the diff
touches, graded the build-lane DoD conditions, and proved its new checks can
fail. **Do not repeat that here.** Cite it.

QA is for the checks the author's own reading cannot make:

- **Mechanical checks that don't depend on the author's judgement**, such as the
  mutation sweep (step 8). An author who has just read the code reaches the same
  conclusions when they read it again. Corrupting a value and watching whether a
  test fails does not depend on what the author believes.
- **What the plan predicted against what happened** (step 6). The build works from
  the sub-step list. Only QA reads the Impact section and asks whether the knock-on
  changes it named actually happened.
- **The callers, found again from the diff** (step 4) instead of from the plan's
  file list. Knock-on changes land in files the plan never named.
- **The version bump** (step 3), which the build does not check.

**Independence.** If this lane runs in the same session as the build, it is not
independent: the same context reaches the same conclusions twice. The mechanical
steps still catch things; the reading steps mostly do not. If a project wants a
real second opinion, send this lane to a fresh subagent with the diff, the plan
and no session history, and expect it to cost what the other reviewers cost.
Running every step without that independence pays the full cost for the weakest
version of the check.

## Which steps run — check this before step 1

The mechanical steps ALWAYS run: the DoD grade (step 5) and the Impact check in
step 6. They are cheap and they work whatever context they run in.

The READING steps (4, the per-sub-step verification in 6, and 8) run when ANY of
these is true:

- the diff touches **more than one new file**, or
- the ticket has **more than six sub-steps**, or
- `track: full`, or
- this lane was sent to a FRESH subagent, so it is actually independent.

**Otherwise skip them, and say so in the QA report with the reason.**

In the same session as the build, the reading steps add little (see
"Independence" above). These triggers spend them only where a diff is big enough
to hide something. Cutting a QA pass down to the two mechanical steps has
returned the same PASS and the same findings in minutes instead of an hour.

## Steps

1. **Check out the PR's branch:** `gh pr checkout <PR-number>`.

2. **Run the suite ONLY if the diff changed since the build's green run**
   (working-style #7: a check is triggered by a change, not by a resume).
   `/adt-build`'s self-review already ran the tests against this diff. Compare
   the PR head SHA now with the SHA the build log records:
   - **No commits since then:** the code, its dependencies and its inputs are the
     same as in that green run. Cite it and move on.
   - **New commits** (a fixup, a rebase that pulled in new main): those commits
     are the change that justifies a run. Scope it to what they touch, the same
     way `/adt-build` step 2 does, not the whole suite by habit. Find the runner
     from the repo: `python3 -m pytest`, the `package.json` test script,
     `go test ./...`, a `Makefile` target, or whatever the project's `CLAUDE.md`
     names. Everything you run must pass, and name what you ran.
     If there is no test runner at all, say so. Never pass silently.

   **Record which of the two you did.** A QA report that doesn't say can't be
   told apart from one that never checked.

3. **Check the version bump** if the project config sets `build_version_file`. If
   it wasn't bumped, that is an automatic FAIL. Skip this if the project has no
   version marker.

4. **Find the callers again FROM THE DIFF, and run the tests that turns up.**
   This is not a second run of the tests the build already ran; cite those. For
   every signature, return type or module global the diff changes, list its
   callers **now**, from the repo rather than from the plan's file list. Confirm
   each caller was updated *and* that some test would fail if it hadn't been.
   Run only the tests this turns up that the build did not already cover. Name
   them and say why they are the ones. A change to a caller lands in files the
   plan never mentioned, so checking against the plan's list can't find it.
   Mandatory on `full` or a guarded path; a judgement call on `standard`.

5. **Grade the DoD. It is the objective contract, so do it before the read.**
   ```
   .claude/hooks/adt-dod.sh <ticket.md> --devteam <.adt/> --lane qa
   ```
   Grade the `done` lane too. **Never scope this step down**, on any pass. The
   conditions are the contract, they are cheap, and a return after a FAIL is
   exactly when one of them quietly turns red. Grade the lanes the ticket
   actually has conditions in. A lane with no conditions passes automatically,
   so note that rather than spending a pass proving it. **Any failing condition
   is an automatic FAIL**, with no judgement needed. A spec with a prose DoD
   should never have reached QA. If one did, flag it as a planning defect.

6. **Check the diff against the plan one sub-step at a time, not in one overall
   read.** A single "does the code match the plan?" look is how "implemented 4
   of 5 sub-steps" gets through.

   **Start from the build log instead of working it out again.** `/adt-build`
   appends a line for each sub-step as it lands. Read that first and check it is
   COMPLETE: every sub-step in the plan has an entry. A missing entry is the
   finding this step exists to catch.

   Then verify for yourself only where the build log can't vouch for itself: a
   sub-step with no entry, one whose entry says a test was skipped or checked
   manually, one named in an earlier finding, and any that the mechanical steps
   (4 and 8) point to. Cite everything else as
   `<id> — build log <date>, tests PASS` without re-reading it. Re-reading a diff
   the author read an hour ago in the same context reaches the author's
   conclusions.

   **On a return after a FAIL,** narrow that set again: to what the new commits
   touch, plus every sub-step named in an earlier finding. A sub-step whose files
   did not change cannot have become unimplemented.

   For each sub-step you do verify, give a verdict:
   - **Find its code** in the diff. If no hunk implements it, that is a finding.
   - **Confirm it does** what the plan describes, not just that something changed
     in the right file.
   - **Confirm its named test exists and passed.** A sub-step with no test, or a
     named test that isn't in the diff, is a finding.
   - **"Verified manually" is a FINDING, not a passing test.** A manual check
     only covers the path the author happened to try, usually the happy one.
     Where a self-contained test really is impossible, the QA report needs an
     explicit exception signed off by the user. Do not pass it silently.
   - Record `<id> — implemented ✓ / tested ✓`, or the gap.

   Then four checks on the whole diff:
   - **Scope** — any change outside every sub-step's scope is a finding.
   - **Completeness** — every sub-step has a verdict.
   - **Callers, from the DIFF and not the plan's file list.** For every signature
     the diff changes, list its callers now and confirm each was updated *and*
     that some test would fail if it hadn't been. Changes to callers happen in
     files the plan never named, so a check against the plan's list can't find
     them.
   - **Impact / ripple** — did every entry in the spec's Impact section actually
     happen? A doc the plan said would change but the diff didn't touch is the
     common "README still describes the old behaviour" miss, and this is where
     it gets caught.

7. **If you cite a dry run as evidence, say what it does differently from the
   real run.** A `--dry-run` exists *because* it behaves differently from the
   real run, so its output does not predict one. A dry-run count of tickets that
   "would update" can be far higher than the real run's, because the dry run
   skips the incremental check that lets the real run push nothing. State the
   difference, or run the real thing against a throwaway copy.

8. **Where a figure is the deliverable, prove that a wrong figure fails a
   check.** A check shaped like the document's tables verifies the tables, not
   the arithmetic stated about them, and the arithmetic is what a reader quotes.

   **When this applies:** the output states figures a reader will quote AND
   something claims to verify them. Examples are a generated report, a rendered
   board, or a dashboard panel whose test asserts its values. It does NOT apply
   just because an output contains numbers. If nothing claims to check the
   values, that is the finding: say so and skip the sweep.

   **What to establish:** no figure the output shows can be corrupted without a
   check failing. Report `mutation sweep: N of M caught; survivors: <list>`. A
   surviving mutation that isn't a restated value, a date or an id is a finding.
   **A sweep that doesn't list its survivors is not a sweep.** If the output
   claims coverage of itself, grade that claim against the sweep, not against its
   own description.

   **Do it in a handful of runs, not one run per figure.** Re-run only the checks
   that could see the figure you changed, never the whole suite. Changing a
   fixture value can't affect the validation, rate-limit or storage checks next
   to it. Batch changes that land in different panels into one pass, or render
   once and compare, then re-run only where the output changed. **If a sweep
   would take more than about ten runs of the affected checks, you are running
   too much per figure. Scope it down before you run it.**

9. **Decide: PASS or FAIL.** Both outcomes go through the cache: a folder move
   (plain `mv`, nothing to commit), a `stage:` update, and a `comments:` entry.
   **Do not run `gh issue edit`.** The sync updates the label, the column and the
   comment. Move the file before you set `stage:`. The render treats the folder
   as the truth, so a stage set while the file is still in `qa/` is rewritten
   back to `qa`, and the next sync pushes that.

   **PASS** — the DoD is green, every sub-step is implemented and tested, every
   Impact entry happened, and there are no changes outside the plan:
   1. Append `## QA report`: `**Result:** PASS`, the date, the tests run (or the
      run you cited), `**Findings:** none`.
   2. Move `qa/ → ready-to-release/`. The code PR stays open until the merge.
   3. Set `stage: ready-to-release`; add a `comments:` entry `{author: qa, at:
      <ISO8601>, body: "QA PASS — <one line>"}`.

   **FAIL** — any sub-step unimplemented or untested, anything failing, or
   changes outside the plan:
   1. Append `## QA report`: `**Result:** FAIL`, the date, the tests run, a
      numbered **Findings** list (each with `file:line`, what's wrong, and what
      was expected), and `**Required action:** Fix and re-handoff.`
   2. Move `qa/ → building/` and write the findings to
      `<project>/.adt/inbox/<timestamp>-qa.md`.
   3. Set `stage: building`; add a `comments:` entry `{author: qa, at:
      <ISO8601>, body: "QA FAIL — <one line>. Findings in the ticket."}`.

## When to /adt-block (rare)

Tests fail in a way that points to a problem with the environment rather than the
code, or the plan and the code differ in a way that needs the user's judgement.
