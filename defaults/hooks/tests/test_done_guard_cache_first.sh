#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that adt-done-guard.sh checks done_evidence on a plain `mv` to done as
# well as a `git mv`, in both the source copy and the installed .claude/hooks/
# copy, and that the two copies match.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
TMP="$(mktemp -d -t dg-cache-first.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
fails=0
ok()   { printf '  ok   %s\n' "$1"; }
bad()  { printf '  FAIL %s\n' "$1"; fails=$((fails+1)); }

mkdir -p "$TMP/done"

# A fixture project with its own board, and the deploy-freshness gate pointed at
# an empty agents dir so it never reads this machine's ~/Library/LaunchAgents.
PROJ="$TMP/proj"
mkdir -p "$PROJ/.adt"
printf '<html><body>board</body></html>\n' > "$PROJ/.adt/kanban.html"
ADT_LAUNCHAGENTS_DIR="$TMP/no-agents"; mkdir -p "$ADT_LAUNCHAGENTS_DIR"
export ADT_LAUNCHAGENTS_DIR
cat > "$TMP/unmet.md" <<'EOF'
---
tokens: unattributed
cost_usd: unattributed
cost_tier: unattributed
slug: unmet
done_evidence:
  - file: kanban.html
    must_contain_regex: 'ZZZ_STRING_THAT_CANNOT_APPEAR_ZZZ'
    lane: done
---
EOF
cat > "$TMP/met.md" <<'EOF'
---
tokens: unattributed
cost_usd: unattributed
cost_tier: unattributed
slug: met
done_evidence:
  - file: kanban.html
    must_contain_regex: '<html'
    lane: done
---
EOF

# Verdict for one (hook, move form, ticket). Only a deny that names done_evidence
# counts, since the other gates can deny for reasons unrelated to the ticket.
verdict() {  # $1=hook $2=form $3=ticket -> prints DOD_DENY|other
  local out
  out="$(printf '{"tool_name":"Bash","tool_input":{"command":"%s %s/%s.md %s/done/%s.md"},"cwd":"%s"}' \
         "$2" "$TMP" "$3" "$TMP" "$3" "$PROJ" | bash "$1" 2>/dev/null)"
  case "$out" in
    *'"deny"'*done_evidence*) echo DOD_DENY ;;
    *)                        echo other ;;
  esac
}

for hook in "$ROOT/defaults/hooks/adt-done-guard.sh" "$ROOT/.claude/hooks/adt-done-guard.sh"; do
  name="${hook#$ROOT/}"
  [ -f "$hook" ] || { bad "$name: missing"; continue; }

  # A plain mv is checked.
  [ "$(verdict "$hook" "mv" unmet)" = DOD_DENY ] && ok "$name: plain mv + unmet done_evidence -> DENY" \
                                                 || bad "$name: plain mv + unmet done_evidence must DENY"
  [ "$(verdict "$hook" "mv" met)" = other ]      && ok "$name: plain mv + met done_evidence -> not denied on evidence" \
                                                 || bad "$name: plain mv + met done_evidence must NOT deny on evidence"
  # A git mv is still checked.
  [ "$(verdict "$hook" "git mv" unmet)" = DOD_DENY ] && ok "$name: git mv + unmet -> DENY" \
                                                     || bad "$name: git mv + unmet must still DENY"
  [ "$(verdict "$hook" "git mv" met)" = other ]      && ok "$name: git mv + met -> not denied on evidence" \
                                                     || bad "$name: git mv + met must NOT deny on evidence"
done

