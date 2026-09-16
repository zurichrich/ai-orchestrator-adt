#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Idempotent GitHub bootstrap for an ADT consumer project: label taxonomy,
# a Projects-v2 board with one Status option per stage, and the .adt/config
# the commands read. Portable — every value comes from projects/<name>.yaml,
# nothing about any one consumer is hardcoded here (team CLAUDE.md rule #1).

set -euo pipefail

# --- Defaults (overridable by the project's github: config block) ----------
# Arrays, not space-strings: `for x in $var` does not word-split under zsh,
# so the taxonomies must be real arrays to iterate portably. The seven stage
# lanes are the ADT lifecycle defaults; a project may override via
# github.stages in its yaml.
# The gh-scope check lives in lib/install-helpers.sh so this file and
# adt-install.sh share ONE implementation (ADT-135 defect 7: both carried the
# same substring bug, and the ticket only spotted the installer's copy).
# shellcheck disable=SC1091
source "${ADT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}/lib/install-helpers.sh"

ADT_DEFAULT_STAGES=(ideas planned building qa blocked ready-to-release done)
ADT_DEFAULT_PRIORITIES=(P0 P1 P2 P3)
ADT_DEFAULT_TRACKS=(fast standard full)
ADT_DEFAULT_GATES=(ui security trading-path)

# Colours (GitHub label hex, no leading #). Stable so re-runs don't churn.
_label_colour() {
  case "$1" in
    P0) echo "b60205" ;; P1) echo "d93f0b" ;; P2) echo "fbca04" ;; P3) echo "0e8a16" ;;
    track) echo "5319e7" ;; stage) echo "1d76db" ;; gate) echo "c5def5" ;;
    *) echo "ededed" ;;
  esac
}

# --- gh preflight ----------------------------------------------------------
_gh_check_scopes() {
  # project scope is required for board creation; repo for labels.
  if ! gh auth status 2>&1 | grep -q "Token scopes"; then
    echo "[init-github] ERROR: gh not authenticated. Run: gh auth login" >&2
    return 1
  fi
  # ADT-135: a SUBSTRING match here is satisfied by `read:project`, so a
  # read-only token passed this gate, the board was found and verified, and
  # every later `gh project item-add` failed. Match the scope exactly.
  if ! adt_scopes_have_project "$(gh auth status 2>&1 | grep "Token scopes" || true)"; then
    echo "[init-github] ERROR: gh token missing the 'project' scope." >&2
    echo "  (read:project is NOT enough — the board needs write access.)" >&2
    echo "  Run: gh auth refresh -h github.com -s project" >&2
    return 1
  fi
}

# --- Labels (idempotent: --force upserts colour/description) ---------------
_ensure_label() {
  local repo="$1" name="$2" colour="$3" desc="$4"
  gh label create "$name" --repo "$repo" --color "$colour" --description "$desc" --force >/dev/null
  echo "  label: $name"
}

# bash 3.2-safe (macOS ships 3.2; a portable product must not need namerefs).
# Stages are passed as trailing positional args: _bootstrap_labels <repo> <stage…>
_bootstrap_labels() {
  local repo="$1"; shift
  echo "[init-github] labels on $repo …"
  local p s t g
  for p in "${ADT_DEFAULT_PRIORITIES[@]}"; do
    _ensure_label "$repo" "$p" "$(_label_colour "$p")" "Priority $p"
  done
  for s in "$@"; do
    _ensure_label "$repo" "stage:$s" "$(_label_colour stage)" "Lifecycle stage: $s"
  done
  for t in "${ADT_DEFAULT_TRACKS[@]}"; do
    _ensure_label "$repo" "track:$t" "$(_label_colour track)" "Impact tier: $t"
  done
  for g in "${ADT_DEFAULT_GATES[@]}"; do
    _ensure_label "$repo" "gate:$g" "$(_label_colour gate)" "Review gate: $g"
  done
}

