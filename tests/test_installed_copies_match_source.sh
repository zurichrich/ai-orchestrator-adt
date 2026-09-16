#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
#
# Tests that the committed .claude/ tree matches what a clean install writes.
# Runs the real installer into a throwaway project and compares the two.
#
# Run:  bash tests/test_installed_copies_match_source.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADT_DIR="${1:-$(cd "$HERE/.." && pwd)}"
fails=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fails=$((fails+1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/proj" && git -C "$TMP/proj" init -q .

echo "[test] a clean install reproduces the committed .claude/ tree"
if ! bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$TMP/proj" >"$TMP/install.log" 2>&1; then
  fail "install-defaults.sh exited non-zero"; sed 's/^/      /' "$TMP/install.log" | tail -20
  echo; echo "test_installed_copies_match_source: FAIL"; exit 1
fi

eval "$(python3 - "$ADT_DIR" "$TMP/proj/.claude" <<'PY'
import json, os, re, sys
adt, fresh = sys.argv[1], sys.argv[2]
committed = os.path.join(adt, ".claude")
strip = lambda t: re.sub(r"\n*(?:# adt-bundle: v\S+|<!-- adt-bundle: v\S+ -->)\n*\Z", "\n", t)
mf = json.load(open(os.path.join(fresh, ".adt-manifest.json")))["files"]
managed = {e["path"] for e in mf}
wired = set(re.findall(r"hooks/([a-z0-9-]+\.sh)",
                       json.dumps(json.load(open(os.path.join(adt, "defaults/settings.hooks.json"))))))
unmanaged = sorted(h for h in wired if "hooks/%s" % h not in managed)
drift, missing = [], []
for e in mf:
    p = e["path"]; a = os.path.join(fresh, p); b = os.path.join(committed, p)
    if not os.path.exists(b): missing.append(p); continue
    if strip(open(a, encoding='utf-8', errors='replace').read()) != strip(open(b, encoding='utf-8', errors='replace').read()):
        drift.append(p)
print("MANAGED=%d" % len(managed))
print("UNMANAGED='%s'" % " ".join(unmanaged))
print("MISSING='%s'" % " ".join(missing))
print("DRIFT='%s'" % " ".join(drift))
PY
)"

[ "${MANAGED:-0}" -gt 0 ] && pass "the install manages $MANAGED files" \
                          || fail "the fresh manifest is empty, so nothing was compared"

# The committed manifest lists every file the install ships. Uninstall removes
# files by manifest, so a missing entry is a file it would leave behind.
COMMITTED=$(/usr/bin/python3 -c "import json;print(len(json.load(open('$ADT_DIR/.claude/.adt-manifest.json'))['files']))" 2>/dev/null || echo 0)
if [ "$COMMITTED" = "$MANAGED" ]; then
  pass "the committed manifest lists all $COMMITTED shipped files"
else
  fail "the committed manifest lists $COMMITTED of $MANAGED shipped files"
fi

# Every installed .py imports cleanly from .claude/tools/, so the modules it
# imports were installed beside it.
BAD_IMPORTS=""
for py in "$TMP/proj/.claude/tools/"*.py; do
  [ -e "$py" ] || continue
  mod="$(basename "$py" .py)"
  ( cd "$TMP/proj/.claude/tools" && /usr/bin/python3 -c "import $mod" ) >/dev/null 2>&1 \
    || BAD_IMPORTS="$BAD_IMPORTS $mod"
done
if [ -z "$BAD_IMPORTS" ]; then
  pass "every installed .py imports cleanly from .claude/tools/"
else
  fail "installed .py cannot be imported from .claude/tools/:$BAD_IMPORTS"
fi

if [ -z "$UNMANAGED" ]; then pass "every wired hook is in the install manifest"
else fail "hooks wired in settings.hooks.json but missing from the manifest: $UNMANAGED"; fi

if [ -z "$MISSING" ]; then pass "every managed file exists in the committed .claude/"
else fail "shipped by install, absent from the committed tree: $MISSING"; fi

if [ -z "$DRIFT" ]; then pass "every managed file matches its source"
else
  fail "installed copy has drifted from source: $DRIFT"
  for f in $DRIFT; do
    echo "      --- $f"
    diff <(sed 's/# adt-bundle: v.*//' "$ADT_DIR/.claude/$f" 2>/dev/null) \
         <(sed 's/# adt-bundle: v.*//' "$TMP/proj/.claude/$f" 2>/dev/null) | head -12 | sed 's/^/        /'
  done
fi

echo
if [ "$fails" -eq 0 ]; then echo "All installed-copies-match-source tests passed."; exit 0
else echo "test_installed_copies_match_source: FAIL ($fails)"; exit 1; fi
