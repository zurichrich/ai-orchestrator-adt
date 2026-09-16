# ADT delivery metrics — baseline

<!-- cost-basis: main-session ledger rows only; subagent spend is NOT included for
     work closed before 2026-09-06. Produced by
     `python3 tools/adt_cost_coverage.py .adt/state/cost-ledger.log ~/.adt/agent-dev-team/cache`
     on 2026-09-08. Coverage at that date: measured=10, estimated=31, legacy=30. -->

> **What these figures do and do not count (ADT-247, 2026-09-08).** Every `$`
> here is summed from the cost ledger, which until 2026-09-06 recorded only the
> main session. ADT-224 added a `SubagentStop` hook that records each dispatched
> subagent, but it records **forward only** — no backfill ever reached earlier
> work. So for any ticket closed before that date the figure is main-session
> spend, and the true total is higher by whatever its reviewers and search agents
> cost. ADT-224 measured that gap at 76% on one ticket.
>
> Ticket-level tiers carry this honestly: `measured` means subagent rows are
> present or the hook was live and none ran; `estimated` means the spend was
> never recorded; `legacy` means the work predates the ledger. At 2026-09-08 that
> is 10 measured, 31 estimated, 30 legacy. The per-ticket breakdown is
> reproducible with the command above.
>
> These numbers were **not** recomputed from surviving subagent transcripts. 26
> of the 31 estimated tickets still have transcripts and could be, which would
> need a tool that does not exist — `tools/adt_backfill_cost.py` enriches
> existing ledger rows and does not read `subagents/` at all. Recorded as a
> known limitation rather than presented as a complete accounting.


Captured **before** ADT-114's enforced rules took effect, from this repo's
real backlog — not a fixture. A criterion that says "measured, with a
baseline" is not satisfied by a test proving the counting code works.

Regenerate: `python3 tools/adt_metrics.py --cache-dir ~/.adt/<project>/cache --repo-root .`
Add `--by-track` for the ADT-205 split below.

baseline: 2026-08-21
```
ADT delivery metrics
============================================================
closed tickets:            25
  with follow_ons stamped: 0
follow-on rate:            no stamped tickets yet — no baseline
post-merge defects:        1  <- THE COUNTER-METRIC
    bugs filed after a ticket closed that name it. Paired with the
    follow-on rate so neither can be improved by shifting work into the
    other. A floor, not a census: a defect filed without the reference is
    not counted.
spawns blocked (guard):    no log on this machine

NOT measured, deliberately:
  interrupt rate — 0 BLOCKED markers across 25 closed tickets.
    /adt-block leaves an artifact; a conversational 'what should I do?'
    does not, and that is how interrupts actually happen. A blocked spawn
    is NOT an interrupt and is not reported as one.
  cross-examination rate — NOW MEASURED (ADT-126). See below.
```

## Reading it

- **follow-on rate — no baseline yet.** `follow_ons:` is stamped at close and
  no ticket has closed since the field existed. The first post-change closes
  establish it. Stated rather than reported as 0, because 25 unstamped tickets
  counted as zero would flatter exactly the number this measures.
- **post-merge defects — the counter-metric, and it is non-zero.** The pair is
  the measurement: driving follow-ons to zero by shipping half-finished work
  moves the remainder here. Neither number means anything alone.
- **interrupt rate — not measured, and not faked.** Zero `BLOCKED` markers
  across all closed tickets: `/adt-block` leaves an artifact and a
  conversational "what should I do?" does not, which is how interrupts
  actually happen. That confirms mechanism 4's thesis and destroys its metric
  at the same time.

## Cross-examination rate — measured, and forward-only (ADT-126)

`tools/adt_xexam.py` counts challenge-shaped human turns per ticket, joining
`.adt/state/cost-ledger.log` (which already maps session id ->
ticket for the whole history) to the transcripts under `~/.claude/projects/`.
Full record: the cross-examination baseline, in the private archive.

**The join key is free; the transcripts are not.** Measured 2026-08-22:

```
sessions with a retained transcript: 7/40
tickets measurable:                  5/33
```

Every one of the 7 retained transcripts is from 2026-08-20 or later. **There is
not one pre-ADT-114 transcript left on this machine.** So a pre-change baseline
is not partially recoverable — it is gone, and the metric can only accrue
forward. The plan said this should be checked on a second machine before being
accepted as forward-only; that check is below.

**Coverage travels with every count.** A ticket whose transcripts have expired
reads `—`, never `0`. Without that distinction the metric would improve every
time a file aged out — the most flattering possible failure mode for a trust
metric, and the same shape as ADT-114's interrupt-rate attempt, where 0 `BLOCKED`
markers meant "no artifact", not "no interrupts".

**The detector is a stated proxy.** A regex matches challenge-shaped phrasing; it
cannot know intent. Read it as a trend, never as a count of times the agent was
wrong.

### The r-mac-mini check (sub-step 3e) — attempted, outcome recorded

The plan owed an attempt to recover older transcripts from the second machine
before accepting forward-only. Attempted 2026-08-22:

```
$ ssh -o BatchMode=yes -o ConnectTimeout=6 r-mac-mini 'ls ~/.claude/projects/*/*.jsonl | wc -l'
ssh: Could not resolve hostname r-mac-mini: nodename nor servname provided, or not known
```

The host does not resolve from this machine and has no `~/.ssh/config` entry, so
the retroactive pull could not be performed here. **This is recorded as the
outcome, not as a pending task**: forward-only stands on this machine's evidence.
If r-mac-mini is later reachable, re-running `tools/adt_xexam.py` with its
projects dir added would extend coverage retroactively — the ledger join already
covers every ticket, so only the transcripts are missing.

## By track (ADT-205)

The counter-metric, attributed to a lifecycle tier. Before this split the
post-merge defect count existed but belonged to nothing, so it could not answer
the one question a `track:` recommendation rests on — *do the extra gates
reduce post-merge defects?*

Regenerate: `python3 tools/adt_metrics.py --cache-dir ~/.adt/<project>/cache --repo-root . --by-track`

captured: 2026-09-05T14:45Z — frozen as the gate-tax retro census in the private archive,
which `docs/gate-tax-report.md` cites for the same numbers. The backlog moves, so
two captures on the same day disagree; freezing one is what keeps the two
documents consistent (they differed by a ticket before this was done).
```
by track (ADT-205) — the benefit side, attributed to a lane:
  track             closed   defects  follow-ons
  track: fast            2         0        0.00
  track: full           14         4        0.00
  track: standard       38        12        0.00
  track: unset           2         0           —
    n is the `closed` column. A tier with one closed ticket supports
    no claim; the split is reported so the thinness is visible rather
    than averaged away.
```

**Read the `n` before the rate.** `fast` has two closed tickets and supports no
claim at all. On this snapshot `full` (14 closed, 4 defects) and `standard`
(38 closed, 12 defects) sit at 0.29 and 0.32 defects per delivered ticket — a
gap far too small, at this n, to say the heavier tier bought anything, and it
points the wrong way for the story the tier is sold on. That is a finding about
the *evidence*, not yet about the gates: the two tiers are not assigned at
random, and `full` is triggered by guarded paths, i.e. by the work most likely
to produce a defect in the first place. Confounded, and stated as confounded.
