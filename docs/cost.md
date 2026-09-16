# The cost model — how a ticket gets a dollar figure

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


ADT prices its own output. Every session's `Stop` hook appends to a ledger, a
versioned price table turns those rows into money, and `/adt-close` stamps the
result into the ticket and pushes it to the Issue.

The rule the whole model is built around: **a number always says how it was
reached.** An estimate and a measurement are never silently mixed, and "we
didn't capture it" is never rendered as `$0.00`.

---

## The ledger

`.adt/state/cost-ledger.log` — TSV, append-only, machine-local
and gitignored. One row per `(turn, model, speed)` group, 11 columns:

```
ISO8601  tix-id  input  output  session_id  model  speed  cache_read  cw_5m  cw_1h  tier
```

The first five columns are the original format and keep their positions; the
rest are appended, so a short row still parses on a new reader and a new row
still parses on an old one. A per-session cursor
(`.adt/state/token-cursor/<session_id>`) holds a single integer
so each `Stop` writes the delta since the last one, never a re-count.

**Attribution.** The hook resolves which ticket a session is working on through
an ordered chain of signals. When nothing resolves, the row is keyed
`__unassigned__` rather than dropped or guessed — that spend is real and still
reaches the board total, shown separately.

## What is actually billed

The Messages API bills input in **three disjoint classes**, and `input_tokens` is
only the uncached remainder after the last cache breakpoint:

```
total_input = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
```

An input+output pair alone is roughly a tenth of the real bill on a
cache-heavy agent session, which is why the ledger captures all five classes and
groups them by **model and speed**. Fast mode is not a surcharge on output — it
rebases the base rate itself, and the cache multipliers stack on top of the
rebased rate.

## The price table

`defaults/pricing.json`, USD per million tokens, keyed by model and speed.

Cache rates are **derived, not stored**. The table holds two numbers per model —
base input and output — and the multipliers sit alongside:

| Class | Multiplier on base input |
|---|---|
| cache read | 0.1x |
| cache write, 5-minute TTL | 1.25x |
| cache write, 1-hour TTL | 2x |

Storing five numbers per model would be five chances to get one wrong.

The table carries a `version`, bumped on any rate change, and a stamped cost
records which version priced it — so a rate correction is auditable rather than a
silent rewrite of history.

## One pricer

`tools/adt_cost.py` is the only place a ledger row becomes money. It has no
import of `build_kanban`; `build_kanban` imports it, one direction, no cycle. The
hooks (`adt-token-sum --cost`, `adt-token-total --cost`) reach it through the CLI
at the bottom of the module, resolved by the same relative-path walk `adt-dod.sh`
uses. The sync reaches it the same way.

## The three tiers

| Tier | What it means |
|---|---|
| `measured` | An 11-column row with real per-class counts. Priced exactly. |
| `estimated` | No transcript survives for the row, so per-class counts can't be recovered. Priced from the derived estimator ratio. |
| `legacy` | A five-column row with no model on it. Priced from the same ratio. |

`legacy` is a standing tier, not a migration state: an install still running an
older hook copy keeps appending short rows, and nothing forces the upgrade.
Replacing the hook file takes effect on the very next `Stop` — it is the
per-project copy under `.claude/hooks` that goes stale, not the session.

Whenever a number is not fully `measured`, `fmt_cost` prefixes it with `~`, so an
estimate can never be read as exact at a glance.

`None` is **not** a tier. A ticket with no ledger rows at all prices to `None` and
renders `$ —` — a bare dash would sit next to the token badge's own dash and
leave the reader unable to tell which is which. "We did not capture it" and "it
was free" are different facts, and only one of them is ever true.

**The estimator** lives in `.adt/state/cost-estimator.json`
(`usd_per_mtok`, `p10`, `p90`, `derivation_id`), written by the backfill from the
rows it could measure. It carries a `derivation_id` for the same reason the price
table carries a `version`: a stamped estimate has to say which ratio produced it,
or two machines can stamp different numbers for one ticket with no way to tell
them apart.

## Where the numbers surface

### The board

`kanban.html` shows a per-card cost and a running total. The total sums exactly
the cards currently visible, so it moves with the filters and is always honest
about which cards it counted.

Spend that no card claims — `__unassigned__`, plus ledger ids whose cache file
has since gone — is deliberately NOT folded into that total, because folding in
a figure no filter can affect would break it. The board does not display that
spend either; `_offboard_spend()` still computes it, and
`test_board_total_plus_offboard_reconciles_to_the_ledger` asserts that the board
total plus what it cannot see reconciles to the ledger. That test is how
unclaimed spend stays measurable.

### The Issue

Two different comment kinds, and the distinction matters:

| | Register | Stamp |
|---|---|---|
| Written by | each machine, for its own subtotal | `/adt-close`, once |
| Cardinality | one comment per (machine, ticket) | one comment per ticket |
| Moves? | yes, while work continues | no |
| Authority | live progress | **the number of record** |

The ledger never leaves the machine that did the work, so a second machine
picking the Issue up would otherwise start from zero. Each machine PATCHes only
its own register comment by stored id, so no write races another's — the
not-last-writer-wins property, structurally rather than by convention.

A cursor at `.adt/state/token-checkpoint/<TIX>` holds
`<checkpointed_sum>\t<comment_id>` and advances only *after* a successful upsert,
so a failed call retries the same delta next pass: exact, never double-counted.

**API frugality.** The 5,000/hr pool is shared, and watchers have exhausted it
before. A checkpoint fires only on a stage-label push, at done, or when drift
crosses `CHECKPOINT_DRIFT` (25,000 tokens). The trigger test is purely local —
ledger against cursor — so a no-drift tick costs zero API calls.

### The ticket

`cost_usd:` in frontmatter, stamped once at close by `/adt-close` via
`adt-token-total --cost`, which combines the local ledger with the per-machine
registers on the Issue. It degrades to the local sum when `gh` is unavailable.

## Giving history a cost

`tools/adt_backfill_cost.py` labels every historical row.

Where a session transcript still exists, the true per-class counts are
recoverable exactly: the ledger's fifth column is the session id, and Claude Code
names transcripts `<session_id>.jsonl`. Where it has been pruned they cannot be,
and pruning is the common case.

A row stores the *delta* since the last `Stop`, not a cursor range, so the
backfill replays the session's assistant messages in order and matches cumulative
`(input, output)` against each row's recorded pair. **An ambiguous match is left
`estimated` rather than guessed** — a wrong range would mis-price invisibly, and
a visible approximation beats an invisible error.

It backs the ledger up before any write, rewrites atomically, never downgrades a
row that is already `measured`, and converges: running it twice produces
identical output. Dry-run by default; `--apply` to write.

## Related

- [`docs/references.md`](references.md) — the hook and tool catalogue
- [`docs/backlog-sync.md`](backlog-sync.md) — the register comment's transport
