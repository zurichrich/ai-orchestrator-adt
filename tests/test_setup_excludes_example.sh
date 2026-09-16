#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that setup.sh reports only real projects and does not treat the shipped
# projects/example.yaml template as a configured project.
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
HOME_D="$TMP/home"; USERP="$TMP/userp"
# $HOME_D has no .claude/commands, like a fresh machine.
mkdir -p "$USERP"

# One real project, so the report has something legitimate to name.
PROJ="$TMP/realproj"; mkdir -p "$PROJ"
git -C "$PROJ" init -q
cat > "$USERP/realproj.yaml" <<EOF
name: realproj
path: $PROJ
main_branch: main
cache_dir: $TMP/cache
github:
  repo: me/realproj
  owner: me
kanban:
  id_prefix: ADT
EOF

echo "[test] setup.sh reports only real projects"
out="$(HOME="$HOME_D" ADT_USER_PROJECTS="$USERP" bash "$ADT_DIR/setup.sh" 2>&1 || true)"

# The report block: everything after "Projects configured:".
report="$(printf '%s\n' "$out" | sed -n '/Projects configured:/,/^$/p')"

grep -q "realproj" <<<"$report" \
  && pass "the real project is reported" || fail "real project missing from report"

grep -qw "example" <<<"$report" \
  && fail "'example' named as a configured project" \
  || pass "'example' is not named as configured"

grep -q "Developer/example" <<<"$out" \
  && fail "prints the template's example PROJECT_PATH" \
  || pass "no bogus example PROJECT_PATH line"

echo
if [[ $FAILS -eq 0 ]]; then printf '\033[32mall setup-report tests passed\033[0m\n'; else
  printf '\033[31m%s test(s) failed\033[0m\n' "$FAILS"; fi
exit $(( FAILS > 0 ))
