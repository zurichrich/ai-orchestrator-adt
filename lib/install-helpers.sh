#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ai-orchestrator-adt — lib/install-helpers.sh
#
# Pure helpers shared by adt-install.sh and lib/github-bootstrap.sh (ADT-135).
# Function definitions only — no top-level side effects — so a test can source
# this file and call the functions directly.
#
# Why a separate library rather than inline in adt-install.sh: extracting the
# decisions makes them gradeable as pure functions, and lets the scope check be
# fixed ONCE for the two places that carry it. (The original extraction was also
# forced by ADT-099, which aborted setup.sh on a fresh $HOME and so made a
# whole-installer test red for a reason that was not ADT-135's. That abort is
# fixed and tests/test_install_interview.sh now drives the real installer end to
# end, so this library stands on the two reasons above.)

# --- The interview prompt --------------------------------------------------
# ADT-135 defect 1: the old ask() was a bare `read -r -p` that ignored read's
# exit status. `read` returns 0 for a blank line that was actually supplied (a
# real "take the default") and non-zero ONLY at EOF — i.e. when the prompt was
# never answered at all. Branching on the status is what separates "the
# operator pressed enter" from "nothing was ever on stdin"; a `[[ -t 0 ]]` test
# cannot tell those apart and would also reject the legitimate scripted-answers
# path (tests/test_install_merge.sh pipes blank lines).
#
# Signals a defaulted prompt by EXIT CODE 2, not by setting a variable: the
# installer calls this through `$(...)`, and a variable set in that subshell
# never reaches the parent — the banner would have been unreachable. An exit
# code survives command substitution, so the caller can set the flag itself.
#   0 = answered (or a default the operator supplied by pressing enter)
#   1 = refuse: EOF with no --yes, i.e. the prompt was never answered
#   2 = defaulted at EOF under --yes; the caller labels the run unanswered
ADT_ASK_DEFAULTED="${ADT_ASK_DEFAULTED:-false}"
# Every call site wraps this in `$(...)`, and the installer's own ask() wrapper
# is wrapped again — so a variable set here is two subshells deep and reaches
# the parent from neither. When ADT_ASK_MARKER names a path, a defaulted prompt
# stamps it; the parent tests the FILE. A file crosses a subshell; a variable
# does not. (The exit code below serves a direct caller; the marker serves the
# installer.)
ADT_ASK_MARKER="${ADT_ASK_MARKER:-}"

# ADT-203 run 3: `read` with no timeout hangs forever when stdin is NOT a
# terminal but stays OPEN with nothing on it — a CI runner, an agent harness,
# `ssh` without -n, a backgrounded job with an inherited pipe. EOF never
# arrives, so the branch below is never reached and `--yes` never gets to do
# its job. `adt-install.sh --yes` blocked indefinitely and had to be killed;
# the identical command with `</dev/null` exited 0 in 26s.
#
# The fix bounds the wait ONLY when stdin is not a tty. `[[ -t 0 ]]` is still
# not what decides whether to default — read's exit status is, exactly as
# ADT-135 requires — it only decides whether waiting forever is sane. A
# terminal keeps the unbounded wait, because a human is allowed to think; a
# pipe that has answers (tests/test_install_merge.sh pipes blank lines)
# delivers them immediately and never reaches the timeout.
ADT_ASK_TIMEOUT="${ADT_ASK_TIMEOUT:-10}"

