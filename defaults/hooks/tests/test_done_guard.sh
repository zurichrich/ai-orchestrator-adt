#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests adt-done-guard.sh: the warning and worktree-teardown reminder on a move to
# done, and the three gates that can deny it (done_evidence, broken board links,
# a stale rendering checkout).
#
# Run:  bash agent-dev-team/defaults/hooks/tests/test_done_guard.sh
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GUARD="$HOOK_DIR/adt-done-guard.sh"
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

# Drive the hook with a tool_input.command and capture stdout.
run() { printf '{"tool_input":{"command":"%s"}}' "$1" | bash "$GUARD" 2>/dev/null; }
# Drive with an explicit cwd. The gates find .adt/ and the ticket .md from cwd.
run_cwd() { printf '{"tool_input":{"command":"%s"},"cwd":"%s"}' "$1" "$2" | bash "$GUARD" 2>/dev/null; }
# True if the hook printed a deny decision.
is_deny() { printf '%s' "$1" | grep -q '"permissionDecision": *"deny"'; }

echo "== adt-done-guard.sh =="

# Point the deploy-freshness gate at an empty agents dir so it never reads this
# machine's ~/Library/LaunchAgents. With no plist it only warns. run_g3 below
# overrides this for the deploy-freshness cases.
ADT_LAUNCHAGENTS_DIR="$(mktemp -d)/no-agents"; mkdir -p "$ADT_LAUNCHAGENTS_DIR"
export ADT_LAUNCHAGENTS_DIR

# A throwaway repo with a real linked worktree named after a slug.
REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" commit -q --allow-empty -m init
SLUG="close-path-does-not-tear-down"
git -C "$REPO" worktree add -q "$REPO/../wt-$SLUG" -b "dev/$SLUG" 2>/dev/null
cd "$REPO"   # the hook runs `git worktree list` in cwd

# 1. Move to done with a matching worktree → the done warning and the teardown reminder.
out="$(run "git mv bugs/building/$SLUG.md bugs/done/$SLUG.md")"
echo "$out" | grep -q "reaches done only" && echo "$out" | grep -q "worktree for '$SLUG' still exists" \
  && ok "move to done with matching worktree → done warning + teardown reminder" \
  || bad "expected both messages, got: $out"

# 2. Move to done with no matching worktree → the done warning only.
out="$(run "git mv bugs/building/some-other-ticket.md bugs/done/some-other-ticket.md")"
echo "$out" | grep -q "reaches done only" && ! echo "$out" | grep -q "still exists" \
  && ok "move to done without matching worktree → done warning only" \
  || bad "expected done warning without the teardown reminder, got: $out"

# 3. A path that is not a move to done → no output.
out="$(run "edit src/done_handler.py")"
[ -z "$out" ] && ok "non-done path → no output" || bad "expected no output, got: $out"

# 4. Malformed stdin → exit 0.
printf 'not json' | bash "$GUARD" >/dev/null 2>&1
[ "$?" = "0" ] && ok "malformed stdin → exit 0 (fail-open)" || bad "non-zero exit on bad stdin"

cd /

# ── the deny gates: a project with a .adt/ board and ticket files ──────
# A throwaway repo with its own git root, so the hook resolves the project to it.
G="$(mktemp -d)"
git -C "$G" init -q
git -C "$G" commit -q --allow-empty -m init
mkdir -p "$G/.adt" "$G/tasks/qa" "$G/tasks/done"

# The rendered board. It mentions references.html only inside a tooltip, with no
# link to it yet, and links one sibling page that exists.
cat > "$G/.adt/kanban.html" <<'HTML'
<html><body>
<div class="card" title="see references.html for context">Card</div>
<a href="tickets/foo.html">foo</a>
</body></html>
HTML
mkdir -p "$G/.adt/tickets"; echo "<html>foo</html>" > "$G/.adt/tickets/foo.html"

# ── artifact gate (done_evidence) ──
# 5a. done_evidence requires the references link; the board has only the tooltip → DENY.
cat > "$G/tasks/qa/art.md" <<'MD'
---
tokens: unattributed
cost_usd: unattributed
cost_tier: unattributed
id: ADT-58
done_evidence:
  - file: .adt/kanban.html
    must_contain_regex: '<a [^>]*href="references\.html"'
---
body
MD
out="$(run_cwd "git mv tasks/qa/art.md tasks/done/art.md" "$G")"
is_deny "$out" && ok "artifact gate: tooltip-only match → DENY (not the anchor)" \
  || bad "tooltip-only should DENY, got: $out"

# 5b. Add the link to the board → the same ticket is allowed.
sed -i.bak 's#</body>#<a href="references.html">refs</a></body>#' "$G/.adt/kanban.html" 2>/dev/null \
  || perl -pi -e 's#</body>#<a href="references.html">refs</a></body>#' "$G/.adt/kanban.html"
echo "<html>refs</html>" > "$G/.adt/references.html"   # so the link resolves
out="$(run_cwd "git mv tasks/qa/art.md tasks/done/art.md" "$G")"
! is_deny "$out" && ok "artifact gate: real anchor present → allowed" \
  || bad "real anchor should be allowed, got: $out"

