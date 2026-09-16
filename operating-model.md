# Operating model

How the team works end to end. Read this with `README.md` and the command
reference (`docs/references.md`).

**ADT is the orchestrator.** It is what holds an AI development team to a
defined path — every work item takes the same lanes, every lane has a gate, and
the gates are graded against something the human set rather than against the
agent's account of itself. That orchestration is driven by a human typing the
`/adt-*` slash commands in Claude Code; each command is a playbook the agent
follows when invoked. What ADT is *not* is a background daemon: nothing polls
the backlog on a timer and nothing auto-spawns an agent.

## The state machine

A work item is a single markdown file under
`~/.adt/<project>/cache/<type>/<status>/<slug>.md`. `<type>` ∈
{bugs, enhancements, tasks}; the **folder is the state**. A kanban board
(`tools/build_kanban.py` via the project's shim) renders it.

```
ideas/ ──▶ planned/ ──▶ building/ ──▶ qa/ ──▶ ready-to-release/ ──▶ done/
                            ▲                          │
                            └──────── qa fail ─────────┘
                                        │
                          ┌──any──┴── /adt-block ──▶ blocked/
                          │
                          └── /adt-unblock ──▶ back to prior folder
```

## Driving each stage (you invoke these)

| Stage | Command | What it does |
|---|---|---|
| ideas | `/adt-brief` | capture an idea, or triage dropped CX/R&D input, into a structured brief |
| planned | `/adt-plan`, `/adt-plan-fasttrack`, `/adt-diagnose` | write the plan (or, for a `track: fast` ticket, the minimum approvable artifact); verify a mechanism before any fix |
| building | `/adt-build`, `/adt-build-todone` | build sub-steps (by `stack:`), test, self-review, open PR — or drive it autonomously against an approved checkable DoD |
| qa | `/adt-qa-run`, `/adt-review-security`, `/adt-review-designer` | tests + diff-vs-plan → PASS/FAIL; path-triggered reviewers |
| ready-to-release | `/adt-release-check`, `/adt-review-critical-path` | verify all gates before the merge-offer; the guarded-path reviewer |
| done | `/adt-close` | verify done-from-diff, stamp cost, move to done + retro, tear down |
| blocked | `/adt-block`, `/adt-unblock` | pause on a human question / resume |
| any | `/adt-decide` | record an ADR (the board re-renders automatically each `adt watch` pass) |

The command playbooks above carry the work, keyed by lane. Full catalogue
(by-lane, loop-tagged): `docs/references.md`.

## Cross-cutting reviews (invoked inside a stage)

These run as **isolated subagents** when the diff hits their trigger path —
they review and return a verdict; the operator fixes:

- **Security** — `/adt-review-security` (in qa; also at the `/adt-build` hard
  floor and re-checked at `/adt-release-check`).
- **Designer** — `/adt-review-designer` (when the diff has UI surface).
- **Critical-path** — `/adt-review-critical-path` (money/safety/data path).
- **Intake** — paste a CX observation or R&D finding into a `cx-*`/`rnd-*` file
  in `ideas/`; `/adt-brief` (triage mode) turns it into a real idea.

## Hand-off rules

1. The command moves the backlog file to the next `<status>/` folder and
   sets `stage:` to match. The folder is the source of truth.
2. Significant judgment calls → `decisions.md` via `/adt-decide`.
3. PRs use conventional-commits style: `feat:`/`fix:`, `qa:`, `release:`. (No
   `plan:` PR — a spec lives in the cache, so there is no diff to carry.)
4. Branches: `dev/<feature-slug>` for code, `pm/<slug>` for plans, `chore/<slug>`
   for housekeeping. Only the merger merges to `main` (see the
   multi-agent-git-workflow rule).

## Blocking protocol

When stuck on a question only the human can answer: `/adt-block` writes the
question into the brief, moves the file to `blocked/`, and emails the PO via
`lib/notify.sh`. `/adt-unblock` takes the answer, appends a `## Resolution`,
and moves the file back to its prior stage (`blocked_from:` frontmatter, or
the stage it came from).

## The behavioural layer (the "Karpathy+++" defaults)

Installed into the project's `.claude/` by `setup.sh` — these shape *how* the
agent works between the stages, and run automatically:

- **`working-style.md`** rule (always-on): diagnose-to-mechanism, simplest-first,
  label patch vs root-cause, decide-don't-enumerate, verify-done-from-diff,
  banned recovery-narration phrases.
- **Hooks** (see `defaults/README.md` for the full reference). Before a tool
  runs: `done-guard` (denies a done-move that skipped `/adt-close` or fails its
  DoD), `deferral-guard` (denies an unauthorised Issue create), `test-run-guard`
  (denies an unrequested full-suite run), `destructive-git-guard` (denies a git
  command that would destroy uncommitted work) and `worktree-guard` (denies
  edits in the canonical checkout to the paths a project lists). At the end of a
  turn: `phrase-linter` (flags banned phrases), `close-complete`, `token-log` and
  `terminal-title`. Also `usage-log` (records `/adt-*` invocations),
  `subagent-cost`, and `deploy-guard` (a project template, not wired).

## Communication channels

| Channel | Where | Purpose |
|---|---|---|
| Branches + PRs | target repo | code state + history |
| Backlog folders + kanban | target repo | work-item state machine |
| `decisions.md` | target repo | curated ADRs (`/adt-decide`) |
| `.adt-usage.log` | target repo (gitignored) | command-usage telemetry |
| Email (Resend) | external | PO notifications: blocked, merge-ready |

## What the team is not

- **Not a background daemon.** Nothing polls or auto-spawns; a human invokes
  every command. One command, `/adt-build-todone`, does drive build → qa without
  a per-gate stop — but only against a plan the human approved, only while each
  stage grades green on a machine-checkable Definition of Done, and it stops at
  the merge-offer. There is no autonomous path to a merge.
- Not a CI/CD pipeline. No agent-controlled deploys.
- Not a chatbot. Commands are playbooks, run on demand.

---

## Self-verification — what each gate actually checks

The lanes above say *when* a gate fires. What each one **checks** is designed
around one problem: every check ADT had once took the agent's own output as its
input.

- **plan → build** — `adt-dod.sh --gate` now refuses five things, not two: no
  `done_evidence`, a prose condition, **no independent DoD-coverage review** (or
  a `GAP`/`UNKNOWN` verdict), **an unattached caveat**, and **a dependency
  defect** (a cycle or a dangling `depends_on`).
- **at plan time, what the gates DID is recorded** — each counter-check verdict
  is followed by `adt_dod --record-verdict <gate>`, which appends the round, the
  verdict and the **graded-text hash** to `gate_effects:` frontmatter. `ran` and
  `caused_edit` are then derived by code rather than stamped by anyone, which is
  the difference between this and `follow_ons` (0 on all 28 tickets that stamp
  it). The two gates run serially so each one's hash pair attributes; a block
  with no record reads as `under_recorded`, a discrepancy rather than a refusal.
- **inside build** — the **deviation loop** in `commands/build.md` is the third
  path between inventing and escalating. Fix-it-now is the default; `/adt-block`
  is the loop's overflow, not its first move. Tightening the DoD is autonomous;
  loosening it never is.
- **any turn** — `adt-phrase-linter.sh` flags a claim that something passed with no
  matching command run, and `adt-deferral-guard.sh` denies an unauthorised
  `gh issue create`.
- **build → done** — unchanged: `adt-done-guard.sh` asserts the done-lane evidence.

Full model: [`docs/self-verification.md`](docs/self-verification.md).

What ADT would need to run on GitLab or Jira, under another harness or another
model, is costed in the portability audit, which lives in the private archive.
