#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that a hook command is wired once (ADT-354). The fragment shipped
# adt-test-run-guard.sh twice in one block, and the merge in
# lib/install-defaults.sh copied both, so the guard ran twice on every tool
# call. Checks the fragment itself, a fresh install, a settings.json that
# already holds the duplicate, and that a second merge changes nothing.
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
GUARD='${CLAUDE_PROJECT_DIR}/.claude/hooks/adt-test-run-guard.sh'

# Prints the largest number of times any one command appears in one block.
max_repeat() {
  python3 - "$1" <<'PY'
import json, sys
from collections import Counter
worst = 0
for blocks in json.load(open(sys.argv[1])).get("hooks", {}).values():
    for b in blocks:
        n = Counter(h.get("command") for h in b.get("hooks", []))
        worst = max([worst] + list(n.values()))
print(worst)
PY
}
count_guard() { grep -cF "$GUARD" "$1"; }

newproj() { mkdir -p "$1" && git -C "$1" init -q .; }
install() { bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$1" >"$TMP/install.log" 2>&1; }

echo "[test] the fragment lists no command twice in one block"
n="$(max_repeat "$ADT_DIR/defaults/settings.hooks.json")"
[[ "$n" == "1" ]] && pass "each command appears once per block" \
  || fail "a command appears $n times in one block of settings.hooks.json"

echo "[test] a fresh install wires the test-run guard once"
P="$TMP/fresh"; newproj "$P"
install "$P" || { fail "install-defaults.sh exited non-zero"; tail -20 "$TMP/install.log"; }
c="$(count_guard "$P/.claude/settings.json")"
[[ "$c" == "1" ]] && pass "one test-run-guard entry" || fail "fresh install wired it $c times"

echo "[test] a settings.json that already holds the duplicate is repaired"
P="$TMP/dup"; newproj "$P"; mkdir -p "$P/.claude"
python3 - "$P/.claude/settings.json" "$GUARD" <<'PY'
import json, sys
g = sys.argv[2]
h = lambda c: {"type": "command", "command": c}
json.dump({"hooks": {"PreToolUse": [{"matcher": "Bash|Write|Edit", "hooks": [
    h("${CLAUDE_PROJECT_DIR}/.claude/hooks/adt-done-guard.sh"),
    h("${CLAUDE_PROJECT_DIR}/.claude/hooks/adt-deferral-guard.sh"),
    h(g), h(g)]}]}}, open(sys.argv[1], "w"), indent=2)
PY
[[ "$(count_guard "$P/.claude/settings.json")" == "2" ]] || fail "fixture did not seed the duplicate"
install "$P" || fail "install-defaults.sh exited non-zero on the seeded project"
c="$(count_guard "$P/.claude/settings.json")"
[[ "$c" == "1" ]] && pass "duplicate collapsed to one entry" || fail "still $c entries after the merge"
n="$(max_repeat "$P/.claude/settings.json")"
[[ "$n" == "1" ]] && pass "no command repeats in any block" || fail "a command still appears $n times in one block"

echo "[test] a second merge changes nothing"
before="$(shasum "$P/.claude/settings.json" | cut -d' ' -f1)"
install "$P" || fail "second install exited non-zero"
after="$(shasum "$P/.claude/settings.json" | cut -d' ' -f1)"
[[ "$before" == "$after" ]] && pass "settings.json unchanged by a re-run" || fail "a re-run changed settings.json"

echo
[[ $FAILS -eq 0 ]] && { echo "test_hooks_merge_dedupe: PASS"; exit 0; } \
  || { echo "test_hooks_merge_dedupe: FAIL ($FAILS)"; exit 1; }
