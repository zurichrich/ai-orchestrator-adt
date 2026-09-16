#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that lib/resync.sh regenerates .claude/ from source, names the
# playbooks whose installed copy changed, and reports a refused install.
# Runs against a copy of the repo, because resync rewrites .claude/.
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
WORK="$TMP/repo"
# A plain copy with no git, which resync must also handle.
mkdir -p "$WORK"
for d in lib commands defaults tools .claude docs; do
  [[ -e "$ADT_DIR/$d" ]] && cp -R "$ADT_DIR/$d" "$WORK/$d"
done
[[ -f "$ADT_DIR/VERSION" ]] && cp "$ADT_DIR/VERSION" "$WORK/VERSION"

echo "[test] resync regenerates .claude/ and reports what changed"

# ── Case 1: nothing edited → nothing reported as changed ───────────────────
out1="$(cd "$WORK" && ADT_DIR="$WORK" bash lib/resync.sh 2>&1)"; rc1=$?
[[ $rc1 -eq 0 ]] && pass "a clean resync exits 0" || fail "clean resync exited $rc1"
grep -q "no playbook changed" <<< "$out1" \
  && pass "an unchanged tree reports no playbook changed" \
  || fail "unchanged tree did not report cleanly: $out1"

# ── Case 2: edit ONE playbook → exactly that one is named ──────────────────
TARGET="$WORK/commands/qa-run.md"
[[ -f "$TARGET" ]] || { echo "  fixture error: no commands/qa-run.md"; exit 1; }
printf '\n<!-- resync test marker -->\n' >> "$TARGET"

out2="$(cd "$WORK" && ADT_DIR="$WORK" bash lib/resync.sh 2>&1)"; rc2=$?
[[ $rc2 -eq 0 ]] && pass "resync after an edit exits 0" || fail "resync exited $rc2"

grep -q "resync test marker" "$WORK/.claude/commands/adt-qa-run.md" \
  && pass "the installed copy was regenerated from source" \
  || fail "the installed copy does not carry the source edit"

grep -q "adt-qa-run.md" <<< "$out2" \
  && pass "the changed playbook is named" || fail "the changed playbook is not named: $out2"
grep -q "1 playbook(s) changed" <<< "$out2" \
  && pass "exactly one playbook is reported changed" \
  || fail "wrong count reported: $(grep -o '[0-9]* playbook(s) changed' <<< "$out2")"
# Search only the changed-playbook list, because the installer's own output can
# also mention playbook names.
changed_list="$(sed -n '/playbook(s) changed in this run:/,/^$/p' <<< "$out2")"
grep -q "adt-qa-run.md" <<< "$changed_list" \
  && pass "the changed playbook is in the report's list" \
  || fail "the edited playbook is not in the list: $changed_list"
grep -q "adt-plan.md" <<< "$changed_list" \
  && fail "an unedited playbook was reported as changed" \
  || pass "unedited playbooks are not named"
grep -qi "reads ONCE\|Reload the session" <<< "$out2" \
  && pass "the report says the session's loaded copy is now stale" \
  || fail "the report does not say the loaded copy is stale"

# ── Case 3: a refused install makes resync fail ────────────────────────────
# A symlink in .claude/ makes the installer refuse; resync must fail and say so.
ln -s ../../defaults/skills/adt-diagnose "$WORK/.claude/skills/adt-diagnose-link" 2>/dev/null
mkdir -p "$WORK/defaults/skills/adt-diagnose-link"
rm -rf "$WORK/.claude/skills/adt-diagnose-link"
ln -s ../../defaults/skills/adt-diagnose-link "$WORK/.claude/skills/adt-diagnose-link"
echo "# linked" > "$WORK/defaults/skills/adt-diagnose-link/SKILL.md"

out3="$(cd "$WORK" && ADT_DIR="$WORK" bash lib/resync.sh 2>&1)"; rc3=$?
[[ $rc3 -ne 0 ]] && pass "resync fails when the install refuses" \
                 || fail "resync reported success over a refused install"
grep -q "REFUSED" <<< "$out3" \
  && pass "resync surfaces the refusal" || fail "refusal not surfaced: $out3"
[[ -f "$WORK/defaults/skills/adt-diagnose-link/SKILL.md" ]] \
  && pass "the source survived the refused resync" \
  || fail "resync deleted a source file"

# The installer refuses file by file, so files copied before the refusal are
# already written and the tree may be partly regenerated.
grep -q "is unchanged" <<< "$out3" \
  && fail "resync claims .claude/ is unchanged (expected a partly-regenerated warning)" \
  || pass "resync does not claim the tree is unchanged"
grep -q "PARTLY regenerated" <<< "$out3" \
  && pass "resync says the tree may be partly regenerated" \
  || fail "resync does not warn that the tree may be partly written: $out3"

echo
if [[ $FAILS -eq 0 ]]; then
  echo "All resync tests passed."
else
  echo "$FAILS failure(s)."
fi
exit $(( FAILS > 0 ))