# --- Project board (idempotent: find-or-create by title) -------------------
# Echoes the project number on stdout.
_find_or_create_project() {
  local owner="$1" title="$2"
  local num
  num=$(gh project list --owner "$owner" --format json \
        | jq -r --arg t "$title" '.projects[] | select(.title==$t) | .number' | head -1)
  if [[ -n "$num" ]]; then
    echo "  board: '$title' exists (#$num)" >&2
    echo "$num"; return 0
  fi
  num=$(gh project create --owner "$owner" --title "$title" --format json | jq -r '.number')
  echo "  board: created '$title' (#$num)" >&2
  echo "$num"
}

# Ensure the board's Status single-select has exactly our stage options.
# Projects v2 ships a built-in "Status" field (Todo/In Progress/Done), so on a
# fresh board the field always exists — we must EDIT its options, not create a
# new field. gh 2.x can't edit options in place, so use the GraphQL mutation
# updateProjectV2SingleSelectField (it replaces the whole option set). It's a
# full replace, so it's idempotent: re-running with the same stages is a no-op
# in effect. Item assignments to options that survive are preserved by id;
# options removed lose their assignments (acceptable on bootstrap — there are
# no items yet). Stages passed as trailing positional args (bash 3.2-safe).
_ensure_status_field() {
  local owner="$1" number="$2"; shift 2
  local stages=("$@")
  # Resolve the Status field node id + its current options.
  local field_node existing_csv want_csv
  local flist; flist=$(gh project field-list "$number" --owner "$owner" --format json)
  field_node=$(echo "$flist" | jq -r '.fields[] | select(.name=="Status") | .id' | head -1)
  existing_csv=$(echo "$flist" | jq -r '.fields[] | select(.name=="Status") | (.options // []) | map(.name) | join(",")')
  want_csv=$(IFS=,; echo "${stages[*]}")

  if [[ "$existing_csv" == "$want_csv" ]]; then
    echo "  field: Status already has stages [$want_csv] — no change"
    return 0
  fi

  # Build the options array for the mutation (name + colour + empty desc).
  local opts_json
  opts_json=$(printf '%s\n' "${stages[@]}" \
    | jq -R '{name: ., color: "GRAY", description: ""}' | jq -s '.')

  if [[ -z "$field_node" || "$field_node" == "null" ]]; then
    # No Status field at all (rare) — create via gh, simplest path.
    local opts; opts=$(IFS=,; echo "${stages[*]}")
    gh project field-create "$number" --owner "$owner" --name "Status" \
       --data-type "SINGLE_SELECT" --single-select-options "$opts" >/dev/null
    echo "  field: Status created with stages [$opts]"
    return 0
  fi

  # gh's -F/-f flags coerce to scalars only — they cannot pass a JSON array as
  # a variable (it arrives as a string). Build the full {query, variables}
  # document with jq and feed it via --input so $opts is a real array.
  local payload
  payload=$(jq -n --arg field "$field_node" --argjson opts "$opts_json" '{
    query: "mutation($field:ID!, $opts:[ProjectV2SingleSelectFieldOptionInput!]!) { updateProjectV2Field(input:{fieldId:$field, singleSelectOptions:$opts}) { projectV2Field { ... on ProjectV2SingleSelectField { id } } } }",
    variables: { field: $field, opts: $opts }
  }')
  echo "$payload" | gh api graphql --input - >/dev/null
  echo "  field: Status options set to stages [$want_csv]"
}

# --- Branch protection on the main branch (ADT-165) ------------------------
# Rule §B ("Branch protection on `main`") supplied this payload and nothing ever
# ran it. Now setup offers it, and applies it only on an explicit yes.
#
# NOTE this file runs under `set -euo pipefail` and the NORMAL "unprotected"
# case is a non-zero `gh api` exit. So the read is captured with `|| rc=$?`
# rather than a bare assignment, and every path below returns 0: discovering
# that your main is unprotected must report, never abort the install (success
# criterion 8 — a project that declines still installs cleanly).
_read_protection() {  # <repo> <branch> ; echoes protected|unprotected|unknown
  local repo="$1" branch="$2" out rc=0
  out="$(gh api "repos/$repo/branches/$branch/protection" 2>/dev/null)" || rc=$?
  adt_protection_state "$rc" "$out"
}

