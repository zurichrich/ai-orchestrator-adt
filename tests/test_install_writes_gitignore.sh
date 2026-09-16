#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests the managed block install writes into a project's .gitignore: git
# ignores .adt/, the project's own rules survive, and uninstall removes the block.
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

new_proj() {   # new_proj <dir> — a git repo with one committed file
  mkdir -p "$1"
  git -C "$1" init -q
  git -C "$1" config user.email t@t.t; git -C "$1" config user.name t
  echo "# project" > "$1/README.md"
  git -C "$1" add README.md; git -C "$1" commit -q -m init
}

untracked() { git -C "$1" status --porcelain --untracked-files=normal | awk '{print $2}'; }

# ── Case 1: a project with NO .gitignore ───────────────────────────────────
echo "[test] install into a project with no .gitignore"
P1="$TMP/p1"; new_proj "$P1"
bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$P1" >/dev/null 2>&1
# the paths ADT generates at runtime (install-defaults writes .claude/ only)
mkdir -p "$P1/.adt/state" "$P1/.adt"
echo "<html>generated</html>" > "$P1/.adt/kanban.html"
echo "project: p1" > "$P1/.adt/config.yaml"

[[ -f "$P1/.gitignore" ]] && pass "install created .gitignore" || fail "no .gitignore created"
grep -qx '# ADT:gitignore:start' "$P1/.gitignore" && pass "managed block present" || fail "no managed block"

u="$(untracked "$P1")"
printf '%s\n' "$u" | grep -q '^\.adt/$' \
  && fail ".adt/ shows as untracked (expected ignored)" \
  || pass "git ignores .adt/"
printf '%s\n' "$u" | grep -q '^\.adt/$' \
  && fail ".adt/ shows as untracked (expected ignored)" \
  || pass "git ignores .adt/"
printf '%s\n' "$u" | grep -q '^\.claude/$' \
  && pass ".claude/ is not ignored" \
  || fail ".claude/ was ignored (expected it to show as untracked)"

echo "[test] re-install is idempotent"
bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$P1" >/dev/null 2>&1
[[ "$(grep -c '# ADT:gitignore:start' "$P1/.gitignore")" == "1" ]] \
  && pass "block written once, not appended twice" || fail "block duplicated on re-install"

echo "[test] uninstall removes the file ADT created"
( source "$ADT_DIR/lib/uninstall.sh"; _remove_gitignore_block "$P1/.gitignore" ) >/dev/null 2>&1
[[ ! -e "$P1/.gitignore" ]] \
  && pass "uninstall removed the .gitignore ADT created" \
  || fail "uninstall left an ADT-created .gitignore behind"

# ── Case 2: a project that ALREADY has its own .gitignore ──────────────────
echo "[test] a project's own .gitignore survives install + uninstall"
P2="$TMP/p2"; new_proj "$P2"
printf '# my rules\nnode_modules/\n*.log\n' > "$P2/.gitignore"
before="$(cat "$P2/.gitignore")"
git -C "$P2" add .gitignore; git -C "$P2" commit -q -m gitignore

bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$P2" >/dev/null 2>&1
grep -qx 'node_modules/' "$P2/.gitignore" && pass "authored rules still present after install" \
  || fail "install clobbered the project's own .gitignore"
grep -qx '.adt/' "$P2/.gitignore" && pass "ADT rules appended" || fail "ADT rules missing"

( source "$ADT_DIR/lib/uninstall.sh"; _remove_gitignore_block "$P2/.gitignore" ) >/dev/null 2>&1
[[ -f "$P2/.gitignore" ]] && pass "uninstall kept the project's own .gitignore" \
  || fail "uninstall deleted the project's own .gitignore"
[[ "$(cat "$P2/.gitignore")" == "$before" ]] \
  && pass "install → uninstall restored it byte-for-byte" \
  || { fail "round trip changed the authored .gitignore"; diff <(printf '%s' "$before") "$P2/.gitignore" | head -5; }

# ── Case 3: content after the managed block ────────────────────────────────
# In .gitignore the last matching rule wins, so a negation of `.adt/` only works
# below the managed block. A re-install must leave it there.
echo "[test] rules the project put AFTER the managed block survive a re-install"
P3="$TMP/p3"; new_proj "$P3"
printf '# my rules\n.wrangler/\n' > "$P3/.gitignore"
bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$P3" >/dev/null 2>&1
printf '!docs/keep/*/.adt/\n!docs/keep/*/.adt/**\n' >> "$P3/.gitignore"
after_first="$(cat "$P3/.gitignore")"

bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$P3" >/dev/null 2>&1

grep -qx '.wrangler/' "$P3/.gitignore" \
  && pass "the line above the block is not joined to what follows" \
  || { fail ".wrangler/ was mangled by the block rewrite"; grep -n 'wrangler' "$P3/.gitignore"; }
grep -qx '!docs/keep/\*/.adt/' "$P3/.gitignore" \
  && pass "trailing negation survived" || fail "trailing negation lost"
blk="$(grep -n '# ADT:gitignore:end' "$P3/.gitignore" | cut -d: -f1)"
neg="$(grep -n '!docs/keep/\*/.adt/$' "$P3/.gitignore" | cut -d: -f1)"
[[ -n "$blk" && -n "$neg" && "$neg" -gt "$blk" ]] \
  && pass "trailing negation stayed below the managed block" \
  || fail "the negation moved above the block: block=$blk neg=$neg"
[[ "$(cat "$P3/.gitignore")" == "$after_first" ]] \
  && pass "a re-install is idempotent" \
  || { fail "re-install changed the file"; diff <(printf '%s' "$after_first") "$P3/.gitignore" | head -8; }

mkdir -p "$P3/docs/keep/tree1/.adt"
echo "kept" > "$P3/docs/keep/tree1/.adt/note.md"
if git -C "$P3" check-ignore -q docs/keep/tree1/.adt/note.md; then
  fail "git still ignores the file the negation re-includes"
else
  pass "git honours the trailing negation"
fi

if [[ $FAILS -eq 0 ]]; then
  echo; echo "All gitignore tests passed."
else
  echo; echo "$FAILS test(s) FAILED."; exit 1
fi
