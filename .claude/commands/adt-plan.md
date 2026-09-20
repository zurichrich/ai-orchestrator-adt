---
adt_managed: ADT-087
name: adt-plan
description: Turn a brief into a spec — design, impact/ripple analysis, and a machine-checkable Definition of Done — then derive the sub-steps from it. Move ideas→planned.
adt-budget: 330
---

# /adt-plan

The output is a **spec**: a design, an impact analysis, and a machine-checkable
Definition of Done, with sub-steps derived from those three. Write them in that
order. A sub-step list written first is a guess at a solution nobody designed,
and that is how a ticket ends up "done" but not finished.

The DoD becomes the ticket's `done_evidence:`, which every later lane grades
against. Make it checkable here, because no later lane can.

## Pre-flight

1. Take the highest-priority oldest item in `~/.adt/<project>/cache/ideas/`.
   Skip `cx-` and `rnd-` files, which need triage first. Read it fully.
2. **Bind the session, then verify it — fail closed.**
   Run `.claude/hooks/adt-mark-tix.sh <ID>`,
   then immediately `.claude/hooks/adt-verify-bind.sh <ID>`.
   The mark hook fails open, so it can miss without saying so. If verify exits
   non-zero, **STOP**, re-run both, and do not plan unbound.
3. Read the project's `CLAUDE.md`.

## Steps

1. **Understand the existing code first.** Find the files the change touches,
   the patterns already used for similar work, the tests that cover the area,
   and any design doc. You cannot design a change without knowing the current
   shape.

2. **Write the spec** into the backlog file's `## Plan (PM)` section, top to
   bottom: design, then impact, then DoD, then sub-steps.

```markdown
## Plan (PM)

### Problem & goal
<the user-facing problem, why it matters, who for. One paragraph, standing alone.
If this is hard to state, the brief isn't ready — /adt-block.>

### Design
- **Approach:** the mechanism in prose — what changes, how the pieces interact,
  the data and control flow. Enough to understand the solution before any
  sub-step.
- **Why this approach:** the alternatives and why they lost. Lead with the
  simplest thing that could work and say what forces more. "Simplest, nothing
  forces more" is a strong design — record it as one.
- **Key decisions:** any choice a reader would question, with its reason. Worth
  re-reading in six months → also `/adt-decide`.

### Impact / ripple analysis
- <surface touched> → <what must change> → <sub-step that owns it>
- ... or "none beyond the core change — verified by grepping for <term>"

### Definition of Done (machine-checkable)
<Leave this section EMPTY in the body. Author the DoD once, in the ticket's
`done_evidence:` frontmatter, in the shape below; the block that appears here is
generated from it at push time and overwritten on every push.>

```yaml
done_evidence:
  - id: build-green                 # optional — needed only as a depends_on
                                    # target or a `-> DoD:<id>` caveat anchor
    must_run: '<command that exits 0 when this slice is done>'
    was_red_at: <sha>               # proves a real red→green
    lane: build                     # plan | build | qa | done
  - id: rendered
    depends_on: build-green         # optional — unmet upstream is can't-verify
    file: <a rendered artifact under .adt/>
    must_contain_regex: '<the element that proves it>'
    lane: done
```

### Sub-steps  (DERIVED from Design + Impact — not invented)
- 1a [stack: <backend|frontend|database|fullstack>] — <action>

### Test plan
- New test: <name + what it covers> — this is HOW each `must_run` passes.
- Manual verification: <only where no hermetic test is possible. Flag it:
  "verified manually" is a recorded exception, not a passing test.>
  -> WAIVED: <why no machine form exists>
  <The `-> WAIVED:` line is REQUIRED. `adt_dod.py --gate` reads the Test plan
  for caveats and refuses one that points at nothing:
  `REFUSE  unattached caveat`. A caveat is either resolved by a condition
  (`-> DoD:<id>`) or recorded as a waiver. Try the anchor first, since a
  waiver drops the condition and an anchor keeps it graded.>

### Design-doc section
<which section of the project's design doc governs this — or "n/a">

### Risks
- <risk and mitigation>

### Threat model
(filled by the adt-security-reviewer subagent if security_review_required)

### Design notes
(filled below if ui_review_required)
```

