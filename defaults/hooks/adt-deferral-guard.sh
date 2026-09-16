#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT default. PreToolUse hook: blocks a Bash command that creates an Issue
# (`gh issue create`, or a `gh api` REST or GraphQL call that creates one)
# unless a human authorised it.
#
# Filing a follow-on ticket mid-build takes work out of the ticket being graded,
# so the agent cannot approve it for itself. The hook reads the session
# transcript and denies the create unless a human turn authorises it. It uses
# the transcript because an agent cannot write a `role: user` turn; a marker
# file, an env var or the session's ticket binding are all things the agent
# writes itself and could fake.
#
# The create is allowed if any of these holds:
#   (a) the current turn is a `/adt-brief` invocation;
#   (b) a human typed the token ADT-APPROVE-FOLLOWON (any case, loose spacing),
#       or a line of the latest human message is just "approve"/"approved"
#       (a list marker such as "1." in front is fine);
#   (c) the latest human message asks for a ticket in plain words.
#
# A slash command writes two role:user records: the invocation and the expanded
# playbook body. So only typed messages count for (b) and (c); anything wrapped
# in a machine prefix such as <command-message> or <command-name> is ignored.
# Otherwise a playbook that mentions the token would approve itself.
#
# If the transcript is missing, unreadable or unparseable, the hook allows the
# create rather than block on its own failure. It denies only when it read the
# transcript and found no authorisation.
#
# Input: PreToolUse stdin JSON with tool_name, tool_input.command, cwd and
# transcript_path. To deny it exits 0 and prints
#   {"hookSpecificOutput":{"hookEventName":"PreToolUse",
#     "permissionDecision":"deny","permissionDecisionReason":"…"}}
set -euo pipefail

input="$(cat)"

# The python body goes to a temp file and runs with the JSON on stdin. Two stdin
# redirections on one line collide, and a heredoc inside $(...) mis-parses
# quotes.
_py="$(mktemp -t deferral-guard.XXXXXX.py)"
trap 'rm -f "$_py"' EXIT
cat > "$_py" <<'PY'
import json, os, re, shlex, sys

RAW = sys.stdin.read()
try:
    d = json.loads(RAW)
except Exception:
    sys.exit(0)                      # unparseable input → allow

if d.get("tool_name") != "Bash":
    sys.exit(0)
cmd = (d.get("tool_input") or {}).get("command", "") or ""

# Only a command that creates an Issue. The guard reads the command's words, not
# its text (ADT-354): matching the text denied a read-only `grep` whose pattern
# named the command, and missed `gh api`, which creates an Issue just the same.
OPS = "();<>|&`\n"                      # shell punctuation that ends a command
SHELLS = {"bash", "sh", "zsh"}
ISSUES_ENDPOINT = re.compile(r"^/?repos/[^/\s]+/[^/\s]+/issues/?$")
REPO_FLAGS = {"-R", "--repo"}           # gh flags that take a value
VALUE_FLAGS = {"-H", "--header", "-q", "--jq", "-t", "--template",
               "--hostname", "-p", "--preview", "--cache"}
FIELD_FLAGS = {"-f", "-F", "--field", "--raw-field", "--input"}

def next_word(it):
    """The next word that is not a flag, skipping `-R <repo>`."""
    for t in it:
        if t in REPO_FLAGS:
            next(it, None)
        elif not t.startswith("-"):
            return t
    return None

