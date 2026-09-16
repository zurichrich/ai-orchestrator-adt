#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ai-orchestrator-adt — setup.sh
# Idempotent installer. Safe to re-run after pulling team-repo updates.
#
# What it does:
#  1. Verify dependencies (gh, claude, jq, yq, curl)
#  2. Migrate away any legacy GLOBAL ~/.claude/commands/adt-*.md links (ADT-087 —
#     commands now install per-project; the old global set is removed once)
#  3. For each project in projects/: create the .adt/ skeleton, install
#     the behavioural defaults (rules/skills/agents/hooks AND command copies) into
#     .claude/, add CLAUDE.md pointer
#  4. With --init-github: offer branch protection on the project's main branch
#     (ADT-165 — the rule said main was protected; nothing ever applied it)
#  5. Print next-step instructions
#
# ADT is driven by typing the /adt-* slash commands in Claude Code — there is
# no orchestrator daemon and no launchd service.
#
# Flags:
#   --uninstall       Remove any legacy GLOBAL adt-* slash-command symlinks
#                     (per-project command copies are removed by lib/uninstall.sh)
#   --init-github     Bootstrap GitHub Issues backing store for configured
#                     projects: label taxonomy + Projects board + .adt/config,
#                     and offers to apply branch protection to the project's
#                     main branch when it has none (never applied silently —
#                     an explicit yes, or nothing). Needs a gh token with
#                     'project' scope. Idempotent.
#   --project <name>  Act on ONE project instead of every configured one
#                     (ADT-174). adt-install.sh passes this so installing into
#                     your repo installs into YOUR repo; a bare `setup.sh` is
#                     still the machine-wide reconciler for a post-`git pull`
#                     refresh of every project.

set -euo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ADT_DIR

UNINSTALL=false
INIT_GITHUB=false
ONLY_PROJECT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall)    UNINSTALL=true ;;
    --init-github)  INIT_GITHUB=true ;;
    --project)      ONLY_PROJECT="${2:-}"; shift
                    [[ -n "$ONLY_PROJECT" ]] || { echo "setup: --project needs a value" >&2; exit 2; } ;;
    --project=*)    ONLY_PROJECT="${1#--project=}"
                    [[ -n "$ONLY_PROJECT" ]] || { echo "setup: --project needs a value" >&2; exit 2; } ;;
    -h|--help)
      sed -n '1,37p' "$0"
      exit 0 ;;
  esac
  shift
done

uninstall() {
  # Legacy cleanup only (ADT-087): commands no longer install globally, but a
  # machine upgraded from the old model may still carry global adt-* links.
  # Per-project command copies + defaults are removed by lib/uninstall.sh.
  echo "[uninstall] removing any legacy global slash-command symlinks…"
  find "$HOME/.claude/commands" -maxdepth 1 -name 'adt-*.md' -type l -delete 2>/dev/null || true
  echo "[uninstall] done. Project files in .adt/ folders are NOT removed (they contain backlog data)."
}

if [[ "$UNINSTALL" == true ]]; then
  uninstall
  exit 0
fi

# --- 1. Dependency checks ---
echo "[setup] checking dependencies…"
missing=()
for tool in gh claude jq yq curl git; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    missing+=("$tool")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "[setup] MISSING: ${missing[*]}"
  echo "Install with:"
  for tool in "${missing[@]}"; do
    case "$tool" in
      gh)     echo "  brew install gh && gh auth login" ;;
      claude) echo "  see https://claude.com/claude-code (already installed if you're running this from Claude)" ;;
      jq)     echo "  brew install jq" ;;
      yq)     echo "  brew install yq" ;;
      curl)   echo "  brew install curl" ;;
      git)    echo "  brew install git" ;;
    esac
  done
  exit 1
fi
echo "[setup] all deps present."