> **`file:` is only for a RENDERED artifact under `.adt/`, never a source
> file.** The grader resolves it relative to `--devteam`. A `file:` that points
> at a repo source path fails open without any warning and is never graded.
> Check a source file with `must_run: grep -q … <path>`.

> **Give a condition an `id:` when something else has to point at it.** Two
> things can. One is another condition's `depends_on:`, for a staged DoD where
> the qa condition cannot pass until the build one has. The other is a caveat's
> `-> DoD:<id>` anchor. `adt_dod.py` accepts a caveat in Design, Risks or Test
> plan only if it has that anchor or a `-> WAIVED: <reason>`. Try the anchor
> first: a waiver drops the condition, while an anchor keeps it graded. Without
> these keys there are no dependencies and grading works as before, so add them
> only where the dependency is real. Where B `depends_on` A and A is red, B
> reports **can't-verify** rather than failed. B is not broken; it just cannot
> be called green before the condition it depends on.

> **A fix is not done until the spec says what the code now does.** If you
> correct the code and leave the plan describing the old behaviour, that is a
> defect. `tools/replay/` re-derives DoDs *from the spec text*, so an
> out-of-date Design or Impact bullet is graded as if it were true.

> **Re-planning: REMOVING a condition loosens the DoD.** Re-anchoring an
> out-of-date plan is normal. Cutting conditions from its DoD is a different
> act, even when the cuts look like trimming ceremony. Before removing any
> condition, check what it traces to: a success criterion, a sub-step, a ripple,
> or a named risk. If it traces to any of these it is **coverage**, and you
> cannot cut it on your own judgement. A condition that traces to nothing is
> ceremony. A rewritten plan also **voids its counter-checks**, meaning their
> verdicts no longer cover the current text. Re-run what the tier requires
> (coverage on `fast`/`standard`; both gates on `full`), limited to what the
> rewrite touched. The review is owed at every tier, because the one-review
> limit in step 0 counts reviews of a given text and a rewrite produces new
> text. On `full`, continue the same reviewer agent and send it the changes
> (step 0, the GAP branch).

3. **Impact / ripple analysis.** Grep the repo for the symbol, term or path you
   are changing. Every hit that needs to change is an impact, and each one
   becomes a sub-step and a DoD line. For this change, check:

   - **Docs that name what you're changing:** README, `docs/`, the project
     `CLAUDE.md`, per-area READMEs. These are the ones most often missed.
   - **Catalogues and counts:** the registry row, any "N things" line, and
     however the thing gets installed, wired or registered.
   - **Generated artifacts:** name the regeneration step, and never hand-edit
     the output. A change to a rendered source needs a `file:` condition on the
     RENDERED copy. A grep proves a string is present, but not that the
     document wasn't cut in half.
   - **Matchers keyed to the FORM of what you're changing.** If you change the
     shape of an operation (a command, a path layout, a message prefix, a
     filename pattern), every hook, guard, linter or log parser that matches
     that shape stops firing without any error. Grep the code that *matches*
     the shape as well as the code that *performs* the operation.
   - **Schema / contract:** the schema doc and every parser, validator and
     consumer that reads it.
   - **Callers / importers:** a renamed symbol or moved path affects every call
     site.
   - **Tests that assert the old behaviour:** change them in the same diff.
   - **The other half of a two-way mechanism:** push/pull, read/write,
     encode/decode, install/uninstall. The other half was written under the same
     assumptions and probably has the same flaw. Cover it or say why not.
   - **A decision worth keeping:** flag it for `/adt-decide` now, so it isn't
     lost by close.

   Every ripple you list becomes work for the build. Every one you skip becomes
   work for the human, and nobody tells them. Where a reviewer would expect
   something to change and it doesn't, write the one-line reason, so that "no
   docs change" is recorded as a decision.

