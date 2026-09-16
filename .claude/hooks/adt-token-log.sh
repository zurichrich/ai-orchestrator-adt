#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT default. Stop hook: records the tokens each turn used, per ticket, in an
# append-only ledger that the kanban generator reads.
#
# A Stop hook fires at every turn end and gets transcript_path on stdin. The
# transcript holds every assistant message in the session, so summing all of it
# on each Stop would bill earlier turns again. The hook keeps a per-session
# cursor (how many assistant messages are already billed), sums usage only for
# messages past it, and writes the new count back. Repeated or interrupted
# Stops therefore never bill a message twice.
#
# Which ticket a turn belongs to is a best guess. Signals, in order:
#   1. .adt/state/current-tix.d/<session_id>: the ticket this session named in
#      its prompts (written by adt-usage-log.sh) or picked up (adt-mark-tix.sh).
#   2. the ticket id in this session's git branch name.
#   3. the only ticket in <type>/building/, used only when no other session is
#      active, since the lane is shared by every session.
#   4. the old shared marker .adt/state/current-tix, also single-session only.
#   5. __unassigned__, still counted in the board total but shown separately.
#
# Ledger:  .adt/state/cost-ledger.log
#   TSV, one row per (turn, model, speed) group, 14 columns:
#     ISO8601 <TAB> tix-id <TAB> input <TAB> output <TAB> session_id
#       <TAB> model <TAB> speed <TAB> cache_read <TAB> cw_5m <TAB> cw_1h <TAB> tier
#       <TAB> command <TAB> install <TAB> lane
#   Columns were only ever appended, so older, narrower rows still parse. A
#   5-column row reads as `tier=legacy`, and an 11-column row has no command.
# Cursor:  .adt/state/token-cursor/<session_id>  (single integer)
#
# The ledger is local to this machine. The sync engine
# (adt_sync.checkpoint_tokens) copies each ticket's subtotal onto its GitHub
# Issue as a register comment and tracks what it has copied in
# .adt/state/token-checkpoint/<TIX> ("<checkpointed_sum>\t<comment_id>").
# Other machines see that register, not this ledger; adt-token-total.sh
# combines both for the close stamp. Everything under .adt/state/ is gitignored.
#
# A malformed transcript, a missing file or any parse error never blocks the
# Stop. It always exits 0.
#
# The ledger is never rotated; one short row per turn stays small for months.
set -euo pipefail

input="$(cat)"
transcript="$(printf '%s' "$input" | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("transcript_path",""))' 2>/dev/null || true)"
[ -z "$transcript" ] && exit 0
[ -f "$transcript" ] || exit 0

# session_id from stdin if present; otherwise from the transcript filename,
# which is <sessionId>.jsonl.
session="$(printf '%s' "$input" | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null || true)"
if [ -z "$session" ]; then
  base="$(basename "$transcript")"
  session="${base%.jsonl}"
fi
[ -z "$session" ] && exit 0