# §B's payload, from defaults/rules/multi-agent-git-workflow.md — the PORTABLE
# source, which is what a consumer installs. The load-bearing pair is
# allow_force_pushes + allow_deletions false (a stranded or clobbered main
# becomes impossible); review count 0 + enforce_admins false keep a solo
# maintainer able to merge their own PRs.
#
# `required_status_checks: null` is deliberate and must stay null here. The ADT
# repo's OWN main additionally requires a `cla` status check, and this installer
# must not copy that: a required context is enforced against a workflow that
# resolves from the base ref, so registering `cla` on a repo that has no such
# workflow blocks every PR on a check that can never report. A consumer's repo
# is exactly that repo. Project-specific gates are the project's to add after
# its workflow is on main; ADT ships the floor, not one project's ceiling.
_protection_payload() {
  cat <<'JSON'
{
  "required_status_checks": null,
  "enforce_admins": false,
  "required_pull_request_reviews": { "required_approving_review_count": 0 },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
}

# Decides; delegates the doing to _run_protection_action. The split exists so
# the prompt path is reachable by a test: interactivity is `[[ -t 0 ]]`, and a
# hermetic test has no tty, so a single function would leave the branch that
# actually writes to GitHub ungradeable — the "presence is not evidence" failure
# this ticket is about.
_ensure_branch_protection() {  # <yaml> <repo> <branch>
  local yaml="$1" repo="$2" branch="$3"
  local declined=false interactive=false state action

  [[ "$(yq -r '.github.branch_protection // ""' "$yaml" 2>/dev/null)" == "declined" ]] \
    && declined=true
  # Decided HERE, before adt_ask is reachable. adt_ask refuses at EOF with a
  # non-zero return, and under `set -e` that return would abort init_github —
  # so the non-interactive path must never reach it. This is the codebase's own
  # interactivity test at the decision level (adt-install.sh, adt_scope_action).
  [[ -t 0 ]] && interactive=true

  state="$(_read_protection "$repo" "$branch")"
  action="$(adt_protection_action "$state" "$declined" "$interactive")"
  _run_protection_action "$yaml" "$repo" "$branch" "$state" "$action"
}

_run_protection_action() {  # <yaml> <repo> <branch> <state> <action>
  local yaml="$1" repo="$2" branch="$3" state="$4" action="$5"
  local answer rc=0

  case "$action" in
    noop)
      echo "  protection: $repo@$branch is already protected — no change made."
      return 0 ;;
    skip-declined)
      echo "  protection: $repo@$branch is UNPROTECTED; you declined before — not asking again."
      echo "              (to be asked again, remove github.branch_protection from $yaml)"
      return 0 ;;
    report-only)
      if [[ "$state" == unknown ]]; then
        echo "  protection: could not read $repo@$branch — no admin rights on the repo," >&2
        echo "              or no network. Nothing was changed." >&2
      else
        echo "  protection: $repo@$branch is UNPROTECTED, and this run is not interactive," >&2
        echo "              so nothing was changed. Re-run ./setup.sh --init-github from a" >&2
        echo "              terminal to be asked." >&2
      fi
      return 0 ;;
  esac

  cat <<EOF

  ------------------------------------------------------------------
  $repo@$branch has NO branch protection.

  ADT's own workflow rule says agents never commit to $branch. Right now
  nothing enforces that — the rule is doing the enforcing.

  Enabling it (server-side; works without CI) means:
    * changes land via pull request — no direct pushes to $branch
    * no force-pushes — history cannot be rewritten under you
    * no branch deletion

  What it costs you: nothing as a solo maintainer. Required approving
  reviews stays 0 and admins are not subject to it, so you can still
  merge your own PRs.
  ------------------------------------------------------------------
