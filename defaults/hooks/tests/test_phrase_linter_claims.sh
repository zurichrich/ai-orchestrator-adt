#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests adt-phrase-linter.sh check (c): a claim that something passed is flagged
# unless the command it names ran in the same turn.
# Run:  bash defaults/hooks/tests/test_phrase_linter_claims.sh
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LINT="$HOOK_DIR/adt-phrase-linter.sh"
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

run() { printf '{"transcript_path":"%s"}' "$1" | bash "$LINT" 2>/dev/null; }
jrec() { /usr/bin/python3 -c '
import json,sys
role,kind,payload=sys.argv[1],sys.argv[2],sys.argv[3]
if kind=="text": c=[{"type":"text","text":payload}]
else: c=[{"type":"tool_use","name":"Bash","input":{"command":payload}}]
print(json.dumps({"message":{"role":role,"content":c}}))' "$1" "$2" "$3" >> "$4"; }
user_text() { jrec user text "$1" "$2"; }
asst_text() { jrec assistant text "$1" "$2"; }
asst_bash() { jrec assistant bash "$1" "$2"; }
flagged() { echo "$1" | grep -qi "a claim that something passed"; }

echo "== adt-phrase-linter.sh — check (c), unbacked pass-claims =="

# 1. A named suite claimed passing with nothing run → flagged.
T="$TMP/c1.jsonl"; : > "$T"
user_text "did it work" "$T"
asst_text "I ran it and pytest tools/tests/ passes cleanly." "$T"
out="$(run "$T")"
flagged "$out" && ok "named suite claimed passing, nothing run → flagged" \
                || bad "unbacked pass-claim not flagged, got: $out"

# 2. Same claim, with the matching command run → not flagged.
T="$TMP/c2.jsonl"; : > "$T"
user_text "did it work" "$T"
asst_bash "python3 -m pytest tools/tests/ -q" "$T"
asst_text "pytest tools/tests/ passes cleanly." "$T"
out="$(run "$T")"
flagged "$out" && bad "backed pass-claim wrongly flagged, got: $out" \
                || ok "claim backed by the matching command → not flagged"

# 3. A Bash call that is not the named command does not back the claim.
T="$TMP/c3.jsonl"; : > "$T"
user_text "did it work" "$T"
asst_bash "git status" "$T"
asst_text "pytest tools/tests/ passes cleanly." "$T"
out="$(run "$T")"
flagged "$out" && ok "unrelated command does not back a named claim → flagged" \
                || bad "an unrelated command satisfied the claim, got: $out"

# 4. A claim that names no command, with nothing run → flagged.
T="$TMP/c4.jsonl"; : > "$T"
user_text "status" "$T"
asst_text "All checks are green." "$T"
out="$(run "$T")"
flagged "$out" && ok "claim naming no command, nothing run → flagged" \
                || bad "claim naming no command not flagged, got: $out"

# 5. A claim that names no command is accepted if any command ran.
T="$TMP/c5.jsonl"; : > "$T"
user_text "status" "$T"
asst_bash "make check" "$T"
asst_text "All checks are green." "$T"
out="$(run "$T")"
flagged "$out" && bad "claim with a command run wrongly flagged, got: $out" \
                || ok "claim naming no command, with a command run → accepted"

# 6. No pass-claim → no flag.
T="$TMP/c6.jsonl"; : > "$T"
user_text "status" "$T"
asst_text "Wave A is committed; moving to wave B." "$T"
out="$(run "$T")"
flagged "$out" && bad "fired on a turn with no pass-claim, got: $out" \
                || ok "no pass-claim → no flag"

# 7. A command run in a previous turn does not back this turn's claim.
T="$TMP/c7.jsonl"; : > "$T"
user_text "run the tests" "$T"
asst_bash "python3 -m pytest tools/tests/ -q" "$T"
asst_text "ran" "$T"
user_text "are they still green" "$T"
asst_text "Yes, pytest tools/tests/ passes." "$T"
out="$(run "$T")"
flagged "$out" && ok "prior-turn command does not back this turn's claim → flagged" \
                || bad "a prior-turn run satisfied this turn's claim, got: $out"

echo ""
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
