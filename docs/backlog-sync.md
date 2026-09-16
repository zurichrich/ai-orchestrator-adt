# ADT backlog: GitHub Issues + local cache + background sync

This is the canonical description of how ADT stores and syncs a backlog. A
consuming project points here rather than re-explaining it; the project keeps
only its own specifics (which repo, project number, cache path, how the agent
is run).

## The model

```
   one shared cache (per machine, outside any git checkout)
   ~/.adt/<project>/cache/<type>/<status>/<slug>.md   + kanban.html
        ▲  read/write (instant, no API)        ▲
   Claude session(s)                       renders the local board
        │
        ▼  (background, never on the session hot path)
   adt watch  ──push──▶  GitHub Issues + standard Projects board
              ◀──pull──    (the durable source of truth)
```

- **Source of truth = GitHub Issues** in a backlog repo, one Issue per ticket.
  The board is a standard GitHub **Projects** kanban, one Status column per
  stage (`ideas → planned → building → qa → blocked → ready-to-release → done`).
  The ticket id is derived from the issue number GitHub assigns
  (`<PREFIX>-N`); see "Ticket id == issue number".
- **Local cache** = a fast working copy at `~/.adt/<project>/cache/`, **outside
  every git checkout** (not in the code repo, never committed — stronger than
  gitignored). Keeps the `<type>/<status>/<slug>.md` tree so the renderer reads
  it unchanged. Sessions read/write here instantly; it's **derived** — losing
  it costs one `--pull` resync, not data.
- **`adt watch`** = the REQUIRED background reconciler. Sessions never call `gh`
  for backlog state on the hot path; the agent does, idempotently, every N
  seconds. It pushes cache→Issues, pulls Issues→cache, and re-renders the board.

## Changing a ticket's stage — move the file, don't edit the field

**The folder is the single source of truth for a ticket's status; `stage:` and
`state:` in the frontmatter are a *derived mirror*, not a control.** To change a
ticket's status you **move the file** between `<status>/` folders (`mv
~/.adt/<project>/cache/bugs/building/foo.md
~/.adt/<project>/cache/bugs/done/foo.md`) — you do **not** hand-edit `stage:` or
`state:`. Plain `mv`, never `git mv`: the cache is outside every git checkout
(see above), so `git mv` resolves against whatever repo git finds by walking
UP from the cache — which is `$HOME` — and commits the move there.

The renderer's `sync_stage_frontmatter` (in `build_kanban.py`, run every pass)
rewrites both fields from the folder on every run: `stage:` ← the folder name,
and `state:` ← `closed` if the folder is `done/` (or `status: cancelled`) else
`open`. So a hand-edited `stage:`/`state:` that disagrees with the folder is
**silently corrected back to the folder** on the next pass — by design, because
a stale field after a legitimate `mv` is indistinguishable from a hand-edit,
and folder-wins is the safe rule for both.

Practical consequence, and the failure mode to avoid: editing `state: closed`
on a file still sitting in `ideas/` does **not** close the Issue. The next sync
derives `open` from the `ideas/` folder, overwrites your edit, and *pushes that
`open` to GitHub* — actively reopening an Issue you meant to close. The fix is
always the same: `mv` the file into `done/`, then let the sync converge.
There is no ping-pong once the file's folder and the desired state agree — the
"fight" only happens while you're editing the mirror instead of moving the file.

## Why this shape

The goal is parallel Claude sessions coordinating *without* paying GitHub
latency on every ticket op. So tickets live close to the session (local cache,
instant) and the durable/shared truth (Issues) catches up in the background.
Same-machine sessions share one cache → instant coordination; cross-machine
sessions each keep their own cache and meet at Issues (eventually consistent —
a sync interval, seconds). The code repo stays code-only: no backlog files, no
generated board HTML, no tree for parallel sessions to clobber.

## Coordination (claiming work)

Claiming a ticket = setting its `assignees`. `adt watch` mirrors it to the
Issue assignee; other sessions see it on their next pull. Same-machine: instant
(shared cache file). Two sessions editing the *same* ticket file is the only
race, mitigated by atomic writes — sessions normally claim *different* tickets.

## The tools (all in `agent-dev-team/tools/`)

- **`ticket_serializer.py`** — the lossless `.md` frontmatter ⇄ GitHub Issue
  field-map (dependency-free, no PyYAML). `to_issue` / `from_issue` /
  `parse_md` / `emit_md`. Field-map: `priority`/`track`/`stage`/`type`/gates → labels;
  `assignees`/`milestone`/`comments` → native fields; `title` falls back to the
  body H1; `state`/`stateReason` derive from `stage`.
- **`adt_sync.py`** — the reconciler. `reconcile_all(root, pull=…)`: push each
  cache ticket to its Issue (create/update labels/state/body), add it to the
  board + set its Status column, write `issue_number`/`issue_node_id` back; on
  `pull`, bring GitHub-owned fields back and reconstruct missing cache files.
  Idempotent — a converged ticket is a no-op, and (see **API-call discipline**
  below) is skipped *before* any `gh` call, so a converged board syncs for free.
  Concurrency-safe: a pass holds `.adt-sync.lock` in the cache dir, so a second
  one skips instead of double-creating Issues (see **Running the sync**).
- **`adt_watch.py`** — the loop a long-running agent runs: poll the cache for
  changes, `reconcile_all`, re-render `kanban.html`. `--once` for a single pass.
- **`adt_migrate_ordered.py`** — *legacy* (pre-ADT-9): one-shot ordered
  migration that forced `#N == <PREFIX>-N` on a virgin repo. Only for migrating
  an old backlog built under that scheme; new installs never call it. See the
  "Ticket id == issue number" section.
