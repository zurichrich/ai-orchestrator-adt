#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that no playbook in `commands/` names the legacy `development-team/`
# path. `lib/` and `tools/` are out of scope, since migration code has to name
# the path it migrates from.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

LEGACY='development-team/'

echo "[test] commands/ carries no legacy $LEGACY path"

# /usr/bin/grep, because in an agent's shell `grep` may be a ugrep shim with
# different exit codes.
hits="$(/usr/bin/grep -rn -- "$LEGACY" "$REPO/commands/" 2>/dev/null || true)"

if [[ -z "$hits" ]]; then
  pass "no legacy path in any commands/*.md"
else
  fail "legacy paths still in commands/:"
  printf '%s\n' "$hits" | sed "s|$REPO/||" | sed 's/^/      /'
fi

# Positive control: the search finds the string when it is planted.
CTL="$(mktemp -d)"; trap 'rm -rf "$CTL"' EXIT
printf 'write it to %s inbox\n' "$LEGACY" > "$CTL/test_fixture.md"
if /usr/bin/grep -rq -- "$LEGACY" "$CTL/" 2>/dev/null; then
  pass "positive control: the search finds a planted legacy path"
else
  fail "positive control failed: the search cannot find a planted legacy path"
fi

# The directory must contain playbooks for "no hits" to mean anything.
n="$(find "$REPO/commands" -name '*.md' | wc -l | tr -d ' ')"
if [[ "$n" -gt 0 ]]; then
  pass "searched $n playbook(s)"
else
  fail "commands/ has no .md files"
fi

echo
if [[ $FAILS -eq 0 ]]; then
  echo "All no-legacy-paths tests passed."
else
  echo "$FAILS failure(s)."
fi
exit $(( FAILS > 0 ))
