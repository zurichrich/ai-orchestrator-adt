#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests adt-subagent-cost.sh, the SubagentStop hook that writes a cost-ledger
# row for each subagent transcript, alongside adt-token-log.sh on the same session.
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUB_HOOK="$HOOK_DIR/adt-subagent-cost.sh"
TOKEN_HOOK="$HOOK_DIR/adt-token-log.sh"

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

newproj() {
  local d; d="$(mktemp -d)"
  git -C "$d" init -q
  mkdir -p "$d/.adt/state"
  printf '%s' "$d"
}

# A subagent transcript: sidechain rows carrying the parent's sessionId.
write_subagent_transcript() {
  local path="$1" parent_session="$2" agent="$3"; shift 3
  : > "$path"
  for pair in "$@"; do
    local inp="${pair%%:*}" rest="${pair#*:}"
    local out="${rest%%:*}" rest2="${rest#*:}"
    local cread="${rest2%%:*}" cc="${rest2##*:}"
    printf '{"isSidechain":true,"sessionId":"%s","attributionAgent":"%s","type":"assistant","message":{"role":"assistant","model":"claude-opus-5","usage":{"input_tokens":%s,"output_tokens":%s,"cache_read_input_tokens":%s,"cache_creation_input_tokens":%s}}}\n' \
      "$parent_session" "$agent" "$inp" "$out" "$cread" "$cc" >> "$path"
  done
}

# A main-session transcript: no isSidechain marker.
write_main_transcript() {
  local path="$1" session="$2"; shift 2
  : > "$path"
  for pair in "$@"; do
    local inp="${pair%%:*}" out="${pair##*:}"
    printf '{"sessionId":"%s","type":"assistant","message":{"role":"assistant","model":"claude-opus-5","usage":{"input_tokens":%s,"output_tokens":%s,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}\n' \
      "$session" "$inp" "$out" >> "$path"
  done
}

run_sub_hook() {
  printf '{"transcript_path":"%s","cwd":"%s","session_id":"%s"}' "$1" "$2" "$3" | bash "$SUB_HOOK"
}
run_token_hook() {
  printf '{"transcript_path":"%s","cwd":"%s","session_id":"%s"}' "$1" "$2" "$3" | bash "$TOKEN_HOOK"
}

echo "== the subagent cost hook =="

# --- 1. one row, billed to the parent session's ticket ---------------------
P="$(newproj)"; S="$P/.adt/state"
mkdir -p "$S/current-tix.d"; printf 'ADT-224\n' > "$S/current-tix.d/parent-sess"
T="$P/agent-aaa.jsonl"
write_subagent_transcript "$T" "parent-sess" "adt-plan-quality-reviewer" "10:20:300:40"
run_sub_hook "$T" "$P" "parent-sess" >/dev/null 2>&1
LED="$S/cost-ledger.log"
rows="$(wc -l < "$LED" 2>/dev/null | tr -d ' ')"
[ "$rows" = "1" ] && ok "a subagent transcript writes one ledger row" || bad "expected 1 row, got ${rows:-0}"
[ "$(cut -f2 "$LED" 2>/dev/null)" = "ADT-224" ] \
  && ok "billed to the ticket the parent session is bound to" \
  || bad "expected ADT-224, got '$(cut -f2 "$LED" 2>/dev/null)'"

# --- 2. the row is `measured` and keeps the token split ------------------
tier="$(awk -F'\t' '{print $11}' "$LED")"
[ "$tier" = "measured" ] && ok "the row is tier=measured" || bad "expected measured, got '$tier'"
split="$(awk -F'\t' '{print $3"/"$4"/"$8"/"$9}' "$LED")"
[ "$split" = "10/20/300/40" ] \
  && ok "the input/output/cache-read/cache-create split is kept" \
  || bad "expected 10/20/300/40, got '$split'"

# --- 3. column 12 names the subagent type ---------------------------------
col12="$(awk -F'\t' '{print $12}' "$LED")"
[ "$col12" = "subagent:adt-plan-quality-reviewer" ] \
  && ok "column 12 carries subagent:<type>" \
  || bad "expected subagent:adt-plan-quality-reviewer, got '$col12'"

