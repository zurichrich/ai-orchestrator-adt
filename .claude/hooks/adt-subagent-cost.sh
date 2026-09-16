#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT default. SubagentStop hook.
# Bills a subagent's tokens to the ticket that dispatched it, by appending rows
# to .adt/state/cost-ledger.log. adt-token-log.sh bills the main session
# transcript; this hook bills the subagents, which that hook never sees.
#
# How it differs from adt-token-log.sh:
#
# 1. The cursor is keyed on the transcript file, not the session. A subagent
#    transcript carries the parent's sessionId, so a per-session cursor would
#    find nothing new and write no row. The cursors live in their own directory,
#    subagent-cursor/, because adt-token-log.sh counts recent files in
#    token-cursor/ to detect concurrent sessions, and subagent cursors there
#    would look like extra sessions.
#
# 2. Rows are tagged `measured`. The subagent transcript has the full usage
#    split (input, output, cache_read, cache_creation). An `estimated` row is
#    priced as input+output only, which would drop most of the tokens.
#
# Which transcripts it bills. SubagentStop passes the parent session's
# transcript_path, not the subagent's. The subagent transcripts sit next to it:
#   ~/.claude/projects/<proj>/<session>.jsonl          <- what the event passes
#   ~/.claude/projects/<proj>/<session>/subagents/agent-<id>.jsonl
# Each one has its own cursor, so each subagent is billed once, incrementally,
# however many times the event fires. If the event passes a subagent transcript
# directly, that one is billed instead. Only rows marked `isSidechain: true`
# count. A transcript with no sidechain rows and no subagents/ directory writes
# nothing, since adt-token-log.sh already bills the parent.
#
# Every error is swallowed: this runs after every subagent, and a missing row
# is better than a failing hook.
set -uo pipefail

input="$(cat 2>/dev/null || true)"
[ -z "$input" ] && exit 0

transcript="$(printf '%s' "$input" | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("transcript_path",""))' 2>/dev/null || true)"
[ -z "$transcript" ] || [ ! -f "$transcript" ] && exit 0
session="$(printf '%s' "$input" | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null || true)"