# --- 2. Migrate away the legacy GLOBAL command install (ADT-087) ---
# Commands used to symlink into ~/.claude/commands/ (machine-wide, shared across
# every project). They now install per-project as copies (in §3, via
# install-defaults.sh). Remove any stale global adt-* links left by the old model
# — once, here — so a machine doesn't keep a global set the new model never writes.
# The probe runs ONLY when the directory exists (ADT-099). `find` on a missing
# path exits non-zero; `2>/dev/null` hides the message, not the status; pipefail
# carries it out of the pipeline and `set -e` kills the script on the assignment
# — before the `-gt 0` guard below can decide anything. A machine that never
# installed GLOBAL slash-commands has no ~/.claude/commands, so the very first
# install on a fresh machine died here, silently, having written the config and
# nothing else. No directory means no legacy links by definition.
legacy_links=0
if [[ -d "$HOME/.claude/commands" ]]; then
  legacy_links=$(find "$HOME/.claude/commands" -maxdepth 1 -name 'adt-*.md' -type l 2>/dev/null | wc -l | tr -d ' ')
fi
if [[ "$legacy_links" -gt 0 ]]; then
  echo "[setup] migrating off the old global command install: removing $legacy_links legacy ~/.claude/commands/adt-*.md link(s) (commands are now per-project)…"
  find "$HOME/.claude/commands" -maxdepth 1 -name 'adt-*.md' -type l -delete 2>/dev/null || true
fi

# --- 3. Per-project setup ---
# Configs live in two places: per-user (~/.adt/projects, written by adt-install.sh,
# kept out of git) and in-repo (projects/, the shipped example template).
# Each config with a real `path:` is its own project.
#
# ADT-135 defect 6: projects/example.yaml is a SCHEMA REFERENCE, never a real
# project — it points at /Users/you/Developer/example, a path that cannot exist
# on any machine. Globbing it made every run print
# `load-project: PROJECT_PATH=… does not exist` and then report
# `Projects configured: <real>, example`, naming a template as configured.
# The existence check below skipped it from the WORK but not from the REPORT.
# Exclude it at the source instead.
ADT_USER_PROJECTS="${ADT_USER_PROJECTS:-$HOME/.adt/projects}"
shopt -s nullglob
projects=()
for _p in "$ADT_USER_PROJECTS/"*.yaml "$ADT_DIR/projects/"*.yaml; do
  [[ -f "$_p" ]] || continue
  [[ "$(basename "$_p")" == "example.yaml" ]] && continue   # schema reference, not a project
  projects+=("$_p")
done
shopt -u nullglob

# De-dupe by basename: a user config shadows an in-repo one of the same name.
# (Plain-string set — macOS ships bash 3.2, which has no associative arrays.)
#
# Every `projects`/`deduped` expansion below uses `${a[@]+"${a[@]}"}` and not
# `"${a[@]}"`: bash 3.2 (the macOS system bash) treats the expansion of an EMPTY
# array as an unbound variable under `set -u`, which line 36 sets, so a machine
# with NO configured project died here rather than reaching the "no projects
# configured" message forty lines down. `projects` is empty on exactly that
# machine — the globs above run under `nullglob`. Same fix as the one in
# lib/install-helpers.sh (ADT-203 follow-up); tests/test_empty_array_set_u.sh
# covers the class.
# The one exception is `projects=("${scoped[@]}")` below, which sits after an
# explicit `${#scoped[@]} -eq 0 → exit 1` guard and so can never be empty.
_seen=" "
deduped=()
for p in ${projects[@]+"${projects[@]}"}; do
  b="$(basename "$p")"
  case "$_seen" in *" $b "*) continue ;; esac
  _seen="$_seen$b "
  deduped+=("$p")
done
projects=(${deduped[@]+"${deduped[@]}"})

