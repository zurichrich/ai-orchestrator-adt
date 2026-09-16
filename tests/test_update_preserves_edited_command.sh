#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Checks that re-running install keeps a /adt-* command the user edited, still
# writes the adt_managed marker on the others, and that uninstall can still
# remove them.
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
PROJ="$TMP/proj"; mkdir -p "$PROJ"
CMDS="$PROJ/.claude/commands"

echo "[test] install → edit a command → re-install"

# ── install 1: the baseline ────────────────────────────────────────────────
bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$PROJ" >/dev/null 2>&1

VICTIM="$CMDS/adt-block.md"          # any ADT command; adt-block is stable
CONTROL="$CMDS/adt-plan.md"          # an un-edited command, for the marker check

[[ -f "$VICTIM" ]] && pass "install wrote $(basename "$VICTIM")" \
  || { fail "install did not write $(basename "$VICTIM")"; echo "FAILS=$FAILS"; exit 1; }
grep -qx 'adt_managed: ADT-087' "$VICTIM" \
  && pass "install wrote the adt_managed marker" || fail "marker missing after install"

# ── the user edits their copy ──────────────────────────────────────────────
SENTINEL="MY-OWN-EDIT-$RANDOM"
printf '\n<!-- %s -->\n' "$SENTINEL" >> "$VICTIM"
before_sha="$(shasum -a 256 "$VICTIM" | awk '{print $1}')"

# ── install 2: the update path ─────────────────────────────────────────────
out="$(bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$PROJ" 2>&1)"

grep -q "$SENTINEL" "$VICTIM" \
  && pass "the user's edit survived the update" \
  || fail "the user's edit was CLOBBERED by the update"

[[ "$(shasum -a 256 "$VICTIM" | awk '{print $1}')" == "$before_sha" ]] \
  && pass "edited command is byte-identical after the update" \
  || fail "edited command changed on disk"

printf '%s\n' "$out" | grep -q "keeping your edited copy of commands/$(basename "$VICTIM")" \
  && pass "update reported that it kept the edited copy" \
  || fail "update did not report keeping the edited copy"

# ── the marker is still written on un-edited commands ─────────────────────
grep -qx 'adt_managed: ADT-087' "$CONTROL" \
  && pass "un-edited command still carries the adt_managed marker" \
  || fail "marker missing on an un-edited command after re-install"

# ── and uninstall must still be able to remove it ──────────────────────────
( source "$ADT_DIR/lib/uninstall.sh"; _remove_adt_commands "$PROJ/.claude" ) >/dev/null 2>&1
[[ ! -e "$CONTROL" ]] \
  && pass "uninstall removed the un-edited ADT command" \
  || fail "uninstall left the ADT command behind"

if [[ $FAILS -eq 0 ]]; then
  echo; echo "All update-preserves-edited-command tests passed."
else
  echo; echo "$FAILS test(s) FAILED."; exit 1
fi
