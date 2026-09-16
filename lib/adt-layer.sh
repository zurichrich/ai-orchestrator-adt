#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# agent-dev-team — lib/adt-layer.sh
#
# Decides what installing the shared ADT layer (.claude/ copies, the CLAUDE.md
# rules block, the .gitignore block) may do to a project that other machines
# share (ADT-384).
#
# The layer is committed, so it belongs to the team. The ADT clone on this
# machine is only this machine's. Before ADT-384 every install path copied the
# clone's layer straight into the working tree, so a machine with a newer clone
# upgraded everyone as a side effect, and a machine with an older one silently
# downgraded them. Now the layer committed on origin/<main> is compared with the
# clone first:
#
#   none      no manifest on origin/<main> (or no origin): first install, write
#             into the working tree as before
#   equal     the clone is the committed version: write nothing
#   newer     the committed commit is an ancestor of the clone: write the
#             upgrade onto a local chore/adt-upgrade-<sha> branch, never into
#             the working tree, so the team reviews it as a PR
#   older     the clone is an ancestor of the committed commit, or does not
#             have that commit at all: refuse, and name the pull
#   diverged  both commits known, neither an ancestor: warn, then treat as newer
#   self      ADT's own repo (below)
#
# ADT's own repo is the exception. There the clone IS the project, so the
# committed manifest always trails HEAD by the commit that committed it, and the
# comparison would call every run an upgrade. It installs in place, as before.
#
# Sourced (setup.sh, adt-install.sh):
#   adt_layer_state <ADT_DIR> <PROJECT_PATH> [main]  sets ADT_LAYER_STATE,
#       ADT_LAYER_COMMITTED, ADT_LAYER_BUNDLE, ADT_LAYER_HEAD, ADT_LAYER_MAIN,
#       ADT_LAYER_MANIFEST
#   adt_layer_apply <ADT_DIR> <PROJECT_PATH> [main]  acts on it; returns 3 when
#       it refuses an older clone. Sets ADT_LAYER_BRANCH on an upgrade.
# Run (the /adt-close step 9 form):
#   lib/adt-layer.sh apply <ADT_DIR> <PROJECT_PATH> [main]
#   lib/adt-layer.sh state <ADT_DIR> <PROJECT_PATH> [main]

_adt_layer_say() { printf '  [layer] %s\n' "$*" >&2; }

# main branch: the argument, else the generated .adt/config.yaml, else
# origin/HEAD, else "main". /adt-close calls this with two arguments only.
_adt_layer_main() {  # <PROJECT_PATH> [main]
  local p="$1" m="${2:-}"
  if [[ -z "$m" && -f "$p/.adt/config.yaml" ]]; then
    m="$(sed -n 's/^main_branch:[[:space:]]*//p' "$p/.adt/config.yaml" | head -1)"
  fi
  if [[ -z "$m" ]]; then
    m="$(git -C "$p" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || true)"
    m="${m#origin/}"
  fi
  printf '%s\n' "${m:-main}"
}

# An identity for "which repository is this clone of", for comparing against the
# manifest's source_repo. Not the github helper below: that one returns empty for
# any non-github remote, which would silently switch the lineage check off.
_adt_layer_origin_id() {  # <DIR>
  # A clone with no origin is normal (the test fixtures, a tarball install), and
  # its `git remote get-url` exit code would kill a caller running with
  # `set -euo pipefail` — which is how this first shipped and took out every
  # install-join test.
  local url
  url="$(git -C "$1" remote get-url origin 2>/dev/null || true)"
  printf '%s' "$url" | sed -E 's#^.*github\.com[:/]##; s#\.git$##'
}

# owner/name when origin is on github.com, else empty (no compare URL then).
_adt_layer_github_repo() {  # <PROJECT_PATH>
  git -C "$1" remote get-url origin 2>/dev/null \
    | sed -nE 's#^(https://([^@/]*@)?github\.com/|git@github\.com:)([^/]+/[^/]+)$#\3#p' \
    | sed 's#\.git$##'
}

