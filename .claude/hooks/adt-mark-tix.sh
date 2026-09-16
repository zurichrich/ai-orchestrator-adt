#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT default. Binds the current session to a ticket.
#
# This is a helper, not a Claude Code hook. A pickup playbook (build, qa-run,
# handoff-qa) calls it when it takes up a ticket. It writes the per-session
# active-ticket marker that adt-token-log.sh (the Stop hook) reads, so each
# turn's token cost is billed to that ticket without the human typing the id in
# every prompt. adt-usage-log.sh only writes the marker when a prompt names
# "<PREFIX>-NNN", which a prompt like "build it" never does.
#
# Usage:  adt-mark-tix.sh <TICKET-ID> [<command>]    e.g.  adt-mark-tix.sh ADT-123
#
# The session key is $CLAUDE_CODE_SESSION_ID, the same id adt-token-log.sh keys
# its ledger rows and cursor files on, and it is exported into a skill-invoked
# Bash shell. ($CLAUDE_SESSION_ID is not set.) If it is missing, the key falls
# back to the transcript filename.
#
# Any missing input exits 0 and writes nothing; it never blocks the playbook.
set -euo pipefail

tix="${1:-}"
[ -z "$tix" ] && exit 0
# Upper-case it (adt-54 → ADT-54) to match the ledger.
tix="$(printf '%s' "$tix" | tr '[:lower:]' '[:upper:]')"
# It must look like PREFIX-NNN; otherwise do nothing.
printf '%s' "$tix" | grep -qE '^[A-Z]+-[0-9]+$' || exit 0

# Session id: the key adt-token-log.sh reads. Fall back to the transcript
# filename, as the hooks do, if the env var is not exported.
session="${CLAUDE_CODE_SESSION_ID:-}"
if [ -z "$session" ] && [ -n "${CLAUDE_TRANSCRIPT_PATH:-}" ]; then
  base="$(basename "$CLAUDE_TRANSCRIPT_PATH")"; session="${base%.jsonl}"
fi
[ -z "$session" ] && exit 0

# Resolve the project root to the canonical checkout, the same way
# adt-usage-log.sh and adt-token-log.sh do, so this writer and the Stop hook
# read the same .adt/. In a linked worktree --git-common-dir points at the main
# .git, whose parent is the canonical tree; in a normal checkout nothing
# changes. Falls back to toplevel, then PWD.
root="$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")"
common_dir="$(git rev-parse --git-common-dir 2>/dev/null || true)"
if [ -n "$common_dir" ]; then
  case "$common_dir" in /*) ;; *) common_dir="$(git rev-parse --show-toplevel 2>/dev/null)/$common_dir" ;; esac
  canonical="$(cd "$common_dir/.." 2>/dev/null && pwd || true)"
  [ -n "$canonical" ] && [ -d "$canonical/.adt" ] && root="$canonical"
fi
[ -d "$root/.adt" ] || exit 0   # not an ADT project; do nothing

# Write only the per-session marker, one file per session. A single shared
# marker would let parallel sessions overwrite each other and bill the wrong
# ticket.
state="$root/.adt/state/current-tix.d"
mkdir -p "$state" 2>/dev/null || exit 0
printf '%s\n' "$tix" > "$state/$session" 2>/dev/null || true
# Lock the binding with a sibling lock file. adt-usage-log.sh does not
# overwrite a locked marker, so a ticket id mentioned in a later prompt cannot
# replace the ticket picked up here. The lock is per session. Running this
# again for a different ticket in the same session rebinds it; the lock stays.
printf '%s\n' "$tix" > "$state/$session.locked" 2>/dev/null || true

# Lock the active-command binding too. Call sites pass only a ticket id, so the
# command is normally the one adt-usage-log.sh already wrote to
# `current-cmd.d/<session>` from the prompt that started this playbook (e.g.
# `/adt-build ADT-123`). Locking it stops a later prompt that mentions another
# command from replacing it. An optional second argument names the command
# explicitly and wins when given.
cmd_state="$root/.adt/state/current-cmd.d"
cmd="${2:-}"
if [ -n "$cmd" ]; then
  cmd="$(printf '%s' "$cmd" | tr '[:upper:]' '[:lower:]' | sed 's|^/||')"
  printf '%s' "$cmd" | grep -qE '^[a-z0-9][a-z0-9-]*$' || cmd=""
fi
if [ -z "$cmd" ] && [ -f "$cmd_state/$session" ]; then
  cmd="$(cat "$cmd_state/$session" 2>/dev/null || true)"
fi
if [ -n "$cmd" ]; then
  mkdir -p "$cmd_state" 2>/dev/null || true
  printf '%s\n' "$cmd" > "$cmd_state/$session" 2>/dev/null || true
  printf '%s\n' "$cmd" > "$cmd_state/$session.locked" 2>/dev/null || true
fi
exit 0

# adt-bundle: v0.1.0
