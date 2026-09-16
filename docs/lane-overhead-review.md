# Where the time goes in an ADT ticket (ADT-339)

The complaint this answers: driving a ticket through ADT takes too long, and the
graders take the same time whatever they are grading.

Everything below is read from `docs/lane-overhead-runs/capture.json`, frozen on
2026-09-10. That file carries the command that produced each of its four groups
and the date it ran. Three sources, none of them new:

- **Turn-gap timing.** `.adt/state/cost-ledger.log` is one row per turn — ISO
  timestamp, ticket id, and the bound command in column 12
  (`defaults/hooks/adt-token-log.sh:32-36`). The gap between consecutive
  main-session rows for one ticket is what the operator waited. Gaps over 15
  minutes are dropped as the operator being away; the capture keeps both figures.
- **Gate wait.** The same ledger tags reviewer turns `subagent:<name>`
  (`defaults/hooks/adt-subagent-cost.sh:197`), so a main-session gap that
  contains subagent rows for the same ticket is time spent waiting on a gate.
  That, not the dispatch span, is the measure — a span stops at the reviewer's
  last row and misses the tail before the main session's next turn.
- **Measured arm wall-clock.** The gate-tax run data, in the private archive (ADT-205).
  That report's USD is modelled; its wall-clock is exact.

Whether a gate changed anything comes from `gate_effects:` frontmatter
(`tools/adt_dod.py:1213`, `:1246`): 15 tickets, 55 recorded rounds.

**Walkthrough tickets: ADT-299, ADT-306, ADT-284.**

**Active minutes recorded: 906 over 19 tickets; the longest single step was 15 minutes.**

---

## Walkthrough — small (ADT-299, size XS, track standard)

A brief filed, planned and built with no counter-check ever run on it — it
carries no `gate_effects` at all.

| step | playbook | wait |
|---|---|---|
| file the brief, claim the Issue | `commands/brief.md:36` | — |
| bind and verify, fail closed | `commands/brief.md:112` | — |
| read the code, write the spec | `commands/plan.md:30` | — |
| critique loop, one round at this tier | `commands/plan.md:164` | — |
| stamp the tier and the DoD | `commands/plan.md:469` | — |
| build by stack, then self-review | `commands/build.md:45` | — |
| close: verify from the diff | `commands/close.md:37` | — |

Measured: **43.9 min** of active wait across 5 turns, all of it attributed to
`adt-brief` by the ledger's command column. The longest single wait was **13.4 min**. One `general-purpose` subagent dispatch accounted for **7.3 min** of it.

The lesson is not that this ticket was slow — it is that an XS ticket on
`track: standard` still spent three quarters of an hour of wall-clock, and no
gate ran to justify the tier it was stamped with.

## Walkthrough — medium (ADT-306, size M, track standard)

| step | playbook | wait |
|---|---|---|
| read the code first | `commands/plan.md:30` | — |
| impact / ripple grep | `commands/plan.md:133` | — |
| critique loop | `commands/plan.md:164` | — |
| coverage counter-check | `commands/plan.md:298` | 2.0 min |
| plan-quality, by spec size | `commands/plan.md:376` | not run |
| grade the DoD before the read | `commands/qa-run.md:104` | — |
| release gates | `commands/release-check.md:23` | — |

Measured: **76.5 min** capped (154.8 min raw) across 18 turns in `adt-plan`.
Longest single wait **12.8 min**. Gate dispatches: `adt-dod-coverage-reviewer`
**2.0 min**, `general-purpose` **30.4 min**.

One coverage round was recorded. It changed nothing (`caused_edit: false`).

## Walkthrough — large (ADT-284, size L, track full)

The heaviest ticket in the capture, and the clearest case of the plan lane
carrying the whole cost.

| step | playbook | wait |
|---|---|---|
| read the code first | `commands/plan.md:30` | — |
| impact / ripple grep | `commands/plan.md:133` | — |
| critique loop, three rounds at `full` | `commands/plan.md:164` | — |
| coverage counter-check | `commands/plan.md:298` | 15.0 min |
| plan-quality counter-check | `commands/plan.md:376` | 4.9 min |
| designer review | `commands/review-designer.md:1` | 3.8 min |
| build sub-steps, then self-review | `commands/build.md:164` | — |
| close, retro, tear down | `commands/close.md:164` | — |

