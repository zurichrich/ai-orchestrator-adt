#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests the install-time facts block setup.sh writes into BACKLOG.md:
#   1. The watcher label comes from _watcher_label, the helper that installs
#      the watcher.
#   2. The facts block is rewritten on every install.
#   3. The facts block states that tickets/ is render output and never synced.
#
# Runs offline, with no gh and no real install.
set -euo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

# 1. _watcher_label produces the installed label form.
source "$ADT_DIR/lib/watcher.sh"
label="$(_watcher_label "agent-dev-team")"
[ "$label" = "com.adt.agent-dev-team.watch" ] \
  && pass "label from _watcher_label matches the installed form" \
  || fail "label mismatch: got '$label'"

# It slugs a name with spaces and capitals.
label2="$(_watcher_label "My Cool Project")"
[ "$label2" = "com.adt.my-cool-project.watch" ] \
  && pass "label slugs a spaced/mixed-case name" \
  || fail "slug mismatch: got '$label2'"

# 2. setup.sh has no `! -f "$base/BACKLOG.md"` guard, so it rewrites every run.
if grep -qE '!\s*-f\s*"\$base/BACKLOG\.md"' "$ADT_DIR/setup.sh"; then
  fail "setup.sh still guards BACKLOG.md with '! -f' — it won't refresh on reinstall"
else
  pass "BACKLOG.md is (re)written every install (no '! -f' seed-once guard)"
fi

# 3. The facts block and the render-output rule are in the writer.
if grep -q "ADT:facts:start" "$ADT_DIR/setup.sh" \
   && grep -q "Render OUTPUT (never synced)" "$ADT_DIR/setup.sh" \
   && grep -q "watcher_label" "$ADT_DIR/setup.sh"; then
  pass "facts block states label + render-output rule"
else
  fail "facts block missing label or render-output rule"
fi

# 4. setup.sh sources watcher.sh for the label.
grep -q 'source "$ADT_DIR/lib/watcher.sh"' "$ADT_DIR/setup.sh" \
  && pass "setup.sh sources watcher.sh for the label" \
  || fail "setup.sh does not source watcher.sh — label would be hardcoded/drift"

echo ""
if [ "$FAILS" -eq 0 ]; then echo "All setup-backlog-facts tests passed."; else echo "$FAILS failed."; exit 1; fi