adt_ask() {  # adt_ask <prompt> <default> [assume_yes] ; echoes the answer
  local p="$1" d="${2:-}" assume_yes="${3:-false}" a rc
  local -a tmo=(); [[ -t 0 ]] || tmo=(-t "$ADT_ASK_TIMEOUT")
  local t0=$SECONDS
  # `${tmo[@]+"${tmo[@]}"}` and not `"${tmo[@]}"`: bash 3.2 — the macOS system
  # bash, and the one `#!/usr/bin/env bash` finds on a stock Mac — treats the
  # expansion of an EMPTY array as an unbound variable under `set -u`, which
  # adt-install.sh sets. tmo is empty on exactly one path: stdin IS a terminal
  # (line above), i.e. every human running the installer interactively. The
  # `+` form expands to nothing when the array is empty and to the quoted
  # elements otherwise, on 3.2 and on 5.x alike (ADT-203 follow-up).
  if [[ -n "$d" ]]; then read -r ${tmo[@]+"${tmo[@]}"} -p "$p [$d]: " a; rc=$?
  else read -r ${tmo[@]+"${tmo[@]}"} -p "$p: " a; rc=$?; fi
  if [[ $rc -ne 0 ]]; then
    # Never answered. bash's documented "greater than 128 on timeout" is NOT
    # portable — bash 3.2 (the macOS system bash) returns 1 for both the
    # timeout and EOF — so the elapsed time is what separates them. Keep
    # "stdin closed" for a real EOF: it is the string the walkthrough quotes.
    local why="stdin closed"
    [[ ${#tmo[@]} -gt 0 && $(( SECONDS - t0 )) -ge $ADT_ASK_TIMEOUT ]] \
      && why="no answer after ${ADT_ASK_TIMEOUT}s; stdin is not a terminal"
    if [[ "$assume_yes" != true ]]; then
      printf '\033[1;31m%s\033[0m\n' "No answer for '$p' ($why)." >&2
      echo "  adt-install.sh interviews you; it will not guess your answers." >&2
      echo "  Run it from a terminal, or pass --yes to accept every default." >&2
      return 1
    fi
    ADT_ASK_DEFAULTED=true
    [[ -n "$ADT_ASK_MARKER" ]] && printf '1' > "$ADT_ASK_MARKER"
    echo "$d"
    return 2
  fi
  echo "${a:-$d}"
}

# --- Ticket-id prefix from evidence already on disk ------------------------
# ADT-135 defect 2: the prefix defaulted to the TIX constant and read a better
# value only from an existing local config — which by definition does not exist
# on a new machine, so an adopted backlog got stamped with the wrong prefix
# durably (adt_sync sets `id` only when absent).
#
# The repo's own history states the prefix for free: conventional-commit
# subjects of the form `<type>(<PREFIX>-N): …`. Echoes the most frequent prefix,
# or nothing when the history carries none (caller keeps its own default).
#
# The board title was considered and rejected as a source: it is written as
# "<name> backlog" at every site that writes it, so it carries no prefix at all.
# Issue close-stamp comments do carry one, but reading them needs an
# authenticated API round-trip at a point in the install before any config
# exists — to learn what git log already states locally.
adt_derive_id_prefix() {  # adt_derive_id_prefix <repo_dir> ; echoes PREFIX or ""
  local dir="${1:?usage: adt_derive_id_prefix <repo_dir>}"
  git -C "$dir" log --format='%s' -400 2>/dev/null \
    | sed -nE 's/^[a-z]+\(([A-Z][A-Z0-9]{1,9})-[0-9]+\).*/\1/p' \
    | sort | uniq -c | sort -rn | head -1 | awk '{print $2}'
}

# Criterion 1's stop decision. A derived prefix that disagrees with the chosen
# one halts the install rather than silently stamping a backlog — unless the
# operator passed --prefix, which is both the value and the stated override, so
# a project that legitimately renames its prefix can still install.
adt_prefix_conflict() {  # <derived> <chosen> <override_given> ; echoes ok|stop
  local derived="${1:-}" chosen="${2:-}" override="${3:-false}"
  if [[ -z "$derived" || "$derived" == "$chosen" || "$override" == true ]]; then
    echo ok
  else
    echo stop
  fi
}

# --- gh token scopes -------------------------------------------------------
# ADT-135 defect 7: the check was `gh auth status | grep "Token scopes" |
# grep -q "project"` — a SUBSTRING match that `read:project` satisfies. A
# read-only token passed the write-scope gate, the install reported success, and
# every `gh project item-add` then failed: a ticket reaching Issues but never
# the board is invisible on the kanban. Split the list and match exactly.
#
# Takes the scope LINE as an argument rather than calling gh itself, so a test
# can feed it a read:project-only string without a token (criterion 9).
adt_scopes_have_project() {  # <token-scopes-line> ; 0 if the write scope is held
  local line="${1:-}" s
  line="${line#*Token scopes:}"
  local IFS=,
  for s in $line; do
    s="${s//\'/}"; s="${s//\"/}"; s="${s// /}"
    [[ "$s" == "project" ]] && return 0
  done
  return 1
}

# The installer is already an interactive session that stops to interview the
# operator, so a missing scope is something to OBTAIN at the point of need, not
# a precondition to report and exit on. Isolated from the I/O so all four
# combinations are gradeable (criteria 8 and 10); the caller drives the actual
# `gh auth refresh`, which is the one part with no hermetic runner.
adt_scope_action() {  # <have_scope> <interactive> ; echoes ok|refresh|abort
  local have="${1:-false}" interactive="${2:-false}"
  if [[ "$have" == true ]]; then echo ok
  elif [[ "$interactive" == true ]]; then echo refresh
  else echo abort
  fi
}

# --- Branch protection (ADT-165) -------------------------------------------
# ADT's multi-agent-git-workflow rule §B states that `main` is server-protected
# and supplies the exact payload — and until this ticket nothing ever ran it, so
# every project installed with an unprotected main and a rule telling its agents
# main was protected. These two helpers are the DECISIONS; the I/O lives in
# lib/github-bootstrap.sh, the same split as adt_scopes_have_project /
# adt_scope_action above. That split is what lets a test observe the classifier
# distinguishing protected from unprotected with no token and no network — the
# ticket's own criterion, written because "the check exists" is not evidence
# that it works.

# The two response shapes, MEASURED against a real repo (2026-09-04), not
# assumed:
#   protected    -> exit 0, stdout = the protection JSON
#   unprotected  -> exit 1, stdout = {"message":"Branch not protected",…,"404"}
# Anything else — 403 (token without admin), a missing repo/branch, no network —
# is `unknown`, NOT `unprotected`. Matching the message rather than the bare 404
# is what keeps "no such branch" out of the unprotected bucket, where it would
# have provoked an offer to protect a branch that does not exist.
adt_protection_state() {  # <gh-exit-code> <gh-stdout> ; echoes protected|unprotected|unknown
  local rc="${1:-1}" body="${2:-}"
  if [[ "$rc" == 0 ]]; then echo protected; return 0; fi
  case "$body" in
    *"Branch not protected"*) echo unprotected ;;
    *)                        echo unknown ;;
  esac
}

# What to DO about that state. Kept separate from the state read so every row of
# the matrix is gradeable without a repo.
#   noop          — already protected: report it, never write. A PUT REPLACES the
#                   whole ruleset, so re-running setup against a repo with
#                   STRONGER protection would silently weaken it to our defaults.
#   prompt        — unprotected, not previously declined, and a human is there.
#   skip-declined — the answer is on record; do not ask again.
#   report-only   — unprotected but nobody is there to ask, or the state could
#                   not be read. Say so; change nothing. Never apply silently.
adt_protection_action() {  # <state> <declined> <interactive> ; echoes the action
  local state="${1:-unknown}" declined="${2:-false}" interactive="${3:-false}"
  case "$state" in
    protected) echo noop ;;
    unprotected)
      if [[ "$declined" == true ]]; then echo skip-declined
      elif [[ "$interactive" == true ]]; then echo prompt
      else echo report-only; fi ;;
    *) echo report-only ;;
  esac
}
