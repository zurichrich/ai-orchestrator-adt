#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests the sweep in adt-token-log.sh: on one session's Stop, it bills the
# unbilled tail of other sessions' transcripts that have gone cold.
# Run:  bash defaults/hooks/tests/test_adt_token_log_sweep.sh
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKEN_HOOK="$HOOK_DIR/adt-token-log.sh"
PASS=0 FAIL=0

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

newproj() {
  local d; d="$(mktemp -d)"
  git -C "$d" init -q
  mkdir -p "$d/.adt/state/current-tix.d"
  mkdir -p "$d/.adt/state/token-cursor"
  printf '%s' "$d"
}

# One transcript, N assistant messages of (in:out).
write_transcript() {
  local path="$1"; shift
  : > "$path"
  for pair in "$@"; do
    printf '{"type":"assistant","message":{"role":"assistant","model":"claude-opus-5","usage":{"input_tokens":%s,"output_tokens":%s}}}\n' \
      "${pair%%:*}" "${pair##*:}" >> "$path"
  done
}

run_hook() {  # <transcript> <cwd> <session>
  printf '{"transcript_path":"%s","cwd":"%s","session_id":"%s"}' "$1" "$2" "$3" \
    | bash "$TOKEN_HOOK"
}

setup() {
  PROJ="$(newproj)"
  STATE="$PROJ/.adt/state"
  LEDGER="$STATE/cost-ledger.log"
  TDIR="$(mktemp -d)"          # stands in for ~/.claude/projects/<project>/
  LIVE="$TDIR/live-session.jsonl"
  DEAD="$TDIR/dead-session.jsonl"
  write_transcript "$LIVE" 10:20
  write_transcript "$DEAD" 5:7 5:7 5:7
  printf 'ADT-111\n' > "$STATE/current-tix.d/live-session"
  printf 'ADT-999\n' > "$STATE/current-tix.d/dead-session"
  # The dead session billed 1 of its 3 messages. Its cursor is older than the
  # transcript, as it is when messages are appended after the last Stop.
  printf '1\n' > "$STATE/token-cursor/dead-session"
  touch -t 201901010000 "$STATE/token-cursor/dead-session"
}

count_rows() {  # <tix>
  [ -f "$LEDGER" ] || { printf '0'; return; }
  awk -F'\t' -v t="$1" '$2==t {n++} END {print n+0}' "$LEDGER"
}

printf '\n== the sweep bills a cold session, and only a cold one ==\n'

# --- 1. a cold session is swept and billed to its own ticket --------------
setup
touch -t 202001010000 "$DEAD"          # 30+ minutes cold
run_hook "$LIVE" "$PROJ" "live-session"
[ "$(count_rows ADT-999)" -gt 0 ] \
  && ok "a cold session's unbilled tail reaches the ledger" \
  || bad "a cold session's unbilled tail reaches the ledger"
[ "$(count_rows ADT-111)" -gt 0 ] \
  && ok "the sweeping session still bills its own turn" \
  || bad "the sweeping session still bills its own turn"

# The tail is TWO messages (cursor was at 1 of 3), so 2*(5+7) = 24 tokens.
swept="$(awk -F'\t' '$2=="ADT-999" {i+=$3; o+=$4} END {print i+o+0}' "$LEDGER")"
[ "$swept" = "24" ] \
  && ok "only the tail beyond the cursor is billed (24 tokens, not 36)" \
  || bad "only the tail beyond the cursor is billed (got $swept, want 24)"

# --- 2. the sweeping session's ticket is not charged ----------------------
own="$(awk -F'\t' '$2=="ADT-111" {i+=$3; o+=$4} END {print i+o+0}' "$LEDGER")"
[ "$own" = "30" ] \
  && ok "the sweeping session's ticket is not charged the dead session's tokens" \
  || bad "the sweeping session's ticket is not charged (got $own, want 30)"

# --- 3. a second Stop does not re-bill ------------------------------------
before="$(count_rows ADT-999)"
touch -t 202001010000 "$DEAD"
run_hook "$LIVE" "$PROJ" "live-session"
[ "$(count_rows ADT-999)" = "$before" ] \
  && ok "a second sweep re-bills nothing (the cursor advanced)" \
  || bad "a second sweep re-bills nothing"

# --- 4. a warm session is not swept ---------------------------------------
setup                                   # $DEAD is freshly written = warm
run_hook "$LIVE" "$PROJ" "live-session"
[ "$(count_rows ADT-999)" = "0" ] \
  && ok "a warm session is not swept" \
  || bad "a warm session is not swept"

# --- 5. control for 4: the same file is swept once cold -------------------
touch -t 202001010000 "$DEAD"
run_hook "$LIVE" "$PROJ" "live-session"
[ "$(count_rows ADT-999)" -gt 0 ] \
  && ok "control: the same file is swept once cold" \
  || bad "control: the same file is swept once cold"

# --- 6. a cold session with no binding bills to __unassigned__ ------------
setup
rm -f "$STATE/current-tix.d/dead-session"
touch -t 202001010000 "$DEAD"
run_hook "$LIVE" "$PROJ" "live-session"
[ "$(count_rows __unassigned__)" -gt 0 ] \
  && ok "a dead session with no ticket bills to __unassigned__" \
  || bad "a dead session with no ticket bills to __unassigned__"

# --- 7. rows keep the 14-column shape -------------------------------------
cols="$(awk -F'\t' '$2=="__unassigned__" {print NF; exit}' "$LEDGER")"
[ "$cols" = "14" ] \
  && ok "a swept row is the same 14 columns as a live one" \
  || bad "a swept row is 14 columns (got $cols)"

# --- 8. the swept row carries the dead session's id -----------------------
sess="$(awk -F'\t' '$2=="__unassigned__" {print $5; exit}' "$LEDGER")"
[ "$sess" = "dead-session" ] \
  && ok "the row is attributed to the session that spent it" \
  || bad "the row carries the dead session's id (got $sess)"

# --- 9. a swept session's cursor is stamped, so it is not re-read ---------
# Checks the cursor's mtime rather than timing, which would be flaky.
setup
touch -t 202001010000 "$DEAD"
run_hook "$LIVE" "$PROJ" "live-session"
[ -n "$(find "$STATE/token-cursor/dead-session" -maxdepth 0 -mmin -5 2>/dev/null)" ] \
  && ok "the cursor is stamped, so the file is not re-read next turn" \
  || bad "the cursor is stamped after a sweep"
# With nothing left to bill, a second pass adds no rows.
before="$(count_rows ADT-999)"
run_hook "$LIVE" "$PROJ" "live-session"
[ "$(count_rows ADT-999)" = "$before" ] \
  && ok "a swept-clean session adds no further rows" \
  || bad "a swept-clean session adds no further rows"

printf '\nPASS=%d FAIL=%d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