EOF
  # assume_yes=true: at EOF we want adt_ask to hand back the default quietly
  # rather than print its red refusal. Either way a non-zero return means the
  # question was never actually answered — which is a "no", but NOT a recorded
  # decline: nobody chose anything, so nothing is remembered.
  answer="$(adt_ask "  Enable branch protection on $repo@$branch? (y/n)" "n" true)" || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "  protection: no answer (stdin closed) — treated as no. Nothing changed," >&2
    echo "              and nothing recorded; you will be asked again." >&2
    return 0
  fi

  case "$answer" in
    y|Y|yes|YES|Yes) ;;
    *)
      BP="declined" yq -i '.github.branch_protection = strenv(BP)' "$yaml"
      echo "  protection: declined — recorded in $yaml; you will not be asked again."
      return 0 ;;
  esac

  # The read above happened BEFORE the human answered, and nothing bounds how
  # long that took. A PUT REPLACES the entire ruleset, so acting on a stale
  # "unprotected" could silently weaken protection somebody else added in the
  # meantime. Re-read and re-assert immediately before writing.
  if [[ "$(_read_protection "$repo" "$branch")" != unprotected ]]; then
    echo "  protection: $repo@$branch became protected while you were deciding —"
    echo "              leaving the existing rules alone."
    return 0
  fi

  if _protection_payload \
     | gh api -X PUT "repos/$repo/branches/$branch/protection" --input - >/dev/null 2>&1; then
    echo "  protection: ENABLED on $repo@$branch (PR required; no force-push; no deletion)"
  else
    echo "  protection: FAILED to enable on $repo@$branch — you need admin on the repo." >&2
    echo "              Nothing was changed." >&2
  fi
  return 0
}

# --- .adt/config in the consumer project -----------------------------------
# Written into the PROJECT, not the team repo. Carries the consumer-specific
# wiring the commands need: repo, owner, id-prefix, backlog path, project #,
# and the stage→label map. No secrets (§D5).
_write_adt_config() {
  local project_path="$1" repo="$2" owner="$3" id_prefix="$4" \
        backlog_root="$5" project_number="$6" project_name="$7" cache="$8" \
        commands_doc_src="$9"; shift 9
  local main_branch="$1"; shift
  local stages=("$@")
  local dir="$project_path/.adt"
  mkdir -p "$dir"
  local src_cfg="${ADT_USER_PROJECTS:-$HOME/.adt/projects}/${project_name}.yaml"
  # worktree_guard_paths (ADT-359): adt-worktree-guard.sh reads it from THIS
  # file, but this file is rewritten on every install and deleted on uninstall,
  # so a value typed here would be lost. The project config is where it is set
  # and survives (install merges hand-written keys; uninstall keeps a .bak).
  local wgp=""
  if [[ -f "$src_cfg" ]] && command -v yq >/dev/null 2>&1; then
    wgp="$(yq -r '.worktree_guard_paths // ""' "$src_cfg" 2>/dev/null || true)"
  fi
  {
    echo "# GENERATED - do not edit; source: $src_cfg"
    echo "#"
    echo "# ADT consumer config — written by every install. No secrets."
    echo "# Every value here is DERIVED from the config named above and is"
    echo "# rewritten on each install (ADT-135 defect 3). id_prefix in"
    echo "# particular has its authoritative definition there, not here:"
    echo "# editing this copy alone drifts the two apart silently."
    echo "project: $project_name"
    echo "repo: $repo"
    echo "owner: $owner"
    echo "id_prefix: $id_prefix"
    echo "backlog_root: $backlog_root"
    echo "project_number: $project_number"
    # cache_dir pins where the sync + renderer read/write tickets. Without it
    # they fall back to ~/.adt/<repo-basename>/cache, which can diverge from the
    # project name. Always emit it so every tool agrees.
    echo "cache_dir: $cache"
    # main_branch (ADT-165): the renderer reads THIS file via adt_sync.
    # load_config, not the per-user yaml where install records it — same reason
    # cache_dir and commands_doc_src are carried across. Without it the board's
    # protection badge would read a hardcoded "main", so a project on `master`
    # would query a branch that does not exist, get a 404, and silently render
    # no badge at all: the check would look installed and report nothing.
    echo "main_branch: $main_branch"
    # commands_doc_src (ADT-066 defect 3, follow-up): the renderer reads THIS
    # file via adt_sync.load_config — NOT the per-user ~/.adt/projects/<name>.yaml
    # where install records it. So emit it here too, or the de-vendored
    # (external-clone) layout drops the 📖 references page + link. Only when
    # set, so an empty value never shadows the renderer's own path fallbacks.
    [[ -n "$commands_doc_src" ]] && echo "commands_doc_src: $commands_doc_src"
    # Quoted, because a pattern such as `*.py` at the start of a plain YAML
    # scalar reads as an alias. Only when set, so the guard stays off by default.
    [[ -n "$wgp" ]] && echo "worktree_guard_paths: \"$wgp\""
    echo "stages:"
    local s
    for s in "${stages[@]}"; do
      echo "  - name: $s"
      echo "    label: stage:$s"
    done
  } > "$dir/config.yaml"
  echo "  config: $dir/config.yaml"
}

