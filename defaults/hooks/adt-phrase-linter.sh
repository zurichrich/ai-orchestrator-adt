#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT default. Stop hook: checks the last assistant turn against
# .claude/rules/working-style.md and warns about
#   (a) banned recovery-narration phrases;
#   (b) a done-claim about a rendered artifact ("on the board", "the link
#       works") in a turn that never opened a .adt/ HTML file;
#   (c) a claim that a test or command passed when no matching command ran in
#       the turn;
#   (d) a ticket log entry written this turn that is longer than the budget;
#   (e) a counted or completeness claim with no counting command behind it
#       (AO-013 gap 3 — working-style #13).
#
# A Stop hook gets metadata, not the message text, so it reads transcript_path
# from stdin and parses the current turn's text and tool calls itself. It only
# warns, through a systemMessage, and always exits 0.
set -euo pipefail

input="$(cat)"
transcript="$(printf '%s' "$input" | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("transcript_path",""))' 2>/dev/null || true)"
[ -z "$transcript" ] && exit 0
[ -f "$transcript" ] || exit 0

/usr/bin/python3 - "$transcript" <<'PY'
import json, sys, re

path = sys.argv[1]
# Banned phrases from working-style.md (case-insensitive, word-ish boundaries).
BANNED = [
    r"now i see the problem clearly",
    r"well that changes everything",
    r"the honest situation is",
    r"\bhonestly\b",
    r"be straight with you",
    r"to be honest with you",
    r"let me be honest",
]

# Walk the transcript once, tracking the current turn (everything after the last
# user message): the last assistant text and the tool_use records since then.
last_text = ""
turn_tool_inputs = []   # list of stringified tool inputs in the current turn
turn_bash_cmds = []     # Bash commands run in the current turn
turn_written = []       # (path, text) written by Write/Edit this turn
try:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            msg = rec.get("message", rec)
            role = msg.get("role")
            if role == "user":
                # A new human turn starts, so reset the per-turn lists. Tool
                # results are also role=user, so they do not reset anything.
                content = msg.get("content", "")
                is_tool_result = isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                )
                if not is_tool_result:
                    turn_tool_inputs = []
                    turn_bash_cmds = []
                    turn_written = []
                continue
            if role != "assistant":
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                if any(texts):
                    last_text = " ".join(t for t in texts if t)
                # Collect tool_use inputs in this turn (Read/Bash/etc).
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        turn_tool_inputs.append(json.dumps(b.get("input", {})))
                        # Keep Bash commands separately so a pass-claim can be
                        # matched against what actually ran.
                        if b.get("name") == "Bash":
                            turn_bash_cmds.append(
                                (b.get("input", {}) or {}).get("command", "") or "")
                        # Keep the decoded text of a write. turn_tool_inputs
                        # holds json.dumps(...), where newlines are escaped, so
                        # its lines cannot be counted.
                        if b.get("name") in ("Write", "Edit"):
                            _in = b.get("input", {}) or {}
                            for _k in ("content", "new_string"):
                                _v = _in.get(_k)
                                if isinstance(_v, str) and _v:
                                    turn_written.append(
                                        (_in.get("file_path", "") or "", _v))
            elif isinstance(content, str) and content:
                last_text = content
except Exception:
    sys.exit(0)

msgs = []

# (d) Write budget. Playbooks set an `adt-budget:` for what they read; this
# caps the length of a `## <date> — <role>` log entry written into a ticket
# file. It only warns, so an over-long entry is visible without blocking.
LOG_ENTRY_BUDGET = 12          # lines, for one `## <date> — <role>` log entry
_LOG_HEAD = re.compile(r"^## \d{4}-\d{2}-\d{2}[ T].*—", re.M)

def _log_entry_lines(blob):
    """Longest run of lines belonging to one log entry in a written blob."""
    worst = 0
    for m in _LOG_HEAD.finditer(blob):
        rest = blob[m.start():]
        nxt = _LOG_HEAD.search(rest, 1)
        entry = rest[: nxt.start()] if nxt else rest
        worst = max(worst, len([l for l in entry.strip().split("\n") if l.strip()]))
    return worst

