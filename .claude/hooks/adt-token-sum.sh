#!/bin/bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# adt-token-sum.sh prints the total tokens (input+output) the token ledger
# attributes to one ticket. /adt-close stamps the result into the ticket's
# `tokens:` frontmatter, so the figure survives a ledger prune or a fresh clone.
#
# Usage:  adt-token-sum.sh <TIX-NNN> [cwd] [--cost]
#   $1      ticket id, matched case-insensitively against the ledger.
#   cwd     optional directory to resolve the project root from ($PWD by default).
#   --cost  print "<micros>\t<tier>" from tools/adt_cost.py instead of tokens.
#
# Prints the sum when the ledger has rows for the id. Prints `unattributed`
# (not 0) when there are no matching rows or no ledger, so a ticket whose work
# was never captured is not stamped as free. The caller stamps the output as
# is; build_kanban renders `unattributed` as 🪙 —.
# Exits 0 on success and 2 when the ticket id is missing. Reads only the local
# ledger, never the network.
set -euo pipefail

tix="${1:-}"
if [ -z "$tix" ]; then
  echo "usage: adt-token-sum.sh <TIX-NNN> [cwd] [--cost]" >&2
  exit 2
fi
# --cost prints "<micros>\t<tier>" instead of the token total.
want_cost=false
cwd="$PWD"
for a in "${@:2}"; do
  case "$a" in
    --cost) want_cost=true ;;
    *) cwd="$a" ;;
  esac
done

# Resolve the project root to the canonical checkout, the same way
# adt-token-log.sh and adt-usage-log.sh do, so this reads the ledger they write
# whichever worktree /adt-close runs from.
root="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null || echo "$cwd")"
common_dir="$(git -C "$cwd" rev-parse --git-common-dir 2>/dev/null || true)"
if [ -n "$common_dir" ]; then
  case "$common_dir" in /*) ;; *) common_dir="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)/$common_dir" ;; esac
  canonical="$(cd "$common_dir/.." 2>/dev/null && pwd || true)"
  [ -n "$canonical" ] && [ -d "$canonical/.adt" ] && root="$canonical"
fi

# Read cost-ledger.log, or the older token-usage.log name if only that exists.
ledger="$root/.adt/state/cost-ledger.log"
[ -f "$ledger" ] || ledger="$root/.adt/state/token-usage.log"
if [ ! -f "$ledger" ]; then
  echo unattributed      # no ledger: the work was never captured
  exit 0
fi

# --cost hands off to the pricer, tools/adt_cost.py. Look for it in the
# installed layout (.claude/tools/) and then the source tree. If it is missing,
# print the sentinel rather than a false $0.
if [ "$want_cost" = true ]; then
  _here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  _pricer=""
  for _c in "$_here/../tools/adt_cost.py" "$_here/../../tools/adt_cost.py"; do
    [ -f "$_c" ] && { _pricer="$_c"; break; }
  done
  if [ -z "$_pricer" ]; then
    echo "unattributed	unattributed"
    exit 0
  fi
  ADT_ROOT="$root" python3 "$_pricer" sum "$tix" "$ledger" \
    2>/dev/null | awk -F'\t' '{print $2"\t"$3}' || echo "unattributed	unattributed"
  exit 0
fi

# Sum input+output (cols 3+4) for rows whose ticket id (col 2) matches,
# case-insensitively. Malformed or non-numeric rows are skipped, as in
# load_token_usage() in build_kanban.py.
/usr/bin/python3 - "$tix" "$ledger" <<'PY'
import re, sys

def canon(tix):
    # Same as build_kanban.canon_tix: strip leading zeros from the number so
    # TIX-58 and TIX-058 count as one ticket.
    s = tix.strip().upper()
    m = re.fullmatch(r"([A-Z]+)-0*([0-9]+)", s)
    return f"{m.group(1)}-{m.group(2)}" if m else s

want = canon(sys.argv[1])
total = 0
matched = False   # did any ledger row match this id?
for ledger in sys.argv[2:]:
    try:
        with open(ledger) as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 4:
                    continue
                if canon(parts[1]) != want:
                    continue
                try:
                    total += int(parts[2]) + int(parts[3])
                    matched = True
                except (ValueError, IndexError):
                    continue
    except OSError:
        pass
# Any matching row prints the sum, even 0. No matching row prints the sentinel,
# so the close stamp says "not captured" instead of `tokens: 0`.
print(total if matched else "unattributed")
PY

# adt-bundle: v0.2.0
