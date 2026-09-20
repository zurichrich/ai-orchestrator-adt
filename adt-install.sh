#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ai-orchestrator-adt — adt-install.sh
#
# One command, run FROM INSIDE the project you want to manage with ADT.
# It interviews you for the few things it can't infer, writes a per-user config
# (outside this repo, so the repo stays clean for open-source), then runs the
# full setup: defaults → GitHub board + labels → adopt existing issues → render.
#
#   cd ~/your-project
#   /path/to/ai-orchestrator-adt/adt-install.sh
#
# Idempotent: safe to re-run. Re-running re-confirms inferred values and
# re-syncs. It writes the ADT defaults into the working tree only while no
# layer is committed on origin (see below). Existing answers are offered as
# defaults.
#
# Several machines, one repo (ADT-384). Before asking anything, the installer
# reads the ADT layer committed on origin/<main>. If ADT is already installed
# there, this machine joins that install: it takes the name, repo, main branch,
# prefix and board title from the committed .claude/adt-project.yaml, sets up
# this machine's config, cache and watcher, finds the existing board by its
# committed title instead of creating one, and never writes the shared .claude/
# layer into your working tree. An ADT clone older than the committed layer stops the install
# and names the pull. A newer one writes its upgrade onto a local
# chore/adt-upgrade-<sha> branch; merging it upgrades ADT for every machine.
#
# Flags:
#   --uninstall   Reverse the install for THIS project: archive the token
#                 ledger to a GitHub issue then delete it, remove the .claude/
#                 ADT symlinks + hook entries, remove the generated config, and
#                 remove the whole .adt/ folder (ADT working state).
#                 NEVER touches GitHub Issues/board or the ticket cache. Keep
#                 authored docs (ADRs/retros) in your project's docs/ — point
#                 decision_log: there — so they're outside the delete path.
#                 Safe to repeat.
#                 On a repo whose ADT layer is committed (ADT-384), it removes
#                 only THIS machine and leaves the committed .claude/ files,
#                 CLAUDE.md and .gitignore alone, because they belong to every
#                 machine that pulls the repo.
#   --everyone    With --uninstall: also remove ADT from the repo for every
#                 machine. The removal of the committed layer is written to a
#                 local chore/adt-uninstall branch (not pushed) for the team to
#                 merge; each machine still runs --uninstall for its watcher.
#   --no-github   Set up the local side only (skip board/labels/pull). Useful
#                 offline or before you've granted gh the 'project' scope.
#                 With --uninstall, also leaves the token ledger in place.
#   --yes         Accept every interview default without a terminal
#                 (--non-interactive is a synonym). Without it, a prompt that
#                 reaches EOF is an ERROR, not a silent default: an unanswered
#                 install is how a whole backlog gets mis-stamped (ADT-135).
#                 A defaults-only run is labelled as such in its output.
#   --prefix <X>  Set the ticket-id prefix explicitly. Also the override for the
#                 mismatch stop: without it, a prefix that disagrees with the one
#                 this repo's own commit history uses halts the install.
#   -h|--help     This header.

set -euo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ADT_DIR
ADT_USER_PROJECTS="${ADT_USER_PROJECTS:-$HOME/.adt/projects}"
PROJECT_DIR="$(pwd)"

DO_GITHUB=true
DO_UNINSTALL=false
EVERYONE=false
ASSUME_YES=false
PREFIX_OVERRIDE=false
PREFIX_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall) DO_UNINSTALL=true ;;
    --everyone)  EVERYONE=true ;;
    --no-github) DO_GITHUB=false ;;
    --yes|--non-interactive) ASSUME_YES=true ;;
    --prefix)    PREFIX_OVERRIDE=true; PREFIX_ARG="${2:-}"; shift
                 [[ -n "$PREFIX_ARG" ]] || { echo "install: --prefix needs a value" >&2; exit 2; } ;;
    --prefix=*)  PREFIX_OVERRIDE=true; PREFIX_ARG="${1#--prefix=}"
                 [[ -n "$PREFIX_ARG" ]] || { echo "install: --prefix needs a value" >&2; exit 2; } ;;
    -h|--help)   sed -n '2,/^set -euo pipefail$/p' "$0" | sed '$d'; exit 0 ;;
    *) echo "install: unknown flag '$1' (see --help)" >&2; exit 2 ;;
  esac
  shift
done

if [[ "$EVERYONE" == true && "$DO_UNINSTALL" != true ]]; then
  echo "install: --everyone only works with --uninstall (adt-install.sh --uninstall --everyone)" >&2
  exit 2
fi

