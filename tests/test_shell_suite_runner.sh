#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
#
# Tests `tests/run-shell-suite.sh` against fixture trees: a clean tree passes,
# and a test that reaches the collector, a failing test, or an empty tree each
# fail the run.
#
# `ADT_SUITE_ROOTS` points the runner at the fixture tree. Fixtures are built in
# `mktemp -d` so the real suite never discovers them.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$REPO/tests/run-shell-suite.sh"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkfixture() {  # mkfixture <dir> ; echoes the dir
  mkdir -p "$1"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$1/test_clean_a.sh"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$1/test_clean_b.sh"
}

# --- 1. a clean tree passes, and reports how many tests it ran --------------
CLEAN="$TMP/clean"; mkfixture "$CLEAN"
out="$(ADT_SUITE_ROOTS="$CLEAN" bash "$RUNNER" 2>&1)"; rc=$?
if (( rc == 0 )); then
  pass "clean fixture tree: runner exits 0"
else
  fail "clean fixture tree: runner exited $rc"
  printf '%s\n' "$out" | sed 's/^/      /'
fi
if grep -q 'ran 2 shell test script(s)' <<< "$out"; then
  pass "runner reports the number of tests it actually ran"
else
  fail "runner did not report running exactly the 2 fixture tests"
fi
if grep -q 'no shell test reached the collector' <<< "$out"; then
  pass "clean tree: runner asserts the capture was empty"
else
  fail "clean tree: runner never reported on the egress capture"
fi

# --- 2. a test that phones home fails the run -------------------------------
# The fixture test exits 0, so only the egress check can fail the run.
EGRESS="$TMP/egress"; mkfixture "$EGRESS"
cat > "$EGRESS/test_phones_home.sh" <<'EOF'
#!/usr/bin/env bash
python3 - <<'PY' >/dev/null 2>&1
import urllib.request, urllib.error
try:
    urllib.request.urlopen("https://adt-telemetry.zurichrich.workers.dev")
except urllib.error.URLError:
    pass
PY
exit 0
EOF
out="$(ADT_SUITE_ROOTS="$EGRESS" bash "$RUNNER" 2>&1)"; rc=$?
if (( rc != 0 )); then
  pass "a test that reaches the collector fails the run (the probe bites)"
else
  fail "a test reached the collector and the runner still exited 0"
fi
if grep -q 'reached the collector' <<< "$out"; then
  pass "the failure names the collector contact"
else
  fail "the run failed without the egress message"
  printf '%s\n' "$out" | sed 's/^/      /'
fi

# --- 3. a failing test fails the run ----------------------------------------
BAD="$TMP/bad"; mkfixture "$BAD"
printf '#!/usr/bin/env bash\nexit 1\n' > "$BAD/test_fails.sh"
out="$(ADT_SUITE_ROOTS="$BAD" bash "$RUNNER" 2>&1)"; rc=$?
if (( rc != 0 )); then
  pass "a failing test fails the run (exit codes are not discarded)"
else
  fail "a test exited 1 and the runner still exited 0"
fi
if grep -q '::error file=.*test_fails\.sh' <<< "$out"; then
  pass "the failing test is named in the output"
else
  fail "the failing test was not named in the output"
fi

# --- 4. an empty tree fails the run -----------------------------------------
EMPTY="$TMP/empty"; mkdir -p "$EMPTY"
out="$(ADT_SUITE_ROOTS="$EMPTY" bash "$RUNNER" 2>&1)"; rc=$?
if (( rc != 0 )); then
  pass "an empty tree fails rather than passing vacuously"
else
  fail "the runner found no tests and exited 0"
fi

if (( FAILS > 0 )); then
  printf '\n\033[31m%d check(s) failed\033[0m\n' "$FAILS"; exit 1
fi
printf '\n\033[32mall checks passed\033[0m\n'
