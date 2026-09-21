# defaults/ — the installable behavioural layer ("Karpathy+++")

These are project-agnostic `.claude/` assets that `setup.sh` installs into a
target project so every project that adopts ADT gets the same
working discipline — not just the stage commands, but *how the agent thinks
and talks* between the gates.

**See also:** the references doc is `../docs/references.md` (the `/adt-*`
commands + skills). This file is the **hook + rule + skill reference** — the
behavioural layer that sits under the commands.

## Rules & skills here

| Asset | What it does | Install target |
|---|---|---|
| `rules/working-style.md` | Always-on behavioural rule: diagnose-to-mechanism, simplest-first, label patches, decide-don't-enumerate, verify-done-from-diff, banned recovery-narration phrases. | `<proj>/.claude/rules/` |
| `skills/adt-diagnose/` | `/adt-diagnose` — forces a verified mechanism (exact line, value at each step) before any fix. Realises working-style #1. | `<proj>/.claude/skills/` |

## Hooks here (8)

The auto-wired ones fire automatically (via `settings.hooks.json`, which
`setup.sh` merges into the project's `.claude/settings.json`); the two
token-summing helpers are operator-invoked CLI tools, not session hooks. All
session hooks **fail open** — a hook error never blocks the user's turn.

| Hook | Event | What it does | Blocks? |
|---|---|---|---|
| `hooks/adt-phrase-linter.sh` | `Stop` | Parses the last assistant turn from the transcript; flags banned recovery-narration phrases, an unbacked done-claim about a rendered artifact, an unbacked **pass-claim** (ADT-114 ENFORCED RULE 1 — a claim that something passed with no matching command run this turn), and an unbacked counted or completeness claim (AO-013 — a sentence carrying both a quantity and a completeness word when nothing in the turn counted anything; working-style #13). Enforces working-style mechanically. | no (warn) |
| `hooks/adt-deferral-guard.sh` | `PreToolUse` (Bash) | **DENIES** `gh issue create`, or any gh api call that creates an Issue (REST or GraphQL), unless the transcript carries a human authorisation — a `/adt-brief` invocation, a typed `ADT-APPROVE-FOLLOWON`, or `approve` on its own line of the latest message (ADT-114 ENFORCED RULE 2 — no casual deferral). It reads the command's words, not its text, so a `grep` that names the command is not denied (ADT-354). Authorisation is read from `role: user` turns that survive the `isSidechain` / `userType` / machine-prefix filter — a subagent's seed record is `role: user` but agent-authored (ADT-153). A human asking in **prose** authorises, as does `/adt-brief` or the typed token. **Fail-open** on infra. | **yes** |
| `hooks/adt-done-guard.sh` | `PreToolUse` (Bash\|Write\|Edit) | **DENIES** a move into `done/` when the ticket has no `tokens:`/`cost_usd:`/`cost_tier:` stamp from `/adt-close` (`unattributed` passes), a `done_evidence` condition failed, the board has a broken link, the rendering checkout is stale, or infrastructure changed with no `recurring_cost:`. Otherwise warns to verify the work is integrated. **Fail-open** on infra. | **yes** |
| `hooks/adt-destructive-git-guard.sh` | `PreToolUse` (Bash) | **DENIES** a git command that would destroy uncommitted work (`reset --hard`, forced `checkout`/`switch`, `checkout --`/`restore` of a dirty path, `clean -f` that would remove files), naming the files and saying to commit or `git stash push -u` first. It asks git what would be lost, so a restore of a clean path is allowed (ADT-359). **Fail-open.** | **yes** |
| `hooks/adt-worktree-guard.sh` | `PreToolUse` (Write\|Edit) | **DENIES** a Write/Edit in the canonical checkout to a path matching `worktree_guard_paths:` (space-separated fnmatch patterns, set in `~/.adt/projects/<name>.yaml` and copied by install into `.adt/config.yaml`), with `git worktree add` as the remedy (§A.3). With no value it does nothing. **Fail-open.** | **yes** |
| `hooks/adt-deploy-guard.sh` | `PreToolUse` (Bash) | Warns before a deploy/server-start. **Template, ships dormant** — not wired in `settings.hooks.json`; triggers are project-specific. A project that wants it wires it and keeps its own filled-in copy as a real file (the installer never clobbers it). | no (warn) |
| `hooks/adt-usage-log.sh` | `UserPromptSubmit` | Logs every `/adt-*` invocation to the project's `.adt-state/` usage log. Run `tools/usage-report.py` for a frequency ranking. | no |
| `hooks/adt-token-log.sh` | `Stop` | Appends per-turn token usage to the project's `.adt-state/cost-ledger.log` — 13 TSV columns since ADT-254 (`ts, tix, input, output, session, model, speed, cache_read, cw_5m, cw_1h, tier, command, install`), one row per `(model, speed)` group so a batch spanning two models prices correctly. The first five columns are the pre-ADT-115 format, unchanged and in place. **Fail-open.** | no |
| `hooks/adt-subagent-cost.sh` | `SubagentStop` | Bills a SUBAGENT's tokens to the ticket that dispatched it (ADT-224). Writes the same 13-column row with `tier=measured` and `command=subagent:<type>`. Its cursor keys on the TRANSCRIPT basename, not the session: a subagent transcript carries the parent's `sessionId`, so a session-keyed cursor would find nothing new and silently write no row. **Fail-open.** | no |
| `hooks/adt-terminal-title.sh` | `SessionStart`, `UserPromptSubmit`, `Stop` | Writes the terminal tab title as `✳ <TICKET> · <latest command or comment>` — ticket from the same per-session marker the token ledger bills to, topic from the current turn: the slash command (with its arguments) when the turn is a command, the comment otherwise. Only a bare acknowledgement ("yes", "go on") keeps the previous one. Claude Code's own title writer is turned off by the `env` block in `settings.hooks.json` so the two don't fight — measured, it rewrites the title on every spinner-glyph change, so it would otherwise overwrite this hook several times a turn. **The editor has to be told to render it:** `terminal.integrated.tabs.title` has to be `"${sequence}"` — the `"${process}"` default shows the version-named launcher binary, which is why untouched tabs all read e.g. `2.1.259`. Since ADT-334 the ADT install sets this for you in VS Code and Cursor, and leaves an existing value alone; a file that is not strict JSON (JSONC comments or a trailing comma) is left untouched and warned about, because a re-dump would delete the operator's comments. Uninstall does not revert it. Reverting a setting someone has come to rely on, while uninstalling something else, is a worse surprise than leaving it. **Fail-open.** | no |
| `hooks/adt-token-sum.sh` | — (CLI) | Operator tool: sums the token ledger per ticket. Not a session hook. | n/a |
| `hooks/adt-reattribute.sh` | — (CLI) | Operator tool: re-attributes ledger rows to a ticket. Not a session hook. | n/a |

## What is NOT here (deliberately)

- **Stage commands** — the `adt-*` playbooks under `commands/`, copied
  per-project into `<project>/.claude/commands/` by `lib/install-defaults.sh`
  (ADT-087). Documented in `../docs/references.md`. The defaults here are the
  behavioural layer that sits *under* the workflow.

## The done gate (`adt-done-guard.sh`) — DENY, not warn (ADT-061)

`done-guard` is the **first ADT hook to hard-deny** (the rest are warn-only). On a
move into `…/done/` — a plain `mv` (the cache-first form) or a `git mv` — it runs
three checks and DENIES the move on a failed assertion;
it fails **open** (warn, don't deny) on an infra error (artifact file absent,
unresolvable root) so it never blocks on its own gap. A PreToolUse hook denies by
printing this on stdout and exiting 0 (verified contract,
code.claude.com/docs/en/hooks.md):

```json
{"hookSpecificOutput":{"hookEventName":"PreToolUse",
  "permissionDecision":"deny","permissionDecisionReason":"…"}}
```

The three gates:

1. **Artifact (`done_evidence`).** If the moved ticket's `.md` declares a
   `done_evidence:` list, each entry's regex must match in the named **rendered**
   file under `.adt/` (the copy the human opens — never the cache,
   never the source `.md`). This is a *declared, executed* assertion (recorded in the decision log):
   the hook can't infer what element a ticket claims, so the ticket states it.

   ```yaml
   done_evidence:
     - file: .adt/kanban.html        # human-opened copy
       must_contain_regex: '<a [^>]*href="references\.html"'   # the real element
   ```

   Write the regex to require the actual element (e.g. the `<a href=…>` anchor),
   not a bare word — that is what rejects a tooltip/substring/source match (the
   ADT-58 failure this gate kills). No `done_evidence:` → nothing to assert here
   (the 404 + freshness gates below still run).
2. **404.** Every local `href`/`src` in `.adt/kanban.html` must
   resolve to a real sibling file — no broken links on the board the human opens.
3. **Deploy-freshness.** The checkout that RENDERS the board (the launchd
   watcher's `ADT_DIR`, read from the installed `~/Library/LaunchAgents/*adt*.plist`)
   must be at-or-ahead of `origin/main`. A stale rendering checkout means the human
   sees output from pre-merge code. No watcher installed → warn (can't-verify is
   not the same as stale).

The companion `adt-phrase-linter.sh` (Stop hook) flags the *claim* side: a done-claim
about a rendered artifact emitted in a turn that made no tool call opening that
`.adt/` file is warned (warn-only by choice, not by constraint: exit 2 on a Stop hook blocks the stop, which is how `adt-close-complete.sh` gates the close path — ADT-336). #1 blocks
the action; the linter flags the unbacked claim.

## Origin

Extracted from a production consumer (2026-06-08) after a working-quality retrospective.
The behavioural layer proved its value there first; these defaults make it
reusable across every project that installs the team.
