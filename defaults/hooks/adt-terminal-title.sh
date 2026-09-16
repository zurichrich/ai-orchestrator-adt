#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT default. SessionStart / UserPromptSubmit / Stop hook: sets the terminal
# tab title to "<TICKET> · <latest command or comment>", so a row of Claude
# Code tabs can be told apart at a glance.
#
# Claude Code writes its own tab title, but /rename pins it, so you get either
# the ticket id or a topic that follows the work, not both. Its automatic topic
# is held in memory only, so a hook cannot read it and add a prefix. This hook
# writes the whole title instead. `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1`, set in
# the same settings block that wires the hook, stops Claude writing one.
# Without it Claude rewrites the title every time its spinner glyph changes and
# overwrites this hook within a second.
#
# VS Code and Cursor default `terminal.integrated.tabs.title` to "${process}",
# the binary name, which for Claude Code is a version number such as "2.1.259".
# Set it to "${sequence}" so the editor shows the title.
#
# It always exits 0 and never blocks a prompt or a stop.
set -uo pipefail

input="$(cat 2>/dev/null || true)"

jget() { printf '%s' "$input" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin).get('$1',''))" 2>/dev/null || true; }
cwd="$(jget cwd)"
prompt="$(jget prompt)"
session="$(jget session_id)"
if [ -z "$session" ]; then
  tp="$(jget transcript_path)"
  if [ -n "$tp" ]; then base="$(basename "$tp")"; session="${base%.jsonl}"; fi
fi
[ -n "$session" ] || session="unknown"

