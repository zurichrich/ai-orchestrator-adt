#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT-384: lib/adt-layer.sh compares the ADT layer committed on a project's
# origin with this machine's ADT clone, and setup.sh acts on the result.
#
# Everything is scratch and offline: the "ADT clone" is a copy of this tree
# committed into a new repo, and each project's origin is a bare repo on disk.
# Prints plain `ok: <claim>` lines (the DoD greps them) and exits 1 on any
# failure.
set -uo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
ok()  { printf 'ok: %s\n' "$1"; }
bad() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
# shellcheck source=lib/scratch-adt.sh
source "$SRC/tests/lib/scratch-adt.sh"   # ADT at commit A, git + HOME isolated

# ── The scratch ADT clone's other commits ────────────────────────────────────
echo "# layer change B" >> "$ADT/defaults/rules/working-style.md"
git -C "$ADT" commit -qam B; B="$(git -C "$ADT" rev-parse HEAD)"
echo "readme change M" >> "$ADT/README.md"
git -C "$ADT" commit -qam M; M="$(git -C "$ADT" rev-parse HEAD)"
git -C "$ADT" checkout -q -b side "$A"
echo "# layer change D" >> "$ADT/defaults/rules/working-style.md"
git -C "$ADT" commit -qam D; D="$(git -C "$ADT" rev-parse HEAD)"

# ── Projects ─────────────────────────────────────────────────────────────────
mkproj() {  # mkproj <name> — bare origin + clone with one commit on main
  local n="$1"
  git init -q --bare "$TMP/$n.git"
  git clone -q "$TMP/$n.git" "$TMP/$n" 2>/dev/null
  echo "# $n" > "$TMP/$n/README.md"; echo "# $n" > "$TMP/$n/CLAUDE.md"
  git -C "$TMP/$n" add -A && git -C "$TMP/$n" commit -q -m init
  git -C "$TMP/$n" push -q origin main 2>/dev/null
}
commit_layer() {  # commit_layer <name> <adt-ref> — the team's committed layer
  local n="$1"
  adt_at "$2"
  "$ADT/lib/install-defaults.sh" "$ADT" "$TMP/$n" >/dev/null 2>&1
  git -C "$TMP/$n" add -A && git -C "$TMP/$n" commit -q -m "layer at $2"
  git -C "$TMP/$n" push -q origin main 2>/dev/null
}
state_of() {  # state_of <name> <adt-ref>
  adt_at "$2"
  bash "$ADT/lib/adt-layer.sh" state "$ADT" "$TMP/$1" main 2>/dev/null
}
clean() { [[ -z "$(git -C "$1" status --porcelain)" ]]; }

mkproj at_a;  commit_layer at_a "$A"
mkproj at_b;  commit_layer at_b "$B"
mkproj bare_p

echo "[test] the six states"
got="none=$(state_of bare_p "$A") equal=$(state_of at_a "$A") newer=$(state_of at_a "$B") older=$(state_of at_b "$A") diverged=$(state_of at_b "$D")"
if [[ "$got" == "none=none equal=equal newer=newer older=older diverged=diverged" ]]; then
  ok "the committed layer compares as equal, newer, older and diverged against the ADT clone"
else
  bad "states: $got"
fi

mkproj unknown; commit_layer unknown "$A"
fake="0123456789abcdef0123456789abcdef01234567"
sed -i.bak "s/\"source_commit\": \"$A\"/\"source_commit\": \"$fake\"/" "$TMP/unknown/.claude/.adt-manifest.json"
rm -f "$TMP/unknown/.claude/.adt-manifest.json.bak"
git -C "$TMP/unknown" commit -qam "unknown commit" && git -C "$TMP/unknown" push -q origin main 2>/dev/null
s="$(state_of unknown "$M")"
[[ "$s" == older ]] \
  && ok "a committed commit the ADT clone cannot find compares as older" \
  || bad "unknown committed commit compared as '$s', not older"