def gh_creates(args):
    """True when `gh <args>` creates an Issue."""
    it = iter(args)
    sub = next_word(it)
    if sub == "issue":
        return next_word(it) == "create"
    if sub != "api":
        return False
    rest = list(it)
    method, fields, endpoint = None, False, None
    it = iter(rest)
    for t in it:
        if t in ("-X", "--method"):
            method = (next(it, None) or "").upper()
        elif t.startswith("--method="):
            method = t.split("=", 1)[1].upper()
        elif t.startswith("-X") and len(t) > 2:
            method = t[2:].upper()
        elif t in FIELD_FLAGS:
            fields = True
            next(it, None)
        elif t.split("=", 1)[0] in FIELD_FLAGS:
            fields = True
        elif t in VALUE_FLAGS:
            next(it, None)
        elif not t.startswith("-") and endpoint is None:
            endpoint = t
    if endpoint == "graphql":
        return any("createIssue" in a for a in rest)
    if endpoint and ISSUES_ENDPOINT.match(endpoint):
        # `gh api` switches to POST when fields are given and no method is.
        return method == "POST" if method else fields
    return False

def argv_creates(argv):
    """True when any `gh` word in this simple command starts an Issue create.

    Every gh word is checked, not only the first word, so a create behind any
    wrapper is caught (`sudo -u root`, `timeout 30`, `find -exec`, `xargs -n 1`)
    without a list of wrapper names to keep up to date. A quoted pattern such
    as `grep "gh issue create"` is a single word, so it never matches."""
    for k, word in enumerate(argv):
        name = os.path.basename(word)
        if name == "gh" and gh_creates(argv[k + 1:]):
            return True
        if name == "eval" and command_creates(" ".join(argv[k + 1:])):
            return True
        if name in SHELLS:
            for j in range(k + 1, len(argv) - 1):
                a = argv[j]
                if a.startswith("-") and not a.startswith("--") and "c" in a:
                    if command_creates(argv[j + 1]):
                        return True
                    break
    return False

def command_creates(text):
    try:
        lex = shlex.shlex(text.replace("\\\n", " "), posix=True,
                          punctuation_chars=OPS)
        lex.whitespace, lex.whitespace_split, lex.commenters = " \t\r", True, ""
        toks = list(lex)
    except ValueError:
        # Unbalanced quotes: shlex cannot read it, so fall back to the old
        # whole-text match. That is stricter, never looser.
        flat = " ".join(text.split())
        return bool(re.search(r"gh\s+issue\s+create\b", flat) or re.search(
            r"gh\s+api\b.*\brepos/[^/\s]+/[^/\s]+/issues(?![/?\w])", flat))
    argv = []
    for t in toks + [";"]:
        if t and all(ch in OPS for ch in t):
            if argv_creates(argv):
                return True
            argv = []
        else:
            argv.append(t)
    return False

if not re.search(r"\bgh\b", cmd) or not command_creates(cmd):
    sys.exit(0)

transcript = d.get("transcript_path") or ""
if not transcript or not os.path.isfile(transcript):
    sys.exit(0)                      # cannot check → allow

TOKEN = "ADT-APPROVE-FOLLOWON"

# Prefixes that mark machine-generated content sent as role:user. Keep this in
# step with `tools/adt_xexam.py:_MACHINE_PREFIXES`.
MACHINE_PREFIXES = ("<command-message>", "<command-name>", "<local-command",
                    "<task-notification>", "<system-reminder>", "<bash-input>",
                    "<bash-stdout>", "<user-prompt-submit-hook>")