# ── Uninstall path (run from the project, like install) ────────────────────
if [[ "$DO_UNINSTALL" == true ]]; then
  if ! git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    printf '\033[1;31m%s\033[0m\n' "Not a git repository: $PROJECT_DIR" >&2
    echo "  Run ./adt-install.sh --uninstall from inside the project to uninstall." >&2
    exit 1
  fi
  # Resolve which project to uninstall. Collect ALL configs whose `path` matches
  # this checkout — there can be more than one (stale/duplicate configs). Prefer
  # the one whose name == the directory basename (the canonical match). If
  # several match and none is canonical, REFUSE rather than guess — deleting the
  # wrong project's config is the failure mode to avoid.
  base="$(basename "$PROJECT_DIR")"
  name=""
  matches=()
  if [[ -d "$ADT_USER_PROJECTS" ]]; then
    for cfg in "$ADT_USER_PROJECTS"/*.yaml; do
      [[ -f "$cfg" ]] || continue
      p="$(yq -r '.path // ""' "$cfg" 2>/dev/null || true)"
      if [[ "$p" == "$PROJECT_DIR" ]]; then
        n="$(basename "$cfg" .yaml)"
        matches+=("$n")
        [[ "$n" == "$base" ]] && name="$n"   # canonical match wins
      fi
    done
  fi
  if [[ -z "$name" ]]; then
    if [[ ${#matches[@]} -eq 1 ]]; then
      name="${matches[0]}"
    elif [[ ${#matches[@]} -gt 1 ]]; then
      printf '\033[1;31m%s\033[0m\n' "Multiple configs point at $PROJECT_DIR: ${matches[*]}" >&2
      echo "  None matches the directory name ('$base'). Refusing to guess which to uninstall." >&2
      echo "  Remove the stale one(s) from $ADT_USER_PROJECTS, or rename the right one to $base.yaml." >&2
      exit 1
    else
      name="$base"   # no config at all — fall back to the dir name
    fi
  fi
  # shellcheck disable=SC1091
  source "$ADT_DIR/lib/uninstall.sh"
  un_flags=()
  [[ "$DO_GITHUB" != true ]] && un_flags+=(--no-github)
  [[ "$EVERYONE" == true ]] && un_flags+=(--everyone)
  uninstall_project "$ADT_DIR" "$name" "$PROJECT_DIR" ${un_flags[@]+"${un_flags[@]}"}
  exit 0
fi

say()  { printf '\033[1;36m%s\033[0m\n' "$*"; }   # cyan headers
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m%s\033[0m\n' "$*" >&2; }
# The interview prompt, the prefix derivation and the gh-scope decisions live in
# lib/install-helpers.sh so tests can call them directly (ADT-135).
# shellcheck disable=SC1091
source "$ADT_DIR/lib/install-helpers.sh"

# Where adt_ask records that a prompt was defaulted rather than answered. It
# must be a FILE: ask() is called as `$(ask …)` and calls adt_ask as `$(…)`, so
# a variable set inside is two subshells away from this one.
ADT_ASK_MARKER="$(mktemp)"; : > "$ADT_ASK_MARKER"
# ONE exit trap for the whole script: a second `trap … EXIT` further down would
# silently replace this one and leak the file it was cleaning up.
_adt_cleanup() { rm -f "$ADT_ASK_MARKER" "${core:-}"; }
trap _adt_cleanup EXIT

# ask() delegates to adt_ask, which treats a prompt that reaches EOF as an
# ERROR rather than a silent default (ADT-135 defect 1). `read` returns 0 for a
# blank line that was actually supplied and non-zero only at EOF, so a scripted
# run that pipes answers still works; a run with nothing on stdin does not.
# adt_ask runs inside `$(...)` here, so a variable it sets is confined to that
# subshell. It signals a defaulted prompt with exit code 2 instead, which does
# survive command substitution — that is what makes the banner below reachable.
ask() {
  local a rc
  a="$(adt_ask "$1" "${2:-}" "$ASSUME_YES")"; rc=$?
  case "$rc" in
    0) : ;;
    2) ADT_ASK_DEFAULTED=true ;;
    *) exit 1 ;;
  esac
  printf '%s\n' "$a"
}

# ── Phase 0: preflight (fail fast, before any writes) ──────────────────────
say "▸ Checking prerequisites…"
missing=()
for t in gh jq yq git claude; do command -v "$t" >/dev/null 2>&1 || missing+=("$t"); done
if [[ ${#missing[@]} -gt 0 ]]; then
  err "Missing tools: ${missing[*]}"
  for t in "${missing[@]}"; do case "$t" in
    gh)     echo "  brew install gh && gh auth login" ;;
    claude) echo "  see https://claude.com/claude-code" ;;
    *)      echo "  brew install $t" ;;
  esac; done
  exit 1
fi
ok "all tools present"

if ! git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  err "Not a git repository: $PROJECT_DIR"
  echo "  Run adt-install.sh from inside the project you want ADT to manage."
  exit 1
fi
ok "git repo: $PROJECT_DIR"

# ── Is ADT already installed in this repo? (ADT-384) ───────────────────────
# Asked before any prompt, of origin/<main> and not of this checkout: a second
# machine's clone can be hundreds of commits behind and have no manifest on
# disk. ADT's own repo is not a consumer of itself, so it never joins.
# shellcheck disable=SC1091
source "$ADT_DIR/lib/adt-layer.sh"
def_branch="$(git -C "$PROJECT_DIR" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##' || true)"
[[ -z "$def_branch" ]] && def_branch="$(git -C "$PROJECT_DIR" branch --show-current 2>/dev/null || echo main)"
detect_branch="$def_branch"
adt_layer_state "$ADT_DIR" "$PROJECT_DIR" "$detect_branch"
if [[ "$ADT_LAYER_STATE" == older ]]; then
  adt_layer_refuse_older "$ADT_DIR"
  exit 3
fi
# JOIN: ADT is committed on origin, so this machine joins it. FROM_SHARED: the
# committed .claude/adt-project.yaml supplied the settings, so nothing it holds
# is asked. An install committed before ADT-384 is a JOIN without FROM_SHARED.
JOIN=false; FROM_SHARED=false
j_name=""; j_repo=""; j_main=""; j_prefix=""; j_board=""
if [[ "$ADT_LAYER_STATE" != none && "$ADT_LAYER_STATE" != self ]]; then
  JOIN=true
  shared="$(git -C "$PROJECT_DIR" show "origin/$detect_branch:.claude/adt-project.yaml" 2>/dev/null || true)"
  if [[ -n "$shared" ]]; then
    # One value per line: a tab-separated read would collapse an empty field.
    { IFS= read -r j_name; IFS= read -r j_repo; IFS= read -r j_main
      IFS= read -r j_prefix; IFS= read -r j_board; } < <(yq -r \
      '.name // "", .repo // "", .main_branch // "", .id_prefix // "", .board_title // ""' \
      <<<"$shared" 2>/dev/null) || true
    [[ -n "$j_repo" ]] && FROM_SHARED=true
  fi
  j_where="${j_repo:-$(_adt_layer_github_repo "$PROJECT_DIR")}"
  say "▸ ADT ${ADT_LAYER_BUNDLE:-?} (source ${ADT_LAYER_COMMITTED:0:7}) is already installed in ${j_where:-this repo}; this machine will join it"
fi

if [[ "$DO_GITHUB" == true ]]; then
  if ! gh auth status >/dev/null 2>&1; then
    err "gh is not authenticated."
    echo "  Run: gh auth login        (then re-run adt-install.sh)"
    exit 1
  fi
  # ADT-135 defect 7. Two bugs here, not one. (a) The check was a SUBSTRING
  # match that `read:project` satisfies, so a read-only token passed the
  # write-scope gate, the install reported success, and every later
  # `gh project item-add` failed — a ticket reaching Issues but never the board
  # is invisible on the kanban. adt_scopes_have_project splits the list and
  # matches exactly. (b) A missing scope was a precondition to REPORT rather
  # than one to OBTAIN, even though the installer is already an interactive
  # session that stops to interview the operator. It now drives the same grant
  # inline and continues in one pass.
  scope_line="$(gh auth status 2>&1 | grep "Token scopes" || true)"
  have_scope=false
  adt_scopes_have_project "$scope_line" && have_scope=true
  interactive=false; [[ -t 0 ]] && interactive=true
  case "$(adt_scope_action "$have_scope" "$interactive")" in
    ok) ok "gh authenticated with the 'project' scope" ;;
    refresh)
      say "  gh needs the 'project' scope to write to the board — granting it now."
      echo "  (your browser will open; the token you have holds${scope_line#*Token scopes:})"
      if gh auth refresh -h github.com -s project; then
        scope_line="$(gh auth status 2>&1 | grep "Token scopes" || true)"
        if adt_scopes_have_project "$scope_line"; then
          ok "'project' scope granted"
        else
          err "the refresh completed but the token still lacks the 'project' scope."
          echo "  Run: gh auth refresh -h github.com -s project    (then re-run adt-install.sh)" >&2
          exit 1
        fi
      else
        err "could not obtain the 'project' scope."
        echo "  Run: gh auth refresh -h github.com -s project    (then re-run adt-install.sh)" >&2
        echo "  Or:  ./adt-install.sh --no-github                 (skip the board for now)" >&2
        exit 1
      fi ;;
    abort)
      err "gh token is missing the 'project' scope (needed to write to the board)."
      echo "  This run is non-interactive, so the grant can't be driven here." >&2
      echo "  Run: gh auth refresh -h github.com -s project    (then re-run adt-install.sh)" >&2
      echo "  Or:  ./adt-install.sh --no-github                 (skip the board for now)" >&2
      exit 1 ;;
  esac
fi

# ── Phase 1: infer, then confirm ───────────────────────────────────────────
say "▸ Detecting your project…"
def_name="$(basename "$PROJECT_DIR")"
def_repo=""
if gh repo view --json nameWithOwner >/dev/null 2>&1; then
  def_repo="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
fi

# If a config already exists, offer its values as defaults (idempotent re-run).
# ADT-066 defect 1: a prior uninstall backs the config up to <name>.yaml.bak
# rather than deleting it. Fall back to the .bak so a reinstall recovers (and
# merges from, Phase 2) the hand-authored fields — otherwise "preserve on
# uninstall" would be dead weight that the next install never reads.
existing="$ADT_USER_PROJECTS/$def_name.yaml"
[[ ! -f "$existing" && -f "$existing.bak" ]] && existing="$existing.bak"
if [[ -f "$existing" ]]; then
  ok "found existing config: $existing (values offered as defaults)"
  def_repo="$(yq -r '.github.repo // ""'   "$existing")"
  def_branch="$(yq -r '.main_branch // "main"' "$existing")"
fi

# Split-repo guard. ADT keeps the backlog (Issues) in the SAME repo as the code
# — that's the only layout adt-install.sh supports. If an existing config points its
# backlog at a different repo than this checkout's remote (the ADT-on-ADT
# dogfooding exception), don't silently re-wire it: refuse and defer to the
# hand-maintained config. (Code-repo detection: strip a trailing -backlog.)
if [[ -n "$existing" && -f "$existing" && -n "$def_repo" && -n "${def_repo:-}" ]]; then
  code_remote="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
  if [[ -n "$code_remote" && "$def_repo" != "$code_remote" ]]; then
    err "This project's config backs its backlog with a DIFFERENT repo than the code:"
    echo "    code repo (this checkout): $code_remote" >&2
    echo "    backlog repo (in config):  $def_repo" >&2
    echo "  adt-install.sh only supports backlog == code repo. This split layout is a" >&2
    echo "  hand-maintained exception (e.g. ADT-on-ADT) — edit $existing directly" >&2
    echo "  instead of re-running adt-install.sh. Aborting to avoid mis-wiring it." >&2
    exit 1
  fi
fi

# Joining: the committed .claude/adt-project.yaml answers these, so they are
# not asked. An install committed before ADT-384 has no such file, and asks.
if [[ "$FROM_SHARED" == true ]]; then
  name="${j_name:-$def_name}"
  repo="$j_repo"
  main_branch="${j_main:-$detect_branch}"
  ok "joining with the settings committed in .claude/adt-project.yaml"
else
  [[ "$JOIN" == true ]] && \
    echo "  origin/$detect_branch has no .claude/adt-project.yaml yet, so the install asks."
  name="$(ask 'Project name' "$def_name")"
  repo="$(ask 'GitHub repo (owner/name)' "$def_repo")"
  if [[ -z "$repo" ]]; then
    err "No GitHub repo detected and none entered. ADT needs a repo for the backlog."
    exit 1
  fi
  main_branch="$(ask 'Main branch' "$def_branch")"
fi
owner="${repo%%/*}"
board_title="$name backlog"
[[ "$FROM_SHARED" == true && -n "$j_board" ]] && board_title="$j_board"

# Ticket prefix (ADT-066 defect 2): NEVER derive it from the project name. A
# name-slice (myproject → "MYP") silently forks an existing TIX-N backlog into a
# mixed-prefix mess. Prefer the surviving config's prefix (the common reinstall
# case — kept alive by the merge below); otherwise a neutral constant. The id is
# tied to the issue number, not the prefix, so the prefix is purely cosmetic and
# a stable default beats a clever one.
# ADT-135 defect 2: on a NEW machine there is no local config, so the fallback
# above was the bare TIX constant — and all 40 adopted tickets got stamped
# TIX-N against Issues whose own comments say ADT-. That is durable, not
# cosmetic: adt_sync sets `id` only when absent, so no later sync corrects it.
# The repo's own commit history states the prefix for free; prefer it over the
# constant when there's no config to inherit from.
def_prefix="TIX"
derived_prefix="$(adt_derive_id_prefix "$PROJECT_DIR")"
if [[ -f "$existing" ]]; then
  def_prefix="$(yq -r '.kanban.id_prefix // "TIX"' "$existing")"
elif [[ -n "$derived_prefix" ]]; then
  def_prefix="$derived_prefix"
  ok "ticket prefix '$derived_prefix' derived from this repo's commit history"
fi
if [[ "$PREFIX_OVERRIDE" == true ]]; then
  id_prefix="$PREFIX_ARG"
elif [[ "$FROM_SHARED" == true && -n "$j_prefix" ]]; then
  id_prefix="$j_prefix"
else
  id_prefix="$(ask 'Ticket ID prefix' "$def_prefix")"
fi

# 2b: a chosen prefix that disagrees with the repo's own history halts the
# install rather than silently stamping the backlog. --prefix is the override,
# so a project that legitimately renames its prefix can still install. A prefix
# committed in .claude/adt-project.yaml is the team's own statement of it, so a
# join does not second-guess it from the history.
if [[ ! ( "$FROM_SHARED" == true && -n "$j_prefix" ) ]] \
   && [[ "$(adt_prefix_conflict "$derived_prefix" "$id_prefix" "$PREFIX_OVERRIDE")" == stop ]]; then
  err "Ticket prefix mismatch — refusing to stamp this backlog."
  echo "    you chose:              $id_prefix" >&2
  echo "    this repo's own commits use: $derived_prefix" >&2
  echo "  Stamping the wrong prefix is durable: the sync sets a ticket's id once" >&2
  echo "  and never corrects it, so every branch name, commit scope and board" >&2
  echo "  card would carry it. If '$id_prefix' is right, say so explicitly:" >&2
  echo "    ./adt-install.sh --prefix $id_prefix" >&2
  exit 1
fi

# decision_log: preserve a relocated path across reinstalls. A project may point
# its ADR log at its own docs/ (out of docs/); the unconditional
# config rewrite below must not stomp that back to the default. Same idempotency
# idiom as repo/branch/prefix above.
def_decision_log="docs/decisions.md"
[[ -f "$existing" ]] && def_decision_log="$(yq -r '.decision_log // "'"$def_decision_log"'"' "$existing")"

cache_dir="$HOME/.adt/$name/cache"

# References doc source (ADT-066 defect 3). When ADT is consumed from an external
# clone (de-vendored — no in-repo agent-dev-team/ submodule), adt_watch.py can't
# find references.md at either of its repo-relative fallbacks, so it drops the
# 📖 references page + link. install KNOWS where ADT lives ($ADT_DIR), so write
# that absolute path into the config and the watcher resolves it regardless of
# layout. Only set it when the doc actually exists there (else leave unset and
# let the watcher's in-repo fallback handle the submodule layout).
commands_doc_src=""
[[ -f "$ADT_DIR/docs/references.md" ]] && commands_doc_src="$ADT_DIR/docs/references.md"

echo
say "▸ Will configure:"
# ADT-135 defect 1: a run that took its answers from EOF rather than a human
# must never look like one that was actually answered.
[[ -s "$ADT_ASK_MARKER" ]] && \
  err "non-interactive: every value below is a DEFAULT — nothing was answered"
echo "    name:        $name"
echo "    path:        $PROJECT_DIR"
echo "    repo:        $repo  (owner: $owner)"
echo "    main_branch: $main_branch"
echo "    id_prefix:   $id_prefix"
echo "    board_title: $board_title"
echo "    cache_dir:   $cache_dir"
echo "    GitHub board+pull: $DO_GITHUB"
echo
confirm="$(ask 'Proceed? (y/n)' 'y')"
[[ "$confirm" =~ ^[Yy] ]] || { echo "Aborted."; exit 0; }

# ── Phase 2: write the per-user config (outside the repo) ──────────────────
# ADT-066 defect 1 (the keystone): MERGE the core fields install owns onto the
# surviving config, never overwrite the whole file. A real consumer's config
# carries hand-authored, non-inferable fields (security, notifications, deploy,
# sync_docs, kanban.stage_tools, …) that ADT
# does not author and CANNOT regenerate. The old heredoc clobbered all of them
# on every re-run. Now: write only the core fields to a temp file, then deep-
# merge with the existing config as the BASE and core as the OVERRIDE
# (existing * core) so every authored key survives and the core fields refresh.
# With no existing config the merge degrades to core-only — identical to before.
say "▸ Writing config…"
mkdir -p "$ADT_USER_PROJECTS"
cfg="$ADT_USER_PROJECTS/$name.yaml"

# The core fields install owns/infers. commands_doc_src is added below only when
# resolved, so we don't write an empty key that would shadow a hand-set one.
core="$(mktemp)"   # removed by _adt_cleanup (the single EXIT trap above)
cat > "$core" <<EOF
# ADT project config — generated by adt-install.sh. Lives outside the team repo so
# your paths/secrets never enter git. Full schema: $ADT_DIR/projects/example.yaml
name: $name
path: $PROJECT_DIR
main_branch: $main_branch
remote: origin
claude_md: CLAUDE.md
cache_dir: $cache_dir
decision_log: $def_decision_log
merger: $owner

github:
  repo: $repo
  owner: $owner
  board_title: "$board_title"
  # stages: omit → ADT default 7-lane lifecycle
  #   (ideas planned building qa blocked ready-to-release done)

kanban:
  # This is the authoritative definition of id_prefix (ADT-135 defect 3). The
  # project's own .adt/config.yaml also carries an id_prefix: line — that copy is
  # DERIVED from this one, regenerated by init_github on every install. Change
  # it here and re-run adt-install.sh; editing the derived copy alone drifts.
  id_prefix: $id_prefix
  board_out: $cache_dir/kanban.html
EOF
# Defect 3: only emit commands_doc_src when we resolved an actual file, so it
# refreshes a stale path but never overwrites a hand-set one with empty.
if [[ -n "$commands_doc_src" ]]; then
  CDS="$commands_doc_src" yq -i '.kanban.commands_doc_src = strenv(CDS)' "$core"
fi

# Merge base: the surviving config for THIS project name — the live <name>.yaml
# on a plain re-run, or the <name>.yaml.bak that uninstall left behind. $existing
# (resolved in Phase 1) already encodes that fallback, but only for $def_name; if
# the operator renamed the project there's no prior config to merge, which is
# correct (a rename starts fresh). Require the base to actually be for this name.
merge_base=""
if [[ "$name" == "$def_name" && -f "$existing" ]]; then
  merge_base="$existing"
elif [[ -f "$cfg" ]]; then
  merge_base="$cfg"        # config already at the target name (e.g. mid-rename re-run)
fi

if [[ -n "$merge_base" ]]; then
  # base * core: the surviving config is the base (authored keys win where core
  # is silent), core overrides the fields install owns. Merge to a temp then move
  # into place so a mid-merge failure can't truncate the live config.
  merged="$(mktemp)"
  # The merge keeps the head comment of BOTH documents, so each re-run stacked
  # another copy of core's header on top (ADT-354). Drop every line of ADT's own
  # header, whichever checkout path it names, and put core's header back once.
  # Lines the user wrote there stay, below the header.
  hdr_expr='. head_comment = ((strenv(HDR) | split("\n")) + ((. | head_comment | split("\n"))
    | map(select(test("generated by adt-install\\.sh|never enter git\\. Full schema:") | not))
    | map(select(. != ""))) | join("\n"))'
  if yq eval-all '. as $i ireduce ({}; . * $i)' "$merge_base" "$core" > "$merged" 2>/dev/null && [[ -s "$merged" ]] \
     && HDR="$(yq '. | head_comment' "$core")" yq -i "$hdr_expr" "$merged"; then
    mv "$merged" "$cfg"
    [[ "$merge_base" == *.bak ]] && rm -f "$merge_base"   # promoted back to live; drop the backup
    ok "config: $cfg (merged from ${merge_base##*/} — hand-authored fields preserved)"
  else
    rm -f "$merged"
    err "config merge failed — leaving the surviving config ($merge_base) untouched."
    echo "  Inspect it by hand; re-run once resolved." >&2
    exit 1
  fi
