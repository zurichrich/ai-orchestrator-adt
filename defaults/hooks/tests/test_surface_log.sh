#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests the agent and skill dispatch tally that adt-token-log.sh writes to
# surface-log.tsv, and that a bad tally never stops the cost-ledger row being written.
#
# Run:  bash agent-dev-team/defaults/hooks/tests/test_surface_log.sh
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKEN_HOOK="$HOOK_DIR/adt-token-log.sh"
PASS=0 FAIL=0

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

newproj() {
  local d; d="$(mktemp -d)"
  git -C "$d" init -q
  mkdir -p "$d/.adt"
  printf '%s' "$d"
}

run_token_hook() {
  local transcript="$1" cwd="$2" session="${3:-s1}"
  printf '{"type":"assistant","message":{}}' >/dev/null   # noop, keeps shellcheck quiet
  printf '{"transcript_path":"%s","cwd":"%s","session_id":"%s"}' \
    "$transcript" "$cwd" "$session" | bash "$TOKEN_HOOK" >/dev/null 2>&1
}

surface_log() { printf '%s/.adt/state/surface-log.tsv' "$1"; }
ledger()      { printf '%s/.adt/state/cost-ledger.log' "$1"; }

# One assistant message carrying usage plus the given tool_use blocks (raw JSON).
msg_with_blocks() {
  local blocks="$1"
  printf '{"type":"assistant","message":{"role":"assistant","usage":{"input_tokens":10,"output_tokens":5},"content":[%s]}}\n' "$blocks"
}

agent_block() { printf '{"type":"tool_use","name":"Agent","input":{"subagent_type":"%s"}}' "$1"; }
skill_block() { printf '{"type":"tool_use","name":"Skill","input":{"skill":"%s"}}' "$1"; }

# ── 1. agent + skill dispatches are tallied, by name ─────────────────────────
P="$(newproj)"; T="$P/t.jsonl"
{ msg_with_blocks "$(agent_block adt-security-reviewer),$(skill_block adt-diagnose)"
  msg_with_blocks "$(agent_block adt-security-reviewer)"; } > "$T"
run_token_hook "$T" "$P" s1
SL="$(surface_log "$P")"
if [ -f "$SL" ]; then ok "surface-log.tsv is written"; else bad "no surface-log.tsv"; fi
if awk -F'\t' '$3=="agent" && $4=="adt-security-reviewer" && $5==2' "$SL" | grep -q .; then
  ok "agent tallied by subagent_type, counted 2"
else bad "agent tally wrong: $(cat "$SL" 2>/dev/null)"; fi
if awk -F'\t' '$3=="skill" && $4=="adt-diagnose" && $5==1' "$SL" | grep -q .; then
  ok "skill tallied by name, counted 1"
else bad "skill tally wrong: $(cat "$SL" 2>/dev/null)"; fi

# ── 2. a repeated Stop with no new messages adds nothing ─────────────────────
run_token_hook "$T" "$P" s1
if [ "$(wc -l < "$SL" | tr -d ' ')" = "2" ]; then
  ok "a second Stop with no new messages appends nothing"
else bad "repeated Stop double-counted: $(wc -l < "$SL") lines"; fi

# ── 3. a malformed dispatch block is skipped ─────────────────────────────────
P="$(newproj)"; T="$P/t.jsonl"
{ msg_with_blocks '{"type":"tool_use","name":"Agent","input":"not-an-object"}'
  msg_with_blocks "$(agent_block adt-design-reviewer)"; } > "$T"
run_token_hook "$T" "$P" s1
SL="$(surface_log "$P")"
if awk -F'\t' '$3=="agent" && $4=="adt-design-reviewer"' "$SL" | grep -q .; then
  ok "a malformed block is skipped; later dispatches still tally"
else bad "malformed block broke the tally: $(cat "$SL" 2>/dev/null)"; fi

# ── 4. malformed dispatch data beside valid usage → ledger row still written ─
P="$(newproj)"; T="$P/t.jsonl"
msg_with_blocks '{"type":"tool_use","name":"Agent","input":null},{"type":"tool_use"},"not-a-block"' > "$T"
run_token_hook "$T" "$P" s1
L="$(ledger "$P")"
if [ -s "$L" ] && awk -F'\t' '$3==10 && $4==5' "$L" | grep -q .; then
  ok "malformed dispatch data in the same message: ledger row still written"
else bad "billing lost to a malformed tally block: $(cat "$L" 2>/dev/null)"; fi

# ── 5. an unwritable surface log → ledger row still written ──────────────────
# A directory where the file belongs makes the append fail.
P="$(newproj)"; T="$P/t.jsonl"
mkdir -p "$P/.adt/state/surface-log.tsv"
msg_with_blocks "$(agent_block adt-security-reviewer)" > "$T"
run_token_hook "$T" "$P" s1
L="$(ledger "$P")"
if [ -s "$L" ] && awk -F'\t' '$3==10 && $4==5' "$L" | grep -q .; then
  ok "unwritable surface log: ledger row still written"
else bad "unwritable surface log cost the ledger row: $(cat "$L" 2>/dev/null)"; fi

printf '\nPASS=%d FAIL=%d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
