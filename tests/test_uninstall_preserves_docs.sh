#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Checks that a project's docs/decisions.md and docs/retros/ are tracked by
# git, that .adt/ is ignored, and that removing .adt/ leaves docs/ intact.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; ADT="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0
ok(){ echo "  PASS $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL $1"; FAIL=$((FAIL+1)); }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
P="$T/proj"; mkdir -p "$P"; git -C "$P" init -q .
git -C "$P" config user.email t@e; git -C "$P" config user.name t

# Install before committing, so the gitignore block keeps .adt/ out of the commit.
bash "$ADT/lib/install-defaults.sh" "$ADT" "$P" >/dev/null 2>&1
mkdir -p "$P/.adt/state" "$P/docs/retros"
printf '2026-09-09T00:00:00Z\tadt-brief\n' > "$P/.adt/state/usage.log"
printf '# decisions\n\n## ADR-001 something we must not lose\n' > "$P/docs/decisions.md"
printf '# the egress quota incident\nWe raised the cap.\n' > "$P/docs/retros/egress.md"
git -C "$P" add -A >/dev/null 2>&1; git -C "$P" commit -qm init >/dev/null 2>&1

[ -d "$P/development-team" ] && bad "the old folder exists" || ok "the old folder is never created"
git -C "$P" check-ignore -q .adt && ok ".adt/ is ignored" || bad ".adt/ is not ignored"
git -C "$P" check-ignore -q docs/decisions.md && bad "docs/ is ignored" || ok "docs/ is not ignored"
git -C "$P" ls-files docs/decisions.md | grep -q . && ok "docs/decisions.md is tracked" || bad "decisions.md untracked"
git -C "$P" ls-files docs/retros/egress.md | grep -q . && ok "docs/retros/ is tracked" || bad "retros untracked"
git -C "$P" ls-files .adt | grep -q . && bad ".adt/ was committed" || ok ".adt/ is not tracked"

# Uninstall removes all of .adt/.
rm -rf "$P/.adt"
[ -f "$P/docs/decisions.md" ] && ok "docs/ survives .adt/ being removed wholesale" || bad "docs/ lost"
[ -f "$P/docs/retros/egress.md" ] && ok "retros survive it too" || bad "retros lost"
git -C "$P" status --porcelain docs/ | grep -q . && bad "docs/ shows as modified" || ok "docs/ recoverable from git"

echo; echo "PASS=$PASS FAIL=$FAIL"; [ "$FAIL" -eq 0 ]
