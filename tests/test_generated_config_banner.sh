#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests the .adt/config.yaml that _write_adt_config renders: the GENERATED
# banner names its source, id_prefix appears once, and every argument lands.
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ADT_DIR
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
PROJ="$TMP/proj"; mkdir -p "$PROJ"
USERP="$TMP/userp"; mkdir -p "$USERP"

echo "[test] the rendered .adt/config.yaml names its source and derives id_prefix"
( export ADT_USER_PROJECTS="$USERP"
  # shellcheck disable=SC1091
  source "$ADT_DIR/lib/github-bootstrap.sh"
  _write_adt_config "$PROJ" "me/repo" "me" "ADT" ".adt/backlog" \
                    "7" "proj" "$TMP/cache" "" "main" ideas planned done
) >/dev/null 2>&1
CFG="$PROJ/.adt/config.yaml"

[[ -f "$CFG" ]] && pass "config rendered" || { fail "config NOT rendered"; exit 1; }

# _write_adt_config takes fixed positional arguments and then a list of stages,
# so an added positional would swallow the first stage.
got_stages="$(awk '/^stages:/{f=1;next} f&&/^  - name: /{print $3}' "$CFG" | tr '\n' ',')"
[[ "$got_stages" == "ideas,planned,done," ]] \
  && pass "all three stages emitted, in order" \
  || fail "stages emitted as [$got_stages], want [ideas,planned,done,]"
grep -q '^main_branch: main$' "$CFG" \
  && pass "main_branch emitted from its own argument" \
  || fail "main_branch not emitted as 'main': $(grep '^main_branch:' "$CFG")"

grep -qF "GENERATED - do not edit" "$CFG" \
  && pass "carries the GENERATED banner" || fail "no GENERATED banner"

# The banner names the file it was generated from.
grep -qF "source: $USERP/proj.yaml" "$CFG" \
  && pass "banner names the per-user config as its source" \
  || fail "banner does not name its source (got: $(grep -m1 GENERATED "$CFG" || true))"

n="$(grep -c '^id_prefix:' "$CFG")"
[[ "$n" -eq 1 ]] && pass "exactly one id_prefix definition (got $n)" \
                 || fail "expected 1 id_prefix line, got $n"

grep -q '^id_prefix: ADT$' "$CFG" \
  && pass "id_prefix carries the value it was given" \
  || fail "id_prefix wrong: $(grep '^id_prefix:' "$CFG" || true)"

grep -qi "authoritative" "$CFG" \
  && pass "states which copy is authoritative" || fail "does not state authority"

echo
if [[ $FAILS -eq 0 ]]; then printf '\033[32mall generated-config tests passed\033[0m\n'; else
  printf '\033[31m%s test(s) failed\033[0m\n' "$FAILS"; fi
exit $(( FAILS > 0 ))