# 5c. The DoD is in the body only, with none in the frontmatter → DENY.
#     The board is good here (5b), so a deny can only come from this gate.
cat > "$G/tasks/qa/bodyonly.md" <<'MD'
---
tokens: unattributed
cost_usd: unattributed
cost_tier: unattributed
id: ADT-273
---

# T

### Definition of Done (machine-checkable)
```yaml
done_evidence:
  - must_run: 'true'
    lane: done
```
MD
out="$(run_cwd "mv tasks/qa/bodyonly.md tasks/done/bodyonly.md" "$G")"
is_deny "$out" && ok "artifact gate: body-only DoD -> DENY" \
  || bad "body-only DoD should DENY, got: $out"

# 5d. The same ticket with the DoD also in frontmatter -> allowed.
cat > "$G/tasks/qa/bodyonly.md" <<'MD'
---
tokens: unattributed
cost_usd: unattributed
cost_tier: unattributed
id: ADT-273
done_evidence:
  - must_run: 'true'
    lane: done
---

# T

### Definition of Done (machine-checkable)
```yaml
done_evidence:
  - must_run: 'true'
    lane: done
```
MD
out="$(run_cwd "mv tasks/qa/bodyonly.md tasks/done/bodyonly.md" "$G")"
! is_deny "$out" && ok "artifact gate: same DoD in frontmatter -> allowed" \
  || bad "frontmatter DoD should be allowed, got: $out"

# ── 404 gate (board links) ──
# 6. A ticket with no done_evidence, but the board links a missing sibling → DENY.
cat > "$G/tasks/qa/link.md" <<'MD'
---
tokens: unattributed
cost_usd: unattributed
cost_tier: unattributed
id: ADT-59
---
body
MD
cat > "$G/.adt/kanban.html" <<'HTML'
<html><body><a href="references.html">refs</a><a href="tickets/gone.html">gone</a></body></html>
HTML
# references.html exists (from 5b); tickets/gone.html does not.
out="$(run_cwd "git mv tasks/qa/link.md tasks/done/link.md" "$G")"
is_deny "$out" && ok "404 gate: board links a missing sibling → DENY" \
  || bad "missing sibling should DENY, got: $out"

# 7. Same board, all links resolve → allowed.
cat > "$G/.adt/kanban.html" <<'HTML'
<html><body><a href="references.html">refs</a><a href="tickets/foo.html">foo</a></body></html>
HTML
out="$(run_cwd "git mv tasks/qa/link.md tasks/done/link.md" "$G")"
! is_deny "$out" && ok "404 gate: all links resolve → allowed" \
  || bad "all-resolve should be allowed, got: $out"

# ── artifact file missing ──
# 8. done_evidence names a file that does not exist → warn, not deny.
cat > "$G/tasks/qa/infra.md" <<'MD'
---
tokens: unattributed
cost_usd: unattributed
cost_tier: unattributed
id: ADT-60
done_evidence:
  - file: .adt/nonexistent.html
    must_contain_regex: 'whatever'
---
body
MD
out="$(run_cwd "git mv tasks/qa/infra.md tasks/done/infra.md" "$G")"
! is_deny "$out" && ok "artifact gate: absent artifact file → fail-open (warn, not deny)" \
  || bad "absent artifact should warn, not deny, got: $out"

# 8b. A DoD with one unverifiable condition and one failing condition → DENY.
#     The unverifiable one is listed first, so a warning must not hide the failure.
cat > "$G/tasks/qa/mask.md" <<'MD'
---
tokens: unattributed
cost_usd: unattributed
cost_tier: unattributed
id: ADT-106F
done_evidence:
  - file: .adt/nonexistent.html
    must_contain_regex: 'whatever'
  - must_run: 'exit 3'
    lane: done
---
body
MD
out="$(run_cwd "git mv tasks/qa/mask.md tasks/done/mask.md" "$G")"
is_deny "$out" && ok "artifact gate: warn does not mask deny (infra + failing → DENY)" \
  || bad "a warning must not hide a failing condition, got: $out"

# ── deploy-freshness gate ──────────────────────────────────────────────────
# ADT_LAUNCHAGENTS_DIR points at a fixture plist naming a fixture rendering
# checkout, whose HEAD is then compared with its origin/main. The ticket has no
# done_evidence and the board has no links, so only this gate can deny.
cat > "$G/.adt/kanban.html" <<'HTML'
<html><body>board</body></html>
HTML
cat > "$G/tasks/qa/fresh.md" <<'MD'
---
tokens: unattributed
cost_usd: unattributed
cost_tier: unattributed
id: ADT-62
---
body
MD

# A fixture rendering checkout REPO_R with an origin REPO_O.
REPO_O="$(mktemp -d)/origin.git"; mkdir -p "$REPO_O"; git -C "$REPO_O" init -q --bare
REPO_R="$(mktemp -d)/render"
git clone -q "$REPO_O" "$REPO_R" 2>/dev/null
git -C "$REPO_R" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base
git -C "$REPO_R" push -q origin HEAD:main 2>/dev/null
git -C "$REPO_R" branch -q --set-upstream-to=origin/main 2>/dev/null || true