4. **Refine the spec: critique and revise until the critique finds nothing.**
   Each round, critique the whole spec, starting with the design:

   - **Design soundness.** Does the Design solve the whole Problem, including
     the hard parts? Is it the simplest approach, with complexity only where a
     constraint forces it? Does each rejected alternative have a reason?
   - **DoD is machine-checkable.** Every condition is a command that exits 0, a
     regex on a rendered file, or a red→green test. **A prose condition is a
     defect.** Each line has a sub-step that makes it pass, and a lane.
   - **DoD is gradeable. Dry-run every condition now**, against the frontmatter
     as `parse_done_evidence` returns it, never a block you extracted by hand.

     ```
     python3 tools/adt_dod.py <ticket.md> --dry-run --devteam <.adt/>
     ```

     This runs every condition through the SAME `_check_run` the grader uses,
     and prints each one's exit code and lane, flagging `EXIT-127`,
     `ALREADY-GREEN` and `NO-WORK`. `NO-WORK` means the command exited 0 but
     its output says it ran nothing, such as pytest's `no tests ran`; the grader
     fails that condition, because a pipeline like `pytest … | tail` reports
     `tail`'s exit code and hides pytest's. Use the dry-run instead of running
     conditions by hand. In an agent's shell, `grep` is a function that wraps
     ugrep, while the grader gets `/bin/sh -c` -> `/usr/bin/grep`, and the two
     can disagree: `grep -qv` returns 1 under one and 0 under the other. So a
     condition can be red when you run it by hand and green under the grader,
     and nothing reports the difference. `must_run` conditions run in the git
     toplevel of the directory you invoke the grader from, so run it from the
     tree that holds the work. `--devteam` is still required, because
     `must_contain_regex` targets resolve under it. A red result caused by the
     thing you haven't built yet is expected. Exit 127, "command not found" or a
     path that doesn't resolve is an authoring defect. Name interpreters the way
     the host runs them (`python3`, not `python`).

     **An unquoted glob in a grep aborts it in zsh, and the empty result looks
     like a search that found nothing.** The agent shell is zsh, which refuses
     `grep -rn "X" --include=*.py .` with `no matches found: --include=*.py`
     and never runs the grep. The error is one line on stderr, easy to miss
     while you look for hits. Use `git grep -n "X" -- '*.py'`, which takes
     pathspecs, or quote the glob (`--include='*.py'`). Never record "no
     matches" from a grep whose output you did not see.

     Then question each condition you wrote. Each of these checks exists
     because a ticket failed without it:

     1. **Reachable?** A pin proves the condition was not always green. It does
        not prove it can go green. What state of the world makes this green,
        and does that state exist? A condition that can never be satisfied
        dry-runs exactly like a good one.
     2. **Red for the right reason?** A red dry-run doesn't say why. Run the
        POSITIVE form and read what matched. If the match isn't what this ticket
        changes, the condition can never go green.
     3. **Green for the reason you intended?** Question the green ones too.
        Dry-run an equality assertion against a CLEAN regeneration, never the
        working tree where the last run's output already sits.
     4. **Pinned on a tracked path?** A pin means nothing on a gitignored or
        generated target. The target is absent from the replay worktree, so a
        negation passes trivially and the grader refuses it as always-green.
     5. **Pin still reachable?** A rebase, amend or squash orphans a pin without
        any warning, which makes the condition un-gradeable instead of failing.
        Re-pin after any rewrite of the branch.
     6. **Can the plan satisfy its own condition?** The board inlines the
        ticket body, so a regex on `kanban.html` is graded against a page that
        contains this plan. Write the pattern so its own text can't match it,
        and run both controls: the negative one (red now) and the positive one.
     7. **Does it check the claim, or only the words?** Prose can contain every
        required token and still say the opposite. Fix the exact sentence in the
        plan and `grep -qF` for it. **If you are adding to a blocklist, stop.**
        The absence direction fails the same way and is easier to miss. A
        `! grep -q "X"` condition is satisfied by a comment that describes X,
        even when no code does X, so it reads as "X is gone" while X is only
        being discussed. Limit a negation to what RUNS, using a path list that
        excludes prose or a pattern that cannot match a comment. Then run the
        positive form and read what it actually matches.
     8. **Does the scope match the claim?** Write the claim as a sentence, then
        check that the condition's path arguments cover everything the claim
        covers. Run the positive form over a WIDER scope and read what it finds.
     9. **Naming a test with `-k` does not check that the test exists.**
        `pytest <file> -q -k <name>` with no match prints `N deselected` and
        **exits 0**, and `_NO_WORK` matches only `no tests ran` / `Ran 0 tests` /
        `0 passed, 0 failed` — so a renamed, mistyped or never-written test
        grades GREEN. Name it as a node id instead:
        `pytest "<file>::<name>" -q`, which exits 4 and prints `no tests ran`
        when the name is absent. Both forms dry-run identically before the file
        exists, so the dry-run cannot tell them apart (AO-007).
    10. **A `grep -qF` literal the build has yet to write must be kept on ONE
        line.** `grep -F` matches within a line, so a literal that the build
        wraps at 80 columns straddles a newline and the condition goes red
        against prose that says exactly what it should. The plan-time dry-run
        cannot catch this — the target prose does not exist yet, so the
        condition is red for the expected reason and looks correctly authored.
        Name each literal in the sub-step that writes it AND say it must be
        unbroken. When it happens anyway, reflow the prose; do not shorten the
        pattern, which loosens an approved condition (AO-005, then AO-007 one
        ticket later — the first time it was recorded only in a retro).

     **Never pin a condition whose only access to the repo is through a REF.** A
     worktree shares the git dir, so `origin/…`, a tag or `HEAD` resolve to the
     current commit, not to the pin, and the replay tells you nothing. **Never
     use `was_red_at: plan` either.** It returns can't-verify, which the loop can
     never clear. The first mistake gives a condition that cannot fail, the
     second one a condition that cannot pass, and both dry-run like a good one.
   - **Impact is complete.** Does any doc, caller, count, artifact, schema entry
     or test that names what you're changing still lack a sub-step?
   - **Sub-steps derive from the above.** Every sub-step names a real file
     (grep to confirm; a sub-step at a file that doesn't exist is the most
     common plan defect), traces to a mechanism or a ripple, and has a DoD line.
     A sub-step containing "and" is two sub-steps, so split it. If the change
     touches a guarded path the brief didn't flag, re-tier to `full`.
   - **Proportionality: is the spec bigger than the ticket?** Every other check
     here asks what is missing, and the coverage reviewer only ever asks for
     more. This check is the only thing in this loop that can make a spec
     smaller, and it is the author checking their own work, which is weak. Step
     0b is the independent check on size, and it fires on spec size rather than
     tier. If the spec creates more than one new file or has more than six
     sub-steps, expect step 0b to grade you on this, and cut the spec before it
     does. Does its size match `size:`/`track:`? For each mechanism, ask **what
     breaks if I delete it?** If the answer is "the next iteration fixes it for
     free", delete it. A step that adds a risk plus a manual exception for that
     risk should be deleted, not given a caveat.
   - **Leftovers.** After any revision, re-read what it touched. Does the plan
     still describe the design you replaced? The plan is an input that later
     lanes act on: `/adt-build` derives sub-steps from it and QA diffs against
     it. A spec that still describes a rejected design gets that design built.
     Fix it in place.

   - **Mechanical checks. Run these YOURSELF before spawning any reviewer.**
     The counter-checks below are the most expensive step in this lane, which is
     also why `commands/build.md` does not grade the DoD after every sub-step.
     Each gap these three checks find would otherwise cost a full review round,
     and use the reviewer's judgement on a string comparison:

     1. **The Test plan agrees with `done_evidence`.** Every file the Test plan
        names as "must still pass" appears as a condition. The most common gap
        is an existing test named as owed and then never graded.
     2. **Every sub-step's `→ Cn` marker resolves** to a condition that would
        fail if that sub-step were skipped. A marker that points at a condition
        which checks something else is as bad as no marker.
     3. **Every success criterion is named by at least one sub-step.**

     A failure here is an authoring defect, and the reviewer should never see
     it. Fix it, then dispatch the reviewer.

   Record `### Plan-critique` ("converged in N rounds; last gaps: …").
   **Round limit, by tier:** three rounds on `full`, **one** on
   `fast`/`standard`. If you are still finding gaps after the limit, the problem
   is a question for the human, not a drafting problem: `/adt-block`.

   **Set `track:` BEFORE this loop, not when you update the frontmatter in
   step 4 below.** Every gate below reads it, and a tier chosen after the gates
   have run has changed nothing. The tier reflects the ticket's risk, not its
   effort. A guarded path (money/trade files, a schema migration,
   `ui_review_required`) is `full` regardless; everything else starts at
   `standard`.

   **Cap the number of DoD conditions by tier.** `fast` ≤ 5 conditions,
   `standard` ≤ 10, `full` unlimited. The loop above has many prompts that ask
   "what is missing" and only one that asks "is this too big", so without a cap
   the DoD only grows. If you are over the cap, keep the conditions that trace
   to a user success criterion and cut the rest. This is the same mapping the
   re-planning rule above requires, used as a budget.

