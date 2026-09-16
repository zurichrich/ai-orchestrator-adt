#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that `setup.sh --project <name>` installs into that project only, that
# a bare `setup.sh` still installs into every configured project, and that
# adt-install.sh passes --project.
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
HOME_D="$TMP/home"; USERP="$TMP/userp"
# $HOME_D has no .claude/commands, as on a fresh machine.
mkdir -p "$USERP"

mk() {  # mk <name> — a git repo + its user config
  local n="$1" d="$TMP/$1"
  mkdir -p "$d"; git -C "$d" init -q
  cat > "$USERP/$n.yaml" <<YAML
name: $n
path: $d
main_branch: main
cache_dir: $TMP/cache-$n
github:
  repo: me/$n
  owner: me
kanban:
  id_prefix: TIX
YAML
}
mk alpha
mk beta

run_setup() { HOME="$HOME_D" ADT_USER_PROJECTS="$USERP" bash "$ADT_DIR/setup.sh" "$@" >/dev/null 2>&1; }

echo "[test] --project installs into that project ONLY"
run_setup --project alpha
[[ -d "$TMP/alpha/.claude/commands" ]] \
  && pass "alpha received the .claude/ layer" || fail "alpha did NOT receive the install"
[[ -e "$TMP/beta/.claude" ]] \
  && fail "beta was written to — the install fanned out to an unrelated project" \
  || pass "beta was left untouched"

echo "[test] a bare run is still the machine-wide reconciler"
run_setup
[[ -d "$TMP/beta/.claude/commands" ]] \
  && pass "bare setup.sh still installs into every configured project" \
  || fail "bare setup.sh no longer reconciles all projects"

echo "[test] an unknown --project is an error, not a silent no-op"
if HOME="$HOME_D" ADT_USER_PROJECTS="$USERP" bash "$ADT_DIR/setup.sh" --project nosuch >/dev/null 2>&1; then
  fail "--project with an unknown name exited 0"
else
  pass "--project with an unknown name exits non-zero"
fi

echo "[test] adt-install.sh passes the project through"
grep -q 'setup.sh" --project "\$name"' "$ADT_DIR/adt-install.sh" \
  && pass "adt-install.sh scopes its setup.sh call" \
  || fail "adt-install.sh still calls setup.sh unscoped"

if [[ $FAILS -eq 0 ]]; then
  echo; echo "All setup project-scope tests passed."
else
  echo; echo "$FAILS test(s) FAILED."; exit 1
fi
