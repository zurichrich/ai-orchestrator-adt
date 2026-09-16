# ADT references

Here's what ai-orchestrator-adt (ADT) ships with, and how each piece works:

**5 loops**, **14 commands**, **6 subagents**, **1 skill**, **18 hooks** (11 wired · 1 template · 6 helpers)

The kanban lane headers list only what a human can **invoke** in that lane — the
slash commands and `/adt-diagnose`. Hooks are not listed there: they fire
regardless, so naming them where an operator reads "what do I do here" was a
category error. A **loop** tag marks a command/skill that iterates
internally until an artifact-bounded exit (a checklist, a list, a green suite) —
never just "until the agent feels done".

The command playbooks below *are* ADT's operating instructions, presented
**by lane** (the kanban columns). Each command links to its source.

- **Commands** (`/adt-*`) — playbooks you follow *inline*; copied per-project to
  `<project>/.claude/commands/adt-<name>.md` at install (the filename is the
  command name). Per-project, — a repo that didn't install ADT sees
  no `/adt-*`.
- **Skills** (`/adt-*`) — also inline, packaged as `.claude/skills/adt-<name>/`.
- **Subagents** — review/investigate in an *isolated* context, return a verdict;
  via `Agent({subagent_type})` or an `/adt-review-*` command.
- **Hooks** — fire *automatically* on events; nobody invokes them.

A command tagged **· loop** in its Type cell iterates internally (loop analysis:
[`loops.md`](../../agent-dev-team/docs/loops.md)); the rest run once.

---

## By lane (the kanban columns)

### ideas — capture & rank
| Item | Type | What it does | Source |
|---|---|---|---|
| `/adt-brief` | command | Capture an idea — fresh, or triage a dropped `cx-*`/`rnd-*` file — into a structured `ideas/` brief. | [brief](../../agent-dev-team/commands/brief.md) |

### planned — design
| Item | Type | What it does | Source |
|---|---|---|---|
| `/adt-plan` | command · loop | Read the code, write the full `## Plan (PM)`, then move the brief ideas→planned in the cache. No plan PR — the spec is reviewed as the Issue body. Loops: draft → critique-vs-codebase → revise until no gaps (max 3 rounds → block). | [plan](../../agent-dev-team/commands/plan.md) |
| `/adt-plan-fasttrack` | command | The `track: fast` tier's planning command: hard-floor check, stamp `track: fast`, a one-paragraph Design and a **repo-wide** machine-checkable DoD (the DoD *is* the ripple analysis), then both counter-checks and the ideas→planned move. Prints the `plan_approved: true` edit and stops — it never writes the approval. Gives a small ticket the artifacts `/adt-build-todone`'s gate demands, so the least-risky work stops being the only work driven by hand. | [plan-fasttrack](../../agent-dev-team/commands/plan-fasttrack.md) |
| `/adt-diagnose` | skill · loop | Verify the mechanism — exact line + value at each step — before any fix. Loops: hypothesise → check value → narrow until one mechanism survives. | [adt-diagnose](../../agent-dev-team/defaults/skills/adt-diagnose/SKILL.md) |

### building — implement
| Item | Type | What it does | Source |
|---|---|---|---|
| `/adt-build` | command · loop | The whole building lane: pick item, build each sub-step by `stack:` (backend/frontend/database/fullstack, internal routing), write its tests, **self-review the diff (non-skippable final step)**, then open the PR. Loops (nested): recurses over the plan's sub-steps, then a self-review fix-until-green inner loop. | [build](../../agent-dev-team/commands/build.md) |
| `/adt-build-todone` | command · loop | Approve a checkable plan ONCE, then drive build→review→qa autonomously against the ticket's machine-checkable `done_evidence:` DoD — advancing a stage only when its DoD slice grades green (via `.claude/hooks/adt-dod.sh`), stopping at the merge-offer (never auto-merges) or when stuck. Re-enterable at QA (`--from=qa`). A `track: fast` ticket takes the **fast entry lane** — same gate, shorter path (`building → ready-to-release`, no QA) — after `/adt-plan-fasttrack` gives it the artifacts the gate demands. The only command that suspends working-style #8's stop-at-every-gate, and only against an approved, all-checkable plan. | [build-todone](../../agent-dev-team/commands/build-todone.md) |

### qa — verify
| Item | Type | What it does | Source |
|---|---|---|---|
| `/adt-qa-run` | command · loop | Full test suite + targeted regression + diff-vs-plan review, exiting PASS (→ ready-to-release) or FAIL (→ building). Loops: verify per sub-step until every one has a verdict; closes the `qa fail → building` spine loop. | [qa-run](../../agent-dev-team/commands/qa-run.md) |
| `/adt-review-security` | command→agent | Hand the diff to the `adt-security-reviewer` subagent (fires in qa, and at the build hard floor + release gate). | [review-security](../../agent-dev-team/commands/review-security.md) |