# A fixture LaunchAgents dir + plist pointing adt_watch.py at REPO_R.
LA="$(mktemp -d)/agents"; mkdir -p "$LA"
cat > "$LA/com.adt.test.watch.plist" <<PLIST
<plist><dict><key>ProgramArguments</key><array>
<string>/usr/bin/python3</string>
<string>$REPO_R/tools/adt_watch.py</string>
<string>--root</string><string>$REPO_R</string><string>--once</string>
</array></dict></plist>
PLIST

run_g3() { printf '{"tool_input":{"command":"%s"},"cwd":"%s"}' "$1" "$G" | ADT_LAUNCHAGENTS_DIR="$LA" bash "$GUARD" 2>/dev/null; }

# 9. Rendering checkout at origin/main → allowed.
out="$(run_g3 "git mv tasks/qa/fresh.md tasks/done/fresh.md")"
! is_deny "$out" && ok "deploy-freshness: checkout at origin/main → allowed" \
  || bad "fresh checkout should be allowed, got: $out"

# 10. Advance origin/main past the checkout's HEAD → checkout is behind → DENY.
#     The new commit is built on origin/main so the push fast-forwards.
REPO_R2="$(mktemp -d)/render2"; git clone -q "$REPO_O" "$REPO_R2" 2>/dev/null
git -C "$REPO_R2" checkout -q -B main origin/main
git -C "$REPO_R2" -c user.email=t@t -c user.name=t commit -q --allow-empty -m ahead
git -C "$REPO_R2" push -q origin main:main 2>/dev/null
# origin/main is now ahead of REPO_R's HEAD.
out="$(run_g3 "git mv tasks/qa/fresh.md tasks/done/fresh.md")"
is_deny "$out" && ok "deploy-freshness: checkout behind origin/main → DENY" \
  || bad "behind checkout should DENY, got: $out"

# 11. No plist in the agents dir → warn, not deny.
LA_EMPTY="$(mktemp -d)/empty-agents"; mkdir -p "$LA_EMPTY"
out="$(printf '{"tool_input":{"command":"git mv tasks/qa/fresh.md tasks/done/fresh.md"},"cwd":"%s"}' "$G" | ADT_LAUNCHAGENTS_DIR="$LA_EMPTY" bash "$GUARD" 2>/dev/null)"
! is_deny "$out" && ok "deploy-freshness: no watcher plist → warn, not deny" \
  || bad "no-watcher should NOT deny, got: $out"

# ── close gate (#0): /adt-close stamped tokens, cost_usd and cost_tier ──
# No watcher, so gate #3 only warns; a deny here can only come from gate #0.
run_close() { printf '{"tool_input":{"command":"mv tasks/qa/close.md tasks/done/close.md"},"cwd":"%s"}' "$G" | ADT_LAUNCHAGENTS_DIR="$LA_EMPTY" bash "$GUARD" 2>/dev/null; }
close_ticket() { { echo "---"; echo "id: ADT-359"; for l in "$@"; do echo "$l"; done; echo "---"; echo; echo "# t"; } > "$G/tasks/qa/close.md"; }

# 9a. No stamp at all → DENY, naming the close gate and /adt-close.
close_ticket
out="$(run_close)"
is_deny "$out" && printf '%s' "$out" | grep -q 'close gate' && printf '%s' "$out" | grep -q '/adt-close' \
  && ok "close gate: a ticket with no cost stamp is denied" \
  || bad "close gate: unstamped ticket should be denied, got: $out"

# 9b. tokens only → DENY, naming the two missing fields.
close_ticket "tokens: 1200"
out="$(run_close)"
is_deny "$out" && printf '%s' "$out" | grep -q 'cost_usd:' && printf '%s' "$out" | grep -q 'cost_tier:' \
  && ! printf '%s' "$out" | grep -q '`tokens:`' \
  && ok "close gate: names only the fields that are missing" \
  || bad "close gate: should name cost_usd and cost_tier only, got: $out"

# 9c. A null value counts as missing.
close_ticket "tokens: 1200" "cost_usd: null" "cost_tier: measured"
out="$(run_close)"
is_deny "$out" && printf '%s' "$out" | grep -q 'cost_usd:' \
  && ok "close gate: cost_usd: null is treated as missing" \
  || bad "close gate: a null cost_usd should be denied, got: $out"

# 9d. unattributed in all three → allowed. The gate asserts presence, not a number.
close_ticket "tokens: unattributed" "cost_usd: unattributed" "cost_tier: unattributed"
out="$(run_close)"
! is_deny "$out" \
  && ok "close gate: unattributed in all three fields is allowed" \
  || bad "close gate: unattributed should pass, got: $out"

rm -rf "$REPO_O" "$REPO_R" "$REPO_R2" "$LA" "$LA_EMPTY" 2>/dev/null || true
rm -rf "$G" 2>/dev/null || true

# Cleanup
git -C "$REPO" worktree remove "$REPO/../wt-$SLUG" --force 2>/dev/null || true
rm -rf "$REPO" "$REPO/../wt-$SLUG" 2>/dev/null || true

echo ""
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = "0" ]