# Resolve the project root to the canonical checkout, the same way
# adt-usage-log.sh does, so the marker writer and this reader use the same
# .adt/. In a linked worktree --git-common-dir points at the main .git, whose
# parent is the canonical tree; in a normal checkout nothing changes. Falls back
# to toplevel, then cwd/PWD.
cwd="$(printf '%s' "$input" | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("cwd",""))' 2>/dev/null || true)"
root="$(git -C "${cwd:-.}" rev-parse --show-toplevel 2>/dev/null || echo "${cwd:-$PWD}")"
common_dir="$(git -C "${cwd:-.}" rev-parse --git-common-dir 2>/dev/null || true)"
if [ -n "$common_dir" ]; then
  case "$common_dir" in /*) ;; *) common_dir="$(git -C "${cwd:-.}" rev-parse --show-toplevel 2>/dev/null)/$common_dir" ;; esac
  canonical="$(cd "$common_dir/.." 2>/dev/null && pwd || true)"
  [ -n "$canonical" ] && [ -d "$canonical/.adt" ] && root="$canonical"
fi
[ -d "$root/.adt" ] || exit 0   # not an ADT project; do nothing

# All ADT runtime state lives under .adt/state/.
state="$root/.adt/state"
mkdir -p "$state" 2>/dev/null || true

# The ledger used to be called `token-usage.log`. If only the old file exists,
# move it to the new name so all rows stay in one file.
ledger="$state/cost-ledger.log"
prev_ledger="$state/token-usage.log"
if [ ! -f "$ledger" ] && [ -f "$prev_ledger" ]; then
  mv "$prev_ledger" "$ledger" 2>/dev/null || true
  # Move the backfill's backup file with it.
  [ -f "$prev_ledger.pre-ADT-115.bak" ] && \
    mv "$prev_ledger.pre-ADT-115.bak" "$ledger.pre-ADT-115.bak" 2>/dev/null || true
fi
cursor_dir="$state/token-cursor"
cursor="$cursor_dir/$session"
# Each session's active ticket has its own marker file. The single shared
# current-tix file is the old marker, read only as a last fallback.
current_tix_dir="$state/current-tix.d"
session_tix_file="$current_tix_dir/$session"
current_tix_file="$state/current-tix"

mkdir -p "$cursor_dir" 2>/dev/null || exit 0

# One window, used twice: a session is active if its cursor moved within this
# many minutes, and a transcript is cold enough to sweep once it has not been
# touched for as long.
ADT_SESSION_WINDOW_MIN=30

# Pick the ticket, in the order listed in the header. The first two signals
# belong to this session alone, so parallel sessions cannot bill each other.
tix="__unassigned__"
# 1. This session's marker.
sess_marker="$session_tix_file"
if [ -f "$sess_marker" ]; then
  t="$(head -1 "$sess_marker" 2>/dev/null | tr -d '[:space:]' || true)"
  [ -n "$t" ] && tix="$t"
fi
# 2. The branch name. The workflow names one branch per ticket
# (dev/tix-194-..., tix-180-...), which covers worktree sessions.
if [ "$tix" = "__unassigned__" ]; then
  # Read the branch from the turn's cwd, not the canonical root: in a worktree
  # session the cwd is on the ticket branch while the canonical tree is on main.
  #
  # Match the project's own id prefix (id_prefix: in .adt/config.yaml),
  # defaulting to TIX, and strip leading zeros (adt-058 → ADT-58) to match the
  # key the readers use.
  id_prefix=""
  cfg="$root/.adt/config.yaml"
  [ -f "$cfg" ] && id_prefix="$(grep -E '^id_prefix:' "$cfg" 2>/dev/null | head -1 | sed -E 's/^id_prefix:[[:space:]]*//; s/[[:space:]]*$//' || true)"
  [ -z "$id_prefix" ] && id_prefix="TIX"
  lc_prefix="$(printf '%s' "$id_prefix" | tr '[:upper:]' '[:lower:]')"
  branch="$(git -C "${cwd:-$root}" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  bt="$(printf '%s' "$branch" | /usr/bin/sed -nE "s/.*${lc_prefix}-?0*([0-9]+).*/${id_prefix}-\1/p" | head -1)"
  [ -n "$bt" ] && tix="$bt"
fi
# 3. The only ticket in <type>/building/ in the local cache. With none or more
# than one, it stays __unassigned__. The id comes from the frontmatter.
#
# Every session shares the building/ lane, so this is used only when this is
# the one active session, judged by how many session cursors changed recently.
# With several sessions running it would bill one session's tokens to whatever
# ticket happens to be alone in building/.
active_sessions=1
if [ "$tix" = "__unassigned__" ]; then
  # Count other sessions' cursor files changed within the window. This
  # session's own cursor is written at the end of the hook, so it may not exist
  # yet. find -mmin works on macOS and Linux. A malformed count becomes 0 so the
  # arithmetic cannot fail.
  others="$(find "$cursor_dir" -maxdepth 1 -type f -mmin "-$ADT_SESSION_WINDOW_MIN" 2>/dev/null | grep -vc "/$session\$" 2>/dev/null || true)"
  case "$others" in ''|*[!0-9]*) others=0 ;; esac
  active_sessions=$((others + 1))   # +1 for this session
fi
if [ "$tix" = "__unassigned__" ] && [ "$active_sessions" -le 1 ]; then
  building_ids="$(/usr/bin/python3 - "$root" <<'PY' 2>/dev/null || true
