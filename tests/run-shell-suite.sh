#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
#
# Runs every shell test (`test_*.sh` under tests/ and defaults/hooks/tests/)
# once, with the egress probe armed. At the end it fails if any test failed, if
# no tests ran, or if any test reached the telemetry collector.
#
# This file is not named `test_*.sh` so that the `find` below does not pick it
# up and run itself.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ADT_SUITE_ROOTS overrides where tests are found, so
# tests/test_shell_suite_runner.sh can point the runner at a fixture tree. A
# relative root resolves against the repo; an absolute one is used as given.
read -r -a _ROOTS <<< "${ADT_SUITE_ROOTS:-tests defaults/hooks/tests}"
ROOTS=()
for r in "${_ROOTS[@]}"; do
  case "$r" in
    /*) ROOTS+=("$r") ;;
    *)  ROOTS+=("$REPO/$r") ;;
  esac
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
CAPTURE="$TMP/egress.tsv"

# Each run gets its own capture file, so a nested run cannot write into its
# parent's capture.
export PYTHONPATH="$REPO/tests/egress_probe${PYTHONPATH:+:$PYTHONPATH}"
export ADT_EGRESS_CAPTURE="$CAPTURE"
# Each test must set the telemetry opt-out itself, so do not inherit it.
unset DO_NOT_TRACK

FAILS=0

# --- positive control ------------------------------------------------------
# Make one deliberate request and check the probe records it. An empty capture
# at the end means nothing unless the probe is known to work.
python3 - <<'PY' >/dev/null 2>&1
import urllib.request, urllib.error
try:
    urllib.request.urlopen("https://adt-telemetry.zurichrich.workers.dev")
except urllib.error.URLError:
    pass
PY
if [[ -s "$CAPTURE" ]]; then
  echo "--- ok: egress probe is armed (positive control)"
else
  echo "::error::egress probe recorded nothing on a deliberate attempt — it is not armed, so an empty capture below would prove nothing"
  FAILS=$((FAILS + 1))
fi
: > "$CAPTURE"

# --- the suite -------------------------------------------------------------
# Run every test and report every failure, with each test's duration.
RAN=0
START=$(date +%s)
while IFS= read -r t; do
  printf '\n=== %s\n' "$t"
  s=$(date +%s)
  if bash "$t"; then
    printf -- '--- ok: %s (%ds)\n' "$t" "$(( $(date +%s) - s ))"
  else
    printf -- '::error file=%s::%s failed\n' "$t" "$t"
    printf -- '--- FAIL: %s (%ds)\n' "$t" "$(( $(date +%s) - s ))"
    FAILS=$((FAILS + 1))
  fi
  RAN=$((RAN + 1))
done < <(find "${ROOTS[@]}" -name 'test_*.sh' | sort)

printf '\nran %d shell test script(s) in %ds; failed: %d\n' \
  "$RAN" "$(( $(date +%s) - START ))" "$FAILS"

# --- final checks ----------------------------------------------------------
# An empty capture also results when no tests ran, so check that tests ran
# before reading the capture.
if (( RAN == 0 )); then
  echo "::error::no shell test scripts were found or executed — the empty capture below proves nothing"
  FAILS=$((FAILS + 1))
fi

if [[ -s "$CAPTURE" ]]; then
  printf '::error::shell tests reached the collector %s time(s):\n' \
    "$(wc -l < "$CAPTURE" | tr -d ' ')"
  cat "$CAPTURE"
  FAILS=$((FAILS + 1))
else
  echo "--- ok: no shell test reached the collector"
fi

if (( FAILS > 0 )); then
  printf '\n%d failure(s)\n' "$FAILS"
  exit 1
fi
printf '\nall shell suites passed\n'
