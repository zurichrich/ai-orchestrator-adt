# Is ADT a loop?

A short analysis of how ADT's Idea→Done process relates to the basic
agent loop (Discover → Plan → Execute → Verify → iterate), and which
ADT commands iterate internally vs. run once.

The per-command verdicts live in [`references.md`](references.md) as the
**Loop?** column (rendered into the kanban's 📖 link). This doc is the
*why* behind that column.

---

## The short answer

**The spine is not a loop. The stages are.**

ADT's outer spine —

```
ideas → planned → building → qa → ready-to-release → done
```

— is a **forward-only ratchet**, not a loop. There are **two** spine-level
loop edges: (1) the `qa fail → building` retry, and (2) the
**`/adt-build-todone` driver** (ADT-81), which loops build→review→qa within
one autonomous run, re-grading the ticket's machine-checkable DoD each pass
and advancing a stage only when that stage's DoD slice grades green. Its exit
is the **DoD all-green at the merge-offer** (or a stuck-stop) — never "the
agent feels done". Everything else advances or blocks; you never loop
`ideas → planned → ideas`. The build-todone edge is *bounded and opt-in*: it
runs only against an approved, all-checkable plan and stops at the merge-offer
(it never auto-merges), so it does not turn the ratchet into a free-running
loop.

But the stages that do real work each wrap a loop. So ADT isn't "a loop
or not" — it's **nested loops inside a state machine**:

| Level | Where | Loop |
|---|---|---|
| **Outer** | the spine | ratchet, *except* the `qa ⇄ building` retry edge |
| **Middle** | inside `building` | `build` recurses over the plan's sub-step list until none remain |
| **Inner** | inside one command | the discover→act→verify micro-loop running as the engine of a single stage |

---

## ADT loops vs. the bare Claude loop

A bare Discover→Plan→Execute→Verify loop is **agent-bounded**: it
terminates when the model judges itself satisfied. The exit condition
lives inside the agent's head — unauditable, and the failure mode is
stopping too early (or never).

Every ADT loop is **artifact-bounded**: it terminates against something
written down — the plan's sub-step list, a green test suite, a QA
findings list, a cited mechanism. The human can read the exit condition
and check it.

> ADT replaces *iterate until the model is satisfied* with *iterate until
> the checklist is satisfied*, at every level of the nest.

That is the whole design value — not the loop, but **where the loop's
exit condition lives**. It's the same instinct behind the behavioural
rules: working-style #5 (claim done only from the diff, not intent), #7
(a check is triggered by a change, not a resume), #8 (stop at stage
gates). They all exist to stop a loop from closing itself on
self-judgement.

---

## Which commands loop, which don't

Three kinds, matching the **Loop?** column in `references.md`:

### loop (iterates today)

- **`/adt-build`** — a **nested loop**. The outer loop recurses over the
  plan's `### Sub-steps` list, dispatching each by `stack:` tag, until none
  lack a done-marker. Its final sub-step runs the **self-review inner loop**
  (fix-and-re-run until the diff is clean and the suite is green), then opens
  the PR. (Self-review used to be its own `/adt-self-review` command; the
  flatten folded it in as the non-skippable final step — but it is still a
  loop, now nested inside build.) Exit: sub-step list exhausted + self-review
  clean + green.
  ([`commands/build.md`](../commands/build.md))
- **`qa ⇄ building`** (spine edge) — `/adt-qa-run`'s FAIL exit writes findings
  and moves the file back to `building/`; `/adt-build` fixes and re-opens the
  PR; repeat. The findings list is the loop's changing artifact. (FAIL/PASS
  used to be `/adt-qa-fail` / `/adt-qa-pass`; they're now the two exits of
  `/adt-qa-run`.)

### loop (added 2026-06-25 — see "Loops built out" below)

- **`/adt-plan`** — drafts the plan, then critiques it against the
  codebase and revises until the critique returns no gaps (max 3 rounds,
  then `/adt-block`).
- **`/adt-qa-run`** — verifies the diff *per sub-step* (code present +
  named test present + green) rather than as one gestalt read.
- **`/adt-diagnose`** — hypothesise → test the value at the line → narrow,
  until exactly one mechanism survives with a cited value (max ~3–4
  rounds, then "still diagnosing").

### pass (runs once — correctly)

Gates and intake/record commands. A gate is a **barrier**, not a worker:
it clears or kicks back, and looping a checklist adds nothing — the
iteration belongs in the stage that *produces* the artifact, not the gate
that checks it. So `/adt-release-check`, `/adt-close`, and `/adt-brief`
(intake) all stay single-pass. (The per-`stack:` sub-step builds inside
`/adt-build` are also single-pass — the loop is their caller, `build`.)

---

## Loops built out (2026-06-25)

Three commands were single-pass and became artifact-bounded loops on that date;
the table below is the full loop-tagged set, kept in step with the `· loop` tags
in `docs/references.md` (ADT-214). Each exit condition is a *changing* artifact,
and each is bounded so a refining loop never becomes a spinning one
(working-style #7/#8):

| Command | Loop added | Exit condition | Bound |
|---|---|---|---|
| `/adt-plan` | draft → critique-vs-codebase → revise | critique returns no gaps | 3 rounds → `/adt-block` the user |
| `/adt-qa-run` | per-sub-step: code + named test + green | every sub-step has a verdict | the plan's sub-step list (finite) |
| `/adt-build` | recurse the sub-steps, then self-review fix-until-green | every sub-step built and the diff reads clean | the plan's sub-step list (finite) |
| `/adt-build-todone` | build → review → qa, advancing a stage only on a green DoD slice | the DoD grades green, or it stops stuck | the merge-offer; never auto-merges |
| `/adt-diagnose` | hypothesise → test value at line → narrow | one mechanism, one cited value | ~3–4 rounds → "still diagnosing" |

**The rule for any future loop:** bound it on a *changing* artifact (a
shrinking critique list, a growing set of verified sub-steps, a narrowing
hypothesis), never on "run it again to be thorough." That's the line
between a refining loop and a spinning one.

---

*Origin: a production consumer, 2026-06-25, from a "is ADT a loop?" analysis. The
visual version of this argument was produced as a Claude artifact in the
same session.*