worst_entry = 0
for _path, _text in turn_written:
    if "cache" not in _path and ".adt" not in _path:
        continue          # only ticket files carry a `## <date> — <role>` log
    worst_entry = max(worst_entry, _log_entry_lines(_text))
if worst_entry > LOG_ENTRY_BUDGET:
    msgs.append(
        f"write budget: a ticket log entry ran {worst_entry} lines against a "
        f"budget of {LOG_ENTRY_BUDGET}. Size the entry to the diff, not to the "
        f"reasoning behind it; a metadata-only change gets one line. "
        f"See commands/*.md `adt-budget:`.")

# (a) Banned recovery-narration phrases.
hits = [p for p in BANNED if re.search(p, last_text, re.IGNORECASE)]
if hits:
    pretty = ", ".join(h.replace(r"\b", "").strip("\\") for h in hits)
    msgs.append(f"banned recovery-narration phrase(s): {pretty}. State the fact plainly; drop the qualifier.")

# (b) Unbacked done-claim about a rendered artifact. It triggers only on a claim
# that mentions the artifact (board, link, renders, page), not on the bare word
# "done", to keep false alarms down.
DONE_CLAIM = re.compile(
    r"(on the board|the link works|links? (?:now )?resolve|renders? (?:correctly|the)|"
    r"(?:verified|confirmed)[^.]{0,40}\b(?:board|link|page|anchor|rendered|artifact)\b|"
    r"\b(?:board|kanban|references\.html)\b[^.]{0,40}\b(?:updated|present|correct|live|shows)\b)",
    re.IGNORECASE)
# The claim is backed if any tool call this turn mentions a .adt/ HTML file
# (a Read, grep or curl of it), i.e. the file the human opens.
ARTIFACT_TOUCH = re.compile(r"\.adt/[^\"']*\.html", re.IGNORECASE)
if DONE_CLAIM.search(last_text):
    touched = any(ARTIFACT_TOUCH.search(t) for t in turn_tool_inputs)
    if not touched:
        msgs.append(
            "a done-claim about a rendered artifact, but no tool call in this turn "
            "opened/asserted the .adt/ file the human opens. Verify the "
            "rendered artifact (not the source/cache) before claiming it — or drop the claim."
        )

# (c) Unbacked claim that a command or suite passed (working-style #10).
# When the claim names a test path, runner or script, that name must appear in
# a Bash command run this turn. Only when it names nothing does any Bash call
# in the turn count as backing.
PASS_CLAIM = re.compile(
    r"\b(?:tests?|test suite|suite|build|lint|typecheck|checks?)\b[^.]{0,40}"
    r"\b(?:pass(?:ed|es|ing)?|green|clean|succeed(?:ed|s)?)\b"
    r"|\ball (?:tests?|checks?)\b[^.]{0,20}\b(?:pass|green)"
    r"|\bexits? (?:with )?0\b",
    re.IGNORECASE)
# What the claim names: a test path, a runner or a script.
SUBJECT = re.compile(
    r"(?:\b(?:pytest|npm|yarn|pnpm|go test|cargo|make|bash|sh|ruff|eslint|tsc|mypy)\b"
    r"|\b[\w./-]+\.(?:py|sh|ts|tsx|js|go|rs)\b"
    r"|\btests?/[\w./-]+)",
    re.IGNORECASE)
if PASS_CLAIM.search(last_text):
    subjects = {m.group(0).lower() for m in SUBJECT.finditer(last_text)}
    joined = " ".join(turn_bash_cmds).lower()
    if subjects:
        backed = any(sub in joined for sub in subjects)
        detail = "none of %s appears in any command run this turn" % sorted(subjects)
    else:
        backed = bool(turn_bash_cmds)
        detail = "no command was run in this turn at all"
    if not backed:
        msgs.append(
            "a claim that something passed, but %s. Run it and cite the result, "
            "or drop the claim (working-style #10: never guess - act only on "
            "verified data)." % detail)

