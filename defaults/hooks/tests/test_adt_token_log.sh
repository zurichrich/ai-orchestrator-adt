#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests adt-token-log.sh (the cost ledger it writes and the ticket each row is
# billed to), plus the ticket markers written by adt-usage-log.sh and adt-mark-tix.sh.
# Run:  bash defaults/hooks/tests/test_adt_token_log.sh
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKEN_HOOK="$HOOK_DIR/adt-token-log.sh"
USAGE_HOOK="$HOOK_DIR/adt-usage-log.sh"
MARK_HOOK="$HOOK_DIR/adt-mark-tix.sh"
SUM_HOOK="$HOOK_DIR/adt-token-sum.sh"
PASS=0 FAIL=0

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

# Fresh tmp "project" with a git repo (the hooks resolve root via git toplevel).
newproj() {
  local d; d="$(mktemp -d)"
  git -C "$d" init -q
  mkdir -p "$d/.adt"
  printf '%s' "$d"
}

# Write a transcript JSONL with N assistant messages, each (input,output).
# Args: <path> then pairs "in:out in:out ...".
write_transcript() {
  local path="$1"; shift
  : > "$path"
  for pair in "$@"; do
    local i="${pair%%:*}" o="${pair##*:}"
    printf '{"type":"assistant","message":{"role":"assistant","usage":{"input_tokens":%s,"output_tokens":%s}}}\n' "$i" "$o" >> "$path"
  done
}

# Drive the token hook with stdin JSON pointing at a transcript + cwd.
run_token_hook() {
  local transcript="$1" cwd="$2" session="${3:-}"
  local json
  if [ -n "$session" ]; then
    json="$(printf '{"transcript_path":"%s","cwd":"%s","session_id":"%s"}' "$transcript" "$cwd" "$session")"
  else
    json="$(printf '{"transcript_path":"%s","cwd":"%s"}' "$transcript" "$cwd")"
  fi
  printf '%s' "$json" | bash "$TOKEN_HOOK"
}

# A transcript whose messages carry model, speed and cache token counts.
# Args: <path> then "model:speed:in:out:cread:cw5:cw1" tuples.
write_rich_transcript() {
  local path="$1"; shift
  : > "$path"
  for t in "$@"; do
    IFS=: read -r m sp i o cr w5 w1 <<< "$t"
    printf '{"type":"assistant","message":{"role":"assistant","model":"%s","usage":{"input_tokens":%s,"output_tokens":%s,"cache_read_input_tokens":%s,"speed":"%s","cache_creation":{"ephemeral_5m_input_tokens":%s,"ephemeral_1h_input_tokens":%s}}}}\n' \
      "$m" "$i" "$o" "$cr" "$sp" "$w5" "$w1" >> "$path"
  done
}

echo "== adt-token-log.sh =="

# 1. Single turn → one row with summed input+output, cursor advanced.
P="$(newproj)"; T="$P/sess.jsonl"
write_transcript "$T" "100:50" "200:30"
run_token_hook "$T" "$P" "sess-1"
LEDGER="$P/.adt/state/cost-ledger.log"
rows="$(wc -l < "$LEDGER" 2>/dev/null | tr -d ' ')"
[ "$rows" = "1" ] && ok "one row appended for a fresh session" || bad "expected 1 row, got ${rows:-0}"
in="$(cut -f3 "$LEDGER")"; out="$(cut -f4 "$LEDGER")"
[ "$in" = "300" ] && [ "$out" = "80" ] && ok "summed input=300 output=80 across 2 msgs" || bad "expected 300/80, got $in/$out"
cur="$(cat "$P/.adt/state/token-cursor/sess-1")"
[ "$cur" = "2" ] && ok "cursor advanced to 2" || bad "expected cursor 2, got $cur"

# 2. Idempotency: re-run with NO new messages → no new row, cursor unchanged.
run_token_hook "$T" "$P" "sess-1"
rows2="$(wc -l < "$LEDGER" | tr -d ' ')"
[ "$rows2" = "1" ] && ok "re-run with no new msgs appends nothing" || bad "expected still 1 row, got $rows2"