echo "[test] apply with two arguments reads the main branch from .adt/config.yaml"
# The /adt-close step 9 form passes no branch. A project on `trunk` whose clone
# has no origin/HEAD makes a broken read fall back to "main" and report `none`.
git init -q --bare "$TMP/trunky.git"
git clone -q "$TMP/trunky.git" "$TMP/trunky" 2>/dev/null
git -C "$TMP/trunky" checkout -q -b trunk
echo "# trunky" > "$TMP/trunky/CLAUDE.md"
git -C "$TMP/trunky" add -A && git -C "$TMP/trunky" commit -q -m init
commit_layer_on() { adt_at "$2"; "$ADT/lib/install-defaults.sh" "$ADT" "$TMP/$1" >/dev/null 2>&1
  git -C "$TMP/$1" add -A && git -C "$TMP/$1" commit -q -m layer && git -C "$TMP/$1" push -q origin "$3" 2>/dev/null; }
commit_layer_on trunky "$A" trunk
mkdir -p "$TMP/trunky/.adt"; printf 'main_branch: trunk\n' > "$TMP/trunky/.adt/config.yaml"
adt_at "$A"
# apply is what /adt-close runs, so apply is what is checked: its message names
# the branch it compared against, and a wrong one would reach `none` and install.
out="$(bash "$ADT/lib/adt-layer.sh" apply "$ADT" "$TMP/trunky" 2>&1)"; rc=$?
if [[ $rc -eq 0 ]] && grep -qF "on origin/trunk matches this ADT clone" <<<"$out" \
   && ! git -C "$TMP/trunky" symbolic-ref -q refs/remotes/origin/HEAD >/dev/null \
   && [[ -z "$(git -C "$TMP/trunky" status --porcelain -- . ':(exclude).adt')" ]]; then
  ok "apply with two arguments reads the main branch from .adt/config.yaml"
else
  bad "two-argument apply on trunk: rc=$rc out: $out"
fi

echo "[test] an older clone refuses"
adt_at "$A"
out="$(bash "$ADT/lib/adt-layer.sh" apply "$ADT" "$TMP/at_b" main 2>&1)"; rc=$?
if [[ $rc -eq 3 ]] && grep -qF "git -C $ADT pull" <<<"$out" && clean "$TMP/at_b"; then
  ok "an older ADT clone refuses and prints the git pull command for the clone"
else
  bad "older clone: rc=$rc, clean=$(clean "$TMP/at_b" && echo y || echo n), out: $out"
fi

echo "[test] unknown-lineage: a pinned commit from another repo goes to the branch"
# ADT was republished from a fresh history (AO-2): every consumer's committed
# manifest pins a commit this clone cannot resolve, and no pull ever will. With
# source_repo naming a different repository, that is a lineage change, not a
# possible downgrade, so it takes the reviewed-branch path.
mkproj lineage; commit_layer lineage "$A"
# the scratch ADT clone has no remote; give it a local one so "which repo is
# this a clone of" has an answer, as it does for a real clone.
git init -q --bare "$TMP/adt-origin.git" 2>/dev/null || true
git -C "$ADT" remote get-url origin >/dev/null 2>&1 \
  || git -C "$ADT" remote add origin "$TMP/adt-origin.git"
adt_at "$B"   # a clone whose layer differs, so there is something to write
/usr/bin/python3 - "$TMP/lineage" <<'PYEOF'
import json, subprocess, sys
p = sys.argv[1]
m = json.load(open(p + "/.claude/.adt-manifest.json"))
m["source_commit"] = "0" * 40          # a commit no clone has
m["source_repo"] = "someone/old-repo"  # from a repository this clone is not
json.dump(m, open(p + "/.claude/.adt-manifest.json", "w"), indent=2)
subprocess.run(["git", "-C", p, "add", ".claude/.adt-manifest.json"], check=True)
subprocess.run(["git", "-C", p, "commit", "-qm", "pin a foreign lineage"], check=True)
subprocess.run(["git", "-C", p, "push", "-q", "origin", "main"], check=True)
PYEOF
out="$(bash "$ADT/lib/adt-layer.sh" apply "$ADT" "$TMP/lineage" main 2>&1)"; rc=$?
branch="$(git -C "$TMP/lineage" for-each-ref --format='%(refname:short)' 'refs/heads/chore/adt-upgrade-*' | head -1)"
if [[ $rc -eq 0 && -n "$branch" ]] && clean "$TMP/lineage"; then
  ok "a layer pinned to another repository's history writes the upgrade branch instead of refusing"
else
  bad "unknown-lineage: rc=$rc out: $out"
fi

