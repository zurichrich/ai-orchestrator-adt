---
name: adt-build
description: Build the active ticket — build each sub-step for its stack, test, self-review, open the PR
adt-budget: 220
---

# /adt-build

The building lane. Pick up the ticket, build each sub-step, self-review the diff,
and open the PR. Which stack a sub-step is built for is decided inside this
command. The self-review is the last step and cannot be skipped.

## Pre-flight

1. **Pick the item.** If a file is already in `~/.adt/<project>/cache/building/`,
   it is the active one: resume it. Otherwise take the oldest of the
   highest-priority items in `planned/`, `mv` it to `building/`, set
   `stage: building`, and create a worktree on `dev/<slug>`. Create the worktree
   before the first edit to a tracked file, and never work in the canonical
   checkout (multi-agent-git-workflow §A.3).
2. **Bind the session to the ticket, then check the binding. If the check fails,
   stop.** Run `.claude/hooks/adt-mark-tix.sh <ID>`, then immediately
   `.claude/hooks/adt-verify-bind.sh <ID>`. The mark hook exits 0 even when it
   wrote nothing, so do not assume it worked. If verify exits non-zero, **STOP**,
   re-run both, and do not build unbound. Turns in an unbound session are billed
   to `__unassigned__`, and the ticket ends up stamped with a wrong cost.
3. **Read the spec, not just the sub-step list.** Build the **Design** as
   written, not one you prefer. Each **Impact / ripple** entry is a sub-step, not
   optional tidy-up. The **`done_evidence:`** is what this build is measured
   against. Then take the next sub-step with no `done` marker.
4. **Check any runtime assumption before building on it**: an env-var name, a
   config key, a default. Check it directly (`env | grep`, `cat <config>`, a
   one-line run). Code built on a wrong runtime assumption silently does nothing
   while looking implemented. If the check contradicts the plan, correct the plan
   in the build log.
5. **Read `track:`. It decides which gates fire.**
   - `fast` — brief → build → done, one commit. No separate plan, QA or reviewer.
   - `standard` — plan → build → qa → done.
   - `full` — guarded path; no gate may be skipped.
   - **Hard floor, which overrides `track:`.** If the diff touches the project's
     critical-path or money files, a schema migration, or a `ui_review_required`
     brief, the ticket is `full` and the path-triggered reviewers are mandatory
     (`/adt-review-critical-path`, `/adt-review-security`, `/adt-review-designer`).
     `track: fast` on such a brief contradicts this: reject it and tell the human
     to re-tier. The paths the diff touches decide the gates, not the tier the
     author chose.

## Build the sub-step — by stack

For every stack: **read every file the sub-step touches before writing**, match
the patterns already there, add no abstraction the codebase doesn't already use,
and don't refactor outside the sub-step. Follow the project's own `CLAUDE.md`.

**Run the tests this sub-step affects**: the ones the plan names, plus any test
of code that imports what changed. Do not run the whole suite here. That runs
once, at self-review, where the whole diff justifies it. If a sub-step really
can affect the whole repo (a shared helper, a config default), say so and run
everything.

Then commit on `dev/<slug>` as `<type>(<scope>): <summary>`.

- **backend** — never start the project's main server. Verify with a unit test or
  `python3 -c "from src.X import Y; ..."`. If the change touches auth, secrets or
  PII, run `/adt-review-security` before returning.
- **frontend** — `npm run build` must pass with no TS errors. Use theme tokens
  only, no hardcoded hex. Dark mode must work. Every new fetch needs loading and
  error states. Use labels and input types for accessibility, and check the
  breakpoints. Bump the version marker once per feature. For a UI surface, run
  `/adt-review-designer`.
- **database — highest risk.** Read the existing schema first. Migrations only go
  forward. A destructive change (drop column, narrow type) means `/adt-block`
  before continuing. **Back up every affected table to
  `backups/<purpose>-<timestamp>/<table>.json` and check the files are non-empty
  BEFORE applying.** Commit the migration and the backup together. Apply only
  through the project's documented path, never against prod from a machine that
  isn't approved for it. Then run `/adt-review-security` for RLS coverage.
- **fullstack** — prefer splitting it. If you don't split it: backend
  first, frontend second, then a **wire test** through the real endpoint. Both
  sides can pass their own unit tests while the contract between them doesn't
  match.