# ── the tty to write to ──────────────────────────────────────────────────────
# A hook has no controlling terminal (/dev/tty is "device not configured"), so
# the OSC sequence goes to the tty of the `claude` process. Walk up the parent
# chain until a process reports one. If none does, fall back to Claude's session
# registry (~/.claude/sessions/<pid>.json with a matching `sessionId`).
find_tty() {
  # Override for tests: set ADT_TITLE_TTY to a file to capture the title, or to
  # "none" for no tty. Tests must set it. Otherwise the walk below finds the tab
  # of the claude session running the tests and renames it.
  if [ -n "${ADT_TITLE_TTY:-}" ]; then
    [ "$ADT_TITLE_TTY" = "none" ] && return 1
    printf '%s' "$ADT_TITLE_TTY"; return 0
  fi
  local p="${PPID:-}" pp tt i f pid
  for i in 1 2 3 4 5 6; do
    [ -n "$p" ] || break
    tt="$(ps -o tty= -p "$p" 2>/dev/null | tr -d ' ' || true)"
    if [ -n "$tt" ] && [ "$tt" != "??" ] && [ -w "/dev/$tt" ]; then printf '/dev/%s' "$tt"; return 0; fi
    pp="$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ' || true)"
    [ -n "$pp" ] && [ "$pp" != "$p" ] || break
    p="$pp"
  done
  for f in "$HOME"/.claude/sessions/*.json; do
    [ -f "$f" ] || continue
    grep -q "\"sessionId\":\"$session\"" "$f" 2>/dev/null || continue
    pid="$(basename "$f" .json)"
    tt="$(ps -o tty= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
    if [ -n "$tt" ] && [ "$tt" != "??" ] && [ -w "/dev/$tt" ]; then printf '/dev/%s' "$tt"; return 0; fi
  done
  return 1
}
TTY="$(find_tty || true)"
[ -n "$TTY" ] || exit 0

# ── project root (canonical checkout, resolved as adt-usage-log.sh does) ──────────
root="$(git -C "${cwd:-.}" rev-parse --show-toplevel 2>/dev/null || echo "${cwd:-.}")"
common_dir="$(git -C "${cwd:-.}" rev-parse --git-common-dir 2>/dev/null || true)"
if [ -n "$common_dir" ]; then
  case "$common_dir" in /*) ;; *) common_dir="$(git -C "${cwd:-.}" rev-parse --show-toplevel 2>/dev/null)/$common_dir" ;; esac
  canonical="$(cd "$common_dir/.." 2>/dev/null && pwd || true)"
  [ -n "$canonical" ] && [ -d "$canonical/.adt" ] && root="$canonical"
fi

# The state kept between turns is disposable, so it lives in TMPDIR rather than
# the project. A non-ADT repo gets a title and no new directories.
cache_dir="${TMPDIR:-/tmp}/adt-terminal-title"
cache="$cache_dir/$(printf '%s' "$session" | tr -c 'A-Za-z0-9._-' '_')"
prev_tix=""; prev_topic=""
if [ -f "$cache" ]; then
  prev_tix="$(sed -n '1p' "$cache" 2>/dev/null || true)"
  prev_topic="$(sed -n '2p' "$cache" 2>/dev/null || true)"
fi

# ── ticket ───────────────────────────────────────────────────────────────────
# This hook only reads the ticket marker; adt-usage-log.sh and adt-mark-tix.sh
# write it.
#
# The prompt is checked before the marker. Hooks on the same event run in no
# fixed order, so on the turn that first names a ticket adt-usage-log.sh may not
# have written the marker yet. The PREFIX-NNN match and zero-stripping are the
# same as adt-usage-log.sh's, so both give the same id.
ID_PREFIX="${ADT_ID_PREFIX:-}"
if [ -z "$ID_PREFIX" ] && [ -f "$root/.adt/config.yaml" ]; then
  ID_PREFIX="$(grep -E '^id_prefix:' "$root/.adt/config.yaml" 2>/dev/null | head -1 | sed -E 's/^id_prefix:[[:space:]]*//; s/[[:space:]]*$//' || true)"
fi
[ -z "$ID_PREFIX" ] && ID_PREFIX="TIX"

marker="$root/.adt/state/current-tix.d/$session"
[ -e "$marker" ] || [ ! -e "$root/.adt/state/current-tix.d/$session" ] \
  || marker="$root/.adt/state/current-tix.d/$session"
tix=""
[ -n "$prompt" ] && tix="$(printf '%s' "$prompt" | grep -oiE "${ID_PREFIX}-[0-9]+" | head -1 | tr '[:lower:]' '[:upper:]' || true)"
[ -z "$tix" ] && [ -f "$marker" ] && tix="$(head -1 "$marker" 2>/dev/null | tr -d '[:space:]' || true)"
[ -z "$tix" ] && tix="$prev_tix"
[ -n "$tix" ] && tix="$(printf '%s' "$tix" | sed -E 's/^([A-Za-z]+)-0*([0-9]+)$/\1-\2/')"

# ── topic ────────────────────────────────────────────────────────────────────
# The topic is the latest prompt: a slash command with its arguments (e.g.
# "/adt-build 170"), or the comment with filler words and ticket ids removed.
# A bare acknowledgement ("yes", "go on") gives nothing, and the previous topic
# is kept.
topic=""
if [ -n "$prompt" ]; then
  topic="$(printf '%s' "$prompt" | /usr/bin/python3 -c '
import re, sys
FILLER = {"i","id","ive","im","we","weve","you","lets","let","please","can","could","would",
          "should","now","ok","okay","so","and","also","just","like","want","need","to","make",
          "the","a","an","pls","hey","hi","do","does","is","it","this","that","my","our"}
ACK = {"yes","yep","yeah","yup","ok","okay","k","sure","go","on","ahead","proceed","continue",
       "do","it","please","thanks","thank","you","carry","next","right","fine","cool","good",
       "great","no","nope"}
p = sys.stdin.read()
cmd = ""
m = re.match(r"\s*/([a-z0-9][a-z0-9:_-]*)(?=\s|$)", p)  # a leading slash command names the turn
if m:
    cmd = "/" + m.group(1)
    p = p[m.end():]
p = re.sub(r"\b[A-Za-z]{2,6}-\d+\b", " ", p)      # ticket ids (already in the prefix)
p = p.replace(chr(8217), "").replace(chr(39), "")   # apostrophes: chr() keeps the shell quoting intact
p = re.sub(r"[^0-9A-Za-z ._-]", " ", p)
w = p.split()
while w and w[0].lower() in FILLER:
    w.pop(0)
if w and all(t.lower() in ACK for t in w):          # "yes" / "go on" says nothing new
    w = []
out = cmd
for t in w:
    if len(out) + len(t) + 1 > 46:
        break
    out = (out + " " + t).strip()
print(out)
' 2>/dev/null || true)"
fi
[ -z "$topic" ] && topic="$prev_topic"
[ -z "$topic" ] && topic="$(basename "$root")"

mkdir -p "$cache_dir" 2>/dev/null && printf '%s\n%s\n' "$tix" "$topic" > "$cache" 2>/dev/null || true

# ── write ────────────────────────────────────────────────────────────────────
if [ -n "$tix" ]; then title="✳ $tix · $topic"; else title="✳ $topic"; fi
printf '\033]0;%s\007' "$title" > "$TTY" 2>/dev/null || true
exit 0