- **`setup.sh --init-github`** — bootstraps a repo's label taxonomy + Projects
  board + `.adt/config.yaml`. Idempotent. Creates **zero** issues.

## Conflict policy

Push and pull own **disjoint field sets**, so there's no two-writer conflict:
push is authoritative for content/stage (title, body, labels, open/closed);
pull is authoritative for GitHub-owned fields (`issue_number`, `issue_node_id`,
`assignees`, GitHub-side comments). Push runs first each pass.

## Ticket id == issue number (ADT-9)

The ticket id is **derived from the GitHub issue number**: `id := <PREFIX>-N`
where `N` is whatever number GitHub assigns. A ticket filed as issue #31 is
`<PREFIX>-31`. The number is claimed at creation (`/adt-brief` runs one
`gh issue create`, or the sync's `reconcile_one` adopts it on first push) and
written into the cache `.md`; `build_kanban.assign_missing_ids` derives the id
from it. There is **no local id counter** and **no requirement that the repo be
virgin** — so ADT drops into any existing repo, even one whose issue/PR numbers
are already well past 1.

**Legacy:** `adt_migrate_ordered.py` predates this. It forced `#N == <PREFIX>-N`
on a virgin repo by creating Issues in strict id order (closed placeholders for
gaps). It is **only** needed to migrate a pre-ADT-9 backlog that was already
built under that scheme; new installs never invoke it and don't need a virgin
repo. See ADT-9.

## Running the sync

`adt watch` is long-running but **not** a heavyweight app: no scheduler, no
domain jobs, no production credentials — only `gh` (against the backlog repo) +
local files, read-only to any product data. Run it in the **foreground** or as
an OS service (e.g. a launchd/systemd user agent), **never** spawned from
inside a Claude session. `--once` on a timer is preferred over a resident loop
so a hung pass is replaced on the next interval (the in-process `--interval` is
irrelevant when each run is a single pass, so it is not printed).

**One pass at a time, enforced (ADT-112).** The never-spawn rule above is the
primary instruction and stays that way — this is the net under it, not a reason
to relax it. Each pass takes an advisory `fcntl.flock` on `.adt-sync.lock` in
the cache dir, held for the duration of the pass. A second pass that finds the
lock held says so — `another pass is running; skipping this tick` from the
watcher, and from the CLI the same plus what did not happen — and **exits 0**.
A skipped tick is normal under a `StartInterval` timer, not a failure, and the
skip defers the work rather than dropping it: the next tick still sees the
change and syncs it. The lock costs zero API calls, and it is released by the
kernel when the process dies, so a crashed or `kill -9`'d pass cannot wedge the
next one.

Without it, two overlapping passes reading the same `issue_number: null` ticket
both take the create branch and one ticket becomes two Issues — the losing Issue
then has no cache file bound to its number, so the next pull reconstructs one
and the duplicate survives being deleted.

That is the loudest race, not the only one, which is why the lock is sized to
the whole pass rather than to the create: `.adt-sync-state.json` is rewritten
wholesale (so an overlapping pass silently discards the other's change-detection
hashes) and the board is written with a plain non-atomic write, so two
concurrent renders can leave a truncated `kanban.html`.

Three entrypoints take it: the `adt watch` tick, `adt_sync.py`'s own CLI (a
human running `--pull` races the timer by design), and the legacy
`adt_migrate_ordered.py`, which reaches the create path directly rather than
through `reconcile_all`. The migrator is the one that exits **non-zero** when it
loses: an ordered migration cannot quietly become a no-op, so it tells you to
stop the watcher instead. `--dry-run` deliberately takes no lock in either CLI —
it writes nothing, so it can neither corrupt the backlog nor justify making a
human wait.

