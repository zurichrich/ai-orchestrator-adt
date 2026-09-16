#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
#
# Scan files for secret-shaped content before ADT makes a previously-gitignored
# path tracked (ADT-301 sub-step 2e).
#
# Why a BASELINE ships here rather than reading the project's config alone: at
# plan time this scan was specified against `security.secret_patterns`, and that
# key is commented out in projects/example.yaml, read by zero .py/.sh files, and
# set in neither real project config. A scan that reads only that key scans an
# empty pattern list and passes everything - a control that looks implemented and
# checks nothing. So ADT ships patterns that bite on a default install, and a
# project's `security.secret_patterns` EXTENDS them; it never supplies them.
#
# Usage: secret_scan <file>...        -> 0 clean, 1 match found (reported), 2 usage
set -uo pipefail

# Baseline: high-signal shapes only. A noisy scanner gets switched off, which is
# worse than a narrow one that is trusted.
_ADT_SECRET_PATTERNS=(
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'AKIA[0-9A-Z]{16}'
  'gh[pousr]_[A-Za-z0-9]{36,}'
  'xox[baprs]-[A-Za-z0-9]{10,}'
  'sk-[A-Za-z0-9]{20,}'
  'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'
  '://[^/@[:space:]]+:[^/@[:space:]]+@'
)
# Case-insensitive half, kept separate because ERE has no inline (?i).
_ADT_SECRET_PATTERNS_I=(
  '(secret|token|password|passwd|api[_-]?key)[[:space:]]*[:=][[:space:]]*.?[A-Za-z0-9/+_-]{16,}'
)

# Project additions: `security: { secret_patterns: [...] }` in .adt/config.yaml or
# the per-user project yaml. Additive by construction - the baseline array is
# never replaced.
_load_project_patterns() {
  # One awk pass, no `| head` - with `set -o pipefail` a SIGPIPE'd sed makes the
  # whole substitution look failed, which is what silently emptied this on the
  # first attempt. awk prints the first match and stops on its own.
  local cfg="${1:-}"
  [[ -n "$cfg" && -f "$cfg" ]] || return 0
  awk '
    /^[[:space:]]*secret_patterns:[[:space:]]*\[/ && !done {
      line = $0
      sub(/^[^[]*\[/, "", line); sub(/\][[:space:]]*$/, "", line)   # LAST bracket: a pattern may contain [0-9]
      n = split(line, parts, ",")
      for (i = 1; i <= n; i++) {
        p = parts[i]
        gsub(/^[[:space:]]*["'"'"']?|["'"'"']?[[:space:]]*$/, "", p)
        if (p != "") print p
      }
      done = 1
    }' "$cfg"
}

secret_scan() {
  local hits=0 f pat
  local -a extra=()
  while IFS= read -r pat; do [[ -n "$pat" ]] && extra+=("$pat"); done < <(
    _load_project_patterns "${ADT_PROJECT_CONFIG:-}" )

  for f in "$@"; do
    [[ -f "$f" ]] || continue
    for pat in "${_ADT_SECRET_PATTERNS[@]}" ${extra[@]+"${extra[@]}"}; do
      if grep -nE -- "$pat" "$f" >/dev/null 2>&1; then
        echo "  SECRET-SHAPED: $f matches /$pat/" >&2
        grep -nE -- "$pat" "$f" 2>/dev/null | head -2 | sed 's/^/      /' >&2
        hits=$((hits + 1))
      fi
    done
    for pat in "${_ADT_SECRET_PATTERNS_I[@]}"; do
      if grep -niE -- "$pat" "$f" >/dev/null 2>&1; then
        echo "  SECRET-SHAPED: $f matches /$pat/i" >&2
        grep -niE -- "$pat" "$f" 2>/dev/null | head -2 | sed 's/^/      /' >&2
        hits=$((hits + 1))
      fi
    done
  done
  [[ $hits -eq 0 ]] || return 1
  return 0
}

# Warn, and WAIT, the first time ADT re-scopes a previously-gitignored path into
# git. This fires on the first re-scope regardless of what the scan found: it
# announces that a tracking default changed, not that a secret was present. A
# non-interactive run (no TTY) refuses rather than assuming yes - silently
# starting to commit a class of file the user has never committed is exactly the
# surprise this exists to prevent.
# Usage: adt_confirm_rescope <path>...   -> 0 proceed, 1 declined
adt_confirm_rescope() {
  local stamp="${ADT_RESCOPE_STAMP:-.adt/state/rescope-acknowledged}"
  [[ -f "$stamp" ]] && return 0          # already acknowledged once; never re-ask
  echo >&2
  echo "  ADT is about to start TRACKING files that were previously gitignored:" >&2
  local p; for p in "$@"; do echo "      $p" >&2; done
  echo "  They will be committed and pushed from now on. Retros are postmortems," >&2
  echo "  so read them once before agreeing." >&2
  if [[ "${ADT_ASSUME_YES:-}" == "1" ]]; then
    mkdir -p "$(dirname "$stamp")" 2>/dev/null || true; : > "$stamp"; return 0
  fi
  if [[ ! -t 0 ]]; then
    echo "  Refusing: no terminal to confirm on. Re-run interactively, or set" >&2
    echo "  ADT_ASSUME_YES=1 if you have read them." >&2
    return 1
  fi
  local ans; read -r -p "  Track these files? [y/N] " ans
  case "$ans" in
    [yY]*) mkdir -p "$(dirname "$stamp")" 2>/dev/null || true; : > "$stamp"; return 0 ;;
    *)     echo "  Declined - leaving them untracked." >&2; return 1 ;;
  esac
}

# Direct invocation: scan the paths given.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  [[ $# -gt 0 ]] || { echo "usage: secret-scan.sh <file>..." >&2; exit 2; }
  secret_scan "$@"
fi