def user_texts(path):
    """Yield (is_machine, text) for every role:user record, oldest first.

    Skips sidechain records and any userType other than external, as
    adt_xexam.human_turns() does. A subagent's first record is the calling
    agent's prompt, sent as role:user with no command wrapper, so without this
    an agent could write text that looks like a human turn. Tool results are
    also role:user but hold no text blocks, so they yield nothing."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            # A subagent's first turn, written by the calling agent.
            if rec.get("isSidechain"):
                continue
            if rec.get("userType") not in (None, "external"):
                continue
            # Unlike adt_xexam.human_turns(), a missing top-level `type` is
            # accepted. Most of the guard's test records have no `type`, and
            # real transcripts always set it to "user".
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
                # Any machine prefix marks content a human did not type.
                machine = any(pfx in t for pfx in MACHINE_PREFIXES)
                yield machine, t

try:
    records = list(user_texts(transcript))
except Exception:
    sys.exit(0)                      # read/parse failed → allow

# (a) The current turn is a /adt-brief invocation. Only the last invocation
# record counts, since that is the command running now.
brief_now = False
for expansion, t in reversed(records):
    m = re.search(r"<command-name>\s*/?([\w-]+)\s*</command-name>", t)
    if m:
        brief_now = m.group(1).lstrip("/") == "adt-brief"
        break

# (b) A human typed the approval token, in any case and with loose spacing.
# Machine records do not count.
TOKEN_RE = re.compile(r"adt[-_ ]?approve[-_ ]?follow[-_ ]?ons?"
                      r"|\bapprove[-_ ]?(?:the[-_ ]?)?follow[-_ ]?ons?\b", re.IGNORECASE)
approved = any(TOKEN_RE.search(t) for machine, t in records if not machine)

# A bare "approve"/"approved" also counts, but only as a whole line of the
# latest human message, optionally after a list marker, because a numbered reply
# to numbered questions is how people answer (ADT-354). "The reviewer will
# approve it" does not authorise anything. Neither does "1. approved 2. ok" on
# one line: the guard cannot tell which item the approval was for.
BARE_RE = re.compile(r"^[^\w\n]*(?:\d+[.)])?[^\w\n]*approved?[^\w\n]*$",
                     re.IGNORECASE | re.MULTILINE)
if not approved:
    for machine, t in reversed(records):
        if machine:
            continue
        approved = bool(BARE_RE.search(t))
        break

# (c) A human asked for a ticket in their own words, e.g. "create a ticket for
# X". This relies on the filtering above: a non-machine record is one the agent
# cannot write. Only the latest human message counts, so one request does not
# authorise every create for the rest of the session.
INTENT_RE = re.compile(
    # A verb such as "create" or "put" near the noun, or a phrase like "in a
    # ticket" or "as a tix" on its own.
    r"(?:\b(?:create|open|file|raise|log|put|add|make|write|record|capture|track|stick)\b"
    r"[^.\n]{0,40}?|\b(?:in|into|as|on)\s+(?:a|an|the)\s+)"
    # "tix" is the word ADT itself uses for a ticket.
    r"\b(?:tickets?|issues?|tix|tixes)\b|/adt-brief", re.IGNORECASE)
asked = False
for machine, t in reversed(records):
    if machine:
        continue
    asked = bool(INTENT_RE.search(t))
    break

decision = "ALLOW" if (brief_now or approved or asked) else "DENY"
why = ("/adt-brief invocation" if brief_now
       else "human authorised (token or bare approval)" if approved
       else "human asked for a ticket in their own words" if asked
       else "no human authorisation in the transcript")

log = os.path.join(d.get("cwd") or ".", ".adt", "state", "deferral-guard.log")
try:
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("%s\t%s\t%s\n" % (decision, why, " ".join(cmd.split())[:200]))
except Exception:
    pass                             # a logging failure never changes the decision

if decision == "ALLOW":
    sys.exit(0)

reason = (
    "deferral-guard: no human has authorised filing a ticket from this session "
    "(no casual deferral). Filing a follow-on takes work out of the ticket being "
    "graded, so you cannot approve it yourself. Fix it now, in this diff, unless "
    "all three of these hold and you state the evidence for each: (1) UNFORESEEN: "
    "it could not reasonably have been seen at plan time ('it wasn't in the plan' "
    "does not count); (2) SIGNIFICANT DEVIATION REQUIRED: a named conflict, a "
    "different root cause in different files, or a decision the human owns "
    "(effort and size do not count); (3) HUMAN APPROVED. To ask, use /adt-block. "
    "To authorise, the human replies `approve` (on its own line; `1. approve` in "
    "a numbered reply counts), `approve follow-on`, or " + TOKEN + " (any case)."
)
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": reason,
}}))
sys.exit(0)
PY
printf '%s' "$input" | /usr/bin/python3 "$_py" 2>/dev/null || true
exit 0