**Caveat: `flock` is unreliable on network filesystems**, and when it is
unavailable the lock **fails open** rather than closed. A cache dir on NFS or
inside a synced folder (Dropbox, iCloud Drive) may not support `flock` at all;
so may a host without `fcntl`, or a cache dir the process cannot write to. In
every one of those cases the pass runs **unlocked** — you get today's
double-create exposure back, plus one line on stderr saying so
(`pass lock unavailable (…) — running UNLOCKED`).

That direction is deliberate and is the load-bearing half of the design. Read
the other way — treating an `flock` that the filesystem cannot perform as
"someone else holds it" — every pass would skip for ever and the board would
silently stop syncing, which is a worse failure than the race the lock prevents.
So only `BlockingIOError` (real contention) skips; every other error proceeds
unlocked and says so. ADT's cache dirs are local by construction
(`~/.adt/<project>/cache`), so this should never fire — if it does, the warning
is telling you the guarantee is off, not that anything is broken.

**The log is quiet by design (ADT-119).** Under `--once` a tick that syncs
nothing prints nothing at all — no banner, no `Wrote …` lines — so a healthy
log is empty and only real events (a sync that moved something, an error, a
newly-assigned id) accumulate. Check liveness with `launchctl list | grep
com.adt` (or `systemctl --user list-timers`), not by watching the log fill up.
The macOS log is rotated by `adt_watch._rotate_log` at **1 MiB**, keeping one
generation at `adt-watch.log.1` — a ceiling of 2 MiB per project. launchd has no
rotation of its own, which is why this lives in the watcher rather than the
plist; on Linux systemd writes to journald, which already rotates, so nothing
rotates there.

## API-call discipline (why the sync doesn't drain the rate limit)

GitHub's REST and GraphQL APIs are **separate ~5000-requests/hour pools**. A
naive reconciler that fetched every Issue on every pass would, on a few-hundred-
ticket board running every minute, exhaust a pool in minutes — and once
exhausted, every call errors, so a retry-everything loop stays exhausted
permanently. `adt_sync` avoids this by **never making a call it can prove is
unnecessary**:

