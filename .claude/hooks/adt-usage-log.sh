#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT default. UserPromptSubmit hook.
# Reads each submitted prompt and does three things. It never blocks the prompt
# and always exits 0 without output.
#
# 1. Usage log. If the prompt contains an /adt-* command, it appends
#    ISO8601<TAB>command to <project>/.adt/state/usage.log.
# 2. Ticket marker. If the prompt names a ticket, it writes the id to
#    <project>/.adt/state/current-tix.d/<session>, so the adt-token-log.sh Stop
#    hook bills the turn to that ticket. An explicit "PREFIX-NNN" anywhere in
#    the prompt counts, with or without an /adt-* command. A bare number
#    ("188 /adt-build") counts only next to an /adt-* command, because a bare
#    number in ordinary conversation ("down 40%") is usually not a ticket. If no
#    ticket is named, the marker keeps its last value.
# 3. Command marker. If the prompt contains an /adt-* command, it writes it to
#    current-cmd.d/<session> for the ledger's command column.
set -euo pipefail

# The project's ticket-id prefix, resolved once $root is known (see below):
#   1. $ADT_ID_PREFIX, else
#   2. id_prefix: from .adt/config.yaml, else
#   3. "TIX".
ID_PREFIX="${ADT_ID_PREFIX:-}"

input="$(cat)"
prompt="$(printf '%s' "$input" | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("prompt",""))' 2>/dev/null || true)"
cwd="$(printf '%s' "$input" | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("cwd",""))' 2>/dev/null || true)"
# The markers are keyed by session_id so parallel sessions do not overwrite
# each other. If session_id is missing, use the transcript file name without
# .jsonl, as adt-token-log.sh does, so writer and reader use the same key.
session="$(printf '%s' "$input" | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null || true)"
if [ -z "$session" ]; then
  tp="$(printf '%s' "$input" | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("transcript_path",""))' 2>/dev/null || true)"
  if [ -n "$tp" ]; then base="$(basename "$tp")"; session="${base%.jsonl}"; fi
fi

# Resolve the project root to the canonical checkout, not the worktree. This
# hook and adt-token-log.sh often run from different worktrees, and both must
# use the same .adt/ directory. In a linked worktree, --git-common-dir points at
# the main repo's .git, whose parent is the canonical checkout. In a normal
# checkout that parent is the toplevel, so nothing changes. On any error fall
# back to the toplevel, then cwd.
root="$(git -C "${cwd:-.}" rev-parse --show-toplevel 2>/dev/null || echo "${cwd:-.}")"
common_dir="$(git -C "${cwd:-.}" rev-parse --git-common-dir 2>/dev/null || true)"
if [ -n "$common_dir" ]; then
  case "$common_dir" in /*) ;; *) common_dir="$(git -C "${cwd:-.}" rev-parse --show-toplevel 2>/dev/null)/$common_dir" ;; esac
  canonical="$(cd "$common_dir/.." 2>/dev/null && pwd || true)"
  [ -n "$canonical" ] && [ -d "$canonical/.adt" ] && root="$canonical"
fi
[ -d "$root/.adt" ] || exit 0   # not an ADT project; do nothing

# Resolve the ticket-id prefix now that $root is known. Without the env
# override, read id_prefix: from .adt/config.yaml with grep, and use "TIX" if
# the file or key is missing.
if [ -z "$ID_PREFIX" ]; then
  cfg="$root/.adt/config.yaml"
  if [ -f "$cfg" ]; then
    ID_PREFIX="$(grep -E '^id_prefix:' "$cfg" 2>/dev/null | head -1 | sed -E 's/^id_prefix:[[:space:]]*//; s/[[:space:]]*$//' || true)"
  fi
fi
[ -z "$ID_PREFIX" ] && ID_PREFIX="TIX"

# All ADT runtime state lives under .adt/state/.
state="$root/.adt/state"
mkdir -p "$state" 2>/dev/null || true

# Match an /adt-<name> token anywhere in the prompt, not just at the start, so
# the "188 /adt-build" shorthand (ticket first, command second) still counts.
cmd="$(printf '%s' "$prompt" | grep -oE '/adt-[a-z0-9-]+' | head -1 | sed 's|^/||' || true)"

# Take the ticket id from the prompt. An explicit "PREFIX-NNN" wins. Otherwise
# use a bare number, but only when an /adt-* command is present.
tix=""
explicit="$(printf '%s' "$prompt" | grep -oiE "${ID_PREFIX}-[0-9]+" | head -1 || true)"
if [ -n "$explicit" ]; then
  tix="$(printf '%s' "$explicit" | tr '[:lower:]' '[:upper:]')"
elif [ -n "$cmd" ]; then
  # Bare number form: "188 /adt-build" or "/adt-plan 188".
  num="$(printf '%s' "$prompt" | grep -oE '(^|[[:space:]])[0-9]{1,5}([[:space:]]|$)' | grep -oE '[0-9]+' | head -1 || true)"
  [ -n "$num" ] && tix="${ID_PREFIX}-${num}"
fi
# Strip leading zeros from the number, so TIX-58 and TIX-058 write the same
# marker. build_kanban.canon_tix and adt-token-sum.sh normalise the same way.
if [ -n "$tix" ]; then
  tix="$(printf '%s' "$tix" | sed -E 's/^([A-Za-z]+)-0*([0-9]+)$/\1-\2/')"
fi
# Write the ticket to the per-session marker, and to the older flat marker
# current-tix, which adt-token-log.sh reads only when there is no per-session
# file.
#
# A ticket id found in prose is a weak signal: in a session that mentions many
# tickets it picks up the last one mentioned. When a stage playbook picks up a
# ticket, adt-mark-tix.sh writes the marker and a lock file,
# current-tix.d/<session>.locked. While the lock exists, prose does not change
# the marker. The lock is per session, so it never affects another session.
locked=""
[ -n "$session" ] && [ -f "$state/current-tix.d/$session.locked" ] && locked=1
if [ -n "$tix" ] && [ -z "$locked" ]; then
  if [ -n "$session" ]; then
    mkdir -p "$state/current-tix.d" 2>/dev/null || true
    printf '%s\n' "$tix" > "$state/current-tix.d/$session" 2>/dev/null || true
  fi
  printf '%s\n' "$tix" > "$state/current-tix" 2>/dev/null || true            # flat (deprecated)
fi

# The active command marker, current-cmd.d/<session>. The ledger's command
# column needs the command active on each turn. usage.log cannot supply it,
# because it is not keyed by session and has no row for a turn that names no
# command.
#
# The marker keeps its value across turns that name no command, like the
# ticket marker. It obeys the same kind of lock: adt-mark-tix.sh writes the
# marker and current-cmd.d/<session>.locked at pickup, and a command mentioned
# in prose ("we'll /adt-close after this") does not change a locked marker.
cmd_locked=""
[ -n "$session" ] && [ -f "$state/current-cmd.d/$session.locked" ] && cmd_locked=1
if [ -n "$cmd" ] && [ -z "$cmd_locked" ] && [ -n "$session" ]; then
  mkdir -p "$state/current-cmd.d" 2>/dev/null || true
  printf '%s\n' "$cmd" > "$state/current-cmd.d/$session" 2>/dev/null || true
fi

# A turn with no /adt-* command writes no usage row. It may still have set the
# ticket marker above.
[ -z "$cmd" ] && exit 0
printf '%s\t%s\n' "$(date -u +%FT%TZ)" "$cmd" >> "$state/usage.log" 2>/dev/null || true

exit 0

# adt-bundle: v0.2.0