## The deviation loop — when the plan didn't anticipate something

Use this whenever you hit something the plan didn't cover. It sits between
making something up and escalating to the human.

1. **Is it really a problem?** Reproduce or observe it. If you can't show it,
   carry on with the sub-step.
2. **Is this the simplest way to solve it?** Write the one-line fix first.
   Anything larger must name what specifically breaks the one-line fix.
   **Before adding a threshold, grep for an existing claim or lock.** A tunable
   constant such as a grace window is a guess that needs maintaining. If the
   codebase already solves the problem with an explicit claim or lock, use that.
   A threshold can appear to work in testing simply because the gaps you measured
   happened to fall inside it.
3. **Does this actually change the plan?** The default is **fix it now, in this
   diff**. Always do that when it is a nit, the unglamorous remainder of the work
   in hand, inside the area the diff already touches, or something you would be
   embarrassed to leave behind. Record it in the build log.

`/adt-block` is for when this loop won't converge. Do not reach for it the moment
something surprises you.

### Say when the scope has grown, at the point you notice

Fixing the rest of the work inside the diff is the default above. That is
different from the work growing into ideas the plan never named, until one PR
holds several plans' worth of change. When you notice that, say so in one line
and offer to split the PR. This describes what the diff now contains. It is not
a request for new work.

### Filing a follow-on ticket is a LAST RESORT (ENFORCED RULE 2)

Deferring work takes it out of what this ticket is graded against, so it loosens
the contract and you cannot approve it yourself. All three of these must hold,
each with evidence:

1. **Unforeseen** — it could not reasonably have been seen at plan time. "It
   wasn't in the plan" is the normal case and does not count.
2. **Significant deviation required** — a named conflict, a different root cause
   in different files, or a decision the human owns.
   **Effort and size do not count.**
3. **Human approved** — through `/adt-block`. There is no way to file one without
   the human.

`adt-deferral-guard.sh` (PreToolUse) enforces this. It denies the call that
creates the Issue unless the transcript has a recorded authorisation from the
human.

### Editing a ticket, plan or playbook: find sections by their structure

A plain string search for a heading also matches any prose that mentions it. If
you then cut from that match to the next `##`, you silently delete everything in
between. Use a regex anchored to the start of the line, check there is exactly
one match before writing, and re-read the section boundaries afterwards.

### Amending the DoD mid-build

**You may tighten the DoD yourself. You may not loosen it.** Adding a condition,
a test, or a wider fix is fine: do it and note it. Waiving a condition, reducing
scope, deferring work, or declaring something out of scope is never
self-approved: record the waiver and its reason, and get the human's answer.

## Tests are part of the build

A sub-step isn't done until the tests the plan names for it exist and pass. They
go in the sub-step's commit, never in a later follow-up.

## After each sub-step

Append `- YYYY-MM-DD HH:MM — <step id> done. Tests: PASS/FAIL.` to the build log,
then build the next one.

**Grade the DoD at the end of the lane, not after every sub-step.** Marking every
sub-step done is only a claim. The build is done when the build-lane conditions
grade green. Between sub-steps, the sub-step's own tests are enough. A grade
creates one worktree per distinct pinned commit and runs every condition twice.

```
.claude/hooks/adt-dod.sh <ticket.md> --devteam <.adt/> --lane build
```

If a condition fails, the build isn't done. After that, EVERY tier runs the
self-review below. The self-review is where the PR is opened (step 8), so a
ticket that skips it reaches `/adt-close` with no PR to close, and the close
aborts. The lighter tiers skip the separate QA lane. They do not skip this.

## Final step — self-review the diff, then open the PR

This cannot be skipped. The build is not complete until you have read the diff
looking for what is wrong with it (working-style #5: never claim done from
intent).

1. **Read the whole of `git diff main...HEAD`.** For each file, ask: did I match
   the existing patterns? Did I add a comment that doesn't explain WHY? Handle a
   case that can't happen? Add an abstraction nothing uses? **And did the design
   change after I wrote the prose in this diff?** Code gets re-checked when the
   design changes, but prose does not. A doc sub-step written before a late
   design change is the most likely thing in the diff to be wrong now.