# --- Entry point -----------------------------------------------------------
# Reads PROJECT_* (from load-project.sh) + the github: block of the yaml.
# Usage: init_github <project_name>
init_github() {
  local name="${1:?usage: init_github <project_name>}"
  # Resolve config: per-user dir (adt-install.sh) first, then in-repo. resolve_
  # project_config comes from load-project.sh, which setup.sh has sourced.
  local yaml
  if command -v resolve_project_config >/dev/null 2>&1; then
    yaml="$(resolve_project_config "$name")" \
      || { echo "[init-github] no config for '$name'" >&2; return 1; }
  else
    yaml="${ADT_USER_PROJECTS:-$HOME/.adt/projects}/${name}.yaml"
    [[ -f "$yaml" ]] || yaml="$ADT_DIR/projects/${name}.yaml"
    [[ -f "$yaml" ]] || { echo "[init-github] no config: $yaml" >&2; return 1; }
  fi

  _gh_check_scopes || return 1

  # Consumer config (github: block; fall back to sensible derivations).
  local repo owner id_prefix backlog_root board_title cache
  repo=$(yq -r '.github.repo // ""' "$yaml")
  owner=$(yq -r '.github.owner // ""' "$yaml")
  id_prefix=$(yq -r '.kanban.id_prefix // "TICKET"' "$yaml")
  backlog_root=$(yq -r '.backlog_root // ""' "$yaml")
  board_title=$(yq -r ".github.board_title // \"${name} backlog\"" "$yaml")
  # Cache dir: explicit cache_dir: in the YAML, else ~/.adt/<name>/cache. Tilde
  # expanded here so the .adt/config.yaml carries an absolute path.
  cache=$(yq -r ".cache_dir // \"$HOME/.adt/${name}/cache\"" "$yaml")
  cache="${cache/#\~/$HOME}"
  # commands_doc_src (ADT-066 defect 3): install records the absolute references
  # path in the per-user yaml under kanban.commands_doc_src. The renderer reads
  # .adt/config.yaml (load_config), not the per-user yaml — so carry the value
  # across here. Empty stays empty (the renderer keeps its own path fallbacks).
  local commands_doc_src
  commands_doc_src=$(yq -r '.kanban.commands_doc_src // ""' "$yaml")
  commands_doc_src="${commands_doc_src/#\~/$HOME}"

  # Stages: read the github.stages array if present, else the ADT default.
  local stages=()
  if [[ "$(yq -r '.github.stages | type' "$yaml")" == "!!seq" ]]; then
    while IFS= read -r s; do stages+=("$s"); done \
      < <(yq -r '.github.stages[]' "$yaml")
  fi
  # An EMPTY `github.stages: []` reads as a !!seq with no entries, so the branch
  # above leaves the array empty — and a board with no lanes is not a board. Fall
  # back to the defaults, which is what a missing key already does. This also
  # keeps `stages` non-empty for the three call sites below: bash 3.2 (the macOS
  # system bash) errors on the expansion of an EMPTY array under `set -u`, which
  # setup.sh sets before sourcing this file, so `"${stages[@]}"` would kill the
  # run rather than pass zero arguments (ADT-299).
  if [[ ${#stages[@]} -eq 0 ]]; then
    stages=("${ADT_DEFAULT_STAGES[@]}")
  fi

  if [[ -z "$repo" ]]; then
    echo "[init-github] ERROR: github.repo missing in $yaml (e.g. owner/name)" >&2
    return 1
  fi
  [[ -z "$owner" ]] && owner="${repo%%/*}"

  echo "[init-github] $name → repo=$repo owner=$owner board='$board_title'"
  _bootstrap_labels "$repo" "${stages[@]}"

  local pnum
  pnum=$(_find_or_create_project "$owner" "$board_title")
  _ensure_status_field "$owner" "$pnum" "${stages[@]}"
  gh project link "$pnum" --owner "$owner" --repo "$repo" >/dev/null 2>&1 || true

  # ADT-165: offer branch protection on the project's main branch. Reads
  # main_branch from the same $yaml every other value here comes from, so a
  # project on `master` is handled without a second source of truth.
  local main_branch
  main_branch=$(yq -r '.main_branch // "main"' "$yaml")
  _ensure_branch_protection "$yaml" "$repo" "$main_branch"

  _write_adt_config "$PROJECT_PATH" "$repo" "$owner" "$id_prefix" \
                    "$backlog_root" "$pnum" "$name" "$cache" \
                    "$commands_doc_src" "$main_branch" "${stages[@]}"

  local board_url="https://github.com/users/$owner/projects/$pnum"
  PROJECT_CACHE_DIR="$cache" \
    _write_backlog_pointer "$PROJECT_PATH" "$name" "$repo" "$board_url" "$id_prefix"
  echo "[init-github] done. Board: $board_url"
}

# --- BACKLOG.md: the clickable pointer to where the backlog actually lives ---
# Rewritten (not appended) every init so the links always reflect the current
# board/repo. This is the file a cloner opens to find their kanban.
_write_backlog_pointer() {
  local project_path="$1" name="$2" repo="$3" board_url="$4" id_prefix="$5"
  local cache="${PROJECT_CACHE_DIR:-$HOME/.adt/$name/cache}"
  local base="$project_path/.adt"
  mkdir -p "$base"
  cat > "$base/BACKLOG.md" <<EOF
# Backlog

The backlog is **GitHub Issues** + a fast local cache that \`adt watch\` keeps
in lockstep. Ticket ids derive from the issue number (\`${id_prefix}-N\`). How
the model works: \`$ADT_DIR/docs/backlog-sync.md\`.

- **Board (local):** \`.adt/kanban.html\` — right-click it in your editor's file tree → **Open in Browser** (refreshed every \`adt watch\` pass).
- **Board (GitHub Project):** $board_url
- **Backlog issues:** https://github.com/$repo/issues
- **Local cache (working surface):** \`$cache\`
- **Decisions / retros:** \`docs/{decisions.md,retros/}\`

Driven by the /adt-* slash commands in Claude Code. Command reference:
\`$ADT_DIR/docs/references.md\`.
EOF
  echo "  backlog: $base/BACKLOG.md (board + kanban links)"
}