echo "[test] unknown commit from the SAME repo still refuses"
mkproj samerepo; commit_layer samerepo "$A"
/usr/bin/python3 - "$TMP/samerepo" "$ADT" <<'PYEOF'
import json, re, subprocess, sys
p, adt = sys.argv[1], sys.argv[2]
m = json.load(open(p + "/.claude/.adt-manifest.json"))
m["source_commit"] = "0" * 40
m["source_repo"] = subprocess.run(["git", "-C", adt, "remote", "get-url", "origin"],
                                  capture_output=True, text=True).stdout.strip() or None
# same normalisation the manifest writer applies
if m["source_repo"]:
    m["source_repo"] = re.sub(r"\.git$", "", re.sub(r"^.*github\.com[:/]", "", m["source_repo"]))
json.dump(m, open(p + "/.claude/.adt-manifest.json", "w"), indent=2)
subprocess.run(["git", "-C", p, "add", ".claude/.adt-manifest.json"], check=True)
subprocess.run(["git", "-C", p, "commit", "-qm", "pin an unpushed commit"], check=True)
subprocess.run(["git", "-C", p, "push", "-q", "origin", "main"], check=True)
PYEOF
out="$(bash "$ADT/lib/adt-layer.sh" apply "$ADT" "$TMP/samerepo" main 2>&1)"; rc=$?
if [[ $rc -eq 3 ]] && clean "$TMP/samerepo"; then
  ok "an unresolvable commit from the same repository still refuses (the downgrade guard)"
else
  bad "same-repo unknown commit: rc=$rc out: $out"
fi

echo "[test] a newer clone writes a branch, not the working tree"
mkproj up; commit_layer up "$A"
git -C "$TMP/up" checkout -q -b work
before="$(shasum -a 256 "$TMP/up/.claude/rules/working-style.md" | cut -d' ' -f1)"
adt_at "$B"
out="$(bash "$ADT/lib/adt-layer.sh" apply "$ADT" "$TMP/up" main 2>&1)"; rc=$?
br="chore/adt-upgrade-${B:0:12}"
after="$(shasum -a 256 "$TMP/up/.claude/rules/working-style.md" | cut -d' ' -f1)"
n_commits="$(git -C "$TMP/up" rev-list --count "origin/main..$br" 2>/dev/null || echo 0)"
changed="$(git -C "$TMP/up" diff --name-only "origin/main" "$br" 2>/dev/null)"
n_wt="$(git -C "$TMP/up" worktree list | wc -l | tr -d ' ')"
if [[ $rc -eq 0 && "$n_commits" == 1 ]] && grep -qx ".claude/rules/working-style.md" <<<"$changed" \
   && [[ "$(git -C "$TMP/up" branch --show-current)" == work && "$before" == "$after" && "$n_wt" == 1 ]] \
   && clean "$TMP/up"; then
  ok "a newer ADT clone commits the layer to a chore/adt-upgrade branch and leaves the current branch and working tree untouched"
else
  bad "newer clone: rc=$rc commits=$n_commits branch=$(git -C "$TMP/up" branch --show-current) same_file=$([[ $before == "$after" ]] && echo y || echo n) worktrees=$n_wt changed=[$changed] out: $out"
fi

adt_at "$M"
out="$(bash "$ADT/lib/adt-layer.sh" apply "$ADT" "$TMP/at_b" main 2>&1)"; rc=$?
if [[ $rc -eq 0 ]] && ! git -C "$TMP/at_b" show-ref --quiet --verify "refs/heads/chore/adt-upgrade-${M:0:12}" \
   && clean "$TMP/at_b"; then
  ok "a newer clone whose only change is the manifest creates no branch"
else
  bad "manifest-only newer clone: rc=$rc, branches: $(git -C "$TMP/at_b" branch | tr '\n' ' ') out: $out"
fi

# ── setup.sh ─────────────────────────────────────────────────────────────────
cfg() {  # cfg <dir> <name> <path> — a per-user project config
  mkdir -p "$1"
  cat > "$1/$2.yaml" <<YAML
name: $2
path: $3
main_branch: main
decision_log: docs/decisions.md
cache_dir: $TMP/cache-$2
github:
  repo: me/$2
  owner: me
kanban:
  id_prefix: TIX
YAML
}
setup() {  # setup <userp> [args…]
  local u="$1"; shift
  ADT_USER_PROJECTS="$u" bash "$ADT/setup.sh" "$@" 2>&1
}