# 3. Delta: a 3rd message arrives → one more row billing ONLY the new message.
write_transcript "$T" "100:50" "200:30" "7:3"
run_token_hook "$T" "$P" "sess-1"
rows3="$(wc -l < "$LEDGER" | tr -d ' ')"
[ "$rows3" = "2" ] && ok "new message → exactly one more row" || bad "expected 2 rows, got $rows3"
lin="$(tail -1 "$LEDGER" | cut -f3)"; lout="$(tail -1 "$LEDGER" | cut -f4)"
[ "$lin" = "7" ] && [ "$lout" = "3" ] && ok "delta row bills only the new msg (7/3)" || bad "expected 7/3, got $lin/$lout"

# 4. The flat current-tix marker sets the ticket. The test creates .adt/state/
#    itself because it writes the marker before the hook runs.
P="$(newproj)"; T="$P/s.jsonl"; write_transcript "$T" "10:5"
mkdir -p "$P/.adt/state"
printf 'TIX-188\n' > "$P/.adt/state/current-tix"
run_token_hook "$T" "$P" "s"
tix="$(cut -f2 "$P/.adt/state/cost-ledger.log")"
[ "$tix" = "TIX-188" ] && ok "bills to the current-tix marker's ticket" || bad "expected TIX-188, got $tix"

# 5. No current-tix → __unassigned__.
P="$(newproj)"; T="$P/s.jsonl"; write_transcript "$T" "10:5"
run_token_hook "$T" "$P" "s"
tix="$(cut -f2 "$P/.adt/state/cost-ledger.log")"
[ "$tix" = "__unassigned__" ] && ok "falls back to __unassigned__" || bad "expected __unassigned__, got $tix"

# 5b. No current-tix but exactly one building/ ticket in the cache → bill to it.
P="$(newproj)"; T="$P/s.jsonl"; write_transcript "$T" "10:5"
mkdir -p "$P/.adt"; printf 'cache_dir: %s\n' "$P/cache" > "$P/.adt/config.yaml"
mkdir -p "$P/cache/enhancements/building"
printf -- '---\nid: TIX-777\ntitle: x\n---\n' > "$P/cache/enhancements/building/x.md"
run_token_hook "$T" "$P" "s"
tix="$(cut -f2 "$P/.adt/state/cost-ledger.log")"
[ "$tix" = "TIX-777" ] && ok "falls back to lone building/ ticket in cache" || bad "expected TIX-777, got $tix"

# 5c. Two building/ tickets in the cache → ambiguous → __unassigned__.
P="$(newproj)"; T="$P/s.jsonl"; write_transcript "$T" "10:5"
mkdir -p "$P/.adt"; printf 'cache_dir: %s\n' "$P/cache" > "$P/.adt/config.yaml"
mkdir -p "$P/cache/enhancements/building" "$P/cache/bugs/building"
printf -- '---\nid: TIX-777\n---\n' > "$P/cache/enhancements/building/x.md"
printf -- '---\nid: TIX-778\n---\n' > "$P/cache/bugs/building/y.md"
run_token_hook "$T" "$P" "s"
tix="$(cut -f2 "$P/.adt/state/cost-ledger.log")"
[ "$tix" = "__unassigned__" ] && ok "two building/ tickets → __unassigned__" || bad "expected __unassigned__, got $tix"

# 6. session_id derived from filename when absent from stdin.
P="$(newproj)"; T="$P/abc-123.jsonl"; write_transcript "$T" "10:5"
run_token_hook "$T" "$P"   # no session arg
[ -f "$P/.adt/state/token-cursor/abc-123" ] && ok "session derived from transcript filename" || bad "cursor file abc-123 not created"

# 7. Missing transcript → exit 0.
P="$(newproj)"
printf '{"transcript_path":"/nonexistent/x.jsonl","cwd":"%s"}' "$P" | bash "$TOKEN_HOOK"; rc=$?
[ "$rc" = "0" ] && ok "missing transcript → exit 0" || bad "expected exit 0, got $rc"

# 8. Garbage JSONL lines → exit 0, no row (no assistant messages).
P="$(newproj)"; T="$P/s.jsonl"; printf 'not json\n{"type":"user"}\n' > "$T"
printf '{"transcript_path":"%s","cwd":"%s","session_id":"s"}' "$T" "$P" | bash "$TOKEN_HOOK"; rc=$?
[ "$rc" = "0" ] && ok "garbage transcript → exit 0" || bad "expected exit 0, got $rc"
[ ! -f "$P/.adt/state/cost-ledger.log" ] && ok "no row written for zero assistant msgs" || bad "unexpected ledger row"

