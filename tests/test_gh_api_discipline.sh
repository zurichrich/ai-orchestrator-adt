#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Checks that no *.sh or *.md file under commands/, defaults/, lib/ or
# .claude/hooks/ calls `gh pr view`, `gh issue view`, `gh pr create` or
# `gh pr merge`. Those use GraphQL; calls should go through `gh api repos/...`.
# Creating and listing issues is allowed.
# tools/*.py is not checked here; `_BANNED_PORCELAIN` in tools/adt_sync.py
# refuses those calls at runtime.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADT_DIR="${1:-$(cd "$HERE/.." && pwd)}"

# The shell spelling: `gh pr view`, `gh issue view`, `gh pr create|merge`.
SHELL_PATTERN='gh (pr|issue) view|gh pr (create|merge)'

shell_hits=""
for d in commands defaults lib .claude/hooks; do
  [ -d "$ADT_DIR/$d" ] || continue
  hits="$(grep -rnE --include='*.sh' --include='*.md' "$SHELL_PATTERN" \
    "$ADT_DIR/$d" 2>/dev/null || true)"
  [ -n "$hits" ] && shell_hits="$shell_hits$hits"$'\n'
done

matches="$(printf '%s' "$shell_hits" | sed '/^$/d')"

if [ -n "$matches" ]; then
  echo "GraphQL gh porcelain found; use gh api repos/... instead:"
  printf '%s\n' "$matches"
  echo
  echo "test_gh_api_discipline: FAIL"
  exit 1
fi

echo "test_gh_api_discipline: ok (commands/ defaults/ lib/ .claude/hooks/ clean;"
echo "  tools/*.py is enforced at the runner — see _BANNED_PORCELAIN in adt_sync.py)"
