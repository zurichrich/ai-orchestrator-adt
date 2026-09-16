#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT helper. Moves token-ledger rows from __unassigned__ to a named ticket,
# for a given time window. Use it when you know which ticket the work in that
# window was for, for example turns that ran in a session started before an
# attribution fix (hooks load at session start, so that session keeps the old
# ones).
#
# By default it only prints the rows it would change. Pass --apply to write.
# It only rewrites rows whose ticket column is __unassigned__, never a row that
# already names a ticket.
#
# Ledger row (TSV): ISO8601 <TAB> tix <TAB> input <TAB> output <TAB> session_id
#   <TAB> model <TAB> speed <TAB> cache_read <TAB> cw_5m <TAB> cw_1h <TAB> tier.
#   Only column 2 is rewritten; every other column passes through unchanged.
#
# Usage:
#   adt-reattribute.sh TIX-202 2026-06-13T12:00 2026-06-13T13:30
#   adt-reattribute.sh TIX-202 2026-06-13T12:00 2026-06-13T13:30 --apply
#   adt-reattribute.sh TIX-202 2026-06-13T13:30 2026-06-13T14:00 --apply --session fe180623
#
# The window bounds are compared as strings against the ISO timestamp, which
# sorts in time order. `from` is inclusive and `to` is exclusive. --session <id>
# limits the change to one session.
set -euo pipefail

tix="${1:-}"; from="${2:-}"; to="${3:-}"
shift 3 2>/dev/null || { echo "usage: $0 <PREFIX-NNN> <from-iso> <to-iso> [--apply] [--session <id>]" >&2; exit 2; }

apply=false
session=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) apply=true; shift ;;
    --session) session="${2:-}"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

root="$(git rev-parse --show-toplevel 2>/dev/null || echo ".")"
# In a linked worktree, use the canonical checkout, which is where the
# attribution hooks write the ledger.
common_dir="$(git rev-parse --git-common-dir 2>/dev/null || true)"
if [ -n "$common_dir" ]; then
  case "$common_dir" in /*) ;; *) common_dir="$root/$common_dir" ;; esac
  canonical="$(cd "$common_dir/.." 2>/dev/null && pwd || true)"
  [ -n "$canonical" ] && [ -d "$canonical/.adt" ] && root="$canonical"
fi

# The project's ticket-id prefix: $ADT_ID_PREFIX, else id_prefix: from
# .adt/config.yaml, else "TIX".
ID_PREFIX="${ADT_ID_PREFIX:-}"
if [ -z "$ID_PREFIX" ] && [ -f "$root/.adt/config.yaml" ]; then
  ID_PREFIX="$(grep -E '^id_prefix:' "$root/.adt/config.yaml" 2>/dev/null | head -1 | sed -E 's/^id_prefix:[[:space:]]*//; s/[[:space:]]*$//' || true)"
fi
[ -z "$ID_PREFIX" ] && ID_PREFIX="TIX"

case "$tix" in
  ${ID_PREFIX}-[0-9]*) ;;
  *) echo "error: first arg must be a ticket id like ${ID_PREFIX}-202, got '$tix'" >&2; exit 2 ;;
esac
[ -n "$from" ] && [ -n "$to" ] || { echo "error: need <from-iso> <to-iso>" >&2; exit 2; }
# Use cost-ledger.log, or the older token-usage.log name if only that exists.
ledger="$root/.adt/state/cost-ledger.log"
[ -f "$ledger" ] || ledger="$root/.adt/state/token-usage.log"
[ -f "$ledger" ] || { echo "no ledger at $ledger" >&2; exit 1; }

# Find matching rows: tix==__unassigned__, from <= ts < to, optional session.
# --session matches as a prefix, so "fe180623" matches "fe180623-5513-…".
matched="$(awk -F'\t' -v from="$from" -v to="$to" -v sess="$session" '
  $2 == "__unassigned__" && $1 >= from && $1 < to && (sess == "" || index($5, sess) == 1) {
    print NR"\t"$0
  }' "$ledger")"

if [ -z "$matched" ]; then
  echo "No __unassigned__ rows in [$from, $to)${session:+ session=$session}. Nothing to do."
  exit 0
fi

echo "Rows that would be re-keyed to $tix:"
printf '%s\n' "$matched" | awk -F'\t' '{printf "  line %s: %s  in=%s out=%s\n", $1, $2, $4, $5}'
total_out="$(printf '%s\n' "$matched" | awk -F'\t' '{s+=$5} END {print s}')"
echo "  ($(printf '%s\n' "$matched" | wc -l | tr -d ' ') rows, $total_out output tokens)"

if ! $apply; then
  echo
  echo "[dry-run] pass --apply to rewrite. No changes made."
  exit 0
fi

# Rewrite column 2 on the same rows, through a temp file so the swap is atomic.
tmp="$(mktemp)"
awk -F'\t' -v OFS='\t' -v from="$from" -v to="$to" -v sess="$session" -v tix="$tix" '
  $2 == "__unassigned__" && $1 >= from && $1 < to && (sess == "" || index($5, sess) == 1) { $2 = tix }
  { print }
' "$ledger" > "$tmp"
mv "$tmp" "$ledger"
echo
echo "-> re-keyed to $tix. adt watch will refresh the 🪙 badges on its next pass."

# adt-bundle: v0.1.0
