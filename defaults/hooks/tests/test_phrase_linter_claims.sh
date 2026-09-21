#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests adt-phrase-linter.sh check (c): a claim that something passed is flagged
# unless the command it names ran in the same turn. Also check (e) (AO-013): a
# counted or completeness claim is flagged unless the turn ran something that
# counts.
# Run:  bash defaults/hooks/tests/test_phrase_linter_claims.sh
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LINT="$HOOK_DIR/adt-phrase-linter.sh"
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

run() { printf '{"transcript_path":"%s"}' "$1" | bash "$LINT" 2>/dev/null; }
jrec() { /usr/bin/python3 -c '
import json,sys
role,kind,payload=sys.argv[1],sys.argv[2],sys.argv[3]
if kind=="text": c=[{"type":"text","text":payload}]
else: c=[{"type":"tool_use","name":"Bash","input":{"command":payload}}]
print(json.dumps({"message":{"role":role,"content":c}}))' "$1" "$2" "$3" >> "$4"; }
user_text() { jrec user text "$1" "$2"; }
asst_text() { jrec assistant text "$1" "$2"; }
asst_bash() { jrec assistant bash "$1" "$2"; }
flagged() { echo "$1" | grep -qi "a claim that something passed"; }

echo "== adt-phrase-linter.sh — check (c), unbacked pass-claims =="

# 1. A named suite claimed passing with nothing run → flagged.
T="$TMP/c1.jsonl"; : > "$T"
user_text "did it work" "$T"
asst_text "I ran it and pytest tools/tests/ passes cleanly." "$T"
out="$(run "$T")"
flagged "$out" && ok "named suite claimed passing, nothing run → flagged" \
                || bad "unbacked pass-claim not flagged, got: $out"

# 2. Same claim, with the matching command run → not flagged.
T="$TMP/c2.jsonl"; : > "$T"
user_text "did it work" "$T"
asst_bash "python3 -m pytest tools/tests/ -q" "$T"
asst_text "pytest tools/tests/ passes cleanly." "$T"
out="$(run "$T")"
flagged "$out" && bad "backed pass-claim wrongly flagged, got: $out" \
                || ok "claim backed by the matching command → not flagged"

# 3. A Bash call that is not the named command does not back the claim.
T="$TMP/c3.jsonl"; : > "$T"
user_text "did it work" "$T"
asst_bash "git status" "$T"
asst_text "pytest tools/tests/ passes cleanly." "$T"
out="$(run "$T")"
flagged "$out" && ok "unrelated command does not back a named claim → flagged" \
                || bad "an unrelated command satisfied the claim, got: $out"

# 4. A claim that names no command, with nothing run → flagged.
T="$TMP/c4.jsonl"; : > "$T"
user_text "status" "$T"
asst_text "All checks are green." "$T"
out="$(run "$T")"
flagged "$out" && ok "claim naming no command, nothing run → flagged" \
                || bad "claim naming no command not flagged, got: $out"

# 5. A claim that names no command is accepted if any command ran.
T="$TMP/c5.jsonl"; : > "$T"
user_text "status" "$T"
asst_bash "make check" "$T"
asst_text "All checks are green." "$T"
out="$(run "$T")"
flagged "$out" && bad "claim with a command run wrongly flagged, got: $out" \
                || ok "claim naming no command, with a command run → accepted"

# 6. No pass-claim → no flag.
T="$TMP/c6.jsonl"; : > "$T"
user_text "status" "$T"
asst_text "Wave A is committed; moving to wave B." "$T"
out="$(run "$T")"
flagged "$out" && bad "fired on a turn with no pass-claim, got: $out" \
                || ok "no pass-claim → no flag"

# 7. A command run in a previous turn does not back this turn's claim.
T="$TMP/c7.jsonl"; : > "$T"
user_text "run the tests" "$T"
asst_bash "python3 -m pytest tools/tests/ -q" "$T"
asst_text "ran" "$T"
user_text "are they still green" "$T"
asst_text "Yes, pytest tools/tests/ passes." "$T"
out="$(run "$T")"
flagged "$out" && ok "prior-turn command does not back this turn's claim → flagged" \
                || bad "a prior-turn run satisfied this turn's claim, got: $out"

counted() { echo "$1" | grep -qi "counted or completeness claim"; }

echo ""
echo "== adt-phrase-linter.sh — check (e), unbacked counted claims =="

# e1. The AO-006 failure, verbatim in shape: a quantity plus a completeness word,
# backed by a grep that counts nothing.
T="$TMP/e1.jsonl"; : > "$T"
user_text "did the markers survive" "$T"
asst_bash "grep -n 'marker' tools/adt_sync.py" "$T"
asst_text "There are 9 distinct markers, all ok." "$T"
out="$(run "$T")"
counted "$out" && ok "counted claim with no counting command -> flagged" \
                || bad "unbacked counted claim not flagged, got: $out"

# e2. The other AO-006 failure: a completeness claim about a count.
T="$TMP/e2.jsonl"; : > "$T"
user_text "how many are left" "$T"
asst_bash "grep -n 'adt-brief\|adt-plan\|adt-close' docs/references.md" "$T"
asst_text "Those are the only two remaining mentions in the docs." "$T"
out="$(run "$T")"
counted "$out" && ok "completeness claim about a count, no counter -> flagged" \
                || bad "unbacked completeness claim not flagged, got: $out"

# e3. The same sentence with a counting command in the turn.
T="$TMP/e3.jsonl"; : > "$T"
user_text "did the markers survive" "$T"
asst_bash "grep -c 'marker' tools/adt_sync.py" "$T"
asst_text "There are 9 distinct markers, all ok." "$T"
out="$(run "$T")"
counted "$out" && bad "backed counted claim wrongly flagged, got: $out" \
                || ok "claim backed by grep -c -> not flagged"

# e4. The false-positive control, taken from this ticket's own Plan-critique
# prose and backed the way this project actually counts: an inline python3
# script using len(). Without the len( arm this fires on verified work.
T="$TMP/e4.jsonl"; : > "$T"
user_text "check the plan" "$T"
asst_bash "python3 -c \"import re;print(len(re.findall(r'must_run', open('t.md').read())))\"" "$T"
asst_text "Test-plan/done_evidence agreement: 12 named files, all in a condition." "$T"
out="$(run "$T")"
counted "$out" && bad "a len(-backed count wrongly flagged, got: $out" \
                || ok "count produced by an inline python3 len( -> not flagged"

# e5. A bare number with no completeness word is not a counted claim. Both
# halves are required, or the check fires on every line reference in a report.
T="$TMP/e5.jsonl"; : > "$T"
user_text "where is it" "$T"
asst_text "The sentinel return sits at tools/adt_watch.py:404, four lines below the quoted block." "$T"
out="$(run "$T")"
counted "$out" && bad "a bare line reference was read as a counted claim: $out" \
                || ok "quantity without a completeness word -> not flagged"

# e6. The regression the cleanup pass found: a pass-claim carries a quantity and
# a completeness word, and a pytest invocation contains no counting command. This
# is the commonest correct sentence in the repo, and check (c) already governs it.
T="$TMP/e6.jsonl"; : > "$T"
user_text "run the tests" "$T"
asst_bash "python3 -m pytest tools/tests/ -q" "$T"
asst_text "All 23 tests pass." "$T"
out="$(run "$T")"
counted "$out" && bad "a pytest-backed pass-claim was flagged by (e): $out" \
                || ok "pass-claim left to check (c) -> not flagged by (e)"

# e7. A sentence whose only number IS a reference carries no quantity at all.
T="$TMP/e7.jsonl"; : > "$T"
user_text "where" "$T"
asst_text "Every marker is at tools/adt_watch.py:404." "$T"
out="$(run "$T")"
counted "$out" && bad "a citation-only sentence was read as a counted claim: $out" \
                || ok "a number that is only a citation -> not flagged"

# e8. The regression QA found: the skip used to exempt the whole sentence, so a
# trailing ticket id switched the check off. A ticket id is close to a habit in
# this repo's prose, so that hole was most of the check's reach.
T="$TMP/e8.jsonl"; : > "$T"
user_text "did the markers survive" "$T"
asst_bash "grep -n 'marker' tools/adt_sync.py" "$T"
asst_text "There are 9 distinct markers, all ok (AO-006)." "$T"
out="$(run "$T")"
counted "$out" && ok "a trailing ticket id no longer exempts the sentence -> flagged" \
                || bad "a ticket id still switches the check off: $out"

# e9. Same, for a file:line reference sitting beside a real count.
T="$TMP/e9.jsonl"; : > "$T"
user_text "how many" "$T"
asst_bash "grep -n 'marker' tools/adt_sync.py" "$T"
asst_text "All 9 markers survived, per tools/adt_sync.py:146." "$T"
out="$(run "$T")"
counted "$out" && ok "a file:line beside a real count -> still flagged" \
                || bad "a citation still switches the check off: $out"

echo ""
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