Measured: **168.5 min** capped (247.8 min raw) across 46 turns, every minute of
it in `adt-plan`. Longest single wait **12.7 min**. Gate dispatches totalled
**23.7 min**. Seven gate rounds were recorded, and here they did earn it —
`caused_edit: true` on two of them.

---

## Where the time goes

**Active minutes recorded: 906 over 19 tickets; the longest single step was 15 minutes.**

Split by what the operator was waiting for:

| waiting on | minutes | share | gaps | median | max |
|---|---|---|---|---|---|
| a subagent | 236 | 26% | 57 | 3.5 min | 12.2 min |
| everything else | 670 | 74% | 172 | 2.8 min | — |

By the command bound at the time — read with the caveat in Gotchas below:

| command | active min | median longest-wait per ticket |
|---|---|---|
| `adt-brief` | 509 | 8.7 min |
| `adt-plan` | 343 | 12.2 min |
| `adt-build` | 31 | 7.9 min |
| `adt-telemetry` | 12 | 5.8 min |
| `adt-close` | 11 | 6.2 min |

Gate wait by reviewer, with the `adt-` rename reconciled (see Gotchas 6):

| reviewer | min | gaps | mean |
|---|---|---|---|
| `adt-dod-coverage-reviewer` | 113.0 | 32 | 3.5 min |
| `adt-plan-quality-reviewer` | 46.8 | 13 | 3.6 min |
| `general-purpose` | 35.6 | 4 | 8.9 min |
| `adt-critical-path-reviewer` | 20.8 | 4 | 5.2 min |
| `adt-security-reviewer` | 12.6 | 3 | 4.2 min |
| `adt-design-reviewer` | 7.3 | 1 | 7.3 min |

**What that wait bought.** Fifteen tickets carry recorded gate rounds, 55 rounds
in total. On **six** of them a gate changed the spec. On **nine** — ADT-247,
ADT-260, ADT-263, ADT-273, ADT-277, ADT-301, ADT-306, ADT-336 and ADT-339 — no
gate ever changed anything. Those nine each waited: 5.2 min, 3.5 min, 3.7 min,
3.5 min, 4.2 min, 3.5 min, 2.0 min, 3.5 min and 3.5 min on their gate dispatches.

The plan-time gates account for **159.8 min** of the 236, a mean of **10.7 min**
per ticket that ran them.

**Against the measured arms.** ADT-205 ran three arms over three matched tickets
(the gate-tax run data, in the private archive): unaided **2.3 min** mean per ticket, fast
lane **9.5 min**, full lane **14.5 min**. Stop points — the number of times the
operator is asked something — were 2-4 unaided, 2-3 on fast, 5-9 on full.

## Gotchas

Found by reading the playbooks against the data.

1. **The command column records the binding, not the lane.** `adt-brief` holds
   509 of 906 minutes because `.claude/hooks/adt-mark-tix.sh` stamps the command
   that picked the ticket up and nothing re-stamps it on a lane move. ADT-301
   shows 134 minutes under `adt-brief` for work done in plan. Every per-command
   figure above inherits this; the gate/non-gate split does not, because it is
   derived from subagent rows rather than the command column.

2. **The Test plan template prescribes wording the gate refuses.**
   `commands/plan.md`'s template says to flag manual verification as
   `"verified manually" is a recorded carve-out, not a passing test`. Writing
   exactly that and running `adt_dod.py --gate` returns
   `REFUSE  unattached caveat`. It passes only with a `-> WAIVED: <reason>`
   anchor, which the Test plan section never mentions — the anchor is explained
   200 lines earlier, in the DoD authoring block.

3. **A `fast` ticket cannot legally clear a GAP.** `commands/plan.md:298` tells a
   `fast` or `standard` ticket to close the named gaps and **not** re-review.
   `tools/adt_dod.py:1018` then refuses any coverage verdict that is not COVERED.
   Reproduced on this ticket: `REFUSE  DoD-coverage review verdict is 'GAP'`.
   Following the playbook on the two cheapest tiers produces a ticket the gate
   will not pass, and neither file mentions the other.

4. **The plan playbook numbers its steps 1, 2, 3, 4, 0, 0b, 1, 2, 3, 4, 5, 6.**
   Steps 0 and 0b sit at `commands/plan.md:298` and `:376`, after step 4 at
   `:164`. At 509 lines it is twice the next-longest playbook
   (`commands/build.md`, 256).

