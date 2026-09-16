#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Checks that installing ADT into a project creates or changes nothing under
# .github/, that the install manifest names no .github path, and that
# lib/uninstall.sh never touches .github/.
#
# Run:  bash agent-dev-team/tests/test_install_writes_no_github.sh
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

echo "== install writes nothing under .github/ =="

# ── 1. A project with no .github/ at all ───────────────────────────────────
P="$TMP/clean"
mkdir -p "$P"; git -C "$P" init -q
git -C "$P" config user.email t@t.t; git -C "$P" config user.name t
echo "# p" > "$P/README.md"; git -C "$P" add -A; git -C "$P" commit -q -m init

bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$P" >"$TMP/log1" 2>&1 \
  || { fail "install-defaults.sh exited non-zero"; tail -10 "$TMP/log1" | sed 's/^/      /'; }

if [ -e "$P/.github" ]; then
  fail "install created .github/: $(find "$P/.github" -type f | sed "s|$P/||" | tr '\n' ' ')"
else
  pass "no .github/ created by install-defaults.sh"
fi

# The install ran, so the check above means something.
[ -d "$P/.claude" ] && pass "install did run (.claude/ present)" \
                    || fail ".claude/ missing, so the install did not run"

# ── 2. A project that already has .github/ keeps it unchanged ──────────────
Q="$TMP/haswf"
mkdir -p "$Q/.github/workflows"; git -C "$Q" init -q
git -C "$Q" config user.email t@t.t; git -C "$Q" config user.name t
printf 'name: theirs\non:\n  push:\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: true\n' \
  > "$Q/.github/workflows/theirs.yml"
echo "# q" > "$Q/README.md"; git -C "$Q" add -A; git -C "$Q" commit -q -m init
before="$(cd "$Q/.github" && find . -type f | sort | xargs shasum 2>/dev/null)"

bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$Q" >"$TMP/log2" 2>&1 || true

after="$(cd "$Q/.github" && find . -type f | sort | xargs shasum 2>/dev/null)"
if [ "$before" = "$after" ]; then
  pass "an existing .github/ is left byte-identical"
else
  fail "install modified .github/:"; diff <(printf '%s\n' "$before") <(printf '%s\n' "$after") | sed 's/^/      /'
fi

# ── 3. The manifest names no .github path ──────────────────────────────────
MF="$P/.claude/.adt-manifest.json"
if [ -f "$MF" ]; then
  if /usr/bin/python3 -c "
import json,sys
files = json.load(open('$MF'))['files']
bad = [f for f in files if '.github' in f]
sys.exit(1 if bad else 0)
"; then
    pass "install manifest declares no .github path"
  else
    fail "install manifest declares a .github path"
  fi
else
  fail "no manifest written — cannot check it"
fi

# ── 4. The uninstaller does not touch .github/ ─────────────────────────────
# Matches `.github/` so the `.github.repo` config key uninstall.sh reads is not flagged.
if grep -qE '\.github/' "$ADT_DIR/lib/uninstall.sh"; then
  fail "lib/uninstall.sh touches the .github/ directory"
else
  pass "lib/uninstall.sh never touches the .github/ directory"
fi

printf '\n'
if [ "$FAILS" -eq 0 ]; then
  echo "All install-writes-no-github tests passed."
else
  echo "FAIL: $FAILS assertion(s)"
  exit 1
fi