# ADT-371: a move into planned/ needs a track. Checked on both copies; the ok
# lines are printed once, after both copies have passed.
# AO-028: a planned/ move also needs a parent chain, so the cache holds one.
C="$TMP/home/.adt/cache"
mkdir -p "$C/tasks/ideas" "$C/tasks/planned" "$C/epics/ideas" "$C/features/ideas"
printf -- '---\nid: T-1\ntype: epic\n---\n' > "$C/epics/ideas/e.md"
printf -- '---\nid: T-2\ntype: feature\nparent: T-1\n---\n' > "$C/features/ideas/f.md"
printf -- '---\nslug: notrack\nparent: T-2\n---\n' > "$C/tasks/ideas/notrack.md"
printf -- '---\nslug: fasttrack\ntrack: fast\nparent: T-2\n---\n' > "$C/tasks/ideas/fasttrack.md"
printf -- '---\nslug: quoted\ntrack: "standard"\nparent: T-2\n---\n' > "$C/tasks/ideas/quoted.md"
plan_move() {  # $1=hook $2=command -> prints the hook's output
  printf '{"tool_name":"Bash","tool_input":{"command":"%s"},"cwd":"%s"}' "$2" "$PROJ" \
    | HOME="$TMP/home" bash "$1" 2>/dev/null
}
plan_fails=0
for hook in "$ROOT/defaults/hooks/adt-done-guard.sh" "$ROOT/.claude/hooks/adt-done-guard.sh"; do
  name="${hook#$ROOT/}"
  out="$(plan_move "$hook" "mv $TMP/home/.adt/cache/tasks/ideas/notrack.md $TMP/home/.adt/cache/tasks/planned/notrack.md")"
  case "$out" in *'"deny"'*"no track"*) ;; *) bad "$name: a move into planned/ with no track must be denied"; plan_fails=1 ;; esac
  out="$(plan_move "$hook" "mv ~/.adt/cache/tasks/ideas/notrack.md ~/.adt/cache/tasks/planned/notrack.md")"
  case "$out" in *'"deny"'*"no track"*) ;; *) bad "$name: a ~ path into planned/ with no track must be denied"; plan_fails=1 ;; esac
  out="$(plan_move "$hook" "mv $TMP/home/.adt/cache/tasks/ideas/fasttrack.md $TMP/home/.adt/cache/tasks/planned/fasttrack.md")"
  [ -z "$out" ] || { bad "$name: a move into planned/ with track: fast must be allowed silently, got: $out"; plan_fails=1; }
  out="$(plan_move "$hook" "mv $TMP/home/.adt/cache/tasks/ideas/quoted.md $TMP/home/.adt/cache/tasks/planned/quoted.md")"
  [ -z "$out" ] || { bad "$name: a quoted track (\"standard\") is a track, like everywhere else ADT reads one, got: $out"; plan_fails=1; }
done
if [ "$plan_fails" -eq 0 ]; then
  ok "a move into planned/ with no track is denied"
  ok "a move into planned/ with track: fast is allowed"
fi

# The two copies match. The installer appends an "# adt-bundle: v<ver>" stamp to
# each installed file, so the comparison ignores that trailing line.
norm() {  # print $1 with a trailing adt-bundle provenance stamp removed
  python3 -c '
import re, sys
t = open(sys.argv[1]).read()
sys.stdout.write(re.sub(r"\n*(?:#|<!--) adt-bundle: v[^\n]*\n?\Z", "\n", t))' "$1"
}

if diff -q <(norm "$ROOT/defaults/hooks/adt-done-guard.sh") \
           <(norm "$ROOT/.claude/hooks/adt-done-guard.sh") >/dev/null; then
  ok "the two adt-done-guard.sh copies match, modulo the adt-bundle stamp"
else
  bad "defaults/hooks/adt-done-guard.sh and .claude/hooks/adt-done-guard.sh have drifted"
fi

# Negative control: an added line is reported as drift.
cp "$ROOT/.claude/hooks/adt-done-guard.sh" "$TMP/drifted.sh"
printf 'echo REAL_DRIFT\n' >> "$TMP/drifted.sh"
if diff -q <(norm "$ROOT/defaults/hooks/adt-done-guard.sh") \
           <(norm "$TMP/drifted.sh") >/dev/null; then
  bad "an injected line was not reported as drift"
else
  ok "an injected line is reported as drift"
fi

if [ "$fails" -eq 0 ]; then echo "PASS (test_done_guard_cache_first.sh)"; exit 0; fi
echo "FAIL: $fails assertion(s) (test_done_guard_cache_first.sh)"; exit 1
