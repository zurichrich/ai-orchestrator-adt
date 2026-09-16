#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests adt-phrase-linter.sh: it flags banned phrases, and a done-claim about
# the board with no tool call that opened it in the same turn.
# Run:  bash defaults/hooks/tests/test_phrase_linter.sh
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LINT="$HOOK_DIR/adt-phrase-linter.sh"
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Drive the hook against a transcript file; capture stdout.
run() { printf '{"transcript_path":"%s"}' "$1" | bash "$LINT" 2>/dev/null; }

# Helpers to append JSONL records (one message per line).
user_text()  { printf '{"message":{"role":"user","content":[{"type":"text","text":"%s"}]}}\n' "$1" >> "$2"; }
asst_text()  { printf '{"message":{"role":"assistant","content":[{"type":"text","text":"%s"}]}}\n' "$1" >> "$2"; }
asst_tool()  { printf '{"message":{"role":"assistant","content":[{"type":"tool_use","name":"%s","input":%s}]}}\n' "$1" "$2" >> "$3"; }

echo "== adt-phrase-linter.sh =="

# 1. A banned phrase is flagged.
T="$TMP/banned.jsonl"; : > "$T"
user_text "do the thing" "$T"
asst_text "honestly this was harder than expected" "$T"
out="$(run "$T")"
echo "$out" | grep -qi "banned recovery-narration" \
  && ok "banned phrase still flagged" || bad "banned phrase not flagged, got: $out"

# 2. Done-claim about the board with no tool call on it in the turn → flagged.
T="$TMP/unbacked.jsonl"; : > "$T"
user_text "is it done" "$T"
asst_text "Yes — the references link is now on the board and the link works." "$T"
out="$(run "$T")"
echo "$out" | grep -qi "no tool call in this turn" \
  && ok "unbacked done-claim about the board → flagged" || bad "unbacked claim not flagged, got: $out"

# 3. Same claim, but the turn read the .adt/ board → not flagged.
T="$TMP/backed.jsonl"; : > "$T"
user_text "is it done" "$T"
asst_tool "Read" '{"file_path":"/proj/.adt/kanban.html"}' "$T"
asst_text "Yes — the references link is now on the board and the link works." "$T"
out="$(run "$T")"
! echo "$out" | grep -qi "no tool call in this turn" \
  && ok "done-claim backed by a .adt/ artifact open → not flagged" \
  || bad "backed claim wrongly flagged, got: $out"

# 4. A bare 'done' that names no board or file → not flagged.
T="$TMP/bareword.jsonl"; : > "$T"
user_text "status" "$T"
asst_text "The build step is done; moving on." "$T"
out="$(run "$T")"
[ -z "$out" ] && ok "bare 'done' with no artifact claim → no flag" \
  || bad "bare 'done' wrongly flagged, got: $out"

# 5. Opening the board in a previous turn does not back this turn's claim.
T="$TMP/priorturn.jsonl"; : > "$T"
user_text "look at the board" "$T"
asst_tool "Read" '{"file_path":"/proj/.adt/kanban.html"}' "$T"
asst_text "looked" "$T"
user_text "is it done" "$T"
asst_text "Yes — confirmed the anchor is present on the board." "$T"
out="$(run "$T")"
echo "$out" | grep -qi "no tool call in this turn" \
  && ok "artifact opened in a previous turn doesn't back this turn's claim → flagged" \
  || bad "prior-turn touch wrongly satisfied the claim, got: $out"

# 6. Missing transcript → exit 0, no output.
printf '{"transcript_path":"/nope/missing.jsonl"}' | bash "$LINT" >/dev/null 2>&1
[ "$?" = 0 ] && ok "missing transcript → exit 0" || bad "non-zero exit on missing transcript"

echo ""
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