- **Change-detection, over push-owned content only.** A sidecar
  `~/.adt/<project>/cache/.adt-sync-state.json` (gitignored, derived) records a
  hash of each ticket at its last successful sync, and a ticket whose hash is
  unchanged is **skipped before any `gh` call**. The hash covers the ticket
  **minus the pull-owned fields** (`_PULL_OWNED`: `issue_number`,
  `issue_node_id`, `assignees`, `updated`) — hashing the raw bytes is what
  caused the ADT-147 outage. `pull_all` writes those fields onto existing cache
  files, and `updated` moves whenever the Issue does, *including* when ADT
  itself upserted a register comment. So a pull-only write moved the hash,
  which re-armed the push, which is where the GraphQL spend lives
  (`_current_issue`, `_project_ctx`) — and each push write moved `updated_at`
  again.

  That loop is a branching process with factor `R = N x f`, where `f` is the
  chance a pulled change provokes a push write. Measured during the incident,
  `f` = 0.71, so the critical machine count is `1/f` = 1.41: **one machine
  decays, two diverge.** One watcher had run for weeks; a second machine on the
  same account exhausted the whole GraphQL pool in 39 minutes. Excluding the
  pull-owned fields sets `f` = 0 by construction, so `R` = 0 for any machine
  count. The fields stay in the file — the board's recency sort reads
  `updated` — they are only outside the hash.
- **Persisted Projects context.** The board's field and option ids
  (`project view` + `field-list`) are cached per process **and** persisted to
  the sidecar under `project_ctx`, because every launchd tick is a fresh
  `--once` process and an in-process-only cache is cold every time — two
  GraphQL calls on any tick carrying a dirty file. Unlike the board index below,
  this one is **re-verified**: persisting it would otherwise trade "always
  fresh" for "never verified", and a Status field that is deleted and recreated
  would leave a dead field id cached forever. So the entry carries a timestamp
  and is re-walked after an hour, and a failed board write drops it immediately
  so the next tick re-walks rather than failing identically.
- **Persisted board index.** The Projects board membership (`issue_number →
  {item_id, status}`) is a multi-page GraphQL walk. It's cached per process
  **and** persisted into the same sidecar, so a fresh `--once` run seeds it from
  disk instead of re-walking the board. The cold walk happens once (or if the
  sidecar is lost); `ensure_on_board` self-heals any stale entry (`item-add` is
  idempotent), so a stale index is safe — that idempotent repair is exactly what
  the context above lacks, which is why only one of the two needs a TTL.
- **Incremental pull (watermark + hourly full sweep).** The pull side asks REST
  "what changed?" instead of re-fetching every Issue: a `pull` block
  (`watermark` = max `updated_at` seen, plus `last_full_pull`), persisted in
  the same sidecar — mandatory, since every launchd tick is a fresh process —
  supplies the watermark passed as the `since` parameter to
  `GET /repos/<owner>/<repo>/issues`. REST bills per *request* from the core
  pool — a converged board costs ~1 near-empty request per tick and **zero
  GraphQL points**. A full sweep (no `since`) runs only when the watermark is
  missing or the last sweep is over an hour old; the sweep is what still
  reconstructs a locally-deleted cache file whose Issue hasn't updated —
  something an incremental pull can never see. The watermark advances only
  after a successful pass, so a rate-limited tick skips nothing on recovery.
  (PRs share the REST /issues endpoint and are dropped by their
  `pull_request` marker.)
- **Rate-limit backoff.** If a `gh` call fails specifically because the limit is
  exhausted, the pass **aborts immediately** (recording one `rate-limited`
  result) rather than attempting — and failing — every remaining ticket. Unsaved
  hashes mean those tickets are simply retried once the quota resets; the board
  self-recovers without intervention.