# --- 4. both hooks on one session, in both orders --------------------------
# Each hook must bill its own transcript in full, whichever runs first.
for order in "sub-first" "parent-first"; do
  PC="$(newproj)"; SC="$PC/.adt/state"
  mkdir -p "$SC/current-tix.d"; printf 'ADT-224\n' > "$SC/current-tix.d/shared-sess"
  MAIN="$PC/main.jsonl";  write_main_transcript "$MAIN" "shared-sess" "100:200"
  SUB="$PC/agent-bbb.jsonl"; write_subagent_transcript "$SUB" "shared-sess" "adt-design-reviewer" "5:6:7:8"
  if [ "$order" = "sub-first" ]; then
    run_sub_hook "$SUB" "$PC" "shared-sess" >/dev/null 2>&1
    run_token_hook "$MAIN" "$PC" "shared-sess" >/dev/null 2>&1
  else
    run_token_hook "$MAIN" "$PC" "shared-sess" >/dev/null 2>&1
    run_sub_hook "$SUB" "$PC" "shared-sess" >/dev/null 2>&1
  fi
  LC="$SC/cost-ledger.log"
  n="$(wc -l < "$LC" 2>/dev/null | tr -d ' ')"
  subrow="$(grep -c 'subagent:adt-design-reviewer' "$LC" 2>/dev/null || echo 0)"
  mainrow="$(awk -F'\t' '$3=="100"' "$LC" 2>/dev/null | wc -l | tr -d ' ')"
  if [ "$n" = "2" ] && [ "$subrow" = "1" ] && [ "$mainrow" = "1" ]; then
    ok "cursor_collision ($order): both hooks bill their own transcript in full"
  else
    bad "cursor_collision ($order): expected 2 rows (1 sub, 1 main), got n=$n sub=$subrow main=$mainrow"
  fi
done

# --- 5. the cursor is not keyed on the session -----------------------------
# A subagent transcript carries the parent's session id, so a session-keyed
# cursor would share the parent's position and bill nothing.
if grep -qE 'cursor_dir/\$session\b' "$SUB_HOOK"; then
  bad "the cursor is keyed on the session"
else
  ok "the cursor is not keyed on the session"
fi
if grep -q 'subagent-cursor' "$SUB_HOOK"; then
  ok "the cursor lives in its own directory, not token-cursor/"
else
  bad "the cursor must have its own directory, not token-cursor/"
fi

# --- 6. idempotence: a re-run appends nothing ------------------------------
run_sub_hook "$T" "$P" "parent-sess" >/dev/null 2>&1
rows2="$(wc -l < "$LED" | tr -d ' ')"
[ "$rows2" = "1" ] && ok "re-run on an unchanged transcript appends nothing" || bad "expected 1 row, got $rows2"

# A grown transcript bills only the new messages.
write_subagent_transcript "$T" "parent-sess" "adt-plan-quality-reviewer" "10:20:300:40" "1:2:3:4"
run_sub_hook "$T" "$P" "parent-sess" >/dev/null 2>&1
rows3="$(wc -l < "$LED" | tr -d ' ')"
newsplit="$(tail -1 "$LED" | awk -F'\t' '{print $3"/"$4"/"$8"/"$9}')"
[ "$rows3" = "2" ] && [ "$newsplit" = "1/2/3/4" ] \
  && ok "a grown transcript bills only the new messages" \
  || bad "expected a second row of 1/2/3/4, got rows=$rows3 split='$newsplit'"

# --- 7. a main-session transcript writes nothing ---------------------------
# adt-token-log.sh already bills that transcript.
PM="$(newproj)"; SM="$PM/.adt/state"
MT="$PM/main-only.jsonl"; write_main_transcript "$MT" "solo-sess" "50:60"
run_sub_hook "$MT" "$PM" "solo-sess" >/dev/null 2>&1
[ ! -s "$SM/cost-ledger.log" ] \
  && ok "a non-sidechain transcript writes no row (no double-billing)" \
  || bad "billed a main-session transcript: $(cat "$SM/cost-ledger.log")"

