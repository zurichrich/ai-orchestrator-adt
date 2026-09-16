#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests adt-test-run-guard.sh: the PreToolUse hook that denies a full-suite
# test run unless the human asked for one in the current turn.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$HERE/../adt-test-run-guard.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fails=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fails=$((fails+1)); }

_json() { /usr/bin/python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$1"; }

reset_tr() { : > "$TMP/t.jsonl"; }
say()     { printf '{"type":"user","message":{"role":"user","content":[{"type":"text","text":%s}]}}\n' "$(_json "$1")" >> "$TMP/t.jsonl"; }
machine() { printf '{"type":"user","message":{"role":"user","content":[{"type":"text","text":%s}]}}\n' "$(_json "<command-name>/adt-qa-run</command-name> $1")" >> "$TMP/t.jsonl"; }

# ALLOW = the hook prints nothing. DENY = it prints a deny decision.
verdict() {
  local out
  out="$(printf '{"tool_name":"Bash","tool_input":{"command":%s},"cwd":"%s","transcript_path":"%s"}' \
        "$(_json "$1")" "$TMP" "${2:-$TMP/t.jsonl}" | bash "$HOOK")"
  case "$out" in "") echo ALLOW ;; *'"deny"'*) echo DENY ;; *) echo "UNEXPECTED:$out" ;; esac
}
expect() { local got; got="$(verdict "$2" "${3:-}")"; [ "$got" = "$1" ] && pass "$1  $2" || fail "want $1 got $got  —  $2"; }

echo "[test] an unauthorised full-suite run is denied"
reset_tr; say "fix the parser bug"
expect DENY "python3 -m pytest tools/tests/ tests/ -q"
expect DENY "bash tests/run-shell-suite.sh"
expect DENY "python3 -m pytest"
expect DENY "cd /repo && python3 -m pytest tools/tests/ tests/"

echo "[test] a targeted run is never gated"
expect ALLOW "python3 -m pytest tools/tests/test_track_tier.py -q"
expect ALLOW "python3 -m pytest -k egress"
expect ALLOW "python3 -m pytest tools/tests/test_adt_dod.py::test_lane_slicing"
expect ALLOW "bash defaults/hooks/tests/test_done_guard.sh"

echo "[test] a suite command inside a quoted heredoc body is not run"
# ALLOW: the suite command only appears in a heredoc body written to a file.
expect ALLOW "python3 - <<'HEREDOC_ALLOW'
open('t.md','w').write('python3 -m pytest tools/tests/ tests/ -q')
HEREDOC_ALLOW"
# DENY: the same command fed to bash through a heredoc, which does run it.
expect DENY "bash <<'HEREDOC_DENY'
python3 -m pytest tools/tests/ tests/ -q
HEREDOC_DENY"

echo "[test] a non-test command is untouched"
expect ALLOW "ls -la"
expect ALLOW "git status"

echo "[test] a human authorises in their own words or with the token"
reset_tr; say "fix the parser"; say "run the tests"
expect ALLOW "python3 -m pytest tools/tests/ tests/ -q"
reset_tr; say "adt run tests"
expect ALLOW "bash tests/run-shell-suite.sh"
reset_tr; say "adt tests"
expect ALLOW "bash tests/run-shell-suite.sh"
# the token holds across later messages; plain words only count in the latest.
reset_tr; say "ADT Run Tests"; say "now just fix the typo"
expect ALLOW "bash tests/run-shell-suite.sh"
reset_tr; say "run the full suite please"
expect ALLOW "python3 -m pytest tools/tests/ tests/ -q"

echo "[test] only the current turn can authorise"
reset_tr; say "run the tests"; say "now just fix the typo"
expect DENY "python3 -m pytest tools/tests/ tests/ -q"

echo "[test] a slash command's role:user text cannot authorise"
reset_tr; say "fix the parser"; machine "run the tests"
expect DENY "python3 -m pytest tools/tests/ tests/ -q"

echo "[test] a missing or broken transcript allows the run"
expect ALLOW "python3 -m pytest tools/tests/ tests/ -q" "$TMP/does-not-exist.jsonl"
printf 'not json at all\n' > "$TMP/broken.jsonl"
expect ALLOW "python3 -m pytest tools/tests/ tests/ -q" "$TMP/broken.jsonl"

echo
[ "$fails" -eq 0 ] && { echo "All test-run-guard tests passed."; exit 0; }
echo "test_test_run_guard: FAIL ($fails)"; exit 1
