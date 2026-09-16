#!/bin/bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# adt-token-total.sh prints the total tokens for one ticket across every
# machine: this machine's ledger sum plus the register comments other machines
# posted on the ticket's GitHub Issue.
#
# The local ledger is gitignored and per machine. Each machine keeps one
# register comment on the Issue (`<!-- adt:tokens machine=<id> total=<N> -->`),
# written by adt watch through adt_sync.checkpoint_tokens. The totals combine as:
#
#   total = max(own ledger sum, own register) + sum of other machines' registers
#
# max() stops this machine's spend being counted twice. The register is
# normally at or below the ledger, and above it after a ledger prune. Other
# machines' registers are spend this ledger never saw, so they are added.
#
# Usage:  adt-token-total.sh <TIX-NNN> [cwd] [--cost]
#   --cost  print "<micros>\t<tier>" instead of tokens, using the adt:cost
#           registers.
#
# /adt-close uses this to stamp `tokens:`. It calls adt-token-sum.sh for the
# local part. Prints the total, or `unattributed` when neither the ledger nor
# any register has data. It makes one paginated REST call for the comments
# (`gh api`, never the GraphQL porcelain). If gh or the config fails, it prints
# the local sum and a note on stderr.
# Exits 0 on success and 2 when the ticket id is missing.
set -euo pipefail

tix="${1:-}"
if [ -z "$tix" ]; then
  echo "usage: adt-token-total.sh <TIX-NNN> [cwd]" >&2
  exit 2
fi
# --cost returns the cross-machine cost (micro-dollars and tier) instead of
# tokens, combining the `adt:cost` registers the same way as `adt:tokens`.
want_cost=false
cwd="$PWD"
for a in "${@:2}"; do
  case "$a" in
    --cost) want_cost=true ;;
    *) cwd="$a" ;;
  esac
done
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Local part first. adt-token-sum.sh finds the ledger and the canonical root.
if [ "$want_cost" = true ]; then
  local_pair="$(bash "$here/adt-token-sum.sh" "$tix" "$cwd" --cost)"
  local_sum="$(printf '%s' "$local_pair" | cut -f1)"
  local_tier="$(printf '%s' "$local_pair" | cut -f2)"
  [ "$local_sum" = "-" ] && local_sum="unattributed"
else
  local_sum="$(bash "$here/adt-token-sum.sh" "$tix" "$cwd")"
  local_tier=""
fi

degrade() { # $1 = reason
  echo "adt-token-total: $1 — printing LOCAL sum only" >&2
  printf '%s\n' "$local_sum"
  exit 0
}

