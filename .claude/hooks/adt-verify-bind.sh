#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT default. Checks that the current session is bound to a ticket.
#
# This is not a Claude Code hook. An /adt-* pickup playbook calls it right after
# adt-mark-tix.sh. adt-mark-tix.sh never fails, so if it wrote nothing the
# session's tokens would go to __unassigned__. This helper stops the playbook
# when the marker is missing or names a different ticket.
#
# Usage:  adt-verify-bind.sh <TICKET-ID>     e.g.  adt-verify-bind.sh TIX-61
#   exit 0  the session is bound to <TICKET-ID>
#   exit 1  not bound (no marker, or bound to another ticket); prints why
#
# It finds the session id and the canonical root the same way adt-mark-tix.sh
# does, so it checks the file that script wrote.
set -euo pipefail

tix="${1:-}"
if [ -z "$tix" ]; then
  echo "adt-verify-bind: no ticket id given (usage: adt-verify-bind.sh <ID>)" >&2
  exit 1
fi
# Upper-case the id, as adt-mark-tix.sh and the ledger do.
tix="$(printf '%s' "$tix" | tr '[:lower:]' '[:upper:]')"

# Session id, found as in adt-mark-tix.sh. The env var is
# $CLAUDE_CODE_SESSION_ID, not $CLAUDE_SESSION_ID.
session="${CLAUDE_CODE_SESSION_ID:-}"
if [ -z "$session" ] && [ -n "${CLAUDE_TRANSCRIPT_PATH:-}" ]; then
  base="$(basename "$CLAUDE_TRANSCRIPT_PATH")"; session="${base%.jsonl}"
fi
if [ -z "$session" ]; then
  echo "adt-verify-bind: no session id (CLAUDE_CODE_SESSION_ID unset) — cannot verify binding to $tix" >&2
  exit 1
fi

# Resolve the canonical checkout as adt-mark-tix.sh does. A pickup may run from
# a linked worktree, but the marker lives in the canonical checkout.
root="$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")"
common_dir="$(git rev-parse --git-common-dir 2>/dev/null || true)"
if [ -n "$common_dir" ]; then
  case "$common_dir" in /*) ;; *) common_dir="$(git rev-parse --show-toplevel 2>/dev/null)/$common_dir" ;; esac
  canonical="$(cd "$common_dir/.." 2>/dev/null && pwd || true)"
  [ -n "$canonical" ] && [ -d "$canonical/.adt" ] && root="$canonical"
fi
if [ ! -d "$root/.adt" ]; then
  echo "adt-verify-bind: not an ADT project at $root (no .adt/) — cannot verify binding to $tix" >&2
  exit 1
fi

marker="$root/.adt/state/current-tix.d/$session"
[ -e "$marker" ] || [ ! -e "$root/.adt/state/current-tix.d/$session" ] \
  || marker="$root/.adt/state/current-tix.d/$session"
if [ ! -f "$marker" ]; then
  echo "adt-verify-bind: session not bound to $tix; binding is the pickup event. Run adt-mark-tix.sh $tix first." >&2
  exit 1
fi

bound="$(head -1 "$marker" 2>/dev/null | tr -d '[:space:]')"
if [ "$bound" != "$tix" ]; then
  echo "adt-verify-bind: session is bound to '$bound', not '$tix'. Re-bind with adt-mark-tix.sh $tix before continuing." >&2
  exit 1
fi

exit 0

# adt-bundle: v0.1.0
