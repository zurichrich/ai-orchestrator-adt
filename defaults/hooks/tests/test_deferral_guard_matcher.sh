#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests which Bash commands adt-deferral-guard.sh treats as creating an Issue,
# and the approval forms it accepts (ADT-354). The guard used to match the
# command's text, so it denied a read-only grep that named the command and
# missed `gh api`, which creates an Issue just the same. It now reads the
# command's words. test_deferral_guard.sh covers the transcript side.
#
# Run:  bash defaults/hooks/tests/test_deferral_guard_matcher.sh
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GUARD="$HOOK_DIR/adt-deferral-guard.sh"
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

run() {
  /usr/bin/python3 -c '
import json,sys
print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[2]},
                  "transcript_path":sys.argv[1],"cwd":sys.argv[3]}))' \
    "$1" "$2" "$TMP" | bash "$GUARD" 2>/dev/null
}
denied() { echo "$1" | grep -q '"permissionDecision": *"deny"'; }
user_text() { /usr/bin/python3 -c 'import json,sys; print(json.dumps({"message":{"role":"user","content":[{"type":"text","text":sys.argv[1]}]}}))' "$1" >> "$2"; }

# A transcript with no authorisation in it.
NOAUTH="$TMP/noauth.jsonl"; : > "$NOAUTH"; user_text "carry on with the build" "$NOAUTH"

expect_deny()  { local out; out="$(run "$NOAUTH" "$2")"; denied "$out" && ok "deny: $1" || bad "NOT denied: $1"; }
expect_allow() { local out; out="$(run "$NOAUTH" "$2")"; denied "$out" && bad "wrongly denied: $1" || ok "allow: $1"; }

echo "== adt-deferral-guard.sh: which commands create an Issue =="

C='gh issue create --repo o/r --title x'

# The porcelain create, in every place a shell would run it.
expect_deny "plain create"                         "$C"
expect_deny "quoted body holding ; and |"          "$C"' --body "step 1; step 2 | done"'
expect_deny "multi-line quoted body"               "$C"' --body "line one
line two; still the body"'
expect_deny "after VAR=value"                      "GH_TOKEN=x $C"
expect_deny "after env VAR=value"                  "env GH_TOKEN=x $C"
expect_deny "after &&"                             "cd /tmp && $C"
expect_deny "on a second line"                     "echo hi
$C"
expect_deny "inside bash -c"                       "bash -c \"$C\""
expect_deny "inside sh -lc"                        "sh -lc '$C'"
expect_deny "inside eval"                          "eval \"$C\""
expect_deny "inside \$( )"                         "n=\$($C)"
expect_deny "inside backticks"                     "n=\`$C\`"
expect_deny "absolute path to gh"                  "/usr/local/bin/$C"

# Behind a wrapper. The guard checks every gh word in a command rather than a
# list of wrapper names, so a wrapper nobody listed is caught too.
expect_deny "after command"                        "command $C"
expect_deny "after exec"                           "exec $C"
expect_deny "after sudo -E"                        "sudo -E $C"
expect_deny "after sudo -u root"                   "sudo -u root $C"
expect_deny "after time"                           "time $C"
expect_deny "after nohup"                          "nohup $C"
expect_deny "after xargs -n1"                      "printf 'x' | xargs -n1 $C"
expect_deny "after timeout 30"                     "timeout 30 $C"
expect_deny "after nice"                           "nice -n 5 $C"
expect_deny "inside find -exec"                    "find . -name x -exec $C \\;"
expect_deny "gh -R before the subcommand"          'gh -R o/r issue create --title x'
expect_deny "gh issue -R before create"            'gh issue -R o/r create --title x'

# The REST and GraphQL forms.
expect_deny "gh api with a field (implies POST)"   'gh api repos/o/r/issues -f title=x'
expect_deny "gh api -X POST with --input"          'gh api -X POST repos/o/r/issues --input f.json'
expect_deny "gh api --method=POST"                 'gh api --method=POST /repos/o/r/issues -F title=x'
expect_deny "gh api graphql createIssue"           "gh api graphql -f query='mutation{createIssue(input:{repositoryId:\"R\",title:\"x\"}){issue{number}}}'"

# Unbalanced quotes: shlex cannot parse it, so the old text match decides.
expect_deny "unbalanced quote (fallback)"          "$C"' --title "x'
expect_deny "unbalanced-quote grep (fallback is the stricter match)" "grep '$C file"

# Commands that name the create but do not run it, and reads.
expect_allow "grep for the phrase"                 "grep \"$C\" commands/*.md"
expect_allow "echo of the phrase"                  "echo 'run $C later'"
expect_allow "gh issue list"                       'gh issue list --repo o/r'
expect_allow "gh api GET list with a query"        'gh api "repos/o/r/issues?state=all"'
expect_allow "gh api comment on an Issue"          'gh api repos/o/r/issues/5/comments -f body=x'
expect_allow "gh api -X GET with fields"           'gh api -X GET repos/o/r/issues -f state=all'
expect_allow "gh api labels"                       'gh api repos/o/r/labels -f name=adt:archive'
expect_allow "gh api graphql query"                "gh api graphql -f query='query{viewer{login}}'"
expect_allow "a command with no gh at all"         'ls -la'
expect_allow "gh inside another word"              'echo "through the high road" && ls'

# The guard runs on every Bash call, so a long command must stay cheap. A file
# written through a heredoc full of prose and gh words is the realistic worst
# case: a check whose cost doubled per wrapper word took seconds on one line.
LONG="$TMP/long.txt"
/usr/bin/python3 -c '
line = "if you do this then time will tell while gh sudo nice timeout env xargs " * 3
print("cat > notes.md <<EOF\n" + (line + "\n") * 2000 + "EOF")' > "$LONG"
start=$(date +%s)
expect_allow "2,000-line heredoc of prose and gh words" "$(cat "$LONG")"
elapsed=$(( $(date +%s) - start ))
[ "$elapsed" -le 3 ] && ok "long command checked in ${elapsed}s" \
  || bad "long command took ${elapsed}s (limit 3s)"

echo "== approval forms =="

T="$TMP/numbered.jsonl"; : > "$T"; user_text "1. approved
2. ok
3. merge" "$T"
out="$(run "$T" "$C")"
denied "$out" && bad "numbered reply with approved on its own line was denied" \
  || ok "allow: numbered reply, approved on its own line"

T="$TMP/dash.jsonl"; : > "$T"; user_text "- approve
- then merge" "$T"
out="$(run "$T" "$C")"
denied "$out" && bad "bulleted approve was denied" || ok "allow: bulleted approve on its own line"

T="$TMP/oneline.jsonl"; : > "$T"; user_text "1. approved 2. ok 3. merge" "$T"
out="$(run "$T" "$C")"
denied "$out" && ok "deny: numbered reply on one line" \
  || bad "a one-line numbered reply authorised a filing"

for msg in "write a regression test for that bug" \
           "review these files for issues" \
           "open the file and fix the bug" \
           "any bugs found in adt should be files in adt as an install bug P0"; do
  T="$TMP/intent.jsonl"; : > "$T"; user_text "$msg" "$T"
  out="$(run "$T" "$C")"
  denied "$out" && ok "deny: not a request to file: \"$msg\"" \
    || bad "treated as a request to file: \"$msg\""
done

out="$(run "$NOAUTH" "$C")"
echo "$out" | grep -q "on its own line" && ok "deny reason says approve goes on its own line" \
  || bad "deny reason does not mention 'on its own line'"

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
