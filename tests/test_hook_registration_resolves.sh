#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Checks that every hook registered in defaults/settings.hooks.json exists in
# defaults/hooks/ and has an adt- prefix.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; ADT="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0
ok(){ echo "  PASS $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL $1"; FAIL=$((FAIL+1)); }
missing=""
while IFS= read -r h; do
  [ -f "$ADT/defaults/hooks/$h" ] || missing="$missing $h"
done < <(grep -oE 'hooks/[A-Za-z0-9_.-]+\.sh' "$ADT/defaults/settings.hooks.json" | sed 's|hooks/||' | sort -u)
[ -z "$missing" ] && ok "every registered hook exists in defaults/hooks/" \
                  || bad "registered but missing:$missing"
n=$(grep -oE 'hooks/[A-Za-z0-9_.-]+\.sh' "$ADT/defaults/settings.hooks.json" | sort -u | wc -l | tr -d ' ')
[ "$n" -gt 0 ] && ok "$n hooks registered" \
               || bad "no registrations found in defaults/settings.hooks.json"
unprefixed=$(grep -oE 'hooks/[A-Za-z0-9_.-]+\.sh' "$ADT/defaults/settings.hooks.json" \
             | sed 's|hooks/||' | sort -u | grep -v '^adt-' || true)
[ -z "$unprefixed" ] && ok "every registration is adt- prefixed" || bad "unprefixed: $unprefixed"
echo; echo "PASS=$PASS FAIL=$FAIL"; [ "$FAIL" -eq 0 ]