echo "== adt-usage-log.sh sets the current-tix marker =="

run_usage_hook() {  # <prompt> <cwd>
  printf '{"prompt":"%s","cwd":"%s"}' "$1" "$2" | bash "$USAGE_HOOK"
}

# 9. An explicit ticket id in an /adt prompt.
P="$(newproj)"
run_usage_hook "/adt-build TIX-188" "$P"
v="$(cat "$P/.adt/state/current-tix" 2>/dev/null)"
[ "$v" = "TIX-188" ] && ok "explicit TIX-188 captured" || bad "expected TIX-188, got '$v'"

# 10. Bare-number shorthand: "188 /adt-build".
P="$(newproj)"
run_usage_hook "188 /adt-build" "$P"
v="$(cat "$P/.adt/state/current-tix" 2>/dev/null)"
[ "$v" = "TIX-188" ] && ok "bare-number 188 → TIX-188" || bad "expected TIX-188, got '$v'"

# 11. An explicit ticket id in a prompt with no /adt-* command still sets the marker.
P="$(newproj)"
run_usage_hook "build TIX-188 please" "$P"
v="$(cat "$P/.adt/state/current-tix" 2>/dev/null)"
[ "$v" = "TIX-188" ] && ok "TIX-188 in a prompt with no /adt-* sets the marker" || bad "expected TIX-188, got '$v'"

# 11b. A bare number in a prompt with no /adt-* command does not set the marker.
P="$(newproj)"
run_usage_hook "the stock is down 40 percent today" "$P"
[ ! -f "$P/.adt/state/current-tix" ] && ok "bare number in a prompt with no /adt-* does not set the marker" || bad "current-tix written for bare number"

echo "== a worktree writes to the main checkout's .adt/ =="

# 12. Run both hooks from a linked worktree; the marker and ledger land in the
# main checkout's .adt/, not the worktree's.
command -v git >/dev/null 2>&1 && {
  MAIN="$(mktemp -d)"
  git -C "$MAIN" init -q
  git -C "$MAIN" config user.email t@t && git -C "$MAIN" config user.name t
  mkdir -p "$MAIN/.adt"
  printf 'seed\n' > "$MAIN/seed.txt"
  git -C "$MAIN" add -A && git -C "$MAIN" commit -qm seed
  WT="$(mktemp -d)/wt"
  git -C "$MAIN" worktree add -q -b tix-202-x "$WT" >/dev/null 2>&1

  # adt-usage-log.sh from the worktree → marker in the main checkout.
  run_usage_hook "build TIX-202 now" "$WT"
  [ -f "$MAIN/.adt/state/current-tix" ] && ok "worktree writer → marker in the main checkout" || bad "marker not in the main checkout"
  [ ! -f "$WT/.adt/state/current-tix" ] && ok "worktree writer did not write into the worktree" || bad "marker leaked into worktree"

  # adt-token-log.sh from the worktree → reads that marker, writes the ledger in
  # the main checkout, bills TIX-202.
  T="$WT/sess.jsonl"; write_transcript "$T" "10:5"
  run_token_hook "$T" "$WT" "wt-sess"
  LED="$MAIN/.adt/state/cost-ledger.log"
  [ -f "$LED" ] && ok "worktree reader → ledger in the main checkout" || bad "ledger not in the main checkout"
  rtix="$(cut -f2 "$LED" 2>/dev/null)"
  [ "$rtix" = "TIX-202" ] && ok "worktree turn billed to TIX-202 from the main checkout's marker" || bad "expected TIX-202, got '$rtix'"

  git -C "$MAIN" worktree remove --force "$WT" >/dev/null 2>&1 || true
} || true

# 13. A normal checkout writes the marker in its own .adt/.
P="$(newproj)"
run_usage_hook "build TIX-313 now" "$P"
v="$(cat "$P/.adt/state/current-tix" 2>/dev/null)"
[ "$v" = "TIX-313" ] && ok "normal checkout writes the marker locally" || bad "expected TIX-313, got '$v'"

echo "== each session bills its own ticket when sessions run in parallel =="

