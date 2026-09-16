#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT default. Stop hook: checks that an /adt-close actually got the ticket
# into done/.
#
# adt-done-guard.sh only checks the move into done/, so it never runs when a
# close skips the move and sets the Issue state by hand instead. This hook
# checks the end state. On Stop, if `.adt/state/current-cmd.d/<session>` reads
# `adt-close`, it takes the ticket id from `current-tix.d/<session>` and looks
# for a file under `<cache>/*/done/` whose frontmatter has that `id:` with
# `stage: done` and `state: closed`. If there is none it exits 2, which stops
# Claude from ending the turn and hands stderr back as the reason.
#
# Exit 2 on Stop keeps the conversation going, with stderr as the message. The
# Stop stdin JSON has session_id, transcript_path, cwd, hook_event_name and
# stop_hook_active.
#
# It is limited so it can never trap a session:
#   1. `stop_hook_active: true` on stdin means another hook already continued
#      this Stop event, so it exits 0.
#   2. It blocks once per session. The `current-cmd` marker stays set after the
#      close ends (see adt-mark-tix.sh), so without this a close the human
#      abandoned would be blocked at every Stop. The first block writes
#      `.adt/state/close-complete.d/<session>`; after that it only warns.
#   3. It passes (exit 0) on anything it cannot resolve: no session id, no cmd
#      marker, cmd is not adt-close, no tix marker, no cache root, no parseable
#      frontmatter. It blocks only when the check ran and the file is missing,
#      the same approach as adt-done-guard.sh.
set -uo pipefail

input="$(cat 2>/dev/null || true)"
[ -z "$input" ] && exit 0

# ── stdin: session id + the loop guard ──────────────────────────────────────
read_json() {
  printf '%s' "$input" | /usr/bin/python3 -c \
    'import sys,json
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
print(d.get(sys.argv[1],"") or "")' "$1" 2>/dev/null || true
}

active="$(read_json stop_hook_active)"
case "$active" in True|true|1) exit 0 ;; esac

session="$(read_json session_id)"
[ -z "$session" ] && session="${CLAUDE_CODE_SESSION_ID:-}"
if [ -z "$session" ]; then
  transcript="$(read_json transcript_path)"
  [ -n "$transcript" ] && { base="$(basename "$transcript")"; session="${base%.jsonl}"; }
fi
[ -z "$session" ] && exit 0            # cannot key anything → fail open

cwd="$(read_json cwd)"
[ -z "$cwd" ] && cwd="$PWD"

# ── resolve the canonical checkout the same way adt-mark-tix.sh does, so the
# writer and this reader use the same .adt/ ─────────────────────────────────
root="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null || echo "$cwd")"
common_dir="$(git -C "$cwd" rev-parse --git-common-dir 2>/dev/null || true)"
if [ -n "$common_dir" ]; then
  case "$common_dir" in /*) ;; *) common_dir="$root/$common_dir" ;; esac
  canonical="$(cd "$common_dir/.." 2>/dev/null && pwd || true)"
  [ -n "$canonical" ] && [ -d "$canonical/.adt" ] && root="$canonical"
fi
state="$root/.adt/state"
[ -d "$state" ] || exit 0              # not an ADT project → fail open

# ── gate: is this session running /adt-close? ───────────────────────────────
cmd_marker="$state/current-cmd.d/$session"
[ -f "$cmd_marker" ] || exit 0
cmd="$(tr -d ' \n\t' < "$cmd_marker" 2>/dev/null || true)"
[ "$cmd" = "adt-close" ] || exit 0

tix_marker="$state/current-tix.d/$session"
[ -f "$tix_marker" ] || exit 0         # unbound close → cannot check → fail open
tix="$(tr -d ' \n\t' < "$tix_marker" 2>/dev/null || true)"
[ -z "$tix" ] && exit 0

# ── the check, in python: find the cache root and scan */done/ ─────────────
# The cache root is `cache_dir:` from <root>/.adt/config.yaml, else
# ~/.adt/<project>/cache (as in adt-token-log.sh). The layout is
# <type>/<stage>/<slug>.md for every ticket type, so the glob is */done/*.md.
verdict="$(ROOT="$root" TIX="$tix" /usr/bin/python3 - <<'PY' 2>/dev/null || true
import os, re, glob, sys

