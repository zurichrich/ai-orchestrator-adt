---
adt_managed: ADT-087
name: adt-plan-fasttrack
description: Give a small, low-risk ticket the minimum an approval needs — a machine-checkable DoD, both counter-checks, and the tier stamp — so /adt-build-todone can drive it on its own. Stops at the approval and never writes the approval itself.
adt-budget: 190
---

# /adt-plan-fasttrack

The planning command for the **fast tier**. It writes the minimum the approval
gate demands, and nothing else. `/adt-build-todone` grades against
`done_evidence:`, and without this command a `fast` ticket would have none. The
lowest-risk work would then be the only work that needs a human stop at every
gate.

**Running this command is the tier decision.** Nothing classifies a ticket
automatically. You run it because you have judged the ticket to be fast. The
command checks that judgement against the hard floor and records it.

Use it for a copy fix, a comment, a doc tweak, a rename with no call-site
change, a config default: anything where the next stage would catch nothing.

It writes **no Impact section** (the ripple goes into the DoD, step 3). It runs
**no critique loop**, because the two counter-checks are the review. It opens
**no plan PR**. It **never writes `plan_approved: true`**; the final step exists
to leave that to the human.

## Pre-flight

1. **Read the brief fully.** A fast ticket's success criteria are usually
   already observable ("this string appears nowhere in the repo"), and they are
   the starting point for the DoD. If you cannot turn them into a command that
   exits 0, this is not a fast ticket: run `/adt-plan`.

2. **Bind the session, then verify it — fail closed.**
   Run `.claude/hooks/adt-mark-tix.sh <ID>`,
   then immediately `.claude/hooks/adt-verify-bind.sh <ID>`.
   If verify exits non-zero, **STOP**, re-bind and re-verify before any work.

## Steps

1. **Check the hard floor, then stamp the tier.** A ticket is forced to `full`,
   never `fast`, when it carries `ui_review_required: true` or
   `security_review_required: true`, or when the change touches a guarded path
   (critical-path/money files, a schema migration). The *path* triggers the
   gate, whatever your estimate of the effort.
   - If the floor fires, **STOP**. Name the trigger and say the ticket needs
     `/adt-plan` at `track: full`. Do not stamp the tier.
   - Otherwise, write `track: fast` into the frontmatter. It is recorded once
     and not reopened at later stages.

2. **Write the minimum spec** into `## Plan (PM)`:

   ```markdown
   ## Plan (PM)

   ### Problem & goal
   <one paragraph, so the spec stands alone>

   ### Design
   <ONE paragraph: what changes, in which files, and why nothing simpler works.
   "Simplest, nothing forces more" is a complete answer — but write it. The
   plan-quality counter-check reads this section, and no Design starves it into
   an UNKNOWN, which refuses at the gate.>

   ### Definition of Done (machine-checkable)
   <Leave EMPTY in the body — generated from `done_evidence:` frontmatter at
   push time. Author the contract there; see below.>

   ### Sub-steps
   - 1a [stack: <backend|frontend|database|fullstack>] — <action>
   ```

3. **Write the DoD to cover the whole repo, not single files. It replaces the
   Impact section.** For a fast doc or copy change, the whole ripple is *other
   files that name the same term*. So one condition proves both that the change
   landed and that nothing was missed:

   ```yaml
   done_evidence:
     - must_run: 'grep -rn "<the old string>" <the trees that could carry it> ; test $? -ne 0'
       lane: build
   ```

   This runs the ripple analysis instead of describing it. A condition on a
   single file (`grep -q "<new>" <the one file you remembered>`) only proves you
   changed the file you already had in mind, and that file is never the one that
   gets missed.

   Rules the grader enforces:
   - **Prose is not a condition.** Every entry is a `must_run` that exits 0, or
     a `file:` + `must_contain_regex:`.
   - **`file:` is only for a RENDERED artifact under `.adt/`.** For a source
     file at the repo root, use `must_run: 'grep -q … <path>'`.
   - **Anchor on a string that does not exist yet**, and confirm it is absent
     first. An anchor that already matches existing content cannot fail. Check
     the RENDERED artifact too: the board inlines every ticket body, so a
     condition naming the thing you are building is satisfied by the plan that
     names it. Write the pattern so its own text cannot match it, and run both
     controls: it is red now, and the equivalent existing string matches.
   - **A caveat must attach to a condition or be waived.** Anything in Design,
     Risks or the Test plan that reads as a caveat is refused unless it ends in
     `-> DoD:<id>` (pointing at a condition, which then needs an `id:` key) or
     `-> WAIVED: <reason>`. Prefer the anchor: a waiver drops the condition,
     while an anchor keeps it graded. A fast ticket is one slice, so
     `depends_on:` rarely applies here; `id:` is the key you need.