# Drive adt-usage-log.sh with an explicit session_id (UserPromptSubmit carries it).
run_usage_hook_sess() {  # <prompt> <cwd> <session>
  printf '{"prompt":"%s","cwd":"%s","session_id":"%s"}' "$1" "$2" "$3" | bash "$USAGE_HOOK"
}

# 14. adt-usage-log.sh writes a PER-SESSION marker keyed by session_id.
P="$(newproj)"
run_usage_hook_sess "build TIX-501" "$P" "sessA"
v="$(cat "$P/.adt/state/current-tix.d/sessA" 2>/dev/null)"
[ "$v" = "TIX-501" ] && ok "per-session marker current-tix.d/sessA = TIX-501" || bad "expected TIX-501, got '$v'"

# 15. Two sessions interleave: A names TIX-501, then B names TIX-502, so the flat
#     marker reads TIX-502. Each session still bills its own ticket.
P="$(newproj)"
run_usage_hook_sess "work TIX-501" "$P" "sessA"
run_usage_hook_sess "work TIX-502" "$P" "sessB"        # B writes last → flat marker = TIX-502
flat="$(cat "$P/.adt/state/current-tix" 2>/dev/null)"
[ "$flat" = "TIX-502" ] && ok "flat shared marker holds the last write (TIX-502)" || bad "flat marker '$flat'"
TA="$P/a.jsonl"; write_transcript "$TA" "10:5"
run_token_hook "$TA" "$P" "sessA"
tixA="$(grep -F sessA "$P/.adt/state/cost-ledger.log" | head -1 | cut -f2)"
[ "$tixA" = "TIX-501" ] && ok "session A bills TIX-501 despite flat marker = TIX-502" || bad "session A billed '$tixA', expected TIX-501"
TB="$P/b.jsonl"; write_transcript "$TB" "20:7"
run_token_hook "$TB" "$P" "sessB"
tixB="$(grep -F sessB "$P/.adt/state/cost-ledger.log" | head -1 | cut -f2)"
[ "$tixB" = "TIX-502" ] && ok "session B bills TIX-502" || bad "session B billed '$tixB', expected TIX-502"

# 16. A ticket id in the branch name wins over a lone ticket in building/.
command -v git >/dev/null 2>&1 && {
  MAIN="$(mktemp -d)"; git -C "$MAIN" init -q
  git -C "$MAIN" config user.email t@t && git -C "$MAIN" config user.name t
  mkdir -p "$MAIN/.adt"
  mkdir -p "$MAIN/.adt"; printf 'cache_dir: %s\n' "$MAIN/cache" > "$MAIN/.adt/config.yaml"
  mkdir -p "$MAIN/cache/bugs/building"
  printf -- '---\nid: TIX-999\n---\n' > "$MAIN/cache/bugs/building/other.md"  # a lone building/ ticket
  printf 'seed\n' > "$MAIN/seed.txt"; git -C "$MAIN" add -A && git -C "$MAIN" commit -qm seed
  WT="$(mktemp -d)/wt"; git -C "$MAIN" worktree add -q -b tix-601-feature "$WT" >/dev/null 2>&1
  T="$WT/s.jsonl"; write_transcript "$T" "10:5"
  run_token_hook "$T" "$WT" "sess-branch"   # no per-session marker → branch should win
  rtix="$(grep -F sess-branch "$MAIN/.adt/state/cost-ledger.log" | head -1 | cut -f2)"
  [ "$rtix" = "TIX-601" ] && ok "branch tix-601 wins over lone building/ ticket TIX-999" || bad "expected TIX-601, got '$rtix'"
  git -C "$MAIN" worktree remove --force "$WT" >/dev/null 2>&1 || true
} || true

# 17. No ticket in the branch, no per-session marker, and another session active
#     → __unassigned__, even with a lone ticket in building/. A fresh cursor file
#     for another session stands in for the second active session.
P="$(newproj)"
mkdir -p "$P/.adt"; printf 'cache_dir: %s\n' "$P/cache" > "$P/.adt/config.yaml"
mkdir -p "$P/cache/bugs/building"; printf -- '---\nid: TIX-888\n---\n' > "$P/cache/bugs/building/decoy.md"
mkdir -p "$P/.adt/state/token-cursor"
printf '5\n' > "$P/.adt/state/token-cursor/other-live-session"   # another active session
T="$P/s.jsonl"; write_transcript "$T" "10:5"
run_token_hook "$T" "$P" "lonely"   # cwd is plain repo (branch master/main, no tix)
tix="$(grep -F lonely "$P/.adt/state/cost-ledger.log" | head -1 | cut -f2)"
[ "$tix" = "__unassigned__" ] && ok "another session active + no session signal → __unassigned__" || bad "expected __unassigned__, got '$tix'"

