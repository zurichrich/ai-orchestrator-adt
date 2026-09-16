#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that an in-place install (ADT_DIR == PROJECT_PATH) never deletes its own
# source when a `.claude/` entry is a symlink back into `defaults/`, and that it
# refuses with a message naming the file. Also checks that a plain in-place
# install and the old symlinked-file upgrade still succeed.
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# A minimal ADT tree, just enough for the skills loop to reach copy().
FAKE="$TMP/adt"
mkdir -p "$FAKE/defaults/skills/demo-skill" "$FAKE/.claude/skills"
echo "# demo skill" > "$FAKE/defaults/skills/demo-skill/SKILL.md"
cp "$ADT_DIR/lib/install-defaults.sh" "$FAKE/install-defaults.sh"
[[ -f "$ADT_DIR/lib/commands-marker.sh" ]] && {
  mkdir -p "$FAKE/lib"; cp "$ADT_DIR/lib/commands-marker.sh" "$FAKE/lib/"; }

# The installed copy's directory is a symlink back into the source tree.
ln -s ../../defaults/skills/demo-skill "$FAKE/.claude/skills/demo-skill"

echo "[test] an in-place install must not delete its own source"

SRC="$FAKE/defaults/skills/demo-skill/SKILL.md"
before="$(shasum -a 256 "$SRC" | awk '{print $1}')"

out="$(cd "$FAKE" && bash install-defaults.sh "$FAKE" "$FAKE" 2>&1)"; rc=$?

# 1. The source file survived.
if [[ -f "$SRC" ]]; then
  pass "the source file still exists after an in-place install"
  after="$(shasum -a 256 "$SRC" | awk '{print $1}')"
  [[ "$before" == "$after" ]] \
    && pass "the source file is byte-identical (not truncated or rewritten)" \
    || fail "the source file changed: $before -> $after"
else
  fail "the source file was deleted"
fi

# 2. It refused rather than silently skipping the file.
[[ $rc -ne 0 ]] && pass "the install refused (exit $rc)" \
                || fail "the install reported success despite the symlink"

# 3. The refusal names the file, the source path and the cause.
grep -q "REFUSING" <<< "$out" \
  && pass "the refusal is explicit" || fail "no refusal message: $out"
grep -q "SKILL.md" <<< "$out" \
  && pass "the refusal names the file" || fail "refusal does not name the file"
grep -q "$FAKE/defaults/skills/demo-skill/SKILL.md" <<< "$out" \
  && pass "the refusal names the source path" || fail "refusal omits the src path"
grep -qi "symlink" <<< "$out" \
  && pass "the refusal says WHY (a symlink into the source tree)" \
  || fail "refusal does not explain the cause"
grep -q "PARENT DIRECTORY" <<< "$out" \
  && pass "the refusal names the parent directory as the symlink" \
  || fail "the refusal does not identify WHICH thing is the symlink: $out"

# ── Control: without the symlink the same install succeeds ─────────────────
echo "[test] control — in-place install with no symlink still succeeds"
FAKE2="$TMP/adt2"
mkdir -p "$FAKE2/defaults/skills/demo-skill" "$FAKE2/.claude/skills/demo-skill"
echo "# demo skill" > "$FAKE2/defaults/skills/demo-skill/SKILL.md"
cp "$ADT_DIR/lib/install-defaults.sh" "$FAKE2/install-defaults.sh"
[[ -d "$ADT_DIR/lib" ]] && { mkdir -p "$FAKE2/lib"
  [[ -f "$ADT_DIR/lib/commands-marker.sh" ]] && cp "$ADT_DIR/lib/commands-marker.sh" "$FAKE2/lib/"; }

(cd "$FAKE2" && bash install-defaults.sh "$FAKE2" "$FAKE2" >/dev/null 2>&1); rc2=$?
[[ $rc2 -eq 0 ]] && pass "a real directory installs cleanly (guard is not blanket)" \
                 || fail "the guard refused a legitimate in-place install (rc=$rc2)"
[[ -f "$FAKE2/.claude/skills/demo-skill/SKILL.md" ]] \
  && pass "the control install actually wrote the file" \
  || fail "control install wrote nothing"

# ── Upgrade: an installed file that is itself a symlink is migrated ───────
# Older installs symlinked each file into the source tree; copy() replaces the
# link with a real file. This uses the real source tree, because a minimal one
# fails for unrelated reasons (no commands/, tools/ or VERSION).
echo "[test] an old-style symlinked file is migrated, not refused"
UP="$TMP/upgrade"
mkdir -p "$UP/proj/.claude/rules"
# The installed file is a link into the source tree.
ln -s "$ADT_DIR/defaults/rules/working-style.md" "$UP/proj/.claude/rules/working-style.md"

bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$UP/proj" >/dev/null 2>&1; urc=$?
[[ $urc -eq 0 ]] && pass "the symlink upgrade is not refused" \
                 || fail "an old-style symlinked file was refused (rc=$urc)"
d="$UP/proj/.claude/rules/working-style.md"
[[ -f "$d" && ! -L "$d" ]] && pass "the link was replaced by a real file copy" \
                           || fail "dest is not a real file after install"
[[ -s "$ADT_DIR/defaults/rules/working-style.md" ]] \
  && pass "the source survived the migration" \
  || fail "the migration deleted the source"

echo
if [[ $FAILS -eq 0 ]]; then
  echo "All install-never-deletes-source tests passed."
else
  echo "$FAILS failure(s)."
fi
exit $(( FAILS > 0 ))