echo "[test] setup.sh on a committed layer writes nothing tracked"
adt_at "$A"
mkproj joined; commit_layer joined "$A"
cfg "$TMP/u1" joined "$TMP/joined"
out="$(setup "$TMP/u1" --project joined)"; rc=$?
if [[ $rc -eq 0 && -z "$(git -C "$TMP/joined" status --porcelain -- . ':(exclude).adt')" ]]; then
  ok "setup.sh on a project with a committed layer writes nothing into the working tree"
else
  bad "setup.sh on a committed layer: rc=$rc status: $(git -C "$TMP/joined" status --porcelain | tr '\n' ' ') out: $out"
fi
[[ ! -e "$TMP/joined/docs/decisions.md" ]] \
  && ok "setup.sh does not seed docs/decisions.md on a project with a committed layer" \
  || bad "setup.sh seeded docs/decisions.md on a project whose layer is committed"

echo "[test] setup.sh on a first install writes into the working tree"
mkproj first
cfg "$TMP/u2" first "$TMP/first"
out="$(setup "$TMP/u2" --project first)"; rc=$?
if [[ $rc -eq 0 && -f "$TMP/first/.claude/.adt-manifest.json" ]]; then
  ok "with no committed layer on origin setup.sh installs into the working tree as before"
else
  bad "first install: rc=$rc, no manifest in the working tree. out: $out"
fi
[[ -f "$TMP/first/docs/decisions.md" ]] \
  && ok "setup.sh seeds docs/decisions.md on a first install" \
  || bad "setup.sh did not seed docs/decisions.md on a first install"

echo "[test] setup.sh never seeds over a decisions.md that origin already has"
mkproj seeded
mkdir -p "$TMP/seeded/docs"; echo "# our decisions" > "$TMP/seeded/docs/decisions.md"
git -C "$TMP/seeded" add -A && git -C "$TMP/seeded" commit -q -m decisions
git -C "$TMP/seeded" push -q origin main 2>/dev/null
git -C "$TMP/seeded" reset -q --hard HEAD~1     # behind origin: no file on disk
cfg "$TMP/u5" seeded "$TMP/seeded"
out="$(setup "$TMP/u5" --project seeded)"; rc=$?
if [[ $rc -eq 0 && ! -e "$TMP/seeded/docs/decisions.md" ]] && git -C "$TMP/seeded" pull -q 2>/dev/null; then
  ok "setup.sh does not seed docs/decisions.md when origin already has one"
else
  bad "setup.sh seeded over origin's decisions.md, or the pull failed: rc=$rc out: $out"
fi

echo "[test] a bare setup.sh skips a stale project and carries on"
mkproj a_stale; commit_layer a_stale "$B"
mkproj b_next
cfg "$TMP/u3" a_stale "$TMP/a_stale"; cfg "$TMP/u3" b_next "$TMP/b_next"
adt_at "$A"
out="$(setup "$TMP/u3")"; rc=$?
if [[ $rc -eq 0 ]] && grep -qF "skipping a_stale" <<<"$out" \
   && [[ -f "$TMP/b_next/.claude/.adt-manifest.json" ]] && clean "$TMP/a_stale"; then
  ok "a bare setup.sh skips a project whose ADT clone is older and still sets up the next project"
else
  bad "bare setup with a stale project: rc=$rc out: $out"
fi

echo "[test] ADT's own repo installs in place"
# Give the ADT clone an origin whose committed manifest names a commit it does
# not have. As an ordinary project that reads as older and would be refused.
git -C "$ADT" checkout -q -B main "$M"
git init -q --bare "$TMP/adt.git"
git -C "$ADT" remote add origin "$TMP/adt.git"
git -C "$ADT" push -q origin main 2>/dev/null
cfg "$TMP/u4" adtself "$ADT"
out="$(setup "$TMP/u4" --project adtself)"; rc=$?
wrote="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_commit"])' "$ADT/.claude/.adt-manifest.json" 2>/dev/null)"
if [[ $rc -eq 0 && "$wrote" == "$M" ]]; then
  ok "in ADT own repo the layer installs into the working tree"
else
  bad "ADT's own repo: rc=$rc manifest source_commit=$wrote (want $M) out: $out"
fi

echo
if [[ $FAILS -eq 0 ]]; then echo "All adt-layer tests passed."; else echo "$FAILS adt-layer test(s) FAILED."; exit 1; fi
