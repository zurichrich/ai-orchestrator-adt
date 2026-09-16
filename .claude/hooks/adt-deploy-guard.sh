#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT default. PreToolUse hook template, off by default: warns before a deploy
# or a server start. It logs to .adt/state/deploy-guard.log and prints a
# systemMessage warning. It never denies; switch it to deny only once it has
# proved reliable.
#
# It is not wired in settings.hooks.json. To use it, wire it and set the
# trigger patterns, which are project-specific: set ADT_DEPLOY_PATTERN /
# ADT_SERVER_PATTERN or edit the case patterns in the installed copy. The
# installer keeps a copy you have edited and does not overwrite it.
set -euo pipefail

input="$(cat)"
cmd="$(printf '%s' "$input" | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null || true)"
[ -z "$cmd" ] && exit 0

# Patterns come from project config; these are illustrative defaults.
DEPLOY_PAT="${ADT_DEPLOY_PATTERN:-deploy.sh}"
SERVER_PAT="${ADT_SERVER_PATTERN:-}"

warn() {
  log="${CLAUDE_PROJECT_DIR:-.}/.adt/state/deploy-guard.log"; mkdir -p "$(dirname "$log")" 2>/dev/null || true
  printf '%s  %s :: %s\n' "$(date -u +%FT%TZ)" "$1" "$cmd" >> "$log" 2>/dev/null || true
  echo "{\"systemMessage\":\"⚠ deploy-guard: $2 (warn-only)\"}"
}

case "$cmd" in
  *"$DEPLOY_PAT"*)
    warn deploy "this deploys to production. Confirm tests green, version bumped, and you are on the canonical main checkout — not a worktree."
    ;;
esac
if [ -n "$SERVER_PAT" ]; then
  case "$cmd" in
    *"$SERVER_PAT"*)
      warn server "starting the server/scheduler. Foreground only, production host only. Never via background task."
      ;;
  esac
fi
exit 0

# adt-bundle: v0.1.0
