#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests adt-token-total.sh: a ticket's token total across machines, from the
# local ledger plus the per-machine register comments on its GitHub Issue.
# Run:  bash defaults/hooks/tests/test_adt_token_total.sh
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOTAL_HOOK="$HOOK_DIR/adt-token-total.sh"
PASS=0 FAIL=0

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

# This machine's install id, written into each fixture's .adt/state/install-id.
OWN="11111111-2222-4333-8444-555555555555"

# Fresh tmp project: git repo + .adt/ + .adt/config.yaml + cache
# with one ticket carrying an issue_number.
newproj() {
  local d cache
  d="$(mktemp -d)"; cache="$(mktemp -d)"
  git -C "$d" init -q
  mkdir -p "$d/.adt/state" "$d/.adt" "$cache/bugs/building"
  printf '%s\n' "$OWN" > "$d/.adt/state/install-id"
  cat > "$d/.adt/config.yaml" <<EOF
project: proj
repo: o/r
id_prefix: ADT
cache_dir: $cache
EOF
  cat > "$cache/bugs/building/sample.md" <<'EOF'
---
id: ADT-58
title: Sample
stage: building
issue_number: 58
---

# Sample
EOF
  printf '%s' "$d"
}

ledger_row() { # <proj> <tix> <in> <out>
  printf '2026-07-03T10:00:00Z\t%s\t%s\t%s\tsess1\n' "$2" "$3" "$4" \
    >> "$1/.adt/state/token-usage.log"
}

# gh stub on PATH: prints $GH_COMMENTS_FILE, shaped like the output of
# `gh api --paginate --slurp …/comments` ([[comment,…],…]). Fails when $GH_FAIL is set.
make_gh_stub() {
  local bindir; bindir="$(mktemp -d)"
  cat > "$bindir/gh" <<'EOF'
#!/bin/bash
[ -n "${GH_FAIL:-}" ] && { echo "gh: could not connect" >&2; exit 1; }
cat "$GH_COMMENTS_FILE"
EOF
  chmod +x "$bindir/gh"
  printf '%s' "$bindir"
}

reg_comment() { # <machine> <total> -> one comment object (single-line body)
  printf '{"body":"<!-- adt:tokens machine=%s total=%s --> tokens on %s"}' "$1" "$2" "$1"
}

GH_BIN="$(make_gh_stub)"
export GH_COMMENTS_FILE=""

run_total() { # <proj> <tix>
  PATH="$GH_BIN:$PATH" bash "$TOTAL_HOOK" "$2" "$1" 2>/dev/null
}

echo "== adt-token-total.sh =="

# 1. Cross-machine sum: own ledger + two other machines' registers.
P="$(newproj)"
ledger_row "$P" "ADT-58" 6000 4000                       # own: 10k
GH_COMMENTS_FILE="$(mktemp)"
printf '[[%s,%s]]' "$(reg_comment machine-x 40000)" "$(reg_comment machine-y 2000)" > "$GH_COMMENTS_FILE"
export GH_COMMENTS_FILE
got="$(run_total "$P" "ADT-58")"
[ "$got" = "52000" ] && ok "own ledger + other machines' registers sum (A+B)" \
  || bad "expected 52000, got '$got'"

# 2. This machine's register and ledger count once, as the larger of the two.
printf '[[%s,%s]]' "$(reg_comment "$OWN" 8000)" "$(reg_comment machine-x 5000)" > "$GH_COMMENTS_FILE"
got="$(run_total "$P" "ADT-58")"
[ "$got" = "15000" ] && ok "own register and ledger count once: max(ledger 10k, reg 8k) + 5k" \
  || bad "expected 15000, got '$got'"

# 3. A pruned ledger: this machine's register sets the floor.
P2="$(newproj)"
ledger_row "$P2" "ADT-58" 100 100                        # post-prune remnant: 200
printf '[[%s]]' "$(reg_comment "$OWN" 40000)" > "$GH_COMMENTS_FILE"
got="$(run_total "$P2" "ADT-58")"
[ "$got" = "40000" ] && ok "own register sets the floor for a pruned ledger" \
  || bad "expected 40000, got '$got'"

# 4. Fresh clone: empty ledger, registers only -> their sum.
P3="$(newproj)"
printf '[[%s,%s]]' "$(reg_comment machine-x 30000)" "$(reg_comment machine-y 12000)" > "$GH_COMMENTS_FILE"
got="$(run_total "$P3" "ADT-58")"
[ "$got" = "42000" ] && ok "fresh clone (no ledger) reports registers' sum" \
  || bad "expected 42000, got '$got'"

# 5. No ledger rows and no registers -> "unattributed" rather than 0.
P4="$(newproj)"
printf '[[]]' > "$GH_COMMENTS_FILE"
got="$(run_total "$P4" "ADT-58")"
[ "$got" = "unattributed" ] && ok "no ledger + no registers -> unattributed" \
  || bad "expected unattributed, got '$got'"

# 6. gh failure falls back to the local sum, exit 0.
P5="$(newproj)"
ledger_row "$P5" "ADT-58" 3000 1000
got="$(GH_FAIL=1 run_total "$P5" "ADT-58")"; rc=$?
[ "$got" = "4000" ] && [ "$rc" = "0" ] && ok "gh failure -> local sum, exit 0" \
  || bad "expected 4000/rc0, got '$got'/rc$rc"

# 7. Unknown ticket (no cache file) falls back to the local sum.
got="$(run_total "$P5" "ADT-999")"
[ "$got" = "unattributed" ] && ok "unknown ticket -> local sum (unattributed)" \
  || bad "expected unattributed, got '$got'"

# 8. Ledger rows for ADT-058 count toward a query for ADT-58.
P6="$(newproj)"
ledger_row "$P6" "ADT-058" 100 50
printf '[[%s]]' "$(reg_comment machine-x 1000)" > "$GH_COMMENTS_FILE"
got="$(run_total "$P6" "ADT-58")"
[ "$got" = "1150" ] && ok "ADT-058 ledger rows count for ADT-58" \
  || bad "expected 1150, got '$got'"

echo "== missing install id =="
P9="$(newproj)"
rm -f "$P9/.adt/state/install-id"
ledger_row "$P9" "ADT-58" 4000 3000
printf '[[%s]]' "$(reg_comment "$OWN" 9000)" > "$GH_COMMENTS_FILE"
got="$(run_total "$P9" "ADT-58")"
# With no id file no register is treated as this machine's: 7000 local + 9000 register.
[ "$got" = "16000" ] && ok "a missing id file counts every register" \
  || bad "expected 16000, got '$got'"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = "0" ]
