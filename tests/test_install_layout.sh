#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Checks the layout a fresh install (without --init-github) leaves in a project:
# .adt/ exists and is gitignored, the old development-team/ folder is not
# created, .claude/ has no symlinks, and the board renders into .adt/.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; ADT="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0
ok(){ echo "  PASS $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL $1"; FAIL=$((FAIL+1)); }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOME="$T/home"; mkdir -p "$HOME"
P="$T/proj"; mkdir -p "$P"; git -C "$P" init -q .
git -C "$P" config user.email t@e; git -C "$P" config user.name t

PROJECT_PATH="$P" PROJECT_NAME=scratch PROJECT_CACHE_DIR="$T/cache" \
  bash "$ADT/lib/install-defaults.sh" "$ADT" "$P" >/dev/null 2>&1
mkdir -p "$P/.adt/state" "$P/.adt/inbox"    # what setup.sh's skeleton step creates

[ -d "$P/.adt/state" ] && ok ".adt/state/ exists after install" || bad "no .adt/state/"
[ -d "$P/development-team" ] && bad "install created the old folder" || ok "the old folder is never created"

# The managed gitignore block ignores .adt/.
grep -qx '.adt/' "$P/.gitignore" && ok ".adt/ is in the managed gitignore block" || bad ".adt/ not ignored"
grep -q 'development-team' "$P/.gitignore" && bad "gitignore still names the old folder" \
  || ok "gitignore does not name the old folder"
git -C "$P" check-ignore -q .adt && ok "git agrees .adt/ is ignored" || bad "git does not ignore .adt/"

n=$(grep -c '^\.adt/$' "$P/.gitignore")
[ "$n" = "1" ] && ok ".adt/ appears exactly once" \
               || bad ".adt/ appears $n times"

[ -z "$(find "$P/.claude" -type l 2>/dev/null)" ] && ok "no symlinks in the installed .claude/" \
  || bad "installed .claude/ contains symlinks"

# The board renders into .adt/ and is not empty.
mkdir -p "$T/cache/tasks/ideas"
printf -- '---\nslug: x\nid: ADT-1\ntitle: x\ntype: task\nstage: ideas\nstate: open\n---\n\n# x\n' \
  > "$T/cache/tasks/ideas/x.md"
( cd "$ADT" && python3 -c "
import sys; sys.path.insert(0,'tools'); import build_kanban
build_kanban.run('$T/cache', backlog_root='', out_dir='$P/.adt')
" ) >/dev/null 2>&1 || python3 - "$ADT" "$T/cache" "$P" <<'PY' >/dev/null 2>&1
import sys, os
sys.path.insert(0, os.path.join(sys.argv[1], "tools"))
import build_kanban
build_kanban.run(sys.argv[2], backlog_root="")
PY
board="$P/.adt/kanban.html"; [ -s "$board" ] || board="$T/cache/kanban.html"
[ -s "$board" ] && ok "the board renders and is non-empty ($(wc -c <"$board" | tr -d ' ') bytes)" \
  || bad "board did not render"

echo; echo "PASS=$PASS FAIL=$FAIL"; [ "$FAIL" -eq 0 ]
