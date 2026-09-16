#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT default. PreToolUse hook on Bash.
# Denies a full-suite test run unless a human asked for it. A full run takes
# minutes and is usually not owed when nothing changed since the last green
# run (working-style #7).
#
# Only full-suite runs are gated: `pytest tools/tests/ tests/`, a bare
# `pytest`, and `tests/run-shell-suite.sh`. Targeted runs are always allowed:
# `pytest path/to/one_test.py`, `-k`, `::`, a single shell test.
#
# The run is allowed if the human's latest message:
#   (a) contains the token `adt run tests` (or `adt tests`), in any case, or
#   (b) asks for it in plain words ("run the tests", "run the suite",
#       "full suite", "run qa").
# Only the latest human message counts, so one "run the tests" does not
# authorise every later run in the session.
#
# The decision is read from the transcript only, because an agent cannot write
# a `role: user` turn but can write a marker file or set an env var. The
# role:user reader below is the same as the one in adt-deferral-guard.sh, and
# its machine-prefix list must stay in step with it.
#
# If the transcript is missing, unreadable or has no human records, the run is
# allowed. It is denied only when the transcript was read and has no
# authorisation. Every decision is logged to .adt/state/test-run-guard.log.
set -euo pipefail

input="$(cat)"

_py="$(mktemp -t test-run-guard.XXXXXX.py)"
trap 'rm -f "$_py"' EXIT
cat > "$_py" <<'PY'
import json, os, re, sys

RAW = sys.stdin.read()
try:
    d = json.loads(RAW)
except Exception:
    sys.exit(0)

if d.get("tool_name") != "Bash":
    sys.exit(0)
cmd = ((d.get("tool_input") or {}).get("command", "") or "")

# A heredoc body that is only written to a file (a ticket, a report) is data,
# so a suite command quoted inside it should not count as a run. Bodies are
# stripped only when the delimiter is quoted (<<'EOF' / <<"EOF"), because an
# unquoted delimiter still interpolates. The `<<'EOF'` operator itself is kept
# so `bash <<'EOF'` can still be matched.
def _shell_reads_it(prefix):
    """True when the heredoc feeds a shell, so its body runs as commands.

    `bash <<'EOF'` runs the body as shell, so a suite command in it really
    runs. `python3 - <<'EOF'` runs the body as Python, where the same text is
    just a string. An unrecognised command is treated as a shell, so the guard
    errs towards denying.
    """
    seg = re.split(r"[|;&]|&&|\|\|", prefix)[-1].strip()
    if not seg:
        return False                      # plain `<<EOF` redirect, body is data
    words = seg.split()
    head = os.path.basename(words[0]) if words else ""
    NON_SHELL = ("python", "python3", "cat", "tee", "jq", "sed", "awk", "node",
                 "ruby", "perl", "php", "tr", "grep", "sort", "head", "tail")
    return not any(head.startswith(n) for n in NON_SHELL)


def _strip_quoted_heredoc_bodies(s):
    """Drop the body of every quoted heredoc that is not fed to a shell."""
    out, i = [], 0
    while True:
        m = re.search(r"<<-?\s*(['\"])([A-Za-z_][A-Za-z_0-9]*)\1", s[i:])
        if not m:
            out.append(s[i:])
            break
        start = i + m.start()
        head, delim = s[i:start], m.group(2)
        out.append(head)
        out.append(s[start:i + m.end()])           # keep the `<<'EOF'` operator
        rest = s[i + m.end():]
        term = re.search(r"^[ \t]*%s[ \t]*$" % re.escape(delim), rest, re.M)
        if not term:
            out.append(rest)
            break
        body, tail_at = rest[:term.start()], i + m.end() + term.end()
        if _shell_reads_it(head):
            out.append(body)                       # shell body: keep it visible
        out.append(rest[term.start():term.end()])
        i = tail_at
    return "".join(out)


cmd = " ".join(_strip_quoted_heredoc_bodies(cmd).split())
if not cmd:
    sys.exit(0)

# ── what counts as a full-suite run ──────────────────────────────────────────
# A run is targeted if it names a specific test (a file, `-k`, or `::`).
TARGETED = re.compile(r"(-k\s|::|test_[a-z_0-9]+\.(py|sh)\b)")
FULL = (
    # pytest over the suite directories, or with no path at all
    re.compile(r"\bpytest\b(?![^|;&]*(-k\s|::|test_[a-z_0-9]+\.py))"
               r"[^|;&]*(tools/tests/?\s|tests/?\s|tools/tests/?$|tests/?$)"),
    re.compile(r"\bpytest\b\s*(-[a-zA-Z-]+\s*)*$"),
    re.compile(r"run-shell-suite\.sh"),
)
gated = any(p.search(cmd) for p in FULL)
if gated and TARGETED.search(cmd) and "run-shell-suite.sh" not in cmd:
    gated = False
