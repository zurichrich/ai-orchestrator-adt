#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that adt_sync recognises a project by its .adt/ directory and resolves
# the state directory to .adt/state.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; ADT="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0
ok(){ echo "  PASS $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL $1"; FAIL=$((FAIL+1)); }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

python3 - "$ADT" "$T" <<'PY'
import os, sys
adt, tmp = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(adt, "tools"))
import adt_sync
res = []
def chk(c, msg): res.append(("PASS" if c else "FAIL", msg))

new = os.path.join(tmp, "adtonly"); os.makedirs(os.path.join(new, ".adt", "state"))
chk(adt_sync.is_adt_project(new), "is_adt_project recognises a .adt project")
chk(adt_sync.adt_state_dir(new).endswith("/.adt/state"), "state resolves to .adt/state")

# The old development-team/.adt-state layout is not recognised.
folder = os.path.join(tmp, "folderonly"); os.makedirs(os.path.join(folder, "development-team", ".adt-state"))
chk(not adt_sync.is_adt_project(folder), "a folder-only tree is NOT an ADT project")

both = os.path.join(tmp, "both")
os.makedirs(os.path.join(both, ".adt", "state"))
os.makedirs(os.path.join(both, "development-team", ".adt-state"))
chk(adt_sync.adt_state_dir(both).endswith("/.adt/state"),
    "a stale old folder never diverts state resolution")

none = os.path.join(tmp, "none"); os.makedirs(none)
chk(not adt_sync.is_adt_project(none), "a non-ADT project is not claimed")
chk(adt_sync.adt_state_dir(none).endswith("/.adt/state"), "a fresh project resolves to .adt/state")
chk(adt_sync.adt_state_dir(new, "cost-ledger.log").endswith("state/cost-ledger.log"),
    "leaf components join onto the resolved base")

for status, msg in res: print("  %s %s" % (status, msg))
sys.exit(1 if any(s == "FAIL" for s, _ in res) else 0)
PY
rc=$?
[ $rc -eq 0 ] && PASS=7 || FAIL=1
echo; echo "PASS=$PASS FAIL=$FAIL"; [ "$FAIL" -eq 0 ]