else
  cp "$core" "$cfg"
  ok "config: $cfg"
fi
rm -f "$core"; core=""

# ── Phase 3: execute, in order ─────────────────────────────────────────────
say "▸ Installing slash commands + project defaults…"
# Scope the defaults install to THIS project (ADT-174). Every other phase below
# is already scoped by "$name" — load_project, init_github, the pull's --root —
# and only this one wasn't, so an install here also rewrote every other
# configured project's .claude/ layer. `setup.sh` with no --project remains the
# machine-wide reconciler for a post-`git pull` refresh.
"$ADT_DIR/setup.sh" --project "$name"

# ADT-384: the settings every machine shares, committed so the next machine
# joins without being asked. Written on a first install, and on a join to an
# install committed before this file existed. Never overwritten.
shared_file="$PROJECT_DIR/.claude/adt-project.yaml"
if [[ "$ADT_LAYER_STATE" != self && "$FROM_SHARED" != true && ! -e "$shared_file" ]]; then
  mkdir -p "$PROJECT_DIR/.claude"
  cat > "$shared_file" <<EOF
# ADT settings shared by every machine that installs ADT in this repo (ADT-384).
# Commit this file. adt-install.sh on another machine reads it from
# origin/$main_branch and joins with these values instead of asking for them.
name: $name
repo: $repo
main_branch: $main_branch
id_prefix: $id_prefix
board_title: "$board_title"
EOF
  ok "wrote .claude/adt-project.yaml: commit it so other machines join without questions"