if not gated:
    sys.exit(0)

transcript = d.get("transcript_path") or ""
if not transcript or not os.path.isfile(transcript):
    sys.exit(0)

TOKEN_RE = re.compile(r"\badt[\s-]+(?:run[\s-]+)?tests\b", re.IGNORECASE)

MACHINE_PREFIXES = ("<command-message>", "<command-name>", "<local-command",
                    "<task-notification>", "<system-reminder>", "<bash-input>",
                    "<bash-stdout>", "<user-prompt-submit-hook>")

def user_texts(path):
    """Yield (is_machine, text) for every role:user record, oldest first.

    A subagent's first record is the dispatching agent's prompt, and it has
    role:user with no slash-command wrapper. To keep agent-written text from
    counting as a human, this skips sidechain records, records whose userType
    is not external, and text with any machine prefix. This matches
    adt_xexam.human_turns().

    Tool results are also role:user but have no text blocks, so they yield
    nothing."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            # A subagent's records were written by an agent, not a human.
            if rec.get("isSidechain"):
                continue
            if rec.get("userType") not in (None, "external"):
                continue
            # Unlike adt_xexam.human_turns(), a missing top-level `type` is
            # accepted. The deferral-guard tests build records with no `type`,
            # and real transcripts always carry type "user".
            if rec.get("type") not in (None, "user"):
                continue
            msg = rec.get("message", rec)
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                texts = [content]
            elif isinstance(content, list):
                texts = [b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text"]
            else:
                texts = []
            for t in texts:
                if not t:
                    continue
                # A slash command emits an invocation record AND an expanded
                # playbook-body record; both are role:user. Any of the machine
                # prefixes marks content a human did not type.
                machine = any(pfx in t for pfx in MACHINE_PREFIXES)
                yield machine, t

try:
    records = list(user_texts(transcript))
except Exception:
    sys.exit(0)

# No human records means the hook could not read what it decides on, so allow
# the run. adt-deferral-guard.sh denies in this case instead: filing a ticket
# should fail closed, while an extra test run only costs time.
if not any(not machine for machine, _ in records):
    sys.exit(0)

# (a) a human typed the token. Machine records never count.
approved = any(TOKEN_RE.search(t) for machine, t in records if not machine)

# (b) the latest human message asks for a run in plain words.
INTENT_RE = re.compile(
    r"\b(?:run|rerun|re-run|execute)\b[^.\n]{0,40}?"
    r"\b(?:tests?|suites?|pytest|qa|ci)\b"
    r"|\bfull\s+suite\b|\bwhole\s+suite\b|/adt-qa-run", re.IGNORECASE)
asked = False
for machine, t in reversed(records):
    if machine:
        continue
    asked = bool(INTENT_RE.search(t))
    break

decision = "ALLOW" if (approved or asked) else "DENY"
why = ("typed the adt-tests token" if approved
       else "human asked for a run in their own words" if asked
       else "no human authorisation in the transcript")

log = os.path.join(d.get("cwd") or ".", ".adt", "state", "test-run-guard.log")
os.makedirs(os.path.dirname(log), exist_ok=True)
try:
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("%s\t%s\t%s\n" % (decision, why, cmd[:200]))
except Exception:
    pass

if decision == "ALLOW":
    sys.exit(0)

reason = (
    "test-run-guard: a full-suite run is not authorised in this turn. "
    "Running every test takes minutes, and working-style #7 says to re-run a "
    "check only when something changed since it last passed. A rebase, a "
    "retry or a lane change does not change the code.\n\n"
    "Do one of these instead:\n"
    "  * run only what the diff touched: pytest <the test file(s)> \n"
    "  * state what changed since the last green run, and ask\n"
    "  * skip it and say so in the report\n\n"
    "A human can authorise by saying so, or with the token `adt run tests` "
    "(or `adt tests`). "
    "Targeted runs are never gated."
)
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": reason}}))
sys.exit(0)
PY

printf '%s' "$input" | /usr/bin/python3 "$_py" || true
exit 0

# adt-bundle: v0.1.0