### ready-to-release — gate
| Item | Type | What it does | Source |
|---|---|---|---|
| `/adt-release-check` | command | Verify all release gates (tests, version, security, design, critical-path) before the merge-offer. A barrier, not a worker. | [release-check](../../agent-dev-team/commands/release-check.md) |

### done — close
| Item | Type | What it does | Source |
|---|---|---|---|
| `/adt-close` | command | After the merge: verify done-from-diff, stamp the token cost, move to done/, write the retro, tear down the branch/worktree. | [close](../../agent-dev-team/commands/close.md) |

### cross-cutting (any lane)
| Item | Type | What it does | Source |
|---|---|---|---|
| `/adt-block` | command | Pause on a question only the human can answer; move to blocked/, email. | [block](../../agent-dev-team/commands/block.md) |
| `/adt-unblock` | command | Answer a blocked item, move it back to its prior stage. | [unblock](../../agent-dev-team/commands/unblock.md) |
| `/adt-decide` | command | Record an ADR in decisions.md (the "re-read in 6 months" test). | [decide](../../agent-dev-team/commands/decide.md) |
| `/adt-review-designer` | command→agent | Hand the UI diff to the `adt-design-reviewer` subagent (fires when the diff has UI surface). | [review-designer](../../agent-dev-team/commands/review-designer.md) |
| `/adt-review-critical-path` | command→agent | Hand the diff to the `adt-critical-path-reviewer` subagent (money/safety/data path). | [review-critical-path](../../agent-dev-team/commands/review-critical-path.md) |

---

## Subagents (review in isolation, return a verdict)

The review-heavy work runs as subagents, not commands — they read a lot to
decide a little, so isolating them keeps the main context clean and returns
just the verdict. Invoked via `Agent({subagent_type: "..."})` or the
`/adt-review-*` commands.

| Agent | Loop? | What it reviews |
|---|---|---|
| `adt-security-reviewer` | pass | A diff (or plan) — OWASP + auth + RLS + deps + secrets + project incident classes → one verdict. |
| `adt-design-reviewer` | pass | A UI diff — viewports + dark-mode + theme tokens + responsive → one verdict. |
| `adt-critical-path-reviewer` | pass | The project's money/safety/data-integrity path against its invariants (mandatory on a guarded path; re-checked at release). a consumer project fills this in as `trading-path-reviewer`. |
| `adt-forensic-investigator` | **loop** | "Why did X happen?" — iterates the timeline + causal chain across logs/data/git until the mechanism is pinned. Read-only. The `/adt-diagnose` escalation when the mechanism spans history. |
| `adt-dod-coverage-reviewer` | pass | A ticket's DoD at plan time — does it COVER the spec? → COVERED / GAP / UNKNOWN. |
| `adt-plan-quality-reviewer` | pass | A ticket's PLAN at plan time — does the Design solve the Problem, and is it the simplest thing? → SOUND / FLAWED / UNKNOWN. Not `adt-design-reviewer`, which reviews a UI diff. |