Then, once, after the loop converges:

0. **Counter-check the DoD for COVERAGE, using a separate reviewer, and only on
   larger specs.** The loop above is self-critique: you wrote the spec, the
   DoD, and the verdict on both. Nothing yet checks that the DoD *covers* the
   spec, and without that check the human has to cross-examine every plan.

   **Run this step when ANY of these holds (the same trigger as step 0b):**

   - the spec creates **more than one new file**, or
   - it has **more than six sub-steps**, or
   - `track: full`.

   **Otherwise skip it and say so in the Plan-critique note.** Across fifteen
   measured tickets the plan-time gates averaged 10.7 minutes of operator wait,
   and on nine of them no gate changed the spec. The trigger spends that wait
   only on specs big enough to have a coverage gap. The measurements are in
   `docs/lane-overhead-review.md`.

   A spec over the threshold always gets the review.

   Hand the ticket to the **`adt-dod-coverage-reviewer`** subagent and paste
   its block word for word under `### Plan-critique`:

   ```
   ### DoD-coverage review
   **Verdict:** COVERED | GAP | UNKNOWN
   ```

   The reviewer is independent because its context window is separate from
   YOURS. It does not need a new window every round. It reads the whole spec on
   its first review of a ticket, and every later round continues that same
   agent (see the GAP branch below).

   **Tell it what the mechanical checks already established**, so it spends its
   context on judgement instead of repeating string comparisons: "The
   mechanical checks — test-plan/`done_evidence` agreement, sub-step markers
   resolve, every criterion covered by a sub-step — have already passed. Grade
   judgement only: does the DoD cover what the spec MEANS, and would any
   condition pass while the work was done wrong?" Say this only when those
   checks really did pass. A reviewer told to skip a check that was never run
   is worse than one that repeats it.

   **Then record it:** `python3 tools/adt_dod.py <ticket.md> --record-verdict coverage`

   Recording is two acts, and this applies to both gates. The playbook writes
   the block, because it holds prose the subcommand can't produce. The
   subcommand then appends a record row with the round, the verdict and a
   graded-text hash. **You never type the verdict.** `--record-verdict` takes
   only the gate name and reads the verdict from the block. Typing `SOUND` over
   a pasted `FLAWED` would falsify the metric, which counts only negative
   verdicts. **Run the two gates one after the other**, and get coverage to
   COVERED first, so each verdict covers text only that review could have
   changed.

   - **GAP** → close the named gaps. Don't argue: either the condition exists or
     it doesn't. Then re-review.

     **On every tier, re-review by CONTINUING the same reviewer, never by
     spawning a new one, and send it only what changed.** A closed GAP that is
     never re-reviewed cannot pass the gate: `tools/adt_dod.py` refuses any
     coverage verdict that is not COVERED. A continued reviewer re-reads nothing,
     so the re-review is fast.

     **How to continue it.** `SendMessage` to the agent that returned the GAP. A
     fresh `Agent` call starts with an empty context, so it has to re-read the
     whole ticket and re-establish everything the previous round already found.
     The gate needs a context window separate from YOURS, not a new one every
     round.

     Send it three things and nothing else: which named gaps you closed and how,
     what else changed in the ticket since its last verdict, and an instruction
     to grade those changes rather than re-check what it already passed. The one
     exception is a change that contradicts a finding it made earlier. Say so,
     and have it re-read that section.

     **Round limit, by TIER: two rounds on `fast`/`standard` (the review and one
     re-review of the changes), up to three GAP rounds on `full`.** If it is
     still GAP after the last round the tier allows, take it to the human with
     the remaining gaps named; never loop. The mechanical checks in step 4
     remove the cheap findings that used to make extra rounds necessary. Still
     GAP after the third round on `full` means the spec and the DoD disagree
     about what the ticket is for. That is a question for the human, not a
     drafting problem: `/adt-block` with the remaining gaps named. Each extra
     round costs about as much as the first, while its findings get smaller.
   - **UNKNOWN** → a legitimate answer, but never a pass. Sharpen the spec or
     `/adt-block`.
   - **COVERED** → proceed.

   This is enforced: `adt_dod.py --gate` refuses a ticket with no recorded
   review, a GAP or an UNKNOWN. Recording a verdict the reviewer did not give
   falsifies it.