4. **Write the DoD in the frontmatter, then dry-run it against the real file.**
   `done_evidence:` in the ticket's frontmatter is the only copy anyone writes.
   The fenced block in the body is generated from it at push time and
   overwritten on every push. There is nothing to copy across and no count to
   reconcile. Never hand-edit the body block.

   Then run every condition once. A red result caused by the thing you haven't
   built yet is expected. Exit 127, "command not found" or a path that doesn't
   resolve is an **authoring defect**: fix it now. Name interpreters the way the
   host runs them (`python3`, not `python`). **Never use `was_red_at: plan`.** It
   yields a permanent can't-verify that the loop can never clear. Pin a real
   SHA.

5. **Run BOTH counter-checks. They are the review.** Hand the ticket to each
   subagent and paste its block into the spec word for word:
   - **`adt-dod-coverage-reviewer`** → `### DoD-coverage review`. Do the
     conditions COVER the spec? On `GAP`, close the gaps and re-review. On
     `UNKNOWN`, sharpen the spec or `/adt-block`; UNKNOWN is never a pass.
   - **`adt-plan-quality-reviewer`** → `### Plan-quality review`. Was the change
     worth making, and would anything simpler do? On `FLAWED`, fix the
     *Design*, then redo whatever that change invalidates.

   **Then record each verdict, one after the other.** Get coverage to COVERED
   first, so each recorded verdict covers text only that review could have
   changed:

   ```
   python3 tools/adt_dod.py <ticket.md> --record-verdict coverage
   python3 tools/adt_dod.py <ticket.md> --record-verdict plan-quality
   ```

   These are two separate acts. The playbook writes the block, because it holds
   prose the subcommand cannot produce. The subcommand then appends a record row
   with the round, the verdict and the **graded-text hash**: a digest of every
   `###` section the reviewer read plus `done_evidence`, with whitespace
   normalised. **You never type the verdict and you never compute the hash.**
   `--record-verdict` takes only the gate name and reads the verdict from the
   block. Typing `SOUND` over a pasted `FLAWED` would falsify the metric, which
   counts only negative verdicts.

   Both reviews are cheap, read-only, and run in a separate context. They are
   the only independent check on this path, which is why the fast tier keeps
   them when it drops everything else. A `FLAWED` on something you called a
   copy fix tells you the ticket was given the wrong tier, which the hard floor
   cannot tell you.

6. **Move the ticket, then STOP at the approval.** Move it `ideas/ → planned/`
   and let the sync update the stage, with no `gh` call. Confirm the gate:

   ```
   .claude/hooks/adt-dod.sh <ticket.md> --gate      # expect: APPROVABLE
   ```

   Then print the exact edit the human must make, and stop:

   ```
   Add to the frontmatter of <ticket.md>:

       plan_approved: true

   Then run /adt-build-todone.
   ```

   **Never write it yourself.** `plan_approved` allows an unattended run all
   the way to the merge-offer. An agent that wrote it from its own reading of
   the conversation would be granting itself permission. ADT already refuses the
   same pattern where less is at stake: `adt-deferral-guard.sh` denies a
   ticket-creating call unless the authorisation traces to a `role: user` turn,
   which an agent cannot write. Print the line and let the human type it.

## When to /adt-block

- The success criteria cannot be turned into an objective check.
- The hard floor fires and the human must decide the new tier.
- The "obvious" fix turns out to contain a design choice, which makes it a
  standard ticket rather than a fast one.

<!-- adt-bundle: v0.1.0 -->