import sys, glob, re, os
root = sys.argv[1]

# Find the cache dir: cache_dir: in .adt/config.yaml, else ~/.adt/<project>/cache.
cache = None
cfg = os.path.join(root, ".adt", "config.yaml")
project = os.path.basename(os.path.realpath(root))
try:
    for line in open(cfg):
        s = line.strip()
        if s.startswith("cache_dir:"):
            cache = s.split(":", 1)[1].strip()
        elif s.startswith("project:") and project == os.path.basename(os.path.realpath(root)):
            project = s.split(":", 1)[1].strip() or project
except Exception:
    pass
if not cache:
    cache = os.path.join("~", ".adt", project, "cache")
cache = os.path.abspath(os.path.expanduser(cache))

ids = []
for f in glob.glob(os.path.join(cache, "*", "building", "*.md")):
    try:
        with open(f) as fh:
            head = fh.read(2048)
    except Exception:
        continue
    m = re.search(r'(?m)^id:\s*([A-Za-z]+-[0-9]+)\s*$', head)
    if m:
        ids.append(m.group(1).upper())
ids = sorted(set(ids))
if len(ids) == 1:
    print(ids[0])
PY
)"
  [ -n "$building_ids" ] && tix="$building_ids"
fi

# 4. The old shared marker, read only when nothing else resolved and no other
# session is active, because parallel sessions overwrite it.
if [ "$tix" = "__unassigned__" ] && [ "$active_sessions" -le 1 ]; then
  flat="$current_tix_file"
  if [ -f "$flat" ]; then
    t="$(head -1 "$flat" 2>/dev/null | tr -d '[:space:]' || true)"
    [ -n "$t" ] && tix="$t"
  fi
fi

# Delete per-session marker files not changed in 14 days, since the directory
# gains one file per session. A failed prune is ignored.
for d in "$current_tix_dir"; do
  [ -d "$d" ] && find "$d" -maxdepth 1 -type f -mtime +14 -delete 2>/dev/null || true
done

