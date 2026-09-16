#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that the hooks treat a .adt/ directory as the sign of an ADT project.
# A hook that misses the marker exits 0 silently, so the checks look at the
# files written rather than exit codes.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; ADT="$(cd "$HERE/../../.." && pwd)"
PASS=0; FAIL=0
ok(){ echo "  PASS $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL $1"; FAIL=$((FAIL+1)); }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

# --- a project with only .adt/ ---------------------------------------------
P="$T/adtonly"; mkdir -p "$P/.adt"; git -C "$P" init -q .
printf '{"cwd":"%s","session_id":"s1","hook_event_name":"UserPromptSubmit","prompt":"/adt-brief"}' "$P" \
  | bash "$ADT/defaults/hooks/adt-usage-log.sh" >/dev/null 2>&1
[ -s "$P/.adt/state/usage.log" ] && ok "adt-usage-log fires on a .adt-only project" \
  || bad "adt-usage-log wrote nothing - it did not recognise the marker"
[ -d "$P/development-team" ] && bad "the old folder was created" || ok "the old folder is never created"

# mark-tix resolves the root from cwd and needs a session id in the env.
( cd "$P" && CLAUDE_CODE_SESSION_ID=s9 bash "$ADT/defaults/hooks/adt-mark-tix.sh" ADT-1 ) >/dev/null 2>&1
[ -s "$P/.adt/state/current-tix.d/s9" ] && ok "adt-mark-tix writes the marker under .adt/" \
  || bad "adt-mark-tix wrote no marker"

# --- a project with only the old development-team/ folder is not ADT -------
L="$T/legacyonly"; mkdir -p "$L/development-team/.adt-state"; git -C "$L" init -q .
printf '{"cwd":"%s","session_id":"s2","hook_event_name":"UserPromptSubmit","prompt":"/adt-plan"}' "$L" \
  | bash "$ADT/defaults/hooks/adt-usage-log.sh" >/dev/null 2>&1
[ -e "$L/.adt/state/usage.log" ] && bad "an old folder-only tree was treated as an ADT project" \
  || ok "a folder-only tree is not an ADT project"

# --- a project with neither marker is left untouched -----------------------
N="$T/none"; mkdir -p "$N"; git -C "$N" init -q .
printf '{"cwd":"%s","session_id":"s3","hook_event_name":"UserPromptSubmit","prompt":"/adt-brief"}' "$N" \
  | bash "$ADT/defaults/hooks/adt-usage-log.sh" >/dev/null 2>&1
[ "$(find "$N" -type f -not -path '*/.git/*' | wc -l | tr -d ' ')" = "0" ] \
  && ok "a non-ADT project is untouched" || bad "wrote into a project that is not an ADT project"

# --- every hook in settings.hooks.json runs on a .adt-only project ----------
# Only hooks registered there are run; the argument-driven helpers exit 2 with no args.
crashed=""
for name in $(grep -oE 'hooks/adt-[a-z-]+\.sh' "$ADT/defaults/settings.hooks.json" | sed 's|hooks/||' | sort -u); do
  h="$ADT/defaults/hooks/$name"
  printf '{"cwd":"%s","session_id":"s4","hook_event_name":"UserPromptSubmit","prompt":"/adt-brief"}' "$P" \
    | bash "$h" >/dev/null 2>&1
  rc=$?; [ $rc -gt 1 ] && crashed="$crashed $name:$rc"
done
nw=$(grep -oE 'hooks/adt-[a-z-]+\.sh' "$ADT/defaults/settings.hooks.json" | sort -u | wc -l | tr -d ' ')
[ -z "$crashed" ] && ok "all $nw wired hooks handle a .adt-only project" || bad "hooks errored:$crashed"

echo; echo "PASS=$PASS FAIL=$FAIL"; [ "$FAIL" -eq 0 ]
