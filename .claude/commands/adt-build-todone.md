---
adt_managed: ADT-087
name: adt-build-todone
description: Approve a checkable plan once, then drive the ticket plan→build→qa on its own, grading each stage against the machine-checkable DoD, and stop only at the merge-offer or when stuck
adt-budget: 160
---

# /adt-build-todone

This is the **approve-once** driver. `/adt-build` stops at every lane boundary
and waits for the human. This command drives a ticket through **build → review
→ qa** in one run, grades itself on each pass against a machine-checkable DoD,
and stops only at the **merge-offer** or when it is **stuck**.

**Why this is safe.** The loop advances a stage only when that stage's DoD slice
grades green via `.claude/hooks/adt-dod.sh`. Each condition is a process exit
code or a regex on a rendered file, never the agent's own account of the work.
The gate refuses any plan whose DoD contains a prose condition, so the loop can
never grade its own homework.

This is the only command that suspends stop-at-every-gate. Manual `/adt-build`
and `/adt-qa-run` still stop at every gate, and no flag changes that.

## The Definition of Done — `done_evidence:` frontmatter

Each entry is one objective condition with a `lane:` that says which stage it
gates:

```yaml
done_evidence:
  - must_run: 'python3 -m pytest tools/tests/test_x.py'   # exits 0 → met
    was_red_at: <a real, checkoutable SHA>                # asserts a real red→green
    lane: build
  - file: .adt/kanban.html                    # rendered artifact
    must_contain_regex: '<a [^>]*href="references\.html"'
    lane: done
```

- `must_run` is met when the command exits 0.
- `was_red_at` re-runs the command at that ref in a throwaway worktree, to
  confirm the condition really went from red to green and was not always green.
- `must_contain_regex` is met when it matches in the `.adt/` rendered file.
- `lane` is one of `plan|build|qa|done`; the default is `build`.

**Prose conditions are not allowed.**

**`was_red_at` takes a real ref, never the string `plan`.** The string `plan`
parses, but the grader returns can't-verify for it. `all_green` can never clear
a can't-verify, so the lane it gates never advances and the loop stalls on a
condition that looks correct.

**The gate also refuses four more things.** A DoD can be checkable and still be
incomplete or impossible to satisfy, and it is cheaper to find that here than
mid-loop.

- **No `### DoD-coverage review`, or a `GAP`/`UNKNOWN` verdict.** The session
  that builds against the DoD also wrote it, so a reviewer in a separate context
  must confirm the conditions COVER the spec.
- **No `### Plan-quality review`, or a `FLAWED`/`UNKNOWN` verdict**, when the
  reviewer is calibrated above its bar. The coverage review confirms the DoD
  matches the spec. The plan-quality review confirms the spec was worth
  building: a wrong Design with a faithful DoD passes every other check here.
  This matters most in this command, because a design defect the gate lets
  through runs unchecked until the merge-offer. **A recorded FLAWED still stops
  the loop even where calibration can't enforce it.** Do not start an autonomous
  run against a plan a reviewer called flawed.
- **A caveat that points at nothing** in Design, Risks or Test plan. Each caveat
  must point at the condition that resolves it (`-> DoD:<id>`) or be a recorded
  waiver (`-> WAIVED: <reason>`).
- **A dependency defect**: a `depends_on:` that names an id no condition
  declares, or a cycle. Either one makes the ordering impossible to satisfy.

Where B `depends_on` A, B grades **can't-verify** while A is red, rather than
green. This does not tell the loop that B is broken. It only stops B being
called green before the condition it depends on.

## Pre-flight — the approval gate, fail-closed

1. **Resolve the entry lane.** Use `--from=<lane>` if given. Otherwise read it
   from the cache folder, which is the source of truth: `building/`→build,
   `qa/`→qa, `planned/`→build. A ticket already in `qa/` (built by hand, or
   bounced back) is driven from QA with no ping on each iteration.

2. **Bind the session, then verify it — fail closed.**
   Run `.claude/hooks/adt-mark-tix.sh <ID>`,
   then immediately `.claude/hooks/adt-verify-bind.sh <ID>`.
   If verify exits non-zero, **STOP**, re-bind and re-verify before any work.

3. **Refuse to start unless BOTH hold:**
   - **`plan_approved: true`** is in the frontmatter. This is the human's one
     decision. Without it, STOP with "plan not approved; approve it or run
     /adt-build for gate-per-turn."
   - **Every DoD condition is checkable, as decided by the script.** Run
     `.claude/hooks/adt-dod.sh <ticket.md> --gate`. It prints `APPROVABLE`
     (exit 0) or `REFUSE<tab><reason>` (exit 1). On REFUSE, STOP and relay the
     reason. Never start an autonomous run against a condition the grader can't
     decide, whatever your own reading of it.

4. **Read `track:`. It picks the lane path; it does not decide whether to run.**
   `standard` and `full` take the full path. `fast` takes a shorter path,
   `building → ready-to-release`, with no QA lane, because a fast ticket never
   reaches QA. A fast ticket gets its `done_evidence:`, its two verdicts and its
   tier stamp from `/adt-plan-fasttrack`. If a fast ticket lacks any of those,
   **the gate** refuses it. There is no separate tier check: the gate treats
   every tier the same on purpose.

   **The hard floor overrides the stamp.** If the diff turns out to touch a
   guarded path, the ticket is `full` whatever `track:` says, its reviewers are
   mandatory, and the fast lane is off.

## The loop

Run passes until a stop condition. Each pass:

1. **Re-grade the current lane's slice, but only if something changed since the
   last grade.** A condition that was green last iteration stays green unless
   the diff moved it. Re-grading an untouched slice costs a worktree cycle for
   every pinned condition and proves nothing new. Grade on entry to the lane,
   and after any iteration that edited tracked files:
   ```
   .claude/hooks/adt-dod.sh <ticket.md> --devteam <.adt/> --lane <lane>
   ```

2. **If the slice is all green, advance the stage.** Do the lane playbook's
   work, but skip its stop. Build the next sub-step with `/adt-build`'s stack
   discipline. When build's slice is green, move `building → qa` and run
   `/adt-qa-run`'s checks. When qa's slice is green, move
   `qa → ready-to-release`. Each move is a plain `mv` in the cache, with nothing
   to commit. On the fast lane there is no middle hop: move
   `building → ready-to-release` directly.

3. **If the slice is not green, do the work to make it green**, then loop. The
   stage advances ONLY on green.

**Stop conditions:**

- **Merge-offer reached.** The PR is open and the ready-to-release slice is
  green. **STOP and offer the merge**; the human chooses who merges. The loop
  performs no merge and no `/adt-close`. This command has no path to an
  autonomous merge.
- **Stuck.** Three passes in a row met no new DoD condition, or you hit a
  decision the plan didn't settle. **STOP and ask in-session**, with a one-line
  blocker and the unmet conditions. Never run a fourth identical pass.
- **A non-negotiable gate fires.** A guarded-path reviewer returning REJECT
  counts as stuck. Report it; never clear it yourself.

**Always print the scorecard on a stop.** List every DoD condition with
passed/failed and why, so the human sees exactly what is green.

## When to stop and ask

When stuck, stop and ask in-session. The human decides whether to keep running
with a fix or to convert it to `/adt-block`. A real user decision that the plan
didn't settle goes to `/adt-block`. Never guess at user-visible behaviour to
keep the loop running: a wrong autonomous advance is the failure this command
is designed to prevent.

<!-- adt-bundle: v0.2.0 -->
