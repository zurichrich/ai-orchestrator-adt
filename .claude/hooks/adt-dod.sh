#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT default. Launcher for the Definition of Done grader, adt_dod.py.
#
# The playbooks (/adt-build, /adt-qa-run, /adt-release-check, /adt-plan,
# /adt-build-todone) grade a ticket's DoD with adt_dod.py, which lives in ADT's
# tools/. Install copies this launcher into <project>/.claude/hooks/ and the
# grader into <project>/.claude/tools/, and the playbooks call
# `.claude/hooks/adt-dod.sh …`. All arguments pass straight through.
#
# The grader is found by relative path, which covers two layouts:
#   - installed: .claude/hooks/adt-dod.sh   → ../tools/adt_dod.py  (= .claude/tools/)
#   - source:    defaults/hooks/adt-dod.sh  → ../../tools/adt_dod.py (= repo-root tools/)
set -euo pipefail

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_dod=""
for _cand in "$_here/../tools/adt_dod.py" "$_here/../../tools/adt_dod.py"; do
  [ -f "$_cand" ] && { _dod="$_cand"; break; }
done

if [ -z "$_dod" ]; then
  echo "WARN	adt-dod: could not resolve adt_dod.py near $_here (tried ../tools and ../../tools)" >&2
  exit 0  # a missing grader warns and passes rather than block the lifecycle
fi

exec python3 "$_dod" "$@"

# adt-bundle: v0.1.0