# ── Scope to one project when asked (ADT-174) ──────────────────────────────
# adt-install.sh is the per-project front door: its header and the README both
# say "run FROM INSIDE the project you want to manage", and it already scopes
# every other phase by the name it interviewed for (load_project, init_github,
# the --root of the pull). Only this step forgot, so installing into a new repo
# silently rewrote the .claude/ layer of every OTHER configured project too —
# including tracked files in repos a parallel agent might be working in.
# A bare `setup.sh` still means "every configured project": that IS the
# reconciler, and it stays the documented manual path.
if [[ -n "$ONLY_PROJECT" ]]; then
  scoped=()
  for p in ${projects[@]+"${projects[@]}"}; do
    [[ "$(basename "$p" .yaml)" == "$ONLY_PROJECT" ]] && scoped+=("$p")
  done
  if [[ ${#scoped[@]} -eq 0 ]]; then
    echo "[setup] no config for project '$ONLY_PROJECT' in $ADT_USER_PROJECTS or $ADT_DIR/projects" >&2
    exit 1
  fi
  projects=("${scoped[@]}")
fi

if [[ ${#projects[@]} -eq 0 ]]; then
  echo "[setup] no projects configured. Run ./adt-install.sh from inside a project,"
  echo "        or drop a config in $ADT_USER_PROJECTS/<name>.yaml. Skipping per-project step."
else
  # shellcheck disable=SC1091
  source "$ADT_DIR/lib/load-project.sh"
fi

# shellcheck source=lib/adt-layer.sh
source "$ADT_DIR/lib/adt-layer.sh"

for project_yaml in ${projects[@]+"${projects[@]}"}; do
  project_name=$(basename "$project_yaml" .yaml)
  echo "[setup] project: $project_name"
  # Don't let one bad/example config abort the whole run (set -e). load_project
  # returns non-zero when the path is missing; the guard below skips cleanly.
  if ! load_project "$project_name"; then
    echo "  [setup] skipping $project_name (config not loadable — e.g. the example template)"
    continue
  fi

  if [[ ! -d "$PROJECT_PATH" ]]; then
    echo "  [setup] PROJECT_PATH=$PROJECT_PATH does not exist — skipping"
    continue
  fi

  # ADT-066 defect 4: skip a project whose backlog repo no longer resolves. A
  # leftover config for a deleted repo (e.g. a stale <name>-backlog.yaml) was
  # getting re-set-up on every run. Only probe when we're already touching the
  # network (--init-github) — a local-only setup must never false-prune offline,
  # and the bootstrap below would fail loudly anyway. Warn + skip; don't delete
  # the config (the operator may be mid-rename or temporarily offline).
  if [[ "$INIT_GITHUB" == true ]]; then
    project_repo="$(yq -r '.github.repo // ""' "$project_yaml" 2>/dev/null || true)"
    if [[ -n "$project_repo" ]] && ! gh repo view "$project_repo" >/dev/null 2>&1; then
      echo "  [setup] WARN: backlog repo '$project_repo' does not resolve (deleted/renamed/no access) — skipping $project_name." >&2
      echo "          If this project is gone, remove $project_yaml." >&2
      continue
    fi
  fi

  # .adt/ holds the project's planning docs (decisions, retros) +
  # ADT runtime state. The BACKLOG itself is GitHub Issues + a local cache at
  # ~/.adt/<project>/cache/ — NOT an in-repo .md tree. (Pre-the cache-first migration installs
  # scaffolded an in-repo backlog tree; that tree is retired.
  # See docs/backlog-sync.md.) Backlog bootstrap is `setup.sh --init-github`.
  # ADT-301: .adt/ is the project marker AND the state home, so it is created on
  # every install - not only under --init-github, which is the only thing that
  # used to write .adt/config.yaml. Without it a --no-github install has no
  # marker at all and every hook exits 0.
  base="$PROJECT_PATH/.adt"
  mkdir -p "$base/state" "$base/inbox"

  # The shared local cache lives outside the git checkout (per-machine, never
  # committed). Create it so the renderer + sync have a home from first run.
  cache="${PROJECT_CACHE_DIR:-$HOME/.adt/$project_name/cache}"
  mkdir -p "$cache"

  # ADT-334: write .adt/config.yaml on EVERY install, not only under
  # --init-github. It is the only file carrying `id_prefix:`, and both
  # adt-usage-log.sh and adt-terminal-title.sh read it and fall back to "TIX"
  # when it is absent. On a --no-github install that fallback means no prompt
  # ever matches the project's real ticket ids, so usage-log latches nothing,
  # the token ledger never attributes a turn to a ticket, and the terminal tab
  # never names one. Nothing errors; the whole chain is silently inert.
  #
  # `project_number` is the one value that needs GitHub (_find_or_create_project),
  # so it is written EMPTY here. That is a state the readers already handle:
  # adt_sync.py no-ops all board work without one, and adt_watch.py guards on it.
  # init_github later calls this same function with the real number and
  # overwrites the file, which is already its contract — the generated header
  # says the file is rewritten on each install (ADT-135 defect 3).
  #
  # ADT-301 created .adt/state and .adt/inbox on every install for the same
  # reason and stopped short of the config.
  # shellcheck disable=SC1091
  source "$ADT_DIR/lib/github-bootstrap.sh"
  cfg_repo=$(yq -r '.github.repo // ""' "$project_yaml" 2>/dev/null || true)
  cfg_owner=$(yq -r '.github.owner // ""' "$project_yaml" 2>/dev/null || true)
  [[ -z "$cfg_owner" && -n "$cfg_repo" ]] && cfg_owner="${cfg_repo%%/*}"
  cfg_prefix=$(yq -r '.kanban.id_prefix // "TICKET"' "$project_yaml" 2>/dev/null || true)
  cfg_backlog=$(yq -r '.backlog_root // ""' "$project_yaml" 2>/dev/null || true)
  cfg_main=$(yq -r '.main_branch // "main"' "$project_yaml" 2>/dev/null || true)
  cfg_docsrc=$(yq -r '.commands_doc_src // ""' "$project_yaml" 2>/dev/null || true)
  cfg_stages=()
  if [[ "$(yq -r '.github.stages | type' "$project_yaml" 2>/dev/null || true)" == "!!seq" ]]; then
    while IFS= read -r s; do cfg_stages+=("$s"); done \
      < <(yq -r '.github.stages[]' "$project_yaml")
  fi
  # Empty array + `set -u` kills bash 3.2 on expansion, not passes zero args
  # (ADT-299) — the same guard init_github carries.
  if [[ ${#cfg_stages[@]} -eq 0 ]]; then
    cfg_stages=("${ADT_DEFAULT_STAGES[@]}")
  fi
  # An existing project_number must survive: this runs on every install,
  # including one on a project whose board was bootstrapped long ago, and
  # re-writing it empty would silently switch board sync off.
  cfg_pnum=""
  if [[ -f "$base/config.yaml" ]]; then
    cfg_pnum=$(grep -E '^project_number:' "$base/config.yaml" 2>/dev/null \
               | head -1 | sed -E 's/^project_number:[[:space:]]*//; s/[[:space:]]*$//' || true)
  fi
  _write_adt_config "$PROJECT_PATH" "$cfg_repo" "$cfg_owner" "$cfg_prefix" \
                    "$cfg_backlog" "$cfg_pnum" "$project_name" "$cache" \
                    "$cfg_docsrc" "$cfg_main" "${cfg_stages[@]}"

  # Install the behavioural defaults (rules/skills/agents/hooks) into .claude/.
  # That layer is committed and shared, so adt_layer_apply first compares the
  # copy committed on origin with this machine's ADT clone (ADT-384): a first
  # install writes into the working tree as before, a matching one writes
  # nothing, a newer clone writes onto an upgrade branch, and an older clone is
  # refused. A bare run reconciles every project, so a refusal skips only this
  # one.
  layer_rc=0
  adt_layer_apply "$ADT_DIR" "$PROJECT_PATH" "$cfg_main" || layer_rc=$?
  if [[ $layer_rc -eq 3 ]]; then
    echo "  [setup] skipping $project_name: its committed ADT layer is newer than this ADT clone" >&2
    continue
  elif [[ $layer_rc -ne 0 ]]; then
    exit "$layer_rc"
  fi

  # decisions.md (curated ADRs). ADT does NOT own this file's content — it is
  # project knowledge, appended to by /adt-decide and read by nothing that
  # requires a skeleton. So we only seed a convenience skeleton for the DEFAULT
  # in-repo location, and ONLY when the file is absent (never reseed/overwrite —
  # that would mask a deletion with an empty stub on reinstall). A project that
  # points decision_log: somewhere other than the docs/ default owns the
  # file entirely; ADT seeds nothing and create-on-first-use via /adt-decide,
  # exactly as retros/ already work.
  decision_log="${PROJECT_DECISION_LOG:-docs/decisions.md}"
  decision_seed="$PROJECT_PATH/$decision_log"
  # Only on a first install, and never over a decisions.md that origin already
  # has (ADT-384). A checkout behind origin would get an untracked file that
  # blocks its pull; a machine joining a committed install would get one
  # nobody commits.
  if [[ "$decision_log" == "docs/decisions.md" && ! -f "$decision_seed" \
        && ( "$ADT_LAYER_STATE" == none || "$ADT_LAYER_STATE" == self ) ]] \
     && ! git -C "$PROJECT_PATH" cat-file -e "origin/$cfg_main:$decision_log" 2>/dev/null; then
    mkdir -p "$(dirname "$decision_seed")"
    cat > "$decision_seed" <<EOF
# $project_name — decision log

Curated ADRs. Records the *why* behind non-trivial choices.
Numbering is global, not per-feature.

EOF
  fi

  # BACKLOG.md — the AUTHORITATIVE pointer to the install-time facts (ADT-73).
  # Rewritten on EVERY (re)install, not seeded-once: the old `! -f` guard meant a
  # reinstall never refreshed it, so when ADT was reinstalled under a new launchd
  # label the consumer doc drifted stale and misdirected the agent (a consumer,
  # 2026-06-27). The facts (launchd label, log path, cache root, source-vs-render
  # layout) are computed here from the same helpers that create the watcher, so
  # they can't disagree with what's actually installed. Consumers' CLAUDE.md
  # POINTS at this file rather than restating these facts — one copy, kept current.
  #
  # Source the watcher helpers for the label (com.adt.<slug>.watch) — the very
  # fact that drifted; read it from its definition, never hardcode it.
  # shellcheck source=lib/watcher.sh
  source "$ADT_DIR/lib/watcher.sh"
  watcher_label="$(_watcher_label "$project_name")"
  watcher_log="$PROJECT_PATH/.adt/state/adt-watch.log"
  cat > "$base/BACKLOG.md" <<EOF
# Backlog

The backlog is **GitHub Issues** + a local cache synced by \`adt watch\`. How
the model works lives in the product doc: \`$ADT_DIR/docs/backlog-sync.md\`.

<!-- ADT:facts:start -->
<!-- Managed by ADT (adt-073). Rewritten on every (re)install from the live
     install values — do not hand-edit; your changes will be overwritten.
     Put your own notes OUTSIDE this block. -->
- **Local cache (working surface):** \`$cache\`
- **Ticket SOURCES live at:** \`$cache/<type>/<stage>/<slug>.md\` — \`<type>\` is
  derived per-issue (\`bugs\`/\`enhancements\`/\`tasks\`), defaulting to \`tasks\`
  when an Issue has no resolvable type.
- **Render OUTPUT (never synced):** \`$cache/kanban.html\` — every ticket's detail
  is inlined into the board; click a card to open it. Author tickets in the
  \`<type>/<stage>/\` source tree, never in a render output.
- **Sync watcher (launchd/systemd label):** \`$watcher_label\`
- **Watcher log:** \`$watcher_log\`
- **Bootstrap a backlog repo + board:** \`$ADT_DIR/setup.sh --init-github\`
- **Decisions:** \`$decision_log\` · **Retros:** \`docs/retros/\`
<!-- ADT:facts:end -->

Driven by the /adt-* slash commands in Claude Code. Command reference:
\`$ADT_DIR/docs/references.md\`.
EOF

  echo "  [setup] .adt/ ready (cache: $cache)"
  echo "  [setup] next: ./setup.sh --init-github   # bootstrap the Issues backlog repo + board"

  # GitHub Issues backing store (opt-in via --init-github).
  if [[ "$INIT_GITHUB" == true ]]; then
    # shellcheck disable=SC1091
    source "$ADT_DIR/lib/github-bootstrap.sh"
    init_github "$project_name"
  fi
done

# --- 4. Final summary ---
echo ""
echo "================================================"
echo "ai-orchestrator-adt setup complete"
echo "================================================"
echo "Projects configured:"
for p in ${projects[@]+"${projects[@]}"}; do
  echo "  - $(basename "$p" .yaml)"
done
echo ""
echo "ADT is driven by the /adt-* slash commands inside Claude Code."
echo "See the references: docs/references.md (or the board's 📖 link)."
echo ""
echo "Uninstall:"
echo "  ./setup.sh --uninstall"