# 18. With only one session active, the lone building/ ticket is used.
P="$(newproj)"
mkdir -p "$P/.adt"; printf 'cache_dir: %s\n' "$P/cache" > "$P/.adt/config.yaml"
mkdir -p "$P/cache/bugs/building"; printf -- '---\nid: TIX-777\n---\n' > "$P/cache/bugs/building/x.md"
T="$P/s.jsonl"; write_transcript "$T" "10:5"
run_token_hook "$T" "$P" "solo"
tix="$(grep -F solo "$P/.adt/state/cost-ledger.log" | head -1 | cut -f2)"
[ "$tix" = "TIX-777" ] && ok "single session → lone building/ ticket used" || bad "expected TIX-777, got '$tix'"

# 19. A per-session marker older than 14 days is deleted; a fresh one stays.
P="$(newproj)"
mkdir -p "$P/.adt/state/current-tix.d"
printf 'TIX-1\n' > "$P/.adt/state/current-tix.d/old"
printf 'TIX-2\n' > "$P/.adt/state/current-tix.d/new"
touch -t 202001010000 "$P/.adt/state/current-tix.d/old"   # ancient
T="$P/s.jsonl"; write_transcript "$T" "1:1"
run_token_hook "$T" "$P" "gc-sess"
[ ! -f "$P/.adt/state/current-tix.d/old" ] && ok "stale per-session marker deleted" || bad "stale marker not pruned"
[ -f "$P/.adt/state/current-tix.d/new" ] && ok "fresh per-session marker kept" || bad "fresh marker wrongly pruned"

echo "== the old development-team/ tree is neither read nor created =="
P="$(newproj)"
mkdir -p "$P/development-team/.adt-current-tix.d"
printf 'TIX-840\n' > "$P/development-team/.adt-current-tix.d/legsess"
T="$P/s.jsonl"; write_transcript "$T" "10:5"
run_token_hook "$T" "$P" "legsess"
tix="$(grep -F legsess "$P/.adt/state/cost-ledger.log" | head -1 | cut -f2)"
[ "$tix" = "__unassigned__" ] && ok "a marker in the old tree is not read" \
  || bad "reader still honours the old tree: got '$tix'"

P="$(newproj)"
run_usage_hook_sess "build TIX-841" "$P" "mirsess"
[ -f "$P/.adt/state/current-tix.d/mirsess" ] && ok "the marker is written under .adt/state/" \
  || bad "no marker written"
[ -e "$P/development-team" ] && bad "the writer recreated the old tree" \
  || ok "the writer never creates the old tree"

P="$(newproj)"
run_usage_hook "/adt-build TIX-842" "$P"
[ -s "$P/.adt/state/usage.log" ] && ok "usage log written under .adt/state/" || bad "no usage log"
[ -e "$P/development-team" ] && bad "the usage hook recreated the old tree" \
  || ok "the usage hook never creates the old tree"

echo ""
echo "== a locked ticket marker is not overridden by prose =="

# adt-mark-tix.sh resolves the root from $PWD (not a cwd arg), so run it from
# inside the test project. It keys the session off $CLAUDE_CODE_SESSION_ID.
run_mark() {  # <id> <cwd> <session>
  ( cd "$2" && CLAUDE_CODE_SESSION_ID="$3" bash "$MARK_HOOK" "$1" )
}

# 24. adt-mark-tix.sh writes the per-session marker and a .locked file beside it.
P="$(newproj)"
run_mark "TIX-700" "$P" "locksess"
m="$(cat "$P/.adt/state/current-tix.d/locksess" 2>/dev/null)"
[ "$m" = "TIX-700" ] && ok "adt-mark-tix.sh sets marker TIX-700" || bad "expected TIX-700, got '$m'"
[ -f "$P/.adt/state/current-tix.d/locksess.locked" ] \
  && ok "adt-mark-tix.sh writes a .locked file" || bad "no .locked file written"

