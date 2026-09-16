#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Load a project config and export its values as PROJECT_* env vars.
# Usage: source load-project.sh <project_name>
#        Then: echo $PROJECT_PATH, $PROJECT_BACKLOG_ROOT, etc.

set -euo pipefail

# ADT_DIR defaults to this script's repo root (lib/..); override with the env var.
# BASH_SOURCE may be unset under an interactive `source`; guard it for set -u.
ADT_DIR="${ADT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$PWD/lib/x}")/.." && pwd)}"

# Per-user project configs live OUTSIDE the team checkout so the repo stays
# clean for open-source (no user's absolute paths or emails in git). The
# installer writes here; the repo ships only projects/example.yaml.
ADT_USER_PROJECTS="${ADT_USER_PROJECTS:-$HOME/.adt/projects}"

# Resolve a project config path: user dir first, then the team repo (example/
# dogfooding configs). Echoes the path on stdout; returns 1 if neither exists.
resolve_project_config() {
  local name="${1:?usage: resolve_project_config <name>}"
  local user_cfg="$ADT_USER_PROJECTS/${name}.yaml"
  local repo_cfg="$ADT_DIR/projects/${name}.yaml"
  if [[ -f "$user_cfg" ]]; then echo "$user_cfg"; return 0; fi
  if [[ -f "$repo_cfg" ]]; then echo "$repo_cfg"; return 0; fi
  return 1
}

load_project() {
  local name="${1:?usage: load_project <name>}"
  local file
  if ! file="$(resolve_project_config "$name")"; then
    echo "load-project: no config for '$name' in $ADT_USER_PROJECTS or $ADT_DIR/projects" >&2
    return 1
  fi

  if ! command -v yq >/dev/null 2>&1; then
    echo "load-project: yq not installed (brew install yq)" >&2
    return 1
  fi

  export PROJECT_NAME="$(yq -r '.name' "$file")"
  export PROJECT_PATH="$(yq -r '.path' "$file")"
  export PROJECT_MAIN_BRANCH="$(yq -r '.main_branch' "$file")"
  export PROJECT_REMOTE="$(yq -r '.remote' "$file")"
  export PROJECT_CLAUDE_MD="$(yq -r '.claude_md' "$file")"
  export PROJECT_BACKLOG_ROOT="$(yq -r '.backlog_root' "$file")"
  export PROJECT_DECISION_LOG="$(yq -r '.decision_log' "$file")"
  export PROJECT_MERGER="$(yq -r '.merger' "$file")"
  # specialism removed (ADT-82): the R&D specialism mechanism was dropped with
  # the role flatten. test_commands removed earlier: QA/build playbooks discover
  # the test runner from the repo (pytest/npm test/go test/Makefile), not config.
  export PROJECT_BUILD_VERSION_FILE="$(yq -r '.build_version_file' "$file")"
  export PROJECT_NOTIFY_EMAIL_TO="$(yq -r '.notifications.email.to' "$file")"
  export PROJECT_NOTIFY_EMAIL_FROM="$(yq -r '.notifications.email.from' "$file")"
  export PROJECT_NOTIFY_EMAIL_KEY_ENV="$(yq -r '.notifications.email.api_key_env' "$file")"
  # Optional safety: never send to these addresses (newline-separated). yq emits
  # empty string when the key is absent, which the guard below treats as "no blocklist".
  export PROJECT_NOTIFY_EMAIL_BLOCKLIST="$(yq -r '.notifications.email.blocklist[]?' "$file" 2>/dev/null)"
  export PROJECT_CONFIG_FILE="$file"

  if [[ ! -d "$PROJECT_PATH" ]]; then
    echo "load-project: PROJECT_PATH=$PROJECT_PATH does not exist" >&2
    return 1
  fi
}

# When sourced, do NOT auto-load. Callers must invoke load_project explicitly.
# (Earlier versions auto-loaded from $1, but that picks up unrelated argv args
# when sourced from scripts that already parsed flags.)