# --- 7b. given the parent path, it bills the transcripts in subagents/ -----
# SubagentStop passes the parent session's transcript_path.
PP="$(newproj)"; SP="$PP/.adt/state"
mkdir -p "$SP/current-tix.d"; printf 'ADT-224\n' > "$SP/current-tix.d/live-sess"
PARENT="$PP/live-sess.jsonl"
write_main_transcript "$PARENT" "live-sess" "100:200"
mkdir -p "$PP/live-sess/subagents"
write_subagent_transcript "$PP/live-sess/subagents/agent-one.jsonl" "live-sess" "adt-security-reviewer" "1:2:3:4"
write_subagent_transcript "$PP/live-sess/subagents/agent-two.jsonl" "live-sess" "adt-design-reviewer" "5:6:7:8"
run_sub_hook "$PARENT" "$PP" "live-sess" >/dev/null 2>&1
LP="$SP/cost-ledger.log"
np="$(wc -l < "$LP" 2>/dev/null | tr -d ' ')"
one="$(grep -c 'subagent:adt-security-reviewer' "$LP" 2>/dev/null || true)"
two="$(grep -c 'subagent:adt-design-reviewer' "$LP" 2>/dev/null || true)"
if [ "$np" = "2" ] && [ "$one" = "1" ] && [ "$two" = "1" ]; then
  ok "handed the parent path, both subagent transcripts are found and billed once each"
else
  bad "parent path: expected 2 rows (1 per subagent), got n=$np security=$one design=$two"
fi
# Firing again bills neither transcript a second time.
run_sub_hook "$PARENT" "$PP" "live-sess" >/dev/null 2>&1
np2="$(wc -l < "$LP" | tr -d ' ')"
[ "$np2" = "2" ] && ok "a second SubagentStop re-bills neither transcript" \
  || bad "expected 2 rows after a second fire, got $np2"

# --- 8. negative control: a primed cursor writes no row --------------------
PN="$(newproj)"; SN="$PN/.adt/state"
TN="$PN/agent-ccc.jsonl"
write_subagent_transcript "$TN" "no-bind-sess" "general-purpose" "1:1:1:1"
printf '9999\n' > "$SN/subagent-cursor-primed" 2>/dev/null || true
mkdir -p "$SN/subagent-cursor"; printf '9999\n' > "$SN/subagent-cursor/agent-ccc.jsonl"
run_sub_hook "$TN" "$PN" "no-bind-sess" >/dev/null 2>&1
[ ! -s "$SN/cost-ledger.log" ] \
  && ok "negative control: a primed cursor suppresses the row" \
  || bad "negative control failed: a fully-billed transcript still wrote a row"

# --- 9. fail-open ----------------------------------------------------------
printf '{}' | bash "$SUB_HOOK" >/dev/null 2>&1
[ $? -eq 0 ] && ok "empty payload exits 0" || bad "empty payload must fail open"
printf 'not json' | bash "$SUB_HOOK" >/dev/null 2>&1
[ $? -eq 0 ] && ok "malformed payload exits 0" || bad "malformed payload must fail open"

# --- 10. column 13 carries the install id ---------------------------------
PI="$(newproj)"; PIS="$PI/.adt/state"
mkdir -p "$PIS/current-tix.d"; printf 'ADT-254\n' > "$PIS/current-tix.d/idsess"
printf 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\n' > "$PIS/install-id"
TI="$PI/agent-id.jsonl"
write_subagent_transcript "$TI" "idsess" "adt-security-reviewer" "1:2:3:4"
run_sub_hook "$TI" "$PI" "idsess" >/dev/null 2>&1
c13="$(awk -F'\t' '{print $13}' "$PIS/cost-ledger.log")"
[ "$c13" = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" ] \
  && ok "column 13 carries the install id" \
  || bad "column 13: expected the install id, got '$c13'"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