# 25. A prompt naming a different id does not override a locked marker.
run_usage_hook_sess "investigating TIX-135 branch /adt-build" "$P" "locksess"
m2="$(cat "$P/.adt/state/current-tix.d/locksess" 2>/dev/null)"
[ "$m2" = "TIX-700" ] && ok "prose TIX-135 cannot override locked TIX-700" \
  || bad "lock breached: marker became '$m2' (expected TIX-700)"

# 26. A prompt naming an id still sets an unlocked session's marker.
P="$(newproj)"
run_usage_hook_sess "work on TIX-808 /adt-build" "$P" "freesess"
m3="$(cat "$P/.adt/state/current-tix.d/freesess" 2>/dev/null)"
[ "$m3" = "TIX-808" ] && ok "prose binds an unlocked session" \
  || bad "expected TIX-808 on unlocked session, got '$m3'"

echo ""
echo "== adt-token-sum.sh reports unattributed when there are no rows =="

# 27. No ledger at all → 'unattributed'.
P="$(newproj)"
r="$(bash "$SUM_HOOK" TIX-900 "$P")"
[ "$r" = "unattributed" ] && ok "no ledger → unattributed" || bad "expected unattributed, got '$r'"

# 28. Ledger exists but NO row for the id → 'unattributed'.
P="$(newproj)"
mkdir -p "$P/.adt/state"
printf '2026-06-27T10:00:00Z\tTIX-901\t100\t200\tsess\n' \
  > "$P/.adt/state/cost-ledger.log"
r="$(bash "$SUM_HOOK" TIX-902 "$P")"
[ "$r" = "unattributed" ] && ok "ledger without matching row → unattributed" \
  || bad "expected unattributed, got '$r'"

# 29. A matching row → the sum of input and output.
r="$(bash "$SUM_HOOK" TIX-901 "$P")"
[ "$r" = "300" ] && ok "matching row → integer sum 300" || bad "expected 300, got '$r'"

echo ""
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = "0" ]

echo
echo "== one row per (model, speed), with all five token counts =="

P="$(newproj)"; T="$P/rich.jsonl"
write_rich_transcript "$T" \
  "claude-opus-5:standard:10:20:100:0:5" \
  "claude-opus-4-8:fast:1:2:30:7:0" \
  "claude-opus-5:standard:5:6:50:0:1"
run_token_hook "$T" "$P" "rich1" >/dev/null 2>&1
LED="$P/.adt/state/cost-ledger.log"
rows="$(wc -l < "$LED" | tr -d ' ')"
[ "$rows" = "2" ] && ok "two (model, speed) groups -> two rows" || bad "expected 2 rows, got $rows"

cols="$(head -1 "$LED" | awk -F'\t' '{print NF}')"
[ "$cols" = "14" ] && ok "row carries 14 columns" || bad "expected 14 columns, got $cols"

# opus-5/standard aggregates messages 1 and 3: in 15, out 26, read 150, 1h 6.
line5="$(grep 'claude-opus-5' "$LED" | head -1)"
got="$(printf '%s' "$line5" | awk -F'\t' '{print $3"/"$4"/"$8"/"$9"/"$10}')"
[ "$got" = "15/26/150/0/6" ] && ok "same-model messages aggregate correctly" \
  || bad "expected 15/26/150/0/6, got $got"

# the fast row keeps its own speed and its 5m-TTL write
linef="$(grep 'claude-opus-4-8' "$LED" | head -1)"
gotf="$(printf '%s' "$linef" | awk -F'\t' '{print $7"/"$9}')"
[ "$gotf" = "fast/7" ] && ok "fast row keeps its speed and 5m write" || bad "expected fast/7, got $gotf"

tier="$(head -1 "$LED" | awk -F'\t' '{print $11}')"
[ "$tier" = "measured" ] && ok "hook-written rows are tier=measured" || bad "expected measured, got $tier"

# Re-running on an unchanged transcript appends nothing.
run_token_hook "$T" "$P" "rich1" >/dev/null 2>&1
rows2="$(wc -l < "$LED" | tr -d ' ')"
[ "$rows2" = "2" ] && ok "re-run on unchanged transcript appends nothing" || bad "expected 2 rows, got $rows2"

