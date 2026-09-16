#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Common git operations used by the ADT commands.
# Source this file. PROJECT_PATH and PROJECT_MAIN_BRANCH must be set.

set -euo pipefail

# Pull latest main in the active project (no fast-forward conflict killing).
project_pull_main() {
  (
    cd "$PROJECT_PATH"
    git fetch "$PROJECT_REMOTE" "$PROJECT_MAIN_BRANCH" -q
    git checkout "$PROJECT_MAIN_BRANCH" -q
    git pull --ff-only -q
  )
}

# Pull latest in the team repo too, so we always run the freshest playbooks.
team_pull_main() {
  (
    cd "$ADT_DIR"
    git pull --ff-only -q 2>/dev/null || true
  )
}

# Print backlog state for the active project.
print_backlog_state() {
  local root="$PROJECT_PATH/$PROJECT_BACKLOG_ROOT"
  echo "Backlog state for $PROJECT_NAME:"
  for state in ideas planned building qa ready-to-release blocked done; do
    local count
    count=$(find "$root/$state" -maxdepth 1 -name '*.md' -not -name '.gitkeep' 2>/dev/null | wc -l | tr -d ' ')
    local oldest=""
    if [[ "$count" -gt 0 ]]; then
      oldest=$(ls -1t "$root/$state"/*.md 2>/dev/null | tail -1 | xargs -I{} basename {} .md)
    fi
    printf "  %-18s %3s items  %s\n" "$state:" "$count" "$oldest"
  done
}

# Determine oldest backlog item in a state (lowest mtime). Empty if none.
oldest_in_state() {
  local state="${1:?state required}"
  local root="$PROJECT_PATH/$PROJECT_BACKLOG_ROOT/$state"
  [[ -d "$root" ]] || { echo ""; return 0; }
  ls -1tr "$root"/*.md 2>/dev/null | grep -v '\.gitkeep$' | head -1 || true
}

# Read frontmatter field from a backlog file.
backlog_field() {
  local file="${1:?file required}"
  local field="${2:?field required}"
  awk -v f="$field" '
    /^---$/ { fm = !fm; next }
    fm && $1 == f":" { sub("^[^:]+:[[:space:]]*", ""); print; exit }
  ' "$file"
}

# Check whether the orchestrator is paused for this project.
project_paused() {
  local lock="$PROJECT_PATH/$PROJECT_BACKLOG_ROOT/../.orchestrator.lock"
  # The lock lives under .adt/
  lock="$PROJECT_PATH/.adt/.orchestrator.lock"
  [[ -f "$lock" ]] || [[ -f "$HOME/.agent-dev-team.paused" ]]
}