root = os.environ["ROOT"]
tix = os.environ["TIX"].strip().upper()

cache = None
cfg = os.path.join(root, ".adt", "config.yaml")
project = os.path.basename(os.path.realpath(root))
try:
    for line in open(cfg, encoding="utf-8"):
        s = line.strip()
        if s.startswith("cache_dir:"):
            cache = s.split(":", 1)[1].strip()
except Exception:
    pass
if not cache:
    cache = os.path.join("~", ".adt", project, "cache")
cache = os.path.abspath(os.path.expanduser(cache))
if not os.path.isdir(cache):
    print("OPEN\t")                    # no cache → cannot check → fail open
    sys.exit(0)

def frontmatter(path):
    try:
        text = open(path, encoding="utf-8").read()
    except Exception:
        return None
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) + "\n") if m else None

def field(fm, key):
    m = re.search(r"^%s:\s*(.*)$" % re.escape(key), fm, re.M)
    return m.group(1).strip() if m else ""

parsed_any = False
for f in glob.glob(os.path.join(cache, "*", "done", "*.md")):
    fm = frontmatter(f)
    if fm is None:
        continue
    parsed_any = True
    if field(fm, "id").upper() != tix:
        continue
    stage, state_ = field(fm, "stage"), field(fm, "state")
    if stage == "done" and state_ == "closed":
        print("OK\t")
        sys.exit(0)
    print("BLOCK\t%s carries stage: %s / state: %s — done/ requires "
          "stage: done and state: closed." % (f, stage or "(unset)", state_ or "(unset)"))
    sys.exit(0)

if not parsed_any and glob.glob(os.path.join(cache, "*", "done", "*.md")):
    print("OPEN\t")                    # files present, none parseable → fail open
    sys.exit(0)

print("BLOCK\tno file under %s/*/done/ carries id: %s" % (cache, tix))
PY
)"

decision="$(printf '%s' "$verdict" | head -1 | cut -f1)"
detail="$(printf '%s' "$verdict" | head -1 | cut -f2-)"
[ "$decision" = "BLOCK" ] || exit 0    # OK, OPEN, or no verdict at all → pass

# ── one block per session ───────────────────────────────────────────────────
shot_dir="$state/close-complete.d"
shot="$shot_dir/$session"
if [ -f "$shot" ]; then
  printf '%s' "⚠ adt-close-complete: $tix still has no done/ file ($detail). Already flagged once this session; not blocking again." \
    | /usr/bin/python3 -c 'import sys,json; print(json.dumps({"systemMessage": sys.stdin.read()}))' 2>/dev/null || true
  exit 0
fi
mkdir -p "$shot_dir" 2>/dev/null || true
printf '%s\n' "$tix" > "$shot" 2>/dev/null || true

log="$state/close-complete.log"
printf '%s  BLOCK  %s  %s\n' "$(date -u +%FT%TZ)" "$tix" "$detail" >> "$log" 2>/dev/null || true

cat >&2 <<EOF
adt-close-complete: $tix is not in done/ — this close is not finished.

  $detail

/adt-close is not complete until the CACHE says so; the cache is authoritative
and the Issue is downstream of it. Two steps of commands/close.md remain:

  step 4 — set state_reason and append the ticket's commit SHAs, in place.
           If the ticket has NO cache file (it was filed outside /adt-brief),
           create it in done/ now with full frontmatter, written atomically.
           Do not act on the Issue instead.
  step 5 — plain \`mv\` into <type>/done/ (never \`git mv\` — the cache sits
           outside every git checkout), THEN set stage: done and state: closed.
           Set before the move, a render reverts them and the next sync reopens
           the Issue. No \`gh issue close\`: \`adt watch\` reconciles
           the state and the stage label; read the Issue back until it prints
           closed stage:done.

Setting the Issue state or the stage:done label by hand leaves the cache empty,
so the card never reaches the board and the next reconciliation reopens it.

This fires once per session. Fix it, or say why it should stand.
EOF
exit 2