# Resolve the project root to the canonical checkout, the same way
# adt-token-sum.sh and adt-token-log.sh do.
root="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null || echo "$cwd")"
common_dir="$(git -C "$cwd" rev-parse --git-common-dir 2>/dev/null || true)"
if [ -n "$common_dir" ]; then
  case "$common_dir" in /*) ;; *) common_dir="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)/$common_dir" ;; esac
  canonical="$(cd "$common_dir/.." 2>/dev/null && pwd || true)"
  [ -n "$canonical" ] && [ -d "$canonical/.adt" ] && root="$canonical"
fi

# Find repo: in the config, then the ticket's issue_number by globbing the
# cache for its id:, as the building-lane fallback in adt-token-log.sh does.
# Keep apostrophes out of the Python below: bash 3.2 counts quotes in a heredoc
# inside $(...).
meta="$(/usr/bin/python3 - "$root" "$tix" <<'PY' 2>/dev/null || true
import glob, os, re, sys

root, want_raw = sys.argv[1], sys.argv[2]

def canon(tix):
    # Same normalisation as build_kanban.canon_tix.
    s = tix.strip().upper()
    m = re.fullmatch(r"([A-Z]+)-0*([0-9]+)", s)
    return f"{m.group(1)}-{m.group(2)}" if m else s

want = canon(want_raw)
repo = cache = None
project = os.path.basename(os.path.realpath(root))
try:
    for line in open(os.path.join(root, ".adt", "config.yaml")):
        s = line.strip()
        if s.startswith("repo:"):
            repo = s.split(":", 1)[1].strip()
        elif s.startswith("cache_dir:"):
            cache = s.split(":", 1)[1].strip()
        elif s.startswith("project:"):
            project = s.split(":", 1)[1].strip() or project
except Exception:
    pass
if not cache:
    cache = os.path.join("~", ".adt", project, "cache")
cache = os.path.abspath(os.path.expanduser(cache))

number = None
for f in glob.glob(os.path.join(cache, "*", "*", "*.md")):
    try:
        head = open(f).read(4096)
    except Exception:
        continue
    m = re.search(r"(?m)^id:\s*([A-Za-z]+-[0-9]+)\s*$", head)
    if not m or canon(m.group(1)) != want:
        continue
    n = re.search(r"(?m)^issue_number:\s*([0-9]+)\s*$", head)
    if n:
        number = n.group(1)
        break
if repo and number:
    print(f"{repo}\t{number}")
PY
)"
[ -z "$meta" ] && degrade "no repo/issue_number resolved from cache"
repo="$(printf '%s' "$meta" | cut -f1)"
number="$(printf '%s' "$meta" | cut -f2)"

# REST comments, paginated. --slurp (gh 2.40+) wraps the pages in one JSON
# array of arrays so the output parses as a single document.
comments_json="$(gh api --paginate --slurp "repos/$repo/issues/$number/comments" 2>/dev/null)" \
  || degrade "gh unavailable or issue fetch failed"

# Combine the registers from the comments with the local sum. The JSON is
# passed in the environment. It is not pasted into the script, because a
# comment body may contain quotes or backslashes. It is not passed on stdin,
# because with `python3 -` stdin is the script.
#
# This machine's id is read from .adt/state/install-id in the canonical
# checkout, the same file build_kanban.machine_id() reads, so the two always
# agree. $root is the canonical checkout, which matters because .adt/state/ is
# gitignored and absent from linked worktrees.
own_id_file="$root/.adt/state/install-id"

COMMENTS_JSON="$comments_json" WANT_COST="$want_cost" LOCAL_TIER="$local_tier" \
OWN_ID_FILE="$own_id_file" \
  /usr/bin/python3 - "$local_sum" <<'PY' \
  || degrade "combining step failed"
import json, os, re, sys

local_raw = sys.argv[1]
local = int(local_raw) if local_raw.isdigit() else None
# Read the same file build_kanban.machine_id() reads. On any failure use an
# empty id, which matches no register, rather than a guessed id that could
# match another machine's register.
try:
    own_machine = open(os.environ.get("OWN_ID_FILE", "")).read().strip()
except OSError:
    own_machine = ""

try:
    pages = json.loads(os.environ.get("COMMENTS_JSON", ""))
    # --slurp emits [[page1],[page2],…]; flatten to one comment list.
    comments = [c for page in pages for c in page]
except Exception:
    comments = []

want_cost = os.environ.get("WANT_COST") == "true"
# Cost has its own marker rather than an extra field on adt:tokens. The tokens
# pattern expects `-->` right after total=(\d+), so an extra field would hide
# the register from machines running the older parser.
if want_cost:
    marker = re.compile(r"<!-- adt:cost machine=(\S+) micros=(\d+)")
else:
    marker = re.compile(r"<!-- adt:tokens machine=(\S+) total=(\d+) -->")
regs = {}
for c in comments:
    for m, t in marker.findall(c.get("body") or ""):
        n = int(t)
        if n > regs.get(m, -1):
            regs[m] = n          # a repeated machine keeps its highest value

if local is None and not regs:
    print("unattributed")        # no data anywhere: print the sentinel, not 0
    sys.exit(0)

own_reg = regs.pop(own_machine, 0)
total = max(local or 0, own_reg) + sum(regs.values())
if want_cost:
    # The tier is the local tier, or "measured" when there is none.
    tier = os.environ.get("LOCAL_TIER") or "measured"
    print(f"{total}\t{tier}")
else:
    print(total)
PY