- **A circuit breaker in front of the pass.** `adt_watch` reads both pools
  before starting, and when a pool's *remaining* share is under its floor the
  whole pass is skipped and logged — rather than starting a pass that dies
  mid-write, leaving some tickets reconciled and the rest erroring. The floor is
  a **share** of the pool, not a fixed point count, because the limits are per
  *user*: N machines on one account draw on one budget, so each reserving a
  fraction leaves headroom instead of all of them racing to the bottom.
  (NOT `gh api rate_limit` — ADT-153: it reports `used: 0` for every resource
  whatever the real consumption, which blanked the badge AND made the breaker
  unfireable, since `(5000-0)/5000` never crosses the floor. The GraphQL pool
  comes from the `rateLimit` query, which is accurate and genuinely free; the
  core pool from `X-RateLimit-*` on a real response, at 1 core point per tick.)
- **Converged self-skip.** A converged board is all-noop, and re-asking every
  60s buys nothing. After a few consecutive all-noop ticks the effective
  interval doubles, up to a five-minute cap, and any movement snaps it back.
  Under launchd the cadence is *external* — `lib/watcher.sh` runs
  `adt_watch.py --once` on `StartInterval` — so this cannot be a sleep: the
  process exits each tick. It is a next-due stamp in the sidecar, and a skipped
  tick costs zero API calls. The skip is **silent**: a skipped tick is an idle
  tick, and an idle tick writes nothing to the launchd log (ADT-119). Only the
  breaker above speaks.
  A **local** edit overrides the timer — the fingerprint is a free mtime walk,
  so a stage move reaches the board without waiting out the cap (git-workflow
  §D2). That override is the *only* thing it does: the pass still runs on a due
  tick with nothing changed locally, which is what carries a change made on
  github.com or by another machine into this cache (ADT-149 — gating the pass
  on the override as well made that branch unreachable).

The same discipline binds the **command/hook layer** (ADT-109): the `/adt-*`
playbooks, `defaults/hooks/`, and `lib/` talk to GitHub via `gh api repos/…`
(REST, core pool) — never the `gh pr view --json` / `gh issue view --json` /
`gh pr create` / `gh pr merge` porcelain, which routes through GraphQL and
bills the constrained pool. Every read these stages need (PR mergeability +
commits + head SHA, an issue's `node_id`, an issue's comments) is a flat REST
object, and PR create/merge have flat REST endpoints (`POST /pulls`,
`PUT /pulls/{N}/merge`), so a full ticket lifecycle costs **zero GraphQL
points**. GraphQL remains only where no REST API exists — the Projects v2
calls (install bootstrap, the sync's board walk) — plus the one-shot
`gh issue create`/`list`/`edit` at brief/uninstall time, which are outside
the lifecycle burst. `tests/test_gh_api_discipline.sh` enforces the ban
repo-wide; when authoring a new command or hook, use `gh api` from the start.

Net: steady state is ~0 push calls + ~1 pull request/pass; a real edit is a
handful; a lost sidecar costs one board walk + one full pull to rebuild. Losing
the sidecar is never a correctness problem — it's derived state, like the cache
itself.

History: one consumer's board (~226 tickets) drained the GraphQL pool because an
early `adt_watch` fetched all 226 Issues every tick and a crash-loop paid the
board walk every few seconds. Change-detection + persisted index + backoff
(the cache-first migration follow-up, 2026-06-17) closed all three — on the push side. The pull
side kept a full-body GraphQL `gh issue list` every tick and repeated the same
outage across two watch agents in 2026-07; the incremental REST pull (ADT-101)
closed that. The command/hook layer then repeated it once more during a heavy
PR session (a consumer project, 2026-07-05: one build → qa → release-check →
close walked GraphQL to 0/5000 while the core pool sat at 22/5000); moving its
reads and PR create/merge onto `gh api` REST (ADT-109) closed that.

## What the consuming project provides

Only its own specifics, in `.adt/config.yaml` + its `CLAUDE.md`:
- the backlog **repo** (`owner/name`) and **project number**,
- the **cache path** (`cache_dir`, default `~/.adt/<project>/cache/`),
- the **id prefix** (default `TICKET`; e.g. `TIX`),
- how the project **runs the agent** (service label, interval).

Everything else — the model, the field-map, the tools — is ADT's and lives
here.