# (e) A counted or completeness claim with no counting command behind it
# (AO-013 gap 3, working-style #13). Two failures in one AO-006 session: "9
# distinct markers, all ok" reported from a grep that stopped at newlines, and
# "the only two remaining mentions" from a three-phrase grep that never counted
# mentions. Both were single sentences carrying a quantity AND a completeness
# word, and neither turn ran anything that counts.
#
# Both halves are required IN THE SAME SENTENCE. A number on its own is a line
# reference or a ticket id; a completeness word on its own is ordinary prose.
# Requiring the pair is what keeps this off the rest of a report.
#
# WHAT THIS DOES NOT DO: it checks that the turn counted SOMETHING, not that it
# counted THIS. Check (c) binds a pass-claim to the command it names because a
# claim names a runner; a counted claim names a noun ("markers"), so there is
# nothing to bind to. The honest form would compare the claimed number against
# what the command printed, and the transcript does carry tool results — that is
# a bigger change than a fourth check, and it is written down here rather than
# implied by silence. So this catches the AO-006 shape (nothing counted at all)
# and not a count whose scope was too narrow.
#
# `tools/check_provenance.py` asks the same question of DOCS, line by line, with
# a tuned CLAIM/COMMAND/SKIP set. It is not imported: it does not ship in
# `.claude/tools/`, so a consumer's hook could not load it. The two SKIP
# patterns worth having are copied below, and widening either set should be done
# in both.
COUNT_QTY = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:[\w./-]+\s+){0,2}\w[\w/-]*s\b", re.IGNORECASE)
COUNT_ALL = re.compile(r"\b(?:all|every|only|none|exactly|each)\b", re.IGNORECASE)
# References, not measurements (lifted from check_provenance.SKIP): a file:line
# citation and a ticket id. They are STRIPPED from the sentence before the
# quantity is looked for, rather than exempting the whole sentence. Applied to
# the sentence, "9 distinct markers, all ok (AO-006)" went unchecked while the
# same words without the ticket id were flagged — and a trailing ticket id is
# close to a habit in this repo's prose, so the check's reach was far narrower
# than its tests suggested (QA finding 7).
COUNT_SKIP = re.compile(r":\s*[0-9]+\b|\b[A-Z]{2,4}-[0-9]+\b")
# What counts as counting. `len(` is here because an inline `python3 -` script is
# how this project's own counts are usually produced; without it the check fires
# on correctly verified work, and a check that fires on correct work is one that
# gets worked around.
COUNTING = re.compile(
    r"\bwc\b|\bgrep\b[^|;&]*\s-[A-Za-z]*c\b|--count\b"
    r"|\buniq\b[^|;&]*-c\b|\blen\(", re.IGNORECASE)

# A pass-claim is check (c)'s business, and (c) is the stricter test: it requires
# the named runner to have run. "All 23 tests pass" after a real pytest run
# carries a quantity and a completeness word, and a pytest invocation contains no
# `wc` or `len(` — so without this skip the commonest correct sentence in this
# repo draws a warning from (e) while satisfying (c).
claim = next((s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", last_text)
              if COUNT_QTY.search(COUNT_SKIP.sub(" ", s)) and COUNT_ALL.search(s)
              and not PASS_CLAIM.search(s)), None)
if claim and not COUNTING.search(" ".join(turn_bash_cmds)):
    msgs.append(
        "a counted or completeness claim whose command cannot support it: %r. "
        "No command in this turn counted anything: no wc, grep -c, uniq -c, "
        "--count or len(). Cite the command that produced the number, or drop "
        "the claim (working-style #13)." % claim[:120])

if msgs:
    print(json.dumps({"systemMessage": "⚠ working-style.md: " + " | ".join(msgs)}))
PY
exit 0