# Sum usage for assistant messages past the stored cursor.
#
# All five billed token classes are recorded. The API bills input in three
# separate classes (input_tokens + cache_creation_input_tokens +
# cache_read_input_tokens), and input_tokens is only the uncached part, so
# input + output alone misses most of the cost.
#
# A row covers every message since the last Stop, which can span two models or
# two speeds. Messages are grouped by (model, speed) and each group gets its own
# row, so a ticket worked on with several models is priced correctly.
#
# Output: line 1 is the new cursor; each further line is one group:
#   <model>\t<speed>\t<input>\t<output>\t<cache_read>\t<cw_5m>\t<cw_1h>
# It is a function so the dead-session sweep at the bottom can bill another
# session's transcript with the same code.
_sum_transcript() {   # $1 transcript  $2 cursor  $3 state  $4 session
  /usr/bin/python3 - "$1" "$2" "$3" "$4" <<'PY' 2>/dev/null || true
import json, sys, os

transcript, cursor_path = sys.argv[1], sys.argv[2]
# The same walk also counts Agent and Skill calls. These argv entries are
# optional so a caller that passes only the first two still works.
state_dir = sys.argv[3] if len(sys.argv) > 3 else ""
session_id = sys.argv[4] if len(sys.argv) > 4 else "unknown"
surfaces = {}   # (kind, name) -> count, for messages beyond the cursor

try:
    billed = int(open(cursor_path).read().strip())
except Exception:
    billed = 0

seen = 0
new = 0
groups = {}   # (model, speed) -> [inp, out, cread, cw5, cw1]; insertion-ordered
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
            msg = rec.get("message")
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            seen += 1
            if seen <= billed:
                continue  # already billed in a prior Stop
            new += 1
            # Count Agent and Skill calls. This has its own try so a malformed
            # tool_use block cannot cost the turn its ledger row.
            try:
                for blk in (msg.get("content") or []):
                    if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                        continue
                    tool = blk.get("name")
                    inp = blk.get("input")
                    inp = inp if isinstance(inp, dict) else {}
                    if tool == "Agent":
                        key = ("agent", str(inp.get("subagent_type") or "general-purpose"))
                    elif tool == "Skill":
                        key = ("skill", str(inp.get("skill") or "unknown"))
                    else:
                        continue
                    surfaces[key] = surfaces.get(key, 0) + 1
            except Exception:
                pass
            u = msg.get("usage")
            if not isinstance(u, dict):
                continue
            # `model` absent -> "unknown"; the pricer treats an unknown model as
            # legacy/estimated rather than guessing a nearby model rate.
            # (No apostrophes in this heredoc: it sits inside $(...), where bash
            # still tracks quotes while looking for the closing paren.)
            model = str(msg.get("model") or "unknown").strip() or "unknown"
            # `speed` absent -> "standard", the API default (fast mode is opt-in
            # per request).
            speed = str(u.get("speed") or "standard").strip() or "standard"
            cc = u.get("cache_creation")
            if isinstance(cc, dict):
                cw5 = int(cc.get("ephemeral_5m_input_tokens", 0) or 0)
                cw1 = int(cc.get("ephemeral_1h_input_tokens", 0) or 0)
            else:
                # No TTL split available, so bill the whole write at the 1h
                # rate. Subscription sessions use the 1h cache; on an API-key
                # session this overstates the cost rather than understating it.
                cw5 = 0
                cw1 = int(u.get("cache_creation_input_tokens", 0) or 0)
            g = groups.setdefault((model, speed), [0, 0, 0, 0, 0])
            g[0] += int(u.get("input_tokens", 0) or 0)
            g[1] += int(u.get("output_tokens", 0) or 0)
            g[2] += int(u.get("cache_read_input_tokens", 0) or 0)
            g[3] += cw5
            g[4] += cw1
except Exception:
    sys.exit(0)

# Append the Agent/Skill tally to its own file. It is not printed on stdout,
# because bash parses stdout as the cursor plus ledger groups.
if surfaces and state_dir:
    try:
        import datetime
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        os.makedirs(state_dir, exist_ok=True)
        with open(os.path.join(state_dir, "surface-log.tsv"), "a") as fh:
            for (kind, name), n in sorted(surfaces.items()):
                fh.write("\t".join([ts, session_id, kind, name, str(n)]) + "\n")
    except Exception:
        pass

if new == 0:
    sys.exit(0)  # nothing new since last Stop — append nothing, keep cursor
# new cursor = total assistant messages now seen
print(seen)
for (model, speed), v in groups.items():
    print("\t".join([model, speed] + [str(x) for x in v]))
PY
}

result="$(_sum_transcript "$transcript" "$cursor" "$state" "$session")"

# No early exit on an empty result: `_append_and_advance` checks that itself,
# and the sweep at the bottom must still run.

# Append one ledger row per (model, speed) group, then advance the cursor. The
# rows go first: if the cursor write fails, a row is counted twice rather than
# lost.
#
# Columns 1-11:
#   ts  tix  input  output  session | model  speed  cache_read  cw_5m  cw_1h  tier
# `tier` is always `measured` here, because this hook has the full per-class
# data. The backfill writes `estimated`, and a 5-column row reads as `legacy`.
# Readers (adt_cost, adt-reattribute.sh, adt-token-sum.sh) index by position,
# so new columns only ever go on the end.
#
# Column 12 is the active command, read from `current-cmd.d/<session>`. It
# stays set until the next binding, so turns that name no command are billed to
# the command that was running. It is empty for a session never bound to a
# command, and readers then infer it from the stage label.
cmd_marker="$state/current-cmd.d/$session"
active_cmd=""
if [ -n "$session" ] && [ -f "$cmd_marker" ]; then
  active_cmd="$(tr -d '\n\t' < "$cmd_marker" 2>/dev/null || true)"