adt_layer_state() {  # <ADT_DIR> <PROJECT_PATH> [main]
  local adt="$1" p="$2" main manifest
  main="$(_adt_layer_main "$p" "${3:-}")"
  ADT_LAYER_MAIN="$main"
  ADT_LAYER_STATE="none"; ADT_LAYER_COMMITTED=""; ADT_LAYER_BUNDLE=""; ADT_LAYER_MANIFEST=""
  ADT_LAYER_SOURCE_REPO=""; ADT_LAYER_ORIGIN_REPO=""
  ADT_LAYER_HEAD="$(git -C "$adt" rev-parse HEAD 2>/dev/null || true)"
  if [[ "$adt" -ef "$p" ]]; then
    ADT_LAYER_STATE="self"; return 0
  fi

  git -C "$p" remote get-url origin >/dev/null 2>&1 || return 0
  # A bare fetch: `git fetch origin <branch>` does not move origin/<branch>
  # (multi-agent-git-workflow §B). Offline, the refs already here are used.
  git -C "$p" fetch -q origin 2>/dev/null \
    || _adt_layer_say "could not fetch origin in $p; comparing against the refs already fetched"
  manifest="$(git -C "$p" show "origin/$main:.claude/.adt-manifest.json" 2>/dev/null || true)"
  [[ -n "$manifest" ]] || return 0
  ADT_LAYER_MANIFEST="$manifest"
  { IFS= read -r ADT_LAYER_COMMITTED || true; IFS= read -r ADT_LAYER_BUNDLE || true
    IFS= read -r ADT_LAYER_SOURCE_REPO || true; } < <(
    printf '%s' "$manifest" | /usr/bin/python3 -c 'import json, sys
m = json.load(sys.stdin)
print(m.get("source_commit") or "")
print(m.get("bundle_version") or "")
print(m.get("source_repo") or "")' 2>/dev/null || true)
  ADT_LAYER_ORIGIN_REPO="$(_adt_layer_origin_id "$adt")"

  # A committed manifest with no readable commit cannot be compared. Refusing
  # would leave no remedy, so it goes to the reviewed branch like a divergence.
  if [[ ! "$ADT_LAYER_COMMITTED" =~ ^[0-9a-f]{40}$ ]]; then
    ADT_LAYER_STATE="diverged"; return 0
  fi
  if [[ "$ADT_LAYER_COMMITTED" == "$ADT_LAYER_HEAD" ]]; then
    ADT_LAYER_STATE="equal"; return 0
  fi
  # Fetch the ADT clone only when it lacks the committed commit: every other
  # comparison is answered by what the clone already has.
  if ! git -C "$adt" cat-file -e "$ADT_LAYER_COMMITTED^{commit}" 2>/dev/null \
     && git -C "$adt" remote get-url origin >/dev/null 2>&1; then
    git -C "$adt" fetch -q origin 2>/dev/null \
      || _adt_layer_say "could not fetch origin in the ADT clone $adt; comparing against what it already has"
  fi
  if ! git -C "$adt" cat-file -e "$ADT_LAYER_COMMITTED^{commit}" 2>/dev/null; then
    # The pinned commit is not in this clone even after the fetch above. Two
    # different situations reach here, and only the repo tells them apart:
    # the clone is behind a commit that was never pushed (refuse — it may be a
    # downgrade), or the layer came from a repository that does not share this
    # history at all, which is what ADT's own republication did to every
    # consumer (AO-2). A lineage change is permanent, so refusing leaves no
    # remedy; it goes to the reviewed branch like any other divergence.
    if [[ -n "$ADT_LAYER_SOURCE_REPO" && -n "$ADT_LAYER_ORIGIN_REPO" \
          && "$ADT_LAYER_SOURCE_REPO" != "$ADT_LAYER_ORIGIN_REPO" ]]; then
      ADT_LAYER_STATE="diverged"; return 0
    fi
    ADT_LAYER_STATE="older"   # cannot prove it is not behind
  elif git -C "$adt" merge-base --is-ancestor "$ADT_LAYER_COMMITTED" HEAD; then
    ADT_LAYER_STATE="newer"
  elif git -C "$adt" merge-base --is-ancestor HEAD "$ADT_LAYER_COMMITTED"; then
    ADT_LAYER_STATE="older"
  else
    ADT_LAYER_STATE="diverged"
  fi
  return 0
}

# Run a writer against a detached worktree of origin/<main>, then commit what it
# changed onto a new local <branch> that is never pushed. The upgrade branch
# (below) and `adt-install.sh --uninstall --everyone` (lib/uninstall.sh) both
# use it: each is a change to the committed layer that every machine gets, so
# it goes to the team as a branch instead of into someone's working tree.
#   _adt_layer_on_branch <PROJECT_PATH> <main> <branch> <ignore> <subject> <body> <writer…>
# The writer is called as `<writer…> <worktree>`. <ignore> is a path whose change
# alone does not count, or "". Sets ADT_LAYER_BRANCH to the branch, or to ""
# when the writer changed nothing; ADT_LAYER_BRANCH_EXISTED=true when the branch
# was already there and nothing was written. Returns 1 on failure.
_adt_layer_on_branch() {
  local p="$1" main="$2" branch="$3" ignore="$4" subject="$5" body="$6"; shift 6
  local tmp wt log rc=0
  ADT_LAYER_BRANCH="$branch"; ADT_LAYER_BRANCH_EXISTED=false
  if git -C "$p" show-ref --verify --quiet "refs/heads/$branch"; then
    ADT_LAYER_BRANCH_EXISTED=true
    _adt_layer_say "branch $branch already exists; left as it is"
    return 0
  fi
  tmp="$(mktemp -d)"; wt="$tmp/wt"; log="$tmp/writer.log"
  # Detached: the branch is created only once there is a commit to put on it,
  # so no failure below leaves a branch behind.
  if ! git -C "$p" worktree add -q --detach "$wt" "origin/$main" 2>"$log"; then
    _adt_layer_say "could not create a worktree for $branch:"; cat "$log" >&2
    rm -rf "$tmp"; ADT_LAYER_BRANCH=""; return 1
  fi
  local -a scope=(-- .)
  [[ -n "$ignore" ]] && scope+=(":(exclude)$ignore")
  if ! "$@" "$wt" >"$log" 2>&1; then
    _adt_layer_say "writing $branch failed:"; cat "$log" >&2
    rc=1
  elif [[ -z "$(git -C "$wt" status --porcelain "${scope[@]}")" ]]; then
    rc=2
  elif ! { git -C "$wt" add -A && git -C "$wt" commit -q -m "$subject" -m "$body" \
        && git -C "$p" branch "$branch" "$(git -C "$wt" rev-parse HEAD)"; } >"$log" 2>&1; then
    _adt_layer_say "committing $branch failed:"; cat "$log" >&2
    rc=1
  fi
  git -C "$p" worktree remove --force "$wt" 2>/dev/null || true
  rm -rf "$tmp"
  [[ $rc -eq 0 ]] && return 0
  ADT_LAYER_BRANCH=""
  [[ $rc -eq 2 ]] && return 0
  return 1
}

# The push command and the compare URL for a branch _adt_layer_on_branch wrote.
_adt_layer_say_push() {  # <PROJECT_PATH> <main> <branch>
  local repo
  printf '            git -C %s push -u origin %s\n' "$1" "$3" >&2
  repo="$(_adt_layer_github_repo "$1")"
  [[ -n "$repo" ]] && printf '            https://github.com/%s/compare/%s...%s\n' "$repo" "$2" "$3" >&2
  return 0
}

_adt_layer_upgrade_branch() {  # <ADT_DIR> <PROJECT_PATH>
  local adt="$1" p="$2" short="${ADT_LAYER_HEAD:0:12}"
  # The manifest's source_commit moves on every ADT commit, including ones that
  # change no shipped file. A branch for that alone would be noise.
  _adt_layer_on_branch "$p" "$ADT_LAYER_MAIN" "chore/adt-upgrade-$short" \
    ".claude/.adt-manifest.json" \
    "chore(adt): upgrade the ADT layer to $short" \
    "Written by adt-install/setup from an ADT clone newer than the layer committed on $ADT_LAYER_MAIN (${ADT_LAYER_COMMITTED:0:12}). Merging this upgrades the ADT playbooks and hooks for every machine that pulls $ADT_LAYER_MAIN (ADT-384)." \
    "$adt/lib/install-defaults.sh" "$adt" || return 1
  if [[ -z "$ADT_LAYER_BRANCH" ]]; then
    _adt_layer_say "this ADT clone changes no file in the committed layer; nothing to upgrade"
    return 0
  fi
  [[ "$ADT_LAYER_BRANCH_EXISTED" == true ]] && return 0
  _adt_layer_say "wrote the upgrade to branch $ADT_LAYER_BRANCH (not pushed). Your current branch and working tree are unchanged."
  _adt_layer_say "merging it upgrades the ADT playbooks and hooks for every machine that pulls $ADT_LAYER_MAIN:"
  _adt_layer_say_push "$p" "$ADT_LAYER_MAIN" "$ADT_LAYER_BRANCH"
}

# The refusal for an older clone, after adt_layer_state. adt-install.sh prints
# it before its first prompt; adt_layer_apply prints it for setup.sh and
# /adt-close.
adt_layer_refuse_older() {  # <ADT_DIR>
  local adt="$1"
  if git -C "$adt" cat-file -e "$ADT_LAYER_COMMITTED^{commit}" 2>/dev/null; then
    _adt_layer_say "ADT ${ADT_LAYER_BUNDLE:-?} (source ${ADT_LAYER_COMMITTED:0:7}) is committed on origin/$ADT_LAYER_MAIN, and this ADT clone (${ADT_LAYER_HEAD:0:7}) is older. Refusing to downgrade it."
  else
    _adt_layer_say "ADT ${ADT_LAYER_BUNDLE:-?} is committed on origin/$ADT_LAYER_MAIN from commit ${ADT_LAYER_COMMITTED:0:7}, which this ADT clone does not have, so it cannot tell whether it is older. Refusing."
    _adt_layer_say "if the pull below does not bring that commit, either the machine that installed it had ADT commits that were never pushed, or the layer came from a repository that does not share this history and this manifest predates source_repo (AO-2). In the second case, regenerate the layer from this clone: lib/install-defaults.sh <ADT_DIR> <PROJECT_PATH>, then commit it."
  fi
  _adt_layer_say "update the ADT clone, then run this again:"
  printf '            git -C %s pull\n' "$adt" >&2
}

adt_layer_apply() {  # <ADT_DIR> <PROJECT_PATH> [main]
  local adt="$1" p="$2"
  ADT_LAYER_BRANCH=""
  adt_layer_state "$adt" "$p" "${3:-}"
  case "$ADT_LAYER_STATE" in
    self|none)
      "$adt/lib/install-defaults.sh" "$adt" "$p" ;;
    equal)
      _adt_layer_say "ADT ${ADT_LAYER_BUNDLE:-?} (source ${ADT_LAYER_COMMITTED:0:7}) on origin/$ADT_LAYER_MAIN matches this ADT clone; nothing to install"
      if [[ "$(cat "$p/.claude/.adt-manifest.json" 2>/dev/null || true)" != "$ADT_LAYER_MANIFEST" ]]; then
        _adt_layer_say "this checkout does not have that layer yet. Pull $ADT_LAYER_MAIN to get it:"
        printf '            git -C %s pull\n' "$p" >&2
      fi ;;
    older)
      adt_layer_refuse_older "$adt"
      return 3 ;;
    newer|diverged)
      if [[ "$ADT_LAYER_STATE" == diverged && ! "$ADT_LAYER_COMMITTED" =~ ^[0-9a-f]{40}$ ]]; then
        _adt_layer_say "the ADT layer on origin/$ADT_LAYER_MAIN records no readable ADT commit, so it cannot be compared. Treating this clone (${ADT_LAYER_HEAD:0:7}) as newer."
      elif [[ "$ADT_LAYER_STATE" == diverged ]]; then
        _adt_layer_say "this ADT clone (${ADT_LAYER_HEAD:0:7}) and the committed layer (${ADT_LAYER_COMMITTED:0:7}) have diverged: the clone has commits ADT's origin does not. Treating the clone as newer."
      else
        _adt_layer_say "ADT ${ADT_LAYER_BUNDLE:-?} (source ${ADT_LAYER_COMMITTED:0:7}) is committed on origin/$ADT_LAYER_MAIN; this ADT clone (${ADT_LAYER_HEAD:0:7}) is newer."
      fi
      _adt_layer_upgrade_branch "$adt" "$p" ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -uo pipefail
  cmd="${1:-}"; shift || true
  case "$cmd" in
    state) adt_layer_state "$@"; echo "$ADT_LAYER_STATE" ;;
    apply) adt_layer_apply "$@" ;;
    *) echo "usage: lib/adt-layer.sh state|apply <ADT_DIR> <PROJECT_PATH> [main]" >&2; exit 2 ;;
  esac
fi