fi

# ADT-384: an upgrade reaches other machines only when they pull ADT, so name
# the machines whose last report is older than this clone.
if [[ "$JOIN" == true && ( "$ADT_LAYER_STATE" == newer || "$ADT_LAYER_STATE" == diverged ) ]]; then
  if [[ "$DO_GITHUB" == true ]]; then
    python3 "$ADT_DIR/tools/adt_machines.py" --older-than "$ADT_LAYER_HEAD" \
      --repo "$repo" --adt-dir "$ADT_DIR" || true
  else
    echo "  [machines] --no-github: skipped the list of machines that must pull ADT once the upgrade is merged."
  fi
fi

if [[ "$DO_GITHUB" == true ]]; then
  say "▸ Bootstrapping GitHub board + labels…"
  # shellcheck disable=SC1091
  source "$ADT_DIR/lib/load-project.sh"
  load_project "$name"
  # shellcheck disable=SC1091
  source "$ADT_DIR/lib/github-bootstrap.sh"
  init_github "$name"

  say "▸ Adopting existing issues into the cache…"
  # No tick of our own here (ADT-174): adt_sync prints the adopt summary itself
  # — it owns the result list and knows how many Issues were adopted and how
  # many were deferred. The static success line that used to follow this call
  # was composed from nothing the pull returned, so it reported success for work
  # that had been skipped — which is what a newcomer hits when they file a
  # ticket and then reinstall or set up a second machine.
  if ! python3 "$ADT_DIR/tools/adt_sync.py" --root "$PROJECT_DIR" --pull; then
    err "pull reported errors — your board exists; run the pull again later:"
    echo "  python3 $ADT_DIR/tools/adt_sync.py --root $PROJECT_DIR --pull"
  fi

  say "▸ Rendering the board…"
  # adt_watch --once is the config-aware render path: it resolves the cache dir
  # and renders with backlog_root="" so the cache IS the board source. (Calling
  # build_kanban directly would render the empty in-repo tree instead.)
  python3 "$ADT_DIR/tools/adt_watch.py" --root "$PROJECT_DIR" --once >/dev/null 2>&1 \
    && ok "rendered $cache_dir/kanban.html" \
    || echo "  (render skipped — will render on first 'adt watch' pass)"

  say "▸ Installing the background board-sync watcher…"
  # A watcher MUST exist for every project ADT runs in — without it the board
  # never re-syncs to GitHub after the one render above. launchd (macOS) /
  # systemd --user (Linux). Idempotent: on macOS an unchanged watcher is left alone.
  # shellcheck disable=SC1091
  source "$ADT_DIR/lib/watcher.sh"
  install_watcher "$ADT_DIR" "$name" "$PROJECT_DIR"