# Resolve the project root to the canonical checkout, as adt-token-log.sh does,
# so a linked worktree bills into the same ledger.
cwd="$(printf '%s' "$input" | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("cwd",""))' 2>/dev/null || true)"
root="$(git -C "${cwd:-.}" rev-parse --show-toplevel 2>/dev/null || echo "${cwd:-$PWD}")"
common_dir="$(git -C "${cwd:-.}" rev-parse --git-common-dir 2>/dev/null || true)"
if [ -n "$common_dir" ]; then
  case "$common_dir" in /*) ;; *) common_dir="$(git -C "${cwd:-.}" rev-parse --show-toplevel 2>/dev/null)/$common_dir" ;; esac
  canonical="$(cd "$common_dir/.." 2>/dev/null && pwd || true)"
  [ -n "$canonical" ] && [ -d "$canonical/.adt" ] && root="$canonical"
fi
[ -d "$root/.adt" ] || exit 0

state="$root/.adt/state"
ledger="$state/cost-ledger.log"
# Separate from token-cursor/ (see point 1 in the header).
cursor_dir="$state/subagent-cursor"
mkdir -p "$cursor_dir" 2>/dev/null || true

# Which transcripts to bill: the one passed in if it has sidechain rows,
# otherwise every transcript in the parent's subagents/ directory.
sub_dir="${transcript%.jsonl}/subagents"
targets=""
if /usr/bin/python3 -c 'import sys,json
for line in open(sys.argv[1]):
    try:
        if json.loads(line).get("isSidechain") is True: sys.exit(0)
    except Exception: pass
sys.exit(1)' "$transcript" 2>/dev/null; then
  targets="$transcript"
elif [ -d "$sub_dir" ]; then
  targets="$(ls "$sub_dir"/*.jsonl 2>/dev/null || true)"
fi
[ -z "$targets" ] && exit 0

# The ticket is the one the parent session is bound to.
tix=""
[ -n "$session" ] && [ -f "$state/current-tix.d/$session" ] && \
  tix="$(tr -d '\n\t' < "$state/current-tix.d/$session" 2>/dev/null || true)"
[ -z "$tix" ] && [ -f "$state/current-tix" ] && \
  tix="$(tr -d '\n\t' < "$state/current-tix" 2>/dev/null || true)"
[ -z "$tix" ] && tix="__unassigned__"

for target in $targets; do
  [ -f "$target" ] || continue
  cursor="$cursor_dir/$(basename "$target")"

# Keep apostrophes out of the Python below: bash 3.2 counts quotes in a heredoc
# inside $(...), and an odd number breaks the whole script.
result="$(/usr/bin/python3 - "$target" "$cursor" <<'PY' 2>/dev/null || true
import json, sys

transcript, cursor_path = sys.argv[1], sys.argv[2]
try:
    billed = int(open(cursor_path).read().strip())
except Exception:
    billed = 0

seen = 0
agent_type = ""
groups = {}   # (model, speed) -> [inp, out, cread, cw5, cw1]
try:
    with open(transcript) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            # Only sidechain rows are work done by the subagent, so rows from
            # the parent transcript are never billed here.
            if rec.get("isSidechain") is not True:
                continue
            if not agent_type:
                agent_type = str(rec.get("attributionAgent") or "")
            msg = rec.get("message")
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            seen += 1
            if seen <= billed:
                continue
            u = msg.get("usage")
            if not isinstance(u, dict):
                continue
            model = str(msg.get("model") or "")
            # Subagent transcripts have no speed class, so use the default
            # the pricer uses, `standard`.
            key = (model, "standard")
            acc = groups.setdefault(key, [0, 0, 0, 0, 0])
            acc[0] += u.get("input_tokens", 0) or 0
            acc[1] += u.get("output_tokens", 0) or 0
            acc[2] += u.get("cache_read_input_tokens", 0) or 0
            cc = u.get("cache_creation_input_tokens", 0) or 0
            # The ledger splits cache writes into 5m and 1h. The subagent
            # transcript does not, so the whole amount goes to 5m, the default.
            acc[3] += cc
except Exception:
    sys.exit(0)

print(seen)
print(agent_type)
for (model, speed), a in groups.items():
    print("\t".join([model, speed] + [str(x) for x in a]))
PY
)"

[ -z "$result" ] && continue
new_count="$(printf '%s\n' "$result" | sed -n '1p')"
agent_type="$(printf '%s\n' "$result" | sed -n '2p')"
printf '%s' "$new_count" | grep -qE '^[0-9]+$' || continue

# Column 12, the command column, written as `subagent:<type>`. Other code reads
# this prefix: handback counting excludes these rows, and
# adt_lane_cost.command_lane uses it to give a subagent's turns to the lane that
# dispatched it.
cmd_col="subagent:${agent_type}"

# Column 13 is the install's anonymous id, read from the same file that
# build_kanban.machine_id() and adt-token-total.sh read. $state is in the
# canonical checkout, because .adt/state/ is gitignored and absent from linked
# worktrees. On any failure the column is left empty.
#
# Column 14 is the ticket's lane at the time of the turn. Column 12 records the
# command that bound the session and does not change when the ticket moves, so
# it cannot tell lanes apart. The lane is the name of the cache directory the
# ticket file is in. On any failure the column is left empty and readers fall
# back to inferring the lane from the stage label.
_adt_lane() {
  _lt="$1"
  if [ -z "$_lt" ] || [ "$_lt" = "__unassigned__" ]; then
    return 0
  fi
  _lc="$(sed -n 's/^cache_dir:[[:space:]]*//p' "$root/.adt/config.yaml" 2>/dev/null | head -1)"
  if [ -z "$_lc" ]; then
    return 0
  fi
  case "$_lc" in "~"*) _lc="$HOME${_lc#\~}" ;; esac
  _lf="$(grep -rl --include='*.md' "^id: $_lt\$" "$_lc" 2>/dev/null | head -1)"
  if [ -z "$_lf" ]; then
    return 0
  fi
  basename "$(dirname "$_lf")" 2>/dev/null || true
}

install_id_file="$state/install-id"
install_id=""
[ -f "$install_id_file" ] && install_id="$(tr -d '\n\t' < "$install_id_file" 2>/dev/null || true)"

ts="$(date -u +%FT%TZ)"
lane_col="$(_adt_lane "$tix")"
printf '%s\n' "$result" | tail -n +3 | while IFS="$(printf '\t')" read -r model speed inp out cread cw5 cw1; do
  if [ -n "$model" ]; then
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$ts" "$tix" "$inp" "$out" "$session" \
      "$model" "$speed" "$cread" "$cw5" "$cw1" "measured" \
      "$cmd_col" "$install_id" "$lane_col" >> "$ledger" 2>/dev/null || true
  fi
done
  printf '%s\n' "$new_count" > "$cursor" 2>/dev/null || true
done
exit 0

# adt-bundle: v0.1.0