5. **`track: fast` is the documented default and is almost never used.** Across
   76 closed tickets: 3 `fast`, 49 `standard`, 24 `full`. At most 17 carry a
   guarded-path flag (9 `ui_review_required`, 8 `security_review_required`,
   before overlap), so most of the 73 non-`fast` tickets had no path forcing the
   tier they were given.

6. **Two reviewers were renamed and the ledger holds both names.**
   `dod-coverage-reviewer` became `adt-dod-coverage-reviewer`, and the same for
   plan-quality. Nothing in the repo reconciles the two strings — not
   `tools/adt_lane_cost.py`, not `tools/adt_dod.py` — so any per-reviewer figure
   taken as a naive group-by splits one reviewer into two and halves its mean.
   This review's first draft did exactly that and reported six reviewers as
   eight. The capture now normalises to the current name; the totals were
   unaffected, the per-reviewer means were not.

## Ranked cuts

Ordered by minutes saved per ticket. Each figure states the arithmetic behind it.

| # | change | file | saves | what stops being caught |
|---|---|---|---|---|
| 1 | Gate the coverage counter-check on spec size, the trigger 0b already carries | commands/plan.md | 6.4 min | Coverage review on small specs. Nine of fifteen measured tickets got nothing from it; six did. |
| 2 | Make the tier stamp fail closed — `fast` unless a named guarded path forces otherwise | commands/plan.md | 5.0 min | Nothing. `full` still fires on any guarded path; this only stops it being stamped by habit. |
| 3 | Break the GAP deadlock on `fast`/`standard` | tools/adt_dod.py | 3.5 min | Nothing. The gaps are still closed; only the forced re-read of a ticket the reviewer just read goes. |
| 4 | Put the `-> WAIVED:` anchor in the Test plan template | commands/plan.md | 3.0 min | Nothing. |
| 5 | Stamp the lane, not the binding, on each ledger row | defaults/hooks/adt-token-log.sh | 0 min | Nothing — it saves no time directly. It is what makes 1-4 measurable afterwards. |

**All five were applied**, in the PR that follows this document. This section is
kept as the reasoning behind them, not as a standing proposal.

**Total: 17.9 min per ticket**, against a measured mean of 47.7 min (906 min over
19 tickets) — about 38% of the wait.

The arithmetic, cut by cut:

1. **6.4 min.** Plan-time gate dispatches total 159.8 min over the 15 tickets
   that ran them, a mean of 10.7 min. Nine of those 15 saw no gate change
   anything: `9/15 x 10.7 = 6.4`. **Action:** at `commands/plan.md:298`, copy the
   trigger from `:376` — run gate 0 when the spec creates more than one new file,
   or carries more than six sub-steps, or `track: full`; otherwise skip it and
   say so in `### Plan-critique`. Do not remove the gate: it returned GAP twice on
   this ticket and both verdicts named real defects.
2. **5.0 min.** ADT-205's measured arms: full lane 14.5 min per ticket, fast lane
   9.5. `14.5 - 9.5 = 5.0`. **Action:** at `commands/plan.md:469`, require the
   tier stamp to name the guarded path that forces anything above `fast`, and
   record that path in the ticket. A tier with no named path is `fast`.
3. **3.5 min.** The mean coverage gate wait, `113.0 / 32`. **Action:** at
   `tools/adt_dod.py:1018`, accept a GAP on `fast`/`standard` when every named
   gap carries a recorded close — or drop the no-re-review branch at
   `commands/plan.md:298`. One or the other, not both.
4. **3.0 min.** One plan-lane turn at the measured median gap of 3.0 min — the
   cost of hitting the refusal, finding the anchor and editing. **Action:** in
   `commands/plan.md`'s Test plan template, write the anchor into the template
   line itself.
5. **0 min.** **Action:** in `defaults/hooks/adt-token-log.sh`, write the
   ticket's current stage alongside the bound command, or have
   `.claude/hooks/adt-mark-tix.sh` re-stamp on a lane move. Without it, cuts 1-4
   cannot be shown to have worked.

## The small-ticket path

/adt-brief then /adt-plan-fasttrack then /adt-build-todone then /adt-close