fi

# ── Phase 4: report ────────────────────────────────────────────────────────
echo
say "════════════════════════════════════════════"
say " ADT installed for: $name"
say "════════════════════════════════════════════"
if [[ "$DO_GITHUB" == true ]]; then
  echo "  Board (local):   .adt/kanban.html  →  right-click in your editor's file tree → Open in Browser"
  echo "  Board (GitHub):  see the clickable link in .adt/BACKLOG.md"
fi
echo "  Backlog pointer: $PROJECT_DIR/.adt/BACKLOG.md"
echo
echo "  Next — in Claude Code, from $PROJECT_DIR:"
echo "    /adt-brief             capture your first idea"
echo "    /adt-plan              turn it into a plan"
echo "    /adt-build             build it"
echo
if [[ "$DO_GITHUB" == true ]]; then
  echo "  Board sync runs automatically in the background (every 60s)."
  case "$(uname -s)" in
    Darwin) echo "    check: launchctl list | grep com.adt   ·   logs: .adt/state/adt-watch.log" ;;
    Linux)  echo "    check: systemctl --user list-timers 'adt-watch-*'   ·   logs: journalctl --user -u 'adt-watch-*'" ;;
  esac
else
  echo "  --no-github: no watcher installed. Sync manually: python3 $ADT_DIR/tools/adt_watch.py --root $PROJECT_DIR"