fi
# Column 13 is the install's anonymous id, read from .adt/state/install-id, the
# same file `build_kanban.machine_id()` and `adt-token-total.sh` read. `$state`
# is in the canonical checkout, so every worktree gets the same id. It is empty
# if the file cannot be read.
#
# Column 14 is the ticket's lane at the time of the turn: the name of the cache
# directory its file is in. Column 12 does not change when the ticket moves, so
# this is what time per lane is measured from. It is empty if the lane cannot be
# found, and readers then infer it from the stage label.
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
# `printf '%s\n'`, not '%s': $(...) strips the trailing newline, and `read`
# fails on a last line without one, which would drop the last group. `if`, not
# `[ ] && continue`: under `set -e` a false test as a statement would abort the
# loop mid-write. It is a function so the sweep below writes rows of the same
# shape.
#   $1 result  $2 tix  $3 session  $4 cursor  $5 active_cmd
_append_and_advance() {
  _r="$1"; _tix="$2"; _sess="$3"; _cur="$4"; _cmd="$5"
  _lane="$(_adt_lane "$_tix")"
  _new="$(printf '%s' "$_r" | head -1)"
  [ -z "$_new" ] && return 0
  printf '%s\n' "$_r" | tail -n +2 | while IFS="$(printf '\t')" read -r model speed inp out cread cw5 cw1; do
    if [ -n "$model" ]; then
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$ts" "$_tix" "$inp" "$out" "$_sess" \
        "$model" "$speed" "$cread" "$cw5" "$cw1" "measured" \
        "$_cmd" "$install_id" "$_lane" >> "$ledger" 2>/dev/null || true
    fi
  done
  printf '%s\n' "$_new" > "$_cur" 2>/dev/null || true
}

_append_and_advance "$result" "$tix" "$session" "$cursor" "$active_cmd"

# ------------------------------------------------------------------------
# Dead-session sweep.
#
# Each session bills only up to its own last Stop, so a session that ends
# without another Stop (for example, one that was killed) leaves tokens
# unbilled. A killed session fires no SessionEnd hook, so the sweep runs from
# other sessions' Stops: each Stop bills whatever cold transcripts in the same
# directory still hold.
#
# The directory is the one holding the transcript the harness passed in. A
# transcript is cold once it has gone untouched for ADT_SESSION_WINDOW_MIN,
# the same window used for the active-session count, so a live session is never
# swept.
#
# Swept tokens go to the dead session's own ticket binding, not this session's.
# A swept row carries the time of the sweep, not of the original turn.
# adt_sync.restamp_closed only revisits tickets closed within 7 days, so a row
# swept later than that does not change the ticket's stamp.
sweep_dir="$(dirname "$transcript")"
# One `find` for the whole directory, which also does the cold test with
# `-mmin +N`. A fork per file would slow every turn as sessions pile up.
find "$sweep_dir" -maxdepth 1 -name '*.jsonl' -mmin "+$ADT_SESSION_WINDOW_MIN" \
     2>/dev/null | while IFS= read -r other; do
  [ -f "$other" ] || continue
  other_base="${other##*/}"
  other_session="${other_base%.jsonl}"
  [ "$other_session" = "$session" ] && continue
  other_cursor="$cursor_dir/$other_session"
  # Skip a transcript not changed since its cursor was last written; there is
  # nothing new to bill. A missing cursor counts as older, so a session never
  # swept is still read.
  [ "$other" -nt "$other_cursor" ] || continue
  other_result="$(_sum_transcript "$other" "$other_cursor" "$state" "$other_session")"
  if [ -n "$other_result" ]; then
    # Read the dead session's ticket and command only when there is something
    # to bill.
    other_marker="$current_tix_dir/$other_session"
    other_tix=""
    [ -f "$other_marker" ] && \
      other_tix="$(tr -d '\n\t' < "$other_marker" 2>/dev/null || true)"
    [ -z "$other_tix" ] && other_tix="__unassigned__"
    other_cmd=""
    [ -f "$state/current-cmd.d/$other_session" ] && \
      other_cmd="$(tr -d '\n\t' < "$state/current-cmd.d/$other_session" 2>/dev/null || true)"
    _append_and_advance "$other_result" "$other_tix" "$other_session" \
                        "$other_cursor" "$other_cmd"
  fi
  # Touch the cursor even when nothing was billed, so this transcript fails the
  # `-nt` test above from now on and is not read again.
  touch "$other_cursor" 2>/dev/null || true
done
exit 0

# adt-bundle: v0.2.0
