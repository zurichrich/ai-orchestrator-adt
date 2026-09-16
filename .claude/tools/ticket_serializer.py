# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Lossless ticket .md <-> GitHub Issue JSON serialiser (dependency-free).

The .md backlog file is the local cache; a GitHub Issue is the durable backing
store (a consumer project). This module is the single place that maps between the
two, so the sync process (adt watch) and the commands share one contract.

Dependency-free on purpose: ADT drops into any project with `git clone &&
setup.sh`, no `pip install`. We therefore hand-roll the small subset of YAML
frontmatter the field-map needs (scalars, simple string lists, and the
`comments` list-of-dicts) rather than depend on PyYAML.

Field-map contract (frontmatter key -> Issue field):
  title            -> title
  <body>           -> body            (markdown below the frontmatter fence)
  issue_number     -> number          (GH-owned; never hand-authored)
  issue_node_id    -> id (node id)    (GH-owned)
  state            -> state           (open/closed; derived from stage)
  state_reason     -> stateReason     (completed/not_planned/reopened)
  priority/track/stage/*_review_required/tags -> labels
  assignees        -> assignees       (list of logins)
  milestone        -> milestone.title
  created          -> createdAt
  closed           -> closedAt
  created_by       -> author.login
  comments         -> comments[]      (list of {author, at, body})
Out of scope (documented, not a gap): isPinned, reactionGroups,
closedByPullRequestsReferences.
"""

from __future__ import annotations

import json
import re

# Frontmatter keys whose values are string lists ("- item" blocks or [a, b]).
LIST_KEYS = {"assignees", "tags", "commits"}
# Frontmatter keys whose values are lists of dicts.
# done_evidence (ADT-81/ADT-090): the machine-checkable DoD — a list of
# {must_run|file, ...} entries. Without it here, a sync round-trip mis-parsed
# the block to `done_evidence: null` and orphaned the entries, wiping the DoD
# contract the whole spec-driven lifecycle grades against (ADT-090 found this:
# the first ticket to carry a real done_evidence block through `adt watch`).
# ADT-224 D1a/D3a: `gate_effects` (the per-gate ran / caused-edit record) and
# `dod_snapshot` (the done_evidence digest an amendment count is derived from)
# are both flat dict-lists and MUST be listed here. `_parse_dictlist` is what
# reads a `- k: v` block back as structure; a key absent from this set is read
# by `_coerce_scalar` instead, which stringifies the first line and silently
# discards the rest of the list on the next sync round trip.
DICTLIST_KEYS = {"comments", "done_evidence", "gate_effects", "dod_snapshot"}
# Keys encoded as Issue labels rather than native Issue fields.
# ADT-135 defect 4: `type` was read back from a `type:<v>` label (from_issue,
# ADT-73) but never WRITTEN as one, so no Issue ADT ever pushed carried the
# label its own reconstructor looks for. Adopting a backlog therefore bucketed
# every Issue as `tasks`. Adding it here closes the round trip.
LABEL_SCALAR_KEYS = ("priority", "track", "stage", "type")
# Sync machinery, not ticket metadata (ADT-116). /adt-brief applies this label
# when it creates an Issue and the cache file does not exist yet, so a watch
# tick landing in that gap knows the Issue is spoken for and must not
# reconstruct a duplicate stub. It is deliberately NOT round-tripped into
# frontmatter: it would otherwise fall through to `tags`, show as a chip on the
# card, and — because the push replaces the Issue's label set from the cache —
# be written straight back, making the claim immortal. Skipping it on read is
# also what auto-releases it: once the cache file exists, the first push sees
# the label in `have` but not in `want` and removes it.
FILING_LABEL = "adt:filing"
# Marks an Issue that is deliberately not a ticket: the telemetry archive that
# lib/uninstall.sh files (ADT-354). The sync neither rebuilds a cache file for
# it nor pushes to it, so it never lands on the board.
ARCHIVE_LABEL = "adt:archive"
# Marks the Issue that holds one ADT machine report per machine (ADT-384,
# tools/adt_machines.py). Not a ticket either, so the sync skips it the same way.
INSTALL_LABEL = "adt:install"
# Every label that marks an Issue as not a ticket. The sync skips all of them.
NON_TICKET_LABELS = frozenset({ARCHIVE_LABEL, INSTALL_LABEL})
GATE_KEYS = {  # frontmatter bool -> gate:<name> label
    "ui_review_required": "ui",
    "security_review_required": "security",
    "trading_path_review_required": "trading-path",
}


# --------------------------------------------------------------------------
# Frontmatter (de)serialisation — the dependency-free YAML subset.
# --------------------------------------------------------------------------
def parse_md(text: str) -> dict:
    """Parse a ticket .md into {frontmatter..., 'body': str}.

    Handles flat scalars, `key:` followed by `  - item` string lists, and
    `comments:` as a list of `- author:/at:/body:` blocks. Anything else is
    kept as a scalar string so nothing is silently dropped.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {"body": text}

    # Split frontmatter block from body.
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {"body": text}

    fm_lines = lines[1:end]
    body = "\n".join(lines[end + 1:]).lstrip("\n")

    out: dict = {}
    i = 0
    while i < len(fm_lines):
        raw = fm_lines[i]
        if not raw.strip() or raw.strip().startswith("#"):
            i += 1
            continue
        # key: value  (value may be empty -> a block follows)
        if ":" not in raw:
            i += 1
            continue
        key, _, val = raw.partition(":")
        key = key.strip()
        val = val.strip()

        if key in DICTLIST_KEYS:
            if val == "[]":
                out[key] = []
                i += 1
                continue
            if not val:
                items, i = _parse_dictlist(fm_lines, i + 1)
                out[key] = items
                continue
        if key in LIST_KEYS:
            if val.startswith("[") and val.endswith("]"):
                out[key] = _parse_inline_list(val)
                i += 1
                continue
            if not val:
                items, i = _parse_str_list(fm_lines, i + 1)
                out[key] = items
                continue
            # Legacy single-value scalar form (`commits: abc123`) -> 1-item list,
            # so the in-memory shape matches what emit_md produces (fixed point).
            sc = _coerce_scalar(val)
            out[key] = [sc] if sc is not None else []
            i += 1
            continue
        out[key] = _coerce_scalar(val, key)
        i += 1

    out["body"] = body
    return out


def _indent(s: str) -> int:
    return len(s) - len(s.lstrip(" "))


def _parse_str_list(lines: list, start: int) -> tuple:
    """Parse `  - item` lines starting at `start`. Returns (list, next_index)."""
    items = []
    i = start
    while i < len(lines):
        s = lines[i]
        if not s.strip():
            i += 1
            continue
        if _indent(s) == 0:
            break
        st = s.strip()
        if st.startswith("- "):
            items.append(_coerce_scalar(st[2:].strip()))
            i += 1
        else:
            break
    return items, i


def _parse_dictlist(lines: list, start: int) -> tuple:
    """Parse a list of `- key: val` blocks. Returns (list_of_dicts, next_index)."""
    items = []
    i = start
    cur = None
    while i < len(lines):
        s = lines[i]
        if not s.strip():
            i += 1
            continue
        if _indent(s) == 0:
            break
        st = s.strip()
        if st.startswith("- "):
            if cur is not None:
                items.append(cur)
            cur = {}
            st = st[2:].strip()
        if cur is not None and ":" in st:
            k, _, v = st.partition(":")
            cur[k.strip()] = _coerce_scalar(v.strip())
        i += 1
    if cur is not None:
        items.append(cur)
    return items, i


def _parse_inline_list(val: str) -> list:
    inner = val[1:-1].strip()
    if not inner:
        return []
    return [_coerce_scalar(x.strip()) for x in inner.split(",")]


# Keys whose values are integers (so a round-trip preserves int, not str).
INT_KEYS = {"issue_number", "strike_count"}


def _coerce_scalar(v: str, key: str | None = None):
    if v in ("", "null", "~"):
        return None
    if v in ("true", "false"):
        return v == "true"
    # strip matching quotes
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    if key in INT_KEYS:
        try:
            return int(v)
        except ValueError:
            return v
    return v


def emit_md(data: dict) -> str:
    """Serialise a {frontmatter..., 'body'} dict back to a ticket .md string.

    Emits keys in a stable order so round-tripping is byte-deterministic for
    the fixture test. Unknown keys are preserved (emitted after known ones).
    """
    body = data.get("body", "")
    fm = {k: v for k, v in data.items() if k != "body"}

    # Stable ordering: known ADT keys first, then GH-owned, then the rest.
    order = [
        "slug", "id", "title", "type", "priority", "track", "size", "stage",
        "state", "state_reason", "created", "updated", "closed", "created_by",
        "assignees", "milestone", "tags", "commits",
        "ui_review_required", "security_review_required",
        "strike_count", "last_blocker", "kickoff",
        "issue_number", "issue_node_id", "comments",
    ]
    seen = set()
    lines = ["---"]
    for k in order:
        if k in fm:
            lines.extend(_emit_pair(k, fm[k]))
            seen.add(k)
    for k, v in fm.items():
        if k not in seen:
            lines.extend(_emit_pair(k, v))
    lines.append("---")
    out = "\n".join(lines) + "\n"
    if body:
        out += "\n" + body + ("\n" if not body.endswith("\n") else "")
    return out


def _emit_pair(key: str, val) -> list:
    if key in DICTLIST_KEYS:
        if not val:
            return [f"{key}: []"]
        out = [f"{key}:"]
        for d in val:
            first = True
            for k, v in d.items():
                prefix = "  - " if first else "    "
                out.append(f"{prefix}{k}: {_emit_scalar(v)}")
                first = False
        return out
    if key in LIST_KEYS:
        # A scalar (legacy single-value form, e.g. `commits: abc123`) must be
        # treated as a one-item list — NOT character-iterated as a string.
        if isinstance(val, str):
            val = [val] if val else []
        if not val:
            return [f"{key}: []"]
        return [f"{key}:"] + [f"  - {_emit_scalar(x)}" for x in val]
    return [f"{key}: {_emit_scalar(val)}"]


def _emit_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    # Frontmatter values are single-line by format: a raw newline in a scalar
    # (e.g. a multi-line Issue comment body pulled into `comments:`) would
    # corrupt the emitted file. Flatten at THIS boundary so every producer
    # inherits the contract instead of each call site remembering it (ADT-100).
    s = " ".join(str(v).splitlines())
    # ADT-273: a value whose first and last character are the same quote char is
    # stripped of them by every reader on the way back — `_coerce_scalar` here,
    # `adt_dod._unquote` on the DoD path — because both strip ONE matched pair.
    # Emitting it bare therefore rewrites it: `"a" && "b"` reads back as
    # `a" && "b`, a different command that still looks authored. Wrap it in the
    # OTHER quote char so the reader's strip removes exactly what we added.
    # Positional, not quote-aware, which is why the wrapper may appear inside.
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return '"%s"' % s if s[0] == "'" else "'%s'" % s
    return s


# --------------------------------------------------------------------------
# Ticket dict <-> GitHub Issue JSON (the field-map contract).
# --------------------------------------------------------------------------
# stage -> (state, stateReason) per the field-map contract.
DONE_STAGES = {"done"}
CANCELLED_REASON = "not_planned"


def _derive_state(data: dict) -> tuple:
    """state/stateReason from stage (+ explicit overrides). done -> closed/
    completed; a `status: cancelled` -> closed/not_planned; else open."""
    if data.get("status") == "cancelled":
        return "closed", CANCELLED_REASON
    if (data.get("stage") or "") in DONE_STAGES:
        return "closed", "completed"
    return "open", None


def _h1_from_body(body: str) -> str | None:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return None


# ── The slug carrier (ADT-174) ────────────────────────────────────────────
# `slug` is the one ADT field with no representation on the Issue. from_issue
# merges onto `base`, so it survives while the cache file exists and is lost
# only on RECONSTRUCT, where adt_sync falls back to `issue-<num>`. Every cache
# rebuilt from Issues therefore renamed its own tickets, permanently and
# silently — and a rebuild is the documented recovery path (uninstall/reinstall,
# a second machine), so the loss compounds.
#
# Carry it as an HTML comment at the end of the Issue body: invisible in
# GitHub's rendered view, no extra API call, and it rides the body push that
# already happens. Deliberately NOT a label — one label per ticket would swamp
# a namespace shared with P0 / stage:* / track:*.
#
# The cache .md never holds the trailer: to_issue appends it on the way out and
# from_issue strips it on the way back, so the two directions stay symmetric
# (ADT-101 — never fix one half of a bidirectional mechanism).
_SLUG_TRAILER = "<!-- adt: slug=%s -->"
# Matches EXACTLY the bytes _with_slug_trailer writes: the "\n\n" separator, or
# the start of the body when there is nothing before it, then the comment and a
# single trailing newline.
#
# Both halves of the prefix are load-bearing:
#   - not "\n*" — a greedy prefix eats the body's OWN trailing newlines, so a
#     ticket whose file ends in a blank line came back a byte shorter than it
#     went out.
#   - not "(?:\n\n)?" — an OPTIONAL prefix matches mid-line, so an authored body
#     whose last line merely CONTAINS the shape ("Example: <!-- adt: slug=x -->")
#     was truncated at that point and a slug invented from the author's prose.
#     That is content loss in the durable store, and it is introduced by this
#     mechanism: before it, from_issue never touched the body.
# Requiring the separator means only a comment on its OWN line, after a blank
# line (or as the whole body), is read as ours — which is precisely what the
# writer emits. A body deliberately ending that way is still ambiguous; nothing
# short of a nonce fixes that, and the field is machine-filed tickets.
_SLUG_TRAILER_RE = re.compile(r"(?:\n\n|\A)<!-- adt: slug=([^\n>]*) -->\n?\Z")
# Slugs are kebab-case filenames; refuse anything that could break out of the
# comment or the filename, rather than emitting an unparseable trailer.
_SAFE_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")


def _strip_slug_trailer(body: str) -> str:
    """Remove the trailer, leaving the body exactly as the cache .md holds it.

    Removes exactly the bytes the writer added and nothing more, so
    md -> to_issue -> from_issue is byte-identical for ANY body — including one
    ending in a blank line, which parse_md does produce (it strips exactly one
    trailing newline, so a file ending "\n\n" yields a body ending "\n").
    Getting this wrong by one character rewrites the Issue body on the first
    push and never puts it back.
    """
    return _SLUG_TRAILER_RE.sub("", body or "")


def _slug_from_trailer(body: str):
    """The slug the Issue body carries, or None. Used only on reconstruct."""
    m = _SLUG_TRAILER_RE.search(body or "")
    if not m:
        return None
    slug = m.group(1).strip()
    return slug if _SAFE_SLUG_RE.match(slug) else None


def _with_slug_trailer(body: str, slug) -> str:
    """Body as it should appear ON THE ISSUE. Idempotent: an existing trailer is
    replaced, never doubled, so a re-push is a no-op rather than growth.

    With no usable slug the body is returned UNTOUCHED. It must not gain so much
    as a trailing newline: nothing on the pull side would take it back off
    (_strip_slug_trailer returns early when there is no trailer to find), so a
    byte added here is a permanent one-character rewrite of the Issue body — the
    exact asymmetry this pair of functions exists to avoid, arrived at from the
    other direction.

    UNTOUCHED means untouched in BOTH directions (ADT-322). This returned the
    trailer-stripped copy, so a body that arrived carrying a trailer left
    without one — bytes REMOVED, the same asymmetry read from the far side. The
    docstring said untouched and the code did not, and nothing tested it. Today
    to_issue only ever passes a cache body, which from_issue already stripped,
    so the live push never hit this; it was a trap set for the next caller."""
    base = _strip_slug_trailer(body or "")
    if not slug or not _SAFE_SLUG_RE.match(str(slug)):
        return body or ""
    trailer = _SLUG_TRAILER % slug
    return (base + "\n\n" + trailer + "\n") if base else (trailer + "\n")


# --------------------------------------------------------------------------
# The `### Definition of Done` body block (ADT-273).
#
# The DoD used to exist in two hand-maintained copies: this fenced block and the
# `done_evidence:` frontmatter. Only the frontmatter is read — `adt_dod.py`
# parses frontmatter and nothing else — so the block drifted (4 of 29 tickets
# disagreed on condition count) and a reconstruct, which rebuilds a cache file
# from the Issue body alone, produced a ticket with no DoD at all (10 of 73).
#
# The block is now GENERATED from the frontmatter at push time and parsed back
# on reconstruct. Both directions go through the codec frontmatter already uses
# (`_emit_pair` / `_parse_dictlist`), so the fence contains frontmatter syntax
# verbatim and the round trip is one codec rather than a second parser reading
# prose.
_DOD_HEADING_RE = re.compile(r"^### Definition of Done.*$", re.MULTILINE)
# What counts as one fenced block, shared by the writer (`_render_dod_block`) and
# the reader (`_dod_from_body`) so the two cannot drift apart. They did: the
# writer was tightened over three rounds while the reader kept the original
# loose pattern, and the reader then closed a match on a LATER fence's opening
# line and returned a stale DoD on reconstruct — non-empty, so nothing flagged it.
# Two properties, each bought by a defect:
#   * the content may not contain a line-initial fence, so a block whose own
#     closer is missing does not match at all rather than running on to the next
#     one and eating or swallowing it;
#   * the closer must be a line of its own (`$`), so an OPENING line like ```yaml
#     cannot serve as one.
# A triple backtick inside a condition value stays safe: entry lines are
# indented and both anchors require column 0.
_FENCE_BODY = r"(?:(?!^```)[\s\S])*?"
_FENCE_CLOSE = r"^```[ \t]*$\n?"
# Pinned by test_unmarked_block_is_not_parsed_back. Rewording this constant
# orphans every block emitted before the change: they stop being parsed and
# reconstructs lose the DoD again, which is the bug this exists to close.
_DOD_MARKER = ("# adt:generated — edit done_evidence in the ticket frontmatter; "
               "this block is overwritten on push")

# The opening line of a generated block, shared so the writer's marked-search and
# the reader cannot drift apart on what "marked" looks like.
_MARKED_OPEN = r"```ya?ml\n" + re.escape(_DOD_MARKER) + r"\ndone_evidence:\n"


def _dod_section(body: str):
    """(start, end) of the `### Definition of Done` section, or None (ADT-273).

    Shared by the writer and the reader so they cannot disagree about WHERE the
    block lives. They did: the writer bounded its search to this section while
    the reader searched the whole body, so a marked fence under some earlier
    heading — an old snapshot pasted into a History section, a duplicate
    heading — won the reader's leftmost match and a reconstruct recovered that
    stale block instead of the one just written. Non-empty and plausible, so
    `dod_lost()` could not see it either.
    """
    m = _DOD_HEADING_RE.search(body or "")
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"^### ", body[start:], re.MULTILINE)
    return start, start + (nxt.start() if nxt else len(body) - start)


def _render_dod_block(body: str, evidence) -> str:
    """Body with its DoD fence regenerated from `evidence` (ADT-273).

    Returns the body unchanged in three cases, each deliberate:

    * `evidence` is empty. `_emit_pair` on an empty list yields
      `done_evidence: []`. Writing that over a populated block would erase the
      hand-authored DoD in the 10 legacy body-only Issues on the first push,
      which is the data loss this change was written to stop.
    * `evidence` is not a list of non-empty dicts. A ticket can carry a
      malformed `done_evidence:`: a bare scalar parses as a string, and an entry
      whose key lost its colon (`  - must_run`) parses as `{}`. `_emit_pair`
      raises on the first and emits nothing for the second, which would replace
      a populated block with an empty one. Entries must all be non-empty, so a
      malformed frontmatter list leaves the body alone rather than overwriting
      it with less. This function runs for every ticket on every push, so one
      bad ticket must not stop the sync for the rest either.
    * There is no `### Definition of Done` heading. The heading is the anchor,
      and this function does not invent document structure.
    """
    cleared = isinstance(evidence, list) and not evidence
    if not cleared and not (isinstance(evidence, list) and evidence
                            and all(isinstance(e, dict) and e for e in evidence)):
        return body
    body = body or ""
    bounds = _dod_section(body)
    if bounds is None:
        return body
    start, end = bounds
    section = body[start:end]
    if cleared:
        # `done_evidence: []` is a deliberate edit — a DoD being revised — not a
        # malformed value. A MARKED fence is one we generated, so leaving it in
        # place meant the next reconstruct read the deleted conditions straight
        # back into frontmatter, non-empty, with nothing to signal it. Remove it.
        # An UNMARKED fence is a human's, and is still left alone: that is what
        # protects the 10 legacy body-only tickets, which is the whole reason
        # this early return exists.
        marked = re.search(_MARKED_OPEN + _FENCE_BODY + _FENCE_CLOSE,
                           section, re.DOTALL | re.MULTILINE)
        if not marked:
            return body
        section = section[:marked.start()] + section[marked.end():]
        return body[:start] + section + body[end:]
    fence = ("```yaml\n" + _DOD_MARKER + "\n"
             + "\n".join(_emit_pair("done_evidence", evidence)) + "\n```\n")
    # Three anchors, each bought by a defect.
    # OPEN on `done_evidence:` — so an unrelated yaml example in the section is
    # not what gets replaced. CLOSE on a line-initial fence — so a triple
    # backtick inside a condition value cannot end the match. And PREFER the
    # marked fence: making the marker optional in one pattern meant an unmarked
    # legacy fence matched too, and `re.search` returns the leftmost, so a
    # section holding an unmarked fence before a marked one had the
    # hand-authored block overwritten while the generated one was left stale.
    # Fall back to an unmarked fence only when there is no marked one, which is
    # the ordinary first-render case.
    _tail = r"done_evidence:\n" + _FENCE_BODY + _FENCE_CLOSE
    existing = (
        re.search(_MARKED_OPEN + _FENCE_BODY + _FENCE_CLOSE,
                  section, re.DOTALL | re.MULTILINE)
        or re.search(r"```ya?ml\n" + _tail, section, re.DOTALL | re.MULTILINE))
    if existing:                      # 1b: replace the block that is there
        section = section[:existing.start()] + fence + section[existing.end():]
    else:                             # 1c: insert under a heading with no fence
        section = "\n" + fence + "\n" + section.lstrip("\n")
    return body[:start] + section + body[end:]


def _dod_from_body(body: str):
    """The `done_evidence` a GENERATED block carries, or None (ADT-273).

    Only a block bearing `_DOD_MARKER` is parsed. A legacy hand-authored block is
    free-form prose, and reading prose as structure is exactly the ADT-090 shape
    (a block mis-parsed into frontmatter with no error). Requiring the marker
    means this parses only what this module emitted.
    """
    # Same fence definition AND the same section bounds as the writer, so the two
    # cannot disagree about which block is the DoD.
    bounds = _dod_section(body or "")
    if bounds is None:
        return None
    start, end = bounds
    m = re.search(_MARKED_OPEN + r"(" + _FENCE_BODY + r")" + _FENCE_CLOSE,
                  (body or "")[start:end], re.DOTALL | re.MULTILINE)
    if not m:
        return None
    # _parse_dictlist stops at the first zero-indent line, so it is given the
    # entry lines only — never the marker or the `done_evidence:` header.
    return _parse_dictlist(m.group(1).splitlines(), 0)[0]


def to_issue(data: dict) -> dict:
    """Map a parsed ticket dict to a GitHub Issue JSON shape (the `gh issue
    view --json` shape for the synced subset)."""
    labels = []
    for k in LABEL_SCALAR_KEYS:
        v = data.get(k)
        if v:
            labels.append(v if k == "priority" else f"{k}:{v}")
    for fm_key, gate in GATE_KEYS.items():
        if data.get(fm_key) is True:
            labels.append(f"gate:{gate}")
    for tag in data.get("tags") or []:
        labels.append(tag)

    # title falls back to the body H1 (legacy tickets predate the `title:`
    # field); state/reason derive from stage unless explicitly set.
    title = data.get("title") or _h1_from_body(data.get("body", ""))
    state = data.get("state")
    state_reason = data.get("state_reason")
    if not state:
        state, state_reason = _derive_state(data)
    elif state == "closed" and not state_reason:
        # The board render writes `state: closed` into done and cancelled files
        # but no reason, so a cancelled ticket closed as "completed" (ADT-354).
        state_reason = _derive_state(data)[1]

    issue = {
        "title": title,
        "body": _with_slug_trailer(
            _render_dod_block(data.get("body", ""), data.get("done_evidence")),
            data.get("slug")),
        "number": data.get("issue_number"),
        "id": data.get("issue_node_id"),
        "state": state,
        "stateReason": state_reason,
        "labels": [{"name": n} for n in labels],
        "assignees": [{"login": a} for a in (data.get("assignees") or [])],
        "milestone": ({"title": data["milestone"]} if data.get("milestone") else None),
        "createdAt": data.get("created"),
        "closedAt": data.get("closed"),
        "author": ({"login": data["created_by"]} if data.get("created_by") else None),
        "comments": [
            {"author": {"login": c.get("author")}, "createdAt": c.get("at"),
             "body": c.get("body")}
            for c in (data.get("comments") or [])
        ],
    }
    return issue


def from_issue(issue: dict, base: dict | None = None) -> dict:
    """Map a GitHub Issue JSON dict back to a ticket dict, merging onto `base`
    (the existing .md fields) so ADT-only keys (id, slug, size…) survive."""
    out = dict(base or {})
    out["title"] = issue.get("title")
    # Strip the slug carrier back off, and recover `slug` from it when this is a
    # reconstruct (base carries no slug). ADT-174.
    _raw_body = issue.get("body", "")
    out["body"] = _strip_slug_trailer(_raw_body)
    if not out.get("slug"):
        _carried = _slug_from_trailer(_raw_body)
        if _carried:
            out["slug"] = _carried
    # ADT-273 1d: recover the DoD from the generated block. This is what makes a
    # reconstruct (a cache file rebuilt from the Issue alone) carry its DoD
    # instead of returning zero conditions. On the ordinary pull path the caller
    # copies only _PULL_OWNED, so this value is computed and discarded there.
    _dod = _dod_from_body(out["body"])
    if _dod is not None:
        out["done_evidence"] = _dod
    out["issue_number"] = issue.get("number")
    out["issue_node_id"] = issue.get("id")
    out["state"] = issue.get("state")
    out["state_reason"] = issue.get("stateReason")
    if issue.get("milestone"):
        out["milestone"] = issue["milestone"].get("title")
    out["created"] = issue.get("createdAt")
    # Store the FULL ISO timestamp, not a date-only truncation (ADT-54 follow-up):
    # the kanban recency sort orders within a lane, and several tickets edited the
    # same DAY must still order by the more-recent one. Truncating to YYYY-MM-DD
    # collapses every same-day edit into a tie that falls through to priority/slug
    # — which is exactly why the just-edited ticket sank to the bottom. The card
    # render truncates to date for display; the sort keeps the full precision.
    out["updated"] = issue.get("updatedAt")
    out["closed"] = issue.get("closedAt")
    if issue.get("author"):
        out["created_by"] = issue["author"].get("login")
    out["assignees"] = [a.get("login") for a in (issue.get("assignees") or [])]

    names = [lbl.get("name", "") for lbl in (issue.get("labels") or [])]
    tags = []
    for n in names:
        if n in ("P0", "P1", "P2"):
            out["priority"] = n
        elif n.startswith("track:"):
            out["track"] = n.split(":", 1)[1]
        elif n.startswith("stage:"):
            out["stage"] = n.split(":", 1)[1]
        elif n in ("type:bug", "type:enhancement", "type:task"):
            # ADT-73: recover the issue type from its label. Without this branch
            # a `type:bug` label fell through to `tags`, leaving out["type"]
            # unset — so a pulled/reconstructed Issue lost its type and
            # _cache_path_for's `or "tasks"` default silently bucketed it as a
            # task. Store the SINGULAR form (bug/enhancement/task);
            # _cache_path_for re-pluralises it to the folder name.
            out["type"] = n.split(":", 1)[1]
        elif n.startswith("gate:"):
            gate = n.split(":", 1)[1]
            for fm_key, g in GATE_KEYS.items():
                if g == gate:
                    out[fm_key] = True
        elif n == FILING_LABEL:
            continue  # sync machinery — never ticket metadata (see above)
        else:
            tags.append(n)
    if tags:
        out["tags"] = tags

    out["comments"] = [
        {"author": (c.get("author") or {}).get("login"),
         "at": c.get("createdAt"), "body": c.get("body")}
        for c in (issue.get("comments") or [])
    ]
    return out


if __name__ == "__main__":
    import sys
    text = sys.stdin.read()
    print(json.dumps(to_issue(parse_md(text)), indent=2))

# adt-bundle: v0.1.0