ADT defaults ship `adt-security-reviewer`, `adt-design-reviewer`, `adt-forensic-investigator`,
`ticket-triage`, `adt-critical-path-reviewer`, `adt-dod-coverage-reviewer`,
`adt-plan-quality-reviewer`. Project-specific filled-in instances
(e.g. a consumer's `trading-path-reviewer`) stay as real files in the project.

## Skills (inline, ADT defaults)

Packaged as `.claude/skills/adt-<name>/`; installed by `setup.sh`. Inline (they
share your context), but `disable-model-invocation: true` so you invoke them.

| Skill | Loop? | What it does | Source |
|---|---|---|---|
| `/adt-diagnose` | **loop** | Verify the mechanism — exact line + value at each step — before any fix is proposed (working-style #1). Loops: hypothesise → check value → narrow until one mechanism survives. | [adt-diagnose](../../agent-dev-team/defaults/skills/adt-diagnose/SKILL.md) |

## Hooks (fire automatically)

Wired via `defaults/settings.hooks.json` (merged into the project's
`.claude/settings.json` by `setup.sh`). Full detail: `../defaults/README.md`.

**Class** is derived from `defaults/settings.hooks.json`, the source of truth for
what the harness actually fires: **wired** = the harness runs it on an event;
**template** = ships dormant for a project to copy and wire itself; **helper** =
not a hook at all, a script the playbooks call.

| Hook | Class | Event | What it does | Blocks? |
|---|---|---|---|---|
| `adt-phrase-linter` | wired | Stop | Parses the last assistant turn; flags banned recovery-narration phrases, an unbacked done-claim about a rendered artifact, and an unbacked **pass-claim** (ENFORCED RULE 1 — "the tests pass" with no matching command run this turn). | no (warn) |
| `adt-close-complete` | wired | Stop | **Blocks the stop** (exit 2) when a session whose `current-cmd` marker reads `adt-close` ends without the ticket reaching `<cache>/*/done/` with `stage: done` + `state: closed`. Every other close-path gate fires on an ACTION, so an omitted move fired none of them (ADT-336). Bounded to one block per session and fail-open on every unresolvable input. | **yes** (fail-open on infra) |
| `adt-done-guard` | wired | PreToolUse | **Denies** a `done/` move whose ticket has no `tokens:`/`cost_usd:`/`cost_tier:` stamp (so `/adt-close` did not run; `unattributed` passes), whose `done_evidence` has a condition that ran and FAILED, whose board carries a broken link, or whose rendering checkout is stale; warns otherwise. Also denies a move into planned/ whose ticket has no track of `fast`, `standard` or `full`, so a plan that never set one stops there. A can't-verify condition warns and no longer hides a real failure. | **yes** (fail-open on infra) |
| `adt-deferral-guard` | wired | PreToolUse | **Denies** `gh issue create`, or any gh api call that creates an Issue, unless the transcript carries a human authorisation (ENFORCED RULE 2 — no casual deferral). | **yes** (fail-open on infra) |
| `adt-test-run-guard` | wired | PreToolUse | **Denies** a FULL-suite run (`pytest` over `tools/tests/ tests/`, a bare `pytest`, `run-shell-suite.sh`) unless the human authorised it in the turn in flight — in their own words or with `adt run tests` (or `adt tests`). A targeted run (`-k`, `::`, a named test file) is never gated: that is the change-triggered check working-style #7 actually wants. | **yes** (fail-open on infra) |
| `adt-destructive-git-guard` | wired | PreToolUse | **Denies** a git command that would destroy uncommitted work: `reset --hard`, a forced `checkout`/`switch`, a `checkout --`/`restore` of a dirty path, or a `clean -f` that would remove files. It asks git what would be lost, so a targeted restore of a clean path is allowed, and says to commit or `git stash push -u` first. | **yes** (fail-open on infra) |
| `adt-worktree-guard` | wired | PreToolUse | **Denies** a Write/Edit in the canonical checkout to a path matching `worktree_guard_paths:`, set in `~/.adt/projects/<name>.yaml` and copied by install into `.adt/config.yaml` (multi-agent-git-workflow.md §A.3), naming `git worktree add` as the remedy. With no value it does nothing. | **yes** (fail-open on infra) |
| `adt-usage-log` | wired | UserPromptSubmit | Logs every `/adt-*` invocation to `.adt/state/usage.log`. | no |
| `adt-deploy-guard` | template | PreToolUse | Warns before a deploy/server-start (project template — a project copies + wires its own). | no (fail-open) |
| `adt-token-log` | wired | Stop | Captures cost-of-work in tokens per ticket into an append-only ledger the kanban generator reads. Also tallies `Agent` dispatches by `subagent_type` and `Skill` invocations by name into `.adt/state/surface-log.tsv`, riding the same per-session walk and writing its own file so a tally fault cannot cost a turn its ledger row. The sync (`adt watch`) checkpoints per-machine subtotals from it onto the GitHub Issue as register comments. | no |
| `adt-subagent-cost` | wired | SubagentStop | Bills a SUBAGENT's tokens to the ticket that dispatched it. `adt-token-log` sums the main session transcript only, so subagent work never reached the ledger at all — and seven playbooks dispatch subagents, including `plan`, `release-check` and all three reviewers, so the tier with the most gates was understated most (one ticket's stamp was 76% low). Its cursor keys on the TRANSCRIPT, not the session: a subagent transcript carries the parent's `sessionId`, so a shared cursor would silently write nothing. | no |
| `adt-terminal-title` | wired | SessionStart, UserPromptSubmit, Stop | Writes the terminal tab title as `✳ <TICKET> · <latest command or comment>` — ticket from the same per-session marker the token ledger bills to, topic from the current turn (the slash command with its arguments, or the comment; a bare "yes" keeps the previous one). Renders only where `terminal.integrated.tabs.title` is `"${sequence}"` — the `"${process}"` default shows the version-named launcher binary instead — and the ADT install sets this for you in VS Code and Cursor, and leaves an existing value alone (ADT-334). Uninstall does not revert it. | no (fail-open) |
| `adt-dod` | helper | (playbook-called) | Thin launcher that resolves `adt_dod.py` from a consuming project, so a copied playbook reaches the DoD grader by a local path. Called by 6 playbooks. | n/a |
| `adt-mark-tix` | helper | (playbook-called) | Binds the session to a ticket by writing the per-session token-attribution marker `adt-token-log` reads. Fail-open. Called by 6 playbooks. | n/a |
| `adt-verify-bind` | helper | (playbook-called) | Verifies that bind landed, and **fails closed** if it did not — the pickup precondition, not a hint. Called by 6 playbooks. | n/a |
| `adt-token-sum` | helper | (helper) | Sums tokens for one ticket id from the local ledger only. Composed by `adt-token-total`. | n/a |
| `adt-token-total` | helper | (helper) | Cross-machine total: local ledger + the per-machine register comments on the Issue (`adt:tokens machine=… total=…`). Used by `/adt-close` to stamp `tokens:`. Pass `--cost` for the money total. Degrades to the local sum if `gh` is unavailable. | n/a |
| `adt_backfill_cost` | helper | (helper) | One-shot: re-derives per-class token counts for historical ledger rows from surviving session transcripts, labels each row `measured`/`estimated`, and writes the derived cost estimator. Dry-run by default; `--apply` backs the ledger up before rewriting. | n/a |
| `adt-reattribute` | helper | (helper) | Re-keys `__unassigned__` token-ledger rows to a ticket for a bounded window. | n/a |

### The two enforced rules

Full explanation: [`docs/self-verification.md`](../../agent-dev-team/docs/self-verification.md).

**Rule 1 — verify before asserting.** A claim about the state of the system must
trace to something run or read in that turn. `adt-phrase-linter` matches a pass-claim
against the command actually run, not merely against the presence of one.

**Rule 2 — no casual deferral.** Filing a follow-on ticket needs all three of:
unforeseen, significant deviation required, and human approved. There is no
autonomous path to filing.

Authorisation is read **only from `role: user` transcript turns**, because an
agent cannot write one — every other signal (a marker file, an env var, the
ticket binding) is agent-writable and a gate keyed on one is bypassable. Three
things authorise a create: the current turn being a `/adt-brief` invocation, a
human typing the literal token **`ADT-APPROVE-FOLLOWON`**, or a human asking for
one in **prose** on the last human turn ("create a ticket for X" — the
commonest legitimate path, previously denied). Authorisation is read only from
turns that survive the `isSidechain` / `userType` / machine-prefix filter, in
parity with `tools/adt_xexam.py`'s `human_turns()` — a subagent's seed record is
`role: user` but agent-authored, so it does not count.

## Tools (operator-run, not surfaces)

`tools/*.py` are run by hand or by another tool; they are not hooks, commands,
agents or skills, so `tests/test_surface_catalogue.py` does not police them and
they carry no keep/drop verdict in `docs/surface-review.md`. Listed here so they
are discoverable.

| Tool | What it does |
|---|---|
| `build_dashboard.py` | Renders `.adt/dashboard.html` from the local ledger plus every rollup it can see. Every panel carries `data-coverage="<n> of <n>"` as a value rather than a footnote, so a single-install run reads `1 of 1` through the same code path a multi-install run uses and there is no single-machine mode to rot. Makes NO network calls — the lane split resolves commands locally through `adt_lane_cost.COMMAND_LANE`. |
| `adt_rollup.py` | Rolls this install's ledger into one shareable file per month, `<install-id>-<YYYY-MM>.tsv` under `rollup_dir`. Counts only — no ticket id, session id, path or branch. Each install writes exactly its own file and never another's, which IS the cross-machine transport: files that are never co-written need no locking, no merge and no server, so a second machine joins by pointing at the same directory. |
| `adt_machines.py` | Once a UTC day, from the watch tick: writes this machine's ADT version, clone commit and OS to its own comment on the closed `adt:install` Issue, and saves every machine reported in the last 7 days to `.adt/state/machines.json` for the board's footer. Only comments by the repo's owner, members and collaborators are read. `--older-than <sha> --repo <owner/name>` lists the machines whose ADT is older than `<sha>`; `adt-install.sh` runs it after writing an upgrade branch. `--list --repo <owner/name>` lists every reporting machine; `adt-install.sh --uninstall --everyone` runs it (ADT-384). |
| `adt_register_migrate.py` | Re-keys THIS machine's register comments from a hostname to its install UUID. Dry-run by default; `--verify-totals` proves the amounts survived, `--check` exits non-zero if any hostname-keyed register remains. |
| `adt_usage_report.py` | Reads per-command usage back out of the Analytics Engine dataset. Needs an operator-supplied Cloudflare token in the environment. |