0b. **Counter-check the PLAN. This is triggered by spec size, and by
   `track: full`.** Step 0 asks whether the DoD covers the spec. It cannot ask
   whether the spec was worth building. A wrong Design with a DoD that
   faithfully covers it passes every check and grades green with the wrong
   thing built.

   **Run this step when ANY of these holds:**

   - the spec creates **more than one new file**, or
   - it has **more than six sub-steps**, or
   - `track: full`.

   **Otherwise skip it and say so in the Plan-critique note.**

   **Why the trigger is size and not tier.** `track:` records the ticket's
   risk, but overbuilding depends on the spec's size, and the two are
   unrelated. A tier-only trigger would switch this review off on the small,
   low-risk tickets where specs most often sprawl. This is the only step in the
   lane that asks "is this the simplest thing that solves it", so it is the
   only one that can make a spec smaller. Step 4's proportionality check is the
   author checking their own work, and step 0's coverage reviewer only ever
   asks for more. Without 0b, only the human can shrink a plan.

   The thresholds are set low on purpose. Raise them only on evidence, such as
   a run of SOUND verdicts with no defects on small specs, and not because you
   want to skip the step.

   **The review has a real cost, which is why it has a trigger at all.** A gate
   that cannot fail on a class of ticket only adds cost to that class. A size
   trigger spends the cost where a spec is big enough to be wrong in this way.

   Hand the ticket to the **`adt-plan-quality-reviewer`** subagent. It reads
   `## Problem (user)` before the Design, so the Design cannot redefine the
   problem it is judged against. Paste its block under the coverage block, then
   record it the same way: `--record-verdict plan-quality`.

   - **FLAWED** → fix the *Design*, then redo whatever that invalidates
     (impact, sub-steps, DoD) and re-review. Patching sub-steps under an
     unchanged Design is not a fix.
   - **UNKNOWN** → usually the Problem is too vague to judge a Design against.
     Sharpen it or `/adt-block`. UNKNOWN is never a pass.
   - **SOUND** → proceed.

   **Whether this gate can refuse depends on calibration.** Design quality has
   no ground truth the way coverage does, so `--gate` refuses on it only where
   `docs/plan-quality-calibration.md` records `gating: ENABLED`. Below that bar
   the review still runs and is still recorded; only the automatic refusal is
   turned off. **Run it either way.** The human needs to see a recorded FLAWED
   even when it cannot refuse the ticket.