2. **Run the tests the diff touches, not the whole suite by habit.** This is the
   build-time run. The diff is new, so a run is owed (working-style #7). What is
   owed is the part of the suite that could see this change. Name the files you
   ran and why you chose them.
   - Changed a module: run its tests, plus the tests of modules that import it.
   - Changed a hook or a script: run that hook's or script's test.
   - Changed only prose an agent reads (a playbook, a rule, a doc): no suite. A
     grep cannot tell you whether an agent understood the prose. Run the command
     instead.
   - Changed something structural (the frontmatter schema, the installer's file
     map, a shared fixture): run the full suite, because every test could then
     see the change.

   The full suite is not a safe default. It costs the human minutes and, where a
   project runs CI, money. Run what the change can reach, and say what you
   skipped.

   QA and the release gate *cite* this run instead of repeating it. They re-run
   only if the diff changes afterwards.
3. **Check:** no debug prints, no commented-out blocks, no TODO referring to this
   PR, no unused imports.
4. **For any new check, prove it can fail.** A passing check is evidence only if
   you can name what would have made it fail. In practice:
   - Break the thing it checks, confirm **exactly that check** fails **for the
     expected reason**, then restore. **Restore from a copy, never from git.**
     Run `cp <file> "$TMPDIR/x.bak"`, break the file, then
     `cp "$TMPDIR/x.bak" <file>`. `git checkout --` and `git restore` restore the
     last COMMITTED content. On a file with uncommitted work, that destroys the
     edit you were testing, and `git status` then reads clean because the file
     matches HEAD. A copy doesn't touch git, so this can't happen.
     Then compare **three** file hashes, not a command's exit code: before the
     break, after it, and after the restore. The middle hash matters. A break
     that didn't land leaves the check passing, and that looks exactly like a
     check that works.
   - Assert on what the code produced (the file, the row), not on the call
     returning. Next to a blanket `except`, "it returned" can never fail; only
     the file it wrote can.
   - Test through the **live entry point**, and name the function you called. A
     unit test on a helper can't see that nothing calls the helper. Claiming
     "verified on the live path" when you weren't ends the question without
     answering it.
   - Stub at the boundary that proves **your** claim. A test that stubs the
     function you are testing proves the caller, not the function.
   - Cover **every** value the check claims to cover, and **both** values of each
     input to a guard. Two booleans make four cases. A fixture that leaves a
     field unset can meet the condition by accident.
   - Name where each asserted value comes from. If the answer is "this machine"
     (`$HOME`, an installed copy, a tool on `PATH`, where a remote branch points),
     build a fixture instead. The cheapest way to find these is to run the suite
     in a fresh `git clone`.
   - Keep the failure path distinguishable from the skip path. Never send a
     diagnostic's own error output to `2>/dev/null`.
   - **When the check is itself the deliverable, your own review cannot test
     it**, because you would be using the check to test the check. Plan for an
     outside reviewer.
5. If the `simplify` skill is available, run it on the diff.
6. **If anything fails, fix it and re-run.** Never open a PR with known problems.

Then:

7. `mv` the file `building/ → qa/` and set `stage: qa`. The cache comes first:
   use a plain `mv`, never `git mv` (git walks up from the cache and finds
   `$HOME`), and do not run `gh issue edit`. `adt watch` updates the label and
   the column. The stage move happens independently of the code branch (§D2).
8. **Push `dev/<slug>`** and open the PR through the REST API. The `gh pr`
   commands use the separate GraphQL rate-limit pool, which a heavy PR session
   uses up:
   ```
   gh api repos/{owner}/{repo}/pulls -f title='…' -f head='dev/<slug>' \
     -f base=main -f body='…'
   ```
   Title: `feat: <slug>`. Body: backlog path, what it does, sub-steps, test
   evidence, reviewer verdicts, `Next: /adt-qa-run`.
   **Never use an auto-close keyword** (`Closes #N`, `Fixes #N`, `Resolves #N`).
   GitHub closes the Issue at merge, seconds before `/adt-close` grades the diff,
   so the ticket would close because of a sentence in the PR body rather than
   evidence. Write `Ref #<n>`.

**Stop here.** Opening the PR is the end of the lane. Do not carry on into QA.

## When to /adt-block

A UI/UX choice the plan didn't specify; an external service the project hasn't
connected; a sub-step whose intent is unclear. Don't guess at behaviour the user
will see.
