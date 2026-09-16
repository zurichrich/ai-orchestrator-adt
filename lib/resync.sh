#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
#
# Regenerate this repo's own `.claude/` from its source (ADT-306 sub-steps 2a,
# 2b). `.claude/` is committed AND generated: the harness loads it every
# session, and every file in it is written by `lib/install-defaults.sh` from
# `commands/`, `defaults/` and `tools/`. So editing a playbook leaves the
# installed copy stale, and `tests/test_installed_copies_match_source.sh` red,
# until something regenerates it.
#
# Before this script the only regeneration method was the raw in-place install
# named in `.gitattributes` — `bash lib/install-defaults.sh "$(pwd)" "$(pwd)"` —
# which was also the invocation that could delete a source file (ADT-306
# problem 1, now guarded in copy()). The knowledge was written down; the command
# was not, and it was retyped by hand five times during ADT-301.
#
# WHY THIS IS A SEPARATE SCRIPT rather than an `--resync` flag on the installer:
# the changed-playbook report below is only meaningful when source and
# destination are the same repo. Every consumer project runs install-defaults.sh
# on install, and none of them wants that reporting. Keeping it here leaves the
# installer unchanged for its normal use.
#
# Usage: bash lib/resync.sh          -> 0 regenerated, 1 the install refused
set -uo pipefail

ADT_DIR="${ADT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CLAUDE="$ADT_DIR/.claude"

# Snapshot the installed playbooks BEFORE the run. Compared after, this is what
# tells an operator their session is now running stale instructions (problem 6):
# a playbook is prompt text the harness reads ONCE, at load, so a command whose
# installed copy just changed will keep following the copy loaded at session
# start. Nothing else says so, and the divergence is invisible at exactly the
# moment it matters — /adt-qa-run wrote its FAIL findings to a path that no
# longer existed, against the instruction being followed.
#
# Hashes, not mtimes: install-defaults.sh rewrites every managed file on every
# run, so mtime changes even when the content does not and would report the
# whole command set as stale every time.
declare -a BEFORE_NAMES=() BEFORE_HASHES=()
if [[ -d "$CLAUDE/commands" ]]; then
  while IFS= read -r f; do
    BEFORE_NAMES+=("$(basename "$f")")
    BEFORE_HASHES+=("$(shasum -a 256 "$f" | awk '{print $1}')")
  done < <(find "$CLAUDE/commands" -name '*.md' | sort)
fi

_hash_before() {  # _hash_before <basename> -> the pre-run hash, empty if new
  local want="$1" i
  for i in "${!BEFORE_NAMES[@]}"; do
    [[ "${BEFORE_NAMES[$i]}" == "$want" ]] && { printf '%s' "${BEFORE_HASHES[$i]}"; return; }
  done
}

# Clear the managed files first. Without this, resync cannot do the one thing it
# exists for: copy()'s "keeping your edited copy" guard preserves any installed
# file whose sha has drifted from the manifest, so the files that most need
# regenerating are exactly the ones it refuses to touch — and it tells the
# operator to delete them by hand. In THIS repo that guard is wrong: `.claude/`
# is generated and committed, and .gitattributes says the source is
# authoritative. A consumer's install still gets the guard; only self-hosting
# regeneration bypasses it.
#
# Removing by MANIFEST, not `rm -rf .claude`: the manifest is the record of what
# ADT wrote, so anything a project authored itself is left alone. It also stops
# the stale-manifest truncation — a file present but absent from the manifest is
# read as project-authored, kept, and dropped from the receipt, which is how the
# install came to manage 44 files while the committed manifest listed 43.
MANIFEST="$CLAUDE/.adt-manifest.json"
if [[ -f "$MANIFEST" ]]; then
  cleared=0
  while IFS= read -r rel; do
    [[ -n "$rel" && -f "$CLAUDE/$rel" ]] || continue
    rm -f "$CLAUDE/$rel"; cleared=$((cleared + 1))
  done < <(/usr/bin/python3 -c "
import json, sys
try:
    d = json.load(open('$MANIFEST'))
except Exception:
    sys.exit(0)
for f in d.get('files', []):
    p = f.get('path') if isinstance(f, dict) else f
    if p:
        print(p)
")
  echo "[resync] cleared $cleared managed file(s) so the install rewrites them"
fi

echo "[resync] regenerating $CLAUDE from source"
if ! bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$ADT_DIR"; then
  # NOT "unchanged": the guard is per-file inside copy(), so everything copied
  # before the refusal is already written and the manifest (written last) is not.
  # Claiming otherwise told an operator their tree was clean when it was half
  # regenerated (QA finding 7).
  echo "[resync] the install REFUSED. .claude/ may be PARTLY regenerated — the" >&2
  echo "         refusal stops the run where it happened, and the manifest is" >&2
  echo "         written last, so it is stale. Fix the cause above and re-run." >&2
  exit 1
fi

# ── The changed-playbook report (2b) ───────────────────────────────────────
CHANGED=()
if [[ -d "$CLAUDE/commands" ]]; then
  while IFS= read -r f; do
    name="$(basename "$f")"
    now="$(shasum -a 256 "$f" | awk '{print $1}')"
    was="$(_hash_before "$name")"
    [[ "$now" != "$was" ]] && CHANGED+=("$name")
  done < <(find "$CLAUDE/commands" -name '*.md' | sort)
fi

echo
if [[ ${#CHANGED[@]} -eq 0 ]]; then
  echo "[resync] no playbook changed — any command already loaded is still current."
else
  echo "[resync] ${#CHANGED[@]} playbook(s) changed in this run:"
  printf '           %s\n' "${CHANGED[@]}"
  echo
  echo "[resync] A playbook is prompt text the harness reads ONCE, at session start."
  echo "         A session already running will keep following the copy it loaded,"
  echo "         not the one just written. Reload the session before invoking any"
  echo "         command listed above."
fi