1. **`security_review_required: true`** → recommend running the
   `adt-security-reviewer` subagent against the plan, and on the human's yes put
   its output in the threat model.

   **The security review needs the user's yes. It never runs automatically.**
   Before dispatching it, STOP and put three things in front of the human, then
   wait for an explicit yes:

   - **What it is.** A read-only subagent that runs the OWASP top-10 lens, the
     project's own incident classes, and auth / dependency / secret checks
     against this ticket. It returns one APPROVE / APPROVE-WITH-FIXES / REJECT
     verdict with `file:line` findings. It edits nothing.
   - **Why it should run HERE.** Name the specific exposure in THIS ticket that
     makes it worth the cost: the guarded path it touches, the data it moves,
     the surface it opens. "The frontmatter says `security_review_required`" is
     not a reason; that flag is only what prompted the question. If you cannot
     name a concrete exposure, say so and recommend skipping.
   - **What it costs.** It is the most expensive single step in the lane, on
     the order of 100k subagent tokens for one pass. Say so, so the human knows
     the price they are paying for the named risk.

   A `no` is a legitimate answer. Record it in the ticket with the reason; it is
   not a gap. `security_review_required: true` makes the review the RECOMMENDED
   default and requires you to ask. It does not allow you to spend the human's
   money without their yes.