# An old 5-column row is still summed.
printf '2026-01-01T00:00:00Z\tTIX-500\t100\t200\toldsess\n' >> "$LED"
legacy_sum="$(bash "$SUM_HOOK" TIX-500 "$P")"
[ "$legacy_sum" = "300" ] && ok "legacy 5-column row still sums" || bad "expected 300, got $legacy_sum"

echo "== column 13 carries the install id =="
PI="$(newproj)"; PIS="$PI/.adt/state"
mkdir -p "$PIS"; printf 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\n' > "$PIS/install-id"
TI="$PI/s.jsonl"; write_transcript "$TI" "5:5"
run_token_hook "$TI" "$PI" "id-sess" >/dev/null 2>&1
col13="$(awk -F'\t' '{print $13}' "$PIS/cost-ledger.log")"
[ "$col13" = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" ] \
  && ok "column 13 carries the install id" \
  || bad "column 13 carries the install id: expected the uuid, got '$col13'"

# No id file → 14 columns with an empty 13th.
PN="$(newproj)"
TN="$PN/s.jsonl"; write_transcript "$TN" "1:1"
run_token_hook "$TN" "$PN" "noid-sess" >/dev/null 2>&1
LN="$PN/.adt/state/cost-ledger.log"
nc="$(head -1 "$LN" | awk -F'\t' '{print NF}')"; nv="$(head -1 "$LN" | awk -F'\t' '{print $13}')"
[ "$nc" = "14" ] && [ -z "$nv" ] \
  && ok "a missing id file writes 14 columns with an empty id" \
  || bad "expected 14 cols and an empty id, got $nc cols / '$nv'"

echo "== column 14 carries the ticket's lane =="

PL="$(newproj)"
mkdir -p "$PL/cache/tasks/building"
cat > "$PL/cache/tasks/building/lane-probe.md" <<'TIX'
---
id: ADT-9001
stage: building
---
TIX
printf 'cache_dir: %s\n' "$PL/cache" >> "$PL/.adt/config.yaml"
mkdir -p "$PL/.adt/state/current-tix.d"
printf 'ADT-9001\n' > "$PL/.adt/state/current-tix.d/lane-sess"
TL="$PL/lane.jsonl"; write_transcript "$TL" "1:1"
run_token_hook "$TL" "$PL" "lane-sess" >/dev/null 2>&1
LL="$PL/.adt/state/cost-ledger.log"
lrow="$(head -1 "$LL")"
lc="$(printf '%s' "$lrow" | awk -F'\t' '{print NF}')"
lv="$(printf '%s' "$lrow" | awk -F'\t' '{print $14}')"
ltix="$(printf '%s' "$lrow" | awk -F'\t' '{print $2}')"
[ "$lc" = "14" ] && [ "$lv" = "building" ] \
  && ok "column 14 carries the lane the ticket file sits in" \
  || bad "expected 14 cols and lane 'building', got $lc cols / '$lv' (tix '$ltix')"

# Control: move the ticket to qa/ and the column follows it.
mkdir -p "$PL/cache/tasks/qa"
mv "$PL/cache/tasks/building/lane-probe.md" "$PL/cache/tasks/qa/lane-probe.md"
rm -f "$PL/.adt/state/token-cursor/lane-sess" "$PL/.adt/state/cost-ledger.log"
run_token_hook "$TL" "$PL" "lane-sess" >/dev/null 2>&1
lv2="$(head -1 "$LL" | awk -F'\t' '{print $14}')"
[ "$lv2" = "qa" ] \
  && ok "the lane column follows a lane move" \
  || bad "expected lane 'qa' after the move, got '$lv2'"

# No cache_dir → the lane column is empty.
PE="$(newproj)"
TE="$PE/e.jsonl"; write_transcript "$TE" "1:1"
run_token_hook "$TE" "$PE" "nolane-sess" >/dev/null 2>&1
ev="$(head -1 "$PE/.adt/state/cost-ledger.log" | awk -F'\t' '{print NF"/"$14}')"
[ "$ev" = "14/" ] \
  && ok "an unresolvable lane writes 14 columns with an empty 14th" \
  || bad "expected '14/' got '$ev'"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1

echo
echo "== token-usage.log is migrated to cost-ledger.log =="

# Rows already in token-usage.log are moved into cost-ledger.log.
P="$(newproj)"; T="$P/m.jsonl"
mkdir -p "$P/.adt/state"
printf '2026-01-01T00:00:00Z\tTIX-1\t100\t200\toldsess\n' \
  > "$P/.adt/state/token-usage.log"
write_transcript "$T" "10:20"
run_token_hook "$T" "$P" "mig1" >/dev/null 2>&1
NEW="$P/.adt/state/cost-ledger.log"
OLD="$P/.adt/state/token-usage.log"
[ -f "$NEW" ] && ok "writes to cost-ledger.log" || bad "cost-ledger.log missing"
[ ! -f "$OLD" ] && ok "old file migrated away, not left behind" || bad "token-usage.log still present"
rows="$(wc -l < "$NEW" | tr -d ' ')"
[ "$rows" = "2" ] && ok "pre-rename row survives the migration" || bad "expected 2 rows, got $rows"
grep -q 'TIX-1' "$NEW" && ok "the old row is the one that survived" || bad "old row lost"

# adt-token-sum.sh still reads token-usage.log when cost-ledger.log is absent.
P2="$(newproj)"; mkdir -p "$P2/.adt/state"
printf '2026-01-01T00:00:00Z\tTIX-2\t100\t200\toldsess\n' \
  > "$P2/.adt/state/token-usage.log"
sum="$(bash "$SUM_HOOK" TIX-2 "$P2")"
[ "$sum" = "300" ] && ok "pre-rename ledger is still readable" || bad "expected 300, got $sum"

echo
echo "== column 12 carries the active command across later turns =="
# A command turn followed by turns that name no command: every row carries the command.
PC="$(newproj)"
PC_STATE="$PC/.adt/state"
mkdir -p "$PC_STATE/current-cmd.d"
printf 'adt-build\n' > "$PC_STATE/current-cmd.d/stick-sess"

# One transcript that grows, as in a real session. Separate files would share the
# per-session cursor, so only the first would append a row.
TC="$PC/session.jsonl"
write_transcript "$TC" "10:5"
run_token_hook "$TC" "$PC" "stick-sess" >/dev/null 2>&1
write_transcript "$TC" "10:5" "10:5"
run_token_hook "$TC" "$PC" "stick-sess" >/dev/null 2>&1
write_transcript "$TC" "10:5" "10:5" "10:5"
run_token_hook "$TC" "$PC" "stick-sess" >/dev/null 2>&1

LEDC="$PC_STATE/cost-ledger.log"
rowsc="$(wc -l < "$LEDC" | tr -d ' ')"
[ "$rowsc" = "3" ] && ok "three turns -> three rows" || bad "expected 3 rows, got $rowsc"

col12_all="$(awk -F'\t' '{print $12}' "$LEDC" | sort -u | tr '\n' ',')"
[ "$col12_all" = "adt-build," ] \
  && ok "column 12 keeps the command across turns that name none" \
  || bad "column 12 keeps the command: expected every row 'adt-build', got '$col12_all'"

# A session with no command marker still writes a row, with column 12 empty.
PU="$(newproj)"
TU="$PU/unbound.jsonl"
write_transcript "$TU" "7:3"
run_token_hook "$TU" "$PU" "unbound-sess" >/dev/null 2>&1
LEDU="$PU/.adt/state/cost-ledger.log"
ucols="$(head -1 "$LEDU" | awk -F'\t' '{print NF}')"
ucmd="$(head -1 "$LEDU" | awk -F'\t' '{print $12}')"
[ "$ucols" = "14" ] && [ -z "$ucmd" ] \
  && ok "an unbound session still writes 14 columns with an empty command" \
  || bad "expected 14 cols and an empty command, got $ucols cols / '$ucmd'"

# Control: point the marker at a different command and the column follows it.
printf 'adt-qa-run\n' > "$PC_STATE/current-cmd.d/stick-sess"
write_transcript "$TC" "10:5" "10:5" "10:5" "1:1"
run_token_hook "$TC" "$PC" "stick-sess" >/dev/null 2>&1
lastcmd="$(tail -1 "$LEDC" | awk -F'\t' '{print $12}')"
[ "$lastcmd" = "adt-qa-run" ] \
  && ok "control: the column follows the command marker" \
  || bad "control: expected adt-qa-run, got '$lastcmd'"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
