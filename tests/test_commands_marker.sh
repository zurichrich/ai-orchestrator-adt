#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests lib/commands-marker.sh: the writer adds the adt_managed marker to a
# command's frontmatter, and the reader tells ADT-installed commands from
# project-authored ones.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADT_DIR="$(cd "$HERE/.." && pwd)"
source "$ADT_DIR/lib/commands-marker.sh"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0
pass() { echo "  ok: $1"; pass=$((pass+1)); }
fail() { echo "  FAIL: $1"; fail=$((fail+1)); }

# A realistic command source: leading `---` frontmatter, then body.
cat > "$TMP/src.md" <<'MD'
---
name: adt-plan
description: Read a brief, write the plan.
---

# /adt-plan
body line mentioning adt_managed in prose should NOT count.
MD

# ── writer: injects the marker right after the opening --- ──────────────────
adt_install_command "$TMP/src.md" "$TMP/dest.md"
sed -n '1,3p' "$TMP/dest.md" | grep -qx 'adt_managed: ADT-087' \
  && pass "marker injected on frontmatter line 2" || fail "marker not on line 2"
# name/description still present (frontmatter intact)
grep -qx 'name: adt-plan' "$TMP/dest.md" && pass "name: preserved" || fail "name: lost"
grep -qx 'description: Read a brief, write the plan.' "$TMP/dest.md" \
  && pass "description: preserved" || fail "description: lost"
# the opening --- is still line 1 (frontmatter not broken)
[[ "$(sed -n '1p' "$TMP/dest.md")" == "---" ]] && pass "line 1 still ---" || fail "line 1 not ---"

# ── reader: a copied command IS managed ─────────────────────────────────────
adt_is_managed_command "$TMP/dest.md" && pass "copied command detected as managed" \
  || fail "copied command not detected as managed"

# ── reader: the pristine source is NOT managed ──────────────────────────────
adt_is_managed_command "$TMP/src.md" && fail "source wrongly detected as managed" \
  || pass "pristine source correctly NOT managed"

# ── reader: a project-authored adt-*.md (own frontmatter, no marker) NOT managed
cat > "$TMP/authored.md" <<'MD'
---
name: adt-custom
description: a project's own command.
---
# body
MD
adt_is_managed_command "$TMP/authored.md" && fail "authored command wrongly managed" \
  || pass "project-authored command correctly NOT managed"

# ── reader: marker only in the BODY must not count (frontmatter-scoped) ──────
cat > "$TMP/bodyonly.md" <<'MD'
---
name: adt-x
---
# body
adt_managed: ADT-087
MD
adt_is_managed_command "$TMP/bodyonly.md" && fail "body-only marker wrongly counted" \
  || pass "body-only marker correctly ignored (frontmatter-scoped)"

# ── reader: a file with no frontmatter at all is NOT managed ─────────────────
printf '# just markdown, no frontmatter\n' > "$TMP/nofm.md"
adt_is_managed_command "$TMP/nofm.md" && fail "no-frontmatter wrongly managed" \
  || pass "no-frontmatter correctly NOT managed"

# ── writer: a source with no frontmatter is still marked and installed ───────
# The writer must exit 0 (install runs under set -e) and add frontmatter.
printf '# no frontmatter\nbody\n' > "$TMP/nofm-src.md"
adt_install_command "$TMP/nofm-src.md" "$TMP/nofm-dest.md"; rc=$?
[[ $rc -eq 0 ]] && pass "frontmatter-less source: writer exits 0 (no abort)" \
  || fail "frontmatter-less source: writer exited $rc (would abort install)"
adt_is_managed_command "$TMP/nofm-dest.md" \
  && pass "frontmatter-less source: copy synthesised + marked" \
  || fail "frontmatter-less source: copy not marked"
grep -q '^# no frontmatter' "$TMP/nofm-dest.md" && grep -q '^body' "$TMP/nofm-dest.md" \
  && pass "frontmatter-less source: original body preserved" || fail "body lost"

echo
echo "test_commands_marker: $pass passed, $fail failed"
[[ $fail -eq 0 ]]