2. **`ui_review_required: true`** → fill in `### Design notes`. Read the design
   system, then list the existing components to reuse, a plain-text layout
   sketch, viewport considerations, and the theme tokens that apply.

3. **Branch, but only if this plan will edit tracked files.** The spec is cache
   content, so most planning sessions need no branch.
   **Only if this plan will edit tracked files** (a doc, a tool, a schema): `git worktree add ../<repo>-wt-<slug> -b pm/<slug>`.
   Create the worktree before the first tracked-file edit, and never work in
   the canonical checkout, because a parallel session's `git checkout` can
   switch the shared tree while you are using it.

4. **Update frontmatter.** Set everything except `stage:`, which step 5 sets
   after the move.
   - **`track:`** is decided once, here, and not reopened at later stages.
     **To stamp anything other than `fast`, you must NAME in the ticket the
     guarded path that forces it. If no path is named, the tier is `fast`.**
     In practice the heavier tiers were picked out of habit far more often than
     a guarded path required, and each heavier tier costs more operator time.
     - `fast` (**the default: stamp this unless a path forces otherwise**) is a
       single-repo change that is easy to follow. Brief → build → done.
     - `standard`: name the path, then say in one line what a separate QA pass
       would catch here that the build wouldn't. "It feels bigger" is not a
       path.
     - `full`: name the guarded path: a money/trade file, a schema migration,
       `ui_review_required`, or an auth/secret surface. No gate may be skipped.
     - **Why `fast` is the default.** Measured against an unaided control, the
       full lane cost 6.4x the wall-clock time and 3.1x the money, and its gates
       caught none of the three planted defects the control missed. The
       measurements are in `docs/gate-tax-report.md`.
     - **Hard floor:** a guarded-path ticket is `full`, whatever your estimate
       of the effort. The path sets the tier, not the author.
   - **`done_evidence:`**: write it here, in the frontmatter, and nowhere else.
     The `### Definition of Done` block in the spec body is generated from this
     list at push time and overwritten on every push, so any hand-edit there is
     lost. There is only one copy, so there is nothing to copy across and no
     count to reconcile. Never hand-edit the body block. If the DoD really does
     not apply, say so explicitly rather than leaving it out.

5. **Move the file** `ideas/ → planned/`, then set `stage: planned`. Use a plain
   `mv`, never `git mv`, because the cache is outside every git checkout. Make
   no `gh issue` call: `adt watch` updates the stage label and column. Move
   first: the render treats the folder as the truth, so a stage set while the
   file is still in `ideas/` is rewritten back to `ideas`, and the next sync
   pushes that.

   If the ticket has no track set, adt-done-guard.sh denies the move into planned/.
   Set `track:` in step 4 and move it again.

6. **No plan PR.** The spec lives in the cache, outside every checkout, so there
   is no diff for a PR to carry. `adt watch` pushes the body to the Issue, and
   the spec is reviewed there; the two counter-checks are its review gate. If
   step 3 created a worktree, those edits go through a normal PR on `pm/<slug>`.

## When to /adt-block

The brief is ambiguous about user-visible behaviour; a sub-step needs a
decision about an external service; or two valid approaches differ materially
in UX.

## When to /adt-decide

You are choosing between two valid technical approaches with different
long-term implications, or choosing not to follow a pattern the codebase
usually uses.

<!-- adt-bundle: v0.2.0 -->