fi
echo
# ── Terminal tab titles (ADT-334) ────────────────────────────────────────────
# `adt-terminal-title.sh` writes an OSC 0 title, but VS Code and Cursor default
# `terminal.integrated.tabs.title` to "${process}" — the foreground BINARY name —
# and Claude Code's launcher is version-named, so an untouched tab reads e.g.
# "2.1.259" whatever the hook emits. Until ADT-334 the installer printed a notice
# telling the operator to set it themselves, and wrote nothing; the notice
# scrolled past and the hook installed and visibly did nothing.
#
# The PO's decision (2026-09-10) is to set it WITHOUT asking: the question cannot
# be answered by anyone who does not already know what the setting does. The
# compensating control is that every outcome is REPORTED — this is the one thing
# ADT writes outside the repo it is installing into, so it must be visible even
# though it is not gated.
#
# Three rules, in this order:
#   key absent            -> write "${sequence}", report the file
#   key present, any value -> leave it, report that it was left
#   file is not strict JSON -> leave it, WARN, report the path
# The last one matters more than it looks. VS Code settings are JSONC: comments
# and trailing commas are legal, and a json.dump round-trip would silently delete
# every comment the operator wrote. Refusing costs the feature on that machine and
# nothing else.
#
# This lives in adt-install.sh rather than lib/install-defaults.sh deliberately.
# Ten tests call install-defaults.sh directly and seven isolate no $HOME, so a
# write there would touch the settings of whichever machine graded the suite.
# Only two tests reach adt-install.sh and both redirect HOME already.
#
# ADT_EDITOR_CONFIG_HOME is the test seam, defaulting to $HOME — the same shape as
# ADT_TITLE_TTY in adt-terminal-title.sh. The tests point it at a throwaway tree
# so a suite run can never edit the settings of the machine grading it. It is a
# REDIRECT, not a skip: the writer always runs, so a test cannot accidentally
# grade a code path that did nothing.
_write_editor_tab_title() {
  local base="${ADT_EDITOR_CONFIG_HOME:-$HOME}"
  /usr/bin/python3 - "$base" <<'PY'
import json, os, sys

base = sys.argv[1]
KEY = "terminal.integrated.tabs.title"
VALUE = "${sequence}"

# macOS then Linux, VS Code then Cursor. A path that does not exist is skipped
# silently: not having an editor installed is not a problem to report.
candidates = [
    os.path.join(base, "Library", "Application Support", "Code", "User", "settings.json"),
    os.path.join(base, "Library", "Application Support", "Cursor", "User", "settings.json"),
    os.path.join(base, ".config", "Code", "User", "settings.json"),
    os.path.join(base, ".config", "Cursor", "User", "settings.json"),
]

for path in candidates:
    if not os.path.isfile(path):
        continue
    name = os.path.basename(os.path.dirname(os.path.dirname(path)))
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        # JSONC, or malformed. Leave it alone -- a round-trip would strip the
        # operator's comments, and a rewrite of a file we cannot parse is worse
        # than not having the feature.
        print("  [install] %s: settings.json is not strict JSON (comments or a "
              "trailing comma?) -- left untouched." % name)
        print("            Set %s to \"%s\" yourself to get ticket names in tab titles." % (KEY, VALUE))
        print("            %s" % path)
        continue
    if not isinstance(data, dict):
        print("  [install] %s: settings.json is not an object -- left untouched." % name)
        continue
    if KEY in data:
        print("  [install] %s: %s already set to %r -- left as is." % (name, KEY, data[KEY]))
        continue
    data[KEY] = VALUE
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=4)
        fh.write("\n")
    print("  [install] %s: set %s to \"%s\" so tab titles show the ticket." % (name, KEY, VALUE))
    print("            %s" % path)
PY
}
_write_editor_tab_title
echo
# ADT-203: telemetry defaults to ON, so it is disclosed HERE — at install time,
# on a surface the operator is reading — not from the background watcher, whose
# stderr goes to a log file nobody opens.
echo "  Anonymous usage stats are ON: a random install id, the ADT version, your OS,"
echo "  counts, and what your token ledger cost in dollars — priced on your machine,"
echo "  so model names and per-row detail never leave it."
echo "  Sent daily, and as one last report when you uninstall."
echo "  Never prompts, code, paths, ticket titles or repo names."
echo "  Turn them off with DO_NOT_TRACK=1, or:"
echo "    touch $PROJECT_DIR/.adt/state/telemetry-off"
echo "  What ADT does and does not send: $ADT_DIR/docs/security-posture.md"
