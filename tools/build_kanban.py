#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
"""ADT — generic backlog Kanban generator.

Renders a backlog living under <backlog_root>/<type>/<status>/<slug>.md into a
markdown index, an HTML board, and per-ticket HTML pages. The filesystem
(folder = status) is the source of truth; this tool reflects it.

This is the PORTABLE engine (team-repo rule #1: never hard-code project paths).
Every project-specific value — repo root, backlog path, ID prefix, the
per-stage tooling reminders — comes from config, not constants. A consuming
project ships a ~15-line shim:

    from agent_dev_team.tools import build_kanban
    build_kanban.run(project_root="/path/to/proj", id_prefix="TIX",
                     stage_tools={...})

Or run directly:  python3 build_kanban.py --project-root /path/to/proj

No external dependencies. Frontmatter parser is intentionally small.
"""

from __future__ import annotations

import argparse
import shutil
import html
import re
import subprocess
from collections import defaultdict

# ADT-115: the pricer. One direction only — adt_cost never imports this module,
# so co-locating the ledger parser here and the money there stays acyclic.
import adt_cost
from datetime import datetime
from pathlib import Path

# ── Configurable state (set by configure()/run(); safe module defaults) ──
# Module globals so the render functions read them unchanged; configure()
# rebinds them per project before rendering.
REPO = Path(__file__).resolve().parent.parent
BL = REPO / ".adt" / "backlog"
REPO_URL = ""
ISSUES_URL = ""   # GitHub backlog-repo issues base, e.g. .../my-backlog/issues
BOARD_URL = ""    # GitHub Project board URL
# ADT-172: the repo (or project) the board belongs to, e.g. "owner/repo". Shown
# in the <h1> so a reader with two boards open can tell them apart. Cannot be
# derived here: under adt_watch, REPO is the CACHE dir, so repo_url(REPO) reads
# the cache's remote, and ISSUES_URL may name a dedicated backlog repo rather
# than the project. So the caller passes it. Empty -> the header keeps its
# generic "Project backlog" title.
REPO_NAME = ""
# GitHub API rate-limit snapshot for the header counter (📊 GraphQL a/b ·
# REST c/d API). A point-in-time reading captured by the caller (adt_watch)
# each render; the board is a 60s snapshot so a static value is fine.
# [(label, used, limit), …] — BOTH pools, GraphQL first (ADT-109: the command
# layer is REST-only now, so GraphQL flat + REST moving is the healthy shape
# and showing one pool would hide the confirmation). Empty → counter omitted.
RATE_POOLS: list = []
# Branch-protection snapshot for the header badge (🔒 main protected / 🔓 main
# unprotected). (branch, state) as adt_watch measured it this render, where
# state is "protected" | "unprotected". None → the badge is omitted entirely:
# an unreadable state degrades to ABSENT, never to a confident "protected",
# which is the same contract RATE_POOLS follows and for the same reason
# (ADT-165 exists because a control was reported as working while doing
# nothing).
BRANCH_PROTECTION: tuple | None = None
# ADT-384: the machines syncing this repo and the ADT each last reported, as
# tools/adt_machines.py wrote them to .adt/state/machines.json. Every value came
# from a GitHub comment, so every value is escaped. Empty → no footer.
MACHINES: list = []
# AO-006: the watcher's published validity window (epoch seconds) and the
# project name the agent is installed under. None/"" -> the sync pill is not
# rendered at all. NEVER default the window to 0: the page would read that as
# long expired and paint a board red that has simply never been stamped.
HEALTHY_UNTIL: float | None = None
# Matches the watcher's launchd StartInterval (60s). The board is regenerated
# every tick, so refreshing faster buys a re-read of an unchanged file and
# refreshing slower leaves the tab behind the cache it mirrors.
REFRESH_SECONDS = 60
ID_PREFIX = "TIX"

# {tix_id_upper: total_input_plus_output_tokens} — the cost-of-work per ticket,
# summed from the append-only ledger written by the adt-token-log Stop hook.
# Populated by load_token_usage() in run(); empty until then (so Item.tokens is
# None and the card renders "🪙 —"). See the cost-of-work notes.
TOKEN_USAGE: dict[str, int] = {}
# ADT-115: {tix_id_upper: {"micros": int|None, "tier": str}} — the same ledger,
# priced. Separate dict rather than a widened TOKEN_USAGE so every existing
# reader of TOKEN_USAGE keeps its exact shape.
COST_USAGE: dict[str, dict] = {}
# ADT-115: spend no card claims — `__unassigned__` turns (real work no ticket
# claimed) and ids whose cache file is gone. ADT-172 removed the header badge
# that displayed this; the figure is still computed, because it is the only way
# to tell that the board Σ plus what it cannot see reconciles to the ledger.
# `test_board_total_plus_offboard_reconciles_to_the_ledger` reads it directly.
OFFBOARD: dict = {}
# Where the ledger lives, relative to the project root. The Stop hook appends;
# the generator reads. Tolerated-absent (no hook installed yet → {} → "🪙 —").
TOKEN_LEDGER_REL = ".adt/state/cost-ledger.log"
# Pre-consolidation location — read too during the back-compat window so a board built
# while rows still exist at the old path doesn't show "🪙 —". Summed alongside the
# new ledger. No double-count: the migration copied legacy rows into the new
# ledger then TRUNCATED legacy, so legacy only holds post-cutover rows from
# old-hook sessions (absent from the new ledger). Re-running migration without
# re-truncating legacy WOULD double-count — see adt-token-sum.sh.
# ADT-115 / 5c: the file was `token-usage.log` until the rename. Two logs live
# in .adt-state/ and one name was a substring of the other — `usage.log` (which
# /adt-* command ran) vs `token-usage.log` (the money). `cost-ledger.log` says
# what it holds. Both older names stay READABLE forever: every install in the
# wild has one of them, and a reader that stopped seeing them would silently
# report a ticket as costless.
TOKEN_LEDGER_REL_PREV = ".adt/state/token-usage.log"
# The CODE checkout that owns the gitignored token ledger, set by the caller
# (adt_watch) when REPO is NOT a code tree. Empty → resolve from REPO as before.
# Needed because the cache-first model renders from the cache dir (REPO = cache), which sits
# under an unrelated parent git repo and has no .adt/ — so the ledger
# the adt-token-log hook writes (under the code checkout) is unreachable from
# REPO and every card shows "🪙 —". See the kanban-token-counts-dark fix.
TOKEN_LEDGER_ROOT = ""

TYPES = ["bugs", "enhancements", "tasks"]
TYPE_LABEL = {
    "bugs": "Bugs",
    "enhancements": "Enhancements",
    "tasks": "Tasks",
}
STATUSES = ["ideas", "planned", "building", "qa", "blocked",
            "ready-to-release", "done"]
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "": 3}

# Per-stage reminder of the standard ADT commands for that lane — the built-in
# default shown on every project's board. Project-agnostic: it names only the
# portable /adt-* commands + portable hooks, no project-specific reviewers. (A
# project could still override via configure(stage_tools=...), but the point of
# the default is that no project NEEDS to.)
STAGE_TOOLS = {
    "ideas": "/adt-brief",
    "planned": "/adt-plan · /adt-plan-fasttrack · /adt-diagnose",
    "building": "/adt-build · /adt-build-todone",
    "qa": "/adt-qa-run · /adt-review-security · /adt-review-designer",
    "blocked": "/adt-block · /adt-unblock",
    "ready-to-release": "/adt-release-check · /adt-review-critical-path",
    "done": "/adt-close",
}

# Display-label overrides for a stage. The stage KEY stays canonical everywhere
# (config labels, sync, stage-moves, the `.pill.status.<key>` CSS class); only
# the visible board text is overridden. Default: title-case the key.
STAGE_LABELS = {"planned": "Plan"}


def stage_label(s: str) -> str:
    """The visible label for a stage key — a STAGE_LABELS override, else the
    key title-cased (`ready-to-release` → `Ready To Release`)."""
    return STAGE_LABELS.get(s, s.replace("-", " ").title())


# The /adt-* names that are SKILLS (not slash commands) — coloured differently
# on the board. A project can override via configure(stage_skill_names=...).


def render_stage_tools(tools: str) -> str:
    """Render a STAGE_TOOLS string as invocable tokens.

    Every token in a lane header is something a human TYPES — a slash command or
    a human-invocable skill. It used to colour-code command / skill / agent /
    hook, but that taxonomy had no operational consequence for the reader: you
    invoke `/adt-diagnose` exactly the way you invoke `/adt-plan`. Hooks are no
    longer listed at all (ADT-214) because they are not invocable — they fire
    regardless, so naming them where an operator reads "what do I do here" was a
    category error. Agents were never listed, though the legend advertised them.
    One uniform token, no legend to explain.
    """
    out = []
    for raw in tools.split("·"):
        tok = raw.strip()
        if not tok:
            continue
        out.append(f'<span class="tok">{html.escape(tok)}</span>')
    return " ".join(out)


def repo_url(root: Path) -> str:
    """Best-effort GitHub URL for commit links. Falls back to '' if
    `git remote` isn't available or the origin isn't on GitHub."""
    try:
        import subprocess
        out = subprocess.check_output(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        # Normalise SSH (git@github.com:owner/repo.git) → https
        m = re.match(r"git@github\.com:(.+?)(?:\.git)?$", out)
        if m:
            return f"https://github.com/{m.group(1)}"
        # Strip optional `user[:token]@` from https URLs
        m = re.match(
            r"https://(?:[^@/]+@)?github\.com/(.+?)(?:\.git)?/?$", out,
        )
        if m:
            return f"https://github.com/{m.group(1)}"
    except Exception:
        pass
    return ""


# The references doc. COMMANDS_DOC_SRC is the markdown source (relative to
# project root) the generator reads. ADT-143: it is inlined into the board as a
# :target panel (with every document it links to), not rendered to a sibling
# references.html — the board must never depend on a local file:// navigation.
COMMANDS_DOC_SRC = "agent-dev-team/docs/references.md"
COMMANDS_DOC_URL = "#references"  # an inlined panel, NOT a file (ADT-143):
# a fragment performs no navigation, so a viewer that mangles file:// URLs has
# nothing to act on. Same mechanism ADT-136 used for ticket detail.


def configure(project_root, *, backlog_root=".adt/backlog",
              id_prefix="TIX", stage_tools=None, commands_doc_src=None,
              issues_url="", board_url="", rate_pools=None,
              token_ledger_root="", repo_name="", branch_protection=None,
              machines=None, healthy_until=None):
    """Rebind module config for a project. Call before render/run.

    issues_url / board_url (the cache-first migration): the GitHub backlog repo's issues base
    (e.g. https://github.com/owner/my-backlog/issues) and the Project
    board URL. Cards link to `<issues_url>/<n>`; the header links to the board.
    Distinct from REPO_URL (the CODE repo) because Issues live in the dedicated
    backlog repo, not the code repo.

    repo_name (ADT-172): the repo or project this board belongs to, e.g.
    "owner/repo". Rendered in the <h1> as "<repo_name> issues". Empty -> the
    generic "Project backlog" title.

    rate_pools: a GitHub API rate-limit snapshot for the header counter —
    [(label, used, limit), …], both pools, GraphQL first (📊 GraphQL a / b ·
    REST c / d API). None/empty → counter omitted.

    branch_protection (ADT-165): (branch, state) for the project's main branch,
    state being "protected" or "unprotected". None → the badge is omitted; an
    unreadable state must never render as a confident "protected".

    machines (ADT-384): the machine reports from .adt/state/machines.json, for
    the page footer. None/empty → no footer.

    healthy_until (AO-006): the epoch until which the pass that rendered this
    page vouches for it. The page compares it against the viewer's clock to
    colour the header's sync button. None → no button, because a board that
    cannot say whether it is live must not imply that it is.

    token_ledger_root: the CODE checkout that owns the gitignored token ledger,
    when REPO is NOT a code tree (the cache dir under adt_watch). The ledger is
    resolved from here instead of REPO so the renderer reads the same file the
    adt-token-log hook writes. Empty → resolve from REPO (single-checkout case)."""
    global REPO, BL, REPO_URL, ID_PREFIX, STAGE_TOOLS, ID_RE, COMMANDS_DOC_SRC
    global ISSUES_URL, BOARD_URL, RATE_POOLS, TOKEN_LEDGER_ROOT, REPO_NAME
    global BRANCH_PROTECTION, MACHINES, HEALTHY_UNTIL
    REPO = Path(project_root).resolve()
    TOKEN_LEDGER_ROOT = token_ledger_root
    BL = REPO / backlog_root
    REPO_URL = repo_url(REPO)
    ISSUES_URL = issues_url
    BOARD_URL = board_url
    RATE_POOLS = list(rate_pools or [])
    BRANCH_PROTECTION = branch_protection
    MACHINES = list(machines or [])
    HEALTHY_UNTIL = healthy_until
    REPO_NAME = repo_name
    ID_PREFIX = id_prefix
    ID_RE = re.compile(r"^" + re.escape(ID_PREFIX) + r"-(\d+)$")
    if stage_tools:
        STAGE_TOOLS = dict(stage_tools)
    if commands_doc_src:
        COMMANDS_DOC_SRC = commands_doc_src


# ── Frontmatter parsing ───────────────────────────────────────────────


def parse_frontmatter(text: str) -> dict:
    """Tiny YAML-frontmatter reader. Only handles flat `key: value`
    pairs between `---` lines, which is all our briefs use."""
    out: dict = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        # Loose style (e.g. `Status: idea` near top, no fences)
        for line in lines[:15]:
            m = re.match(r"^([A-Za-z_]+):\s*(.+?)\s*$", line.strip())
            if m:
                out.setdefault(m.group(1).lower(), m.group(2))
        return out
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_]+):\s*(.*?)\s*$", line)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


def extract_title(text: str) -> str:
    """Body H1 first; frontmatter `title:` as fallback (a consumer project:
    hand-written briefs often carry only frontmatter — the H1-fallback fix
    patched the file instead of this, so the class recurred)."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    fm = parse_frontmatter(text)
    title = (fm.get("title") or "").strip().strip('"').strip("'")
    return title or "(untitled)"


def extract_one_line_hook(text: str, max_chars: int = 180) -> str:
    """First non-empty paragraph after the title, trimmed."""
    seen_title = False
    buf: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not seen_title:
            if s.startswith("# "):
                seen_title = True
            continue
        if s.startswith("#"):
            # Hit the next heading
            if buf:
                break
            continue
        if s.startswith(">"):
            continue
        if s == "":
            if buf:
                break
            continue
        buf.append(s)
    txt = " ".join(buf)
    txt = re.sub(r"`([^`]+)`", r"\1", txt)
    if len(txt) > max_chars:
        txt = txt[: max_chars - 1].rstrip() + "…"
    return txt


def strip_frontmatter(text: str) -> str:
    """Return the body of a brief (everything after the closing
    `---` fence). If there's no fence, return the original text."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[i + 1 :]).lstrip()
    return text


# ── Minimal Markdown → HTML converter ────────────────────────────────
#
# Handles only what our briefs actually use:
#  - ATX headings (# .. ######)
#  - Fenced code blocks (``` … ```)
#  - Inline code (`x`)
#  - Bold (**x**) and italic (*x* / _x_)
#  - Links ([text](url))
#  - Bullet lists (- or *) and numbered lists
#  - Blockquotes (> …)
#  - GFM tables (| a | b |\n|---|---|\n| 1 | 2 |)
#  - Horizontal rules (---)
#  - Paragraphs
#
# Deliberately not a general-purpose parser; if a brief uses some
# edge case it'll just render as plain text.


# ADT-143. Local .md links are rewritten to the inlined panel that holds that
# document, so the board never depends on the reader following a file:// link.
# Populated by render_html from the catalogue it inlines; empty elsewhere (the
# standalone renderer path is gone).
DOC_FRAGMENTS: dict = {}


def doc_slug(rel: str) -> str:
    """`commands/plan.md` -> `doc-commands-plan`. Deterministic, so a doc added
    to the catalogue gets a panel id without anything being hand-maintained."""
    stem = re.sub(r"\.md$", "", rel, flags=re.IGNORECASE)
    return "doc-" + re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


def _is_local(url: str) -> bool:
    """A target this board would have to navigate the filesystem to reach."""
    return not url.startswith("#") and not re.match(r"[a-z]+:", url)


def _resolve_doc_fragment(url: str):
    """The panel fragment for a local .md link, or None.

    An EXACT lookup on the raw link text, keyed when the catalogue is read.
    An earlier version guessed by path tail and silently disagreed with the
    reader that built the panels — a bare `loops.md` was stored as
    `docs/loops.md` but looked up as `loops.md`, so two documents got panels
    nothing linked to. One map, written by one function, cannot drift."""
    if not _is_local(url):
        return None
    return DOC_FRAGMENTS.get(url)


def _inline(s: str) -> str:
    """Inline markdown, scanned LEFT TO RIGHT (ADT-143).

    Order matters and neither sequential-regex order is correct:
      - code-first swept the link regex over the `<code>` it had just emitted
        (`html.escape` leaves [ ] ( ) alone), so a link written inside backticks
        became a live anchor — that is how a ticket body put a real
        `../../agent-dev-team/commands/plan.md` href on the board;
      - splitting on code spans first tore apart links whose TEXT is a code
        span — ``[`loops.md`](…)`` — which is how the catalogue writes two of
        its rows, so those two silently stopped linking.
    Scanning left to right settles both by first-come: a backtick opens a code
    span that swallows any link syntax inside it, and a `[` opens a link whose
    text is then rendered for code spans in its own right.
    """
    def link_repl(text_html, raw):
        frag = _resolve_doc_fragment(raw)
        if frag:
            return f'<a href="{html.escape(frag, quote=True)}">{text_html}</a>'
        if _is_local(raw):
            # A local target with no panel — a doc outside the catalogue, or one
            # quoted in a ticket body. Render the TEXT, not a link: the board
            # carries no scheme-dependent local navigation, and a link the
            # reader's viewer may refuse to follow is worse than plain text.
            return text_html
        return f'<a href="{html.escape(raw, quote=True)}">{text_html}</a>'

    def code_only(text: str) -> str:
        return re.sub(r"`([^`]+)`",
                      lambda m: f"<code>{html.escape(m.group(1))}</code>", text)

    link_re = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
    out, k = [], 0
    while k < len(s):
        ch = s[k]
        if ch == "`":
            end = s.find("`", k + 1)
            if end != -1:
                out.append(f"<code>{html.escape(s[k + 1:end])}</code>")
                k = end + 1
                continue
        elif ch == "[":
            m = link_re.match(s, k)
            if m:
                out.append(link_repl(code_only(m.group(1)), m.group(2)))
                k = m.end()
                continue
        out.append(ch)
        k += 1
    out = "".join(out)

    # Bold **x**
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    # Italic *x* (avoid **) and _x_
    out = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"<em>\1</em>", out)
    return out


def _is_table_separator(line: str) -> bool:
    s = line.strip()
    if not s.startswith("|"):
        return False
    cells = [c.strip() for c in s.strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-+:?", c) for c in cells if c)


def md_to_html(text: str) -> str:
    """Convert a brief's markdown body to HTML. Block-level pass with
    inline formatting applied within each block. Returns HTML string
    with no surrounding <body> wrapper."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Fenced code block
        if stripped.startswith("```"):
            i += 1
            code: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # consume closing fence
            out.append(
                "<pre><code>"
                + html.escape("\n".join(code))
                + "</code></pre>"
            )
            continue

        # Horizontal rule
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped):
            out.append("<hr>")
            i += 1
            continue

        # ATX heading
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue

        # Blockquote (multi-line, > prefix)
        if stripped.startswith(">"):
            quoted: list[str] = []
            while i < n and lines[i].lstrip().startswith(">"):
                quoted.append(lines[i].lstrip()[1:].lstrip())
                i += 1
            body = md_to_html("\n".join(quoted))
            out.append(f"<blockquote>{body}</blockquote>")
            continue

        # Table — header row + separator + data rows
        if (
            stripped.startswith("|")
            and i + 1 < n
            and _is_table_separator(lines[i + 1])
        ):
            def row_cells(row: str) -> list[str]:
                return [
                    _inline(c.strip())
                    for c in row.strip().strip("|").split("|")
                ]

            header_cells = row_cells(lines[i])
            i += 2  # skip header + separator
            body_rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                body_rows.append(row_cells(lines[i]))
                i += 1
            thead = (
                "<thead><tr>"
                + "".join(f"<th>{c}</th>" for c in header_cells)
                + "</tr></thead>"
            )
            tbody = "<tbody>" + "".join(
                "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
                for row in body_rows
            ) + "</tbody>"
            # The wrapper scrolls a table too wide for the panel instead of
            # letting it spill past the panel edge (see .table-wrap in TICKET_CSS).
            out.append(f'<div class="table-wrap"><table>{thead}{tbody}</table></div>')
            continue

        # Bullet list — supports multi-line items (continuation lines
        # that don't start a new marker get joined to the previous one)
        if re.match(r"^[-*]\s+", stripped):
            items: list[str] = []
            while i < n:
                ln = lines[i]
                s = ln.strip()
                if s == "":
                    # Peek: does the next non-blank line continue the list?
                    j = i + 1
                    while j < n and lines[j].strip() == "":
                        j += 1
                    if j < n and re.match(r"^[-*]\s+", lines[j].strip()):
                        i = j
                        continue
                    break
                if re.match(r"^[-*]\s+", s):
                    items.append(re.sub(r"^[-*]\s+", "", s))
                    i += 1
                elif items and not re.match(
                    r"^(#|```|>|\||\d+\.\s)", s,
                ):
                    items[-1] = items[-1] + " " + s
                    i += 1
                else:
                    break
            out.append(
                "<ul>"
                + "".join(f"<li>{_inline(it)}</li>" for it in items)
                + "</ul>"
            )
            continue

        # Numbered list — same multi-line handling
        if re.match(r"^\d+\.\s+", stripped):
            items: list[str] = []
            while i < n:
                ln = lines[i]
                s = ln.strip()
                if s == "":
                    j = i + 1
                    while j < n and lines[j].strip() == "":
                        j += 1
                    if j < n and re.match(r"^\d+\.\s+", lines[j].strip()):
                        i = j
                        continue
                    break
                if re.match(r"^\d+\.\s+", s):
                    items.append(re.sub(r"^\d+\.\s+", "", s))
                    i += 1
                elif items and not re.match(
                    r"^(#|```|>|\||[-*]\s)", s,
                ):
                    items[-1] = items[-1] + " " + s
                    i += 1
                else:
                    break
            out.append(
                "<ol>"
                + "".join(f"<li>{_inline(it)}</li>" for it in items)
                + "</ol>"
            )
            continue

        # Blank line → block separator
        if stripped == "":
            i += 1
            continue

        # Paragraph — collect consecutive non-blank lines until a
        # blank or a block-level marker.
        #
        # The current line always goes in, whatever it starts with. Every
        # branch above that could claim it has already declined, and if the
        # loop below were allowed to break on it first, `i` would not move
        # and the outer loop would read the same line forever. A `|` line
        # with no table separator after it did exactly that, hanging the
        # render and the sync pass holding its lock (#363).
        para: list[str] = [stripped]
        i += 1
        while i < n:
            ln = lines[i]
            s = ln.strip()
            if (
                s == ""
                or re.match(r"^#{1,6}\s+", s)
                or s.startswith("```")
                or s.startswith(">")
                or s.startswith("|")
                or re.match(r"^[-*]\s+", s)
                or re.match(r"^\d+\.\s+", s)
                or re.fullmatch(r"-{3,}|\*{3,}|_{3,}", s)
            ):
                break
            para.append(s)
            i += 1
        out.append(f"<p>{_inline(' '.join(para))}</p>")

    return "\n".join(out)


# ── Loading items ─────────────────────────────────────────────────────


def _type_from_fm(fm: dict, folder_type: str) -> str:
    """The ticket's type: its `type:` frontmatter when that names a real type,
    otherwise the folder it was loaded from (ADT-155).

    Accepts both the singular form the schema specifies (`bug`) and the plural
    the folders use (`bugs`), case- and whitespace-insensitively. Anything
    else — absent, blank, `weird` — falls back to `folder_type`, so an authoring
    typo degrades to the old folder-derived behaviour instead of breaking the
    render. That fallback is load-bearing: render_ticket_detail indexes a
    three-key dict by Item.type, and an unrecognised value there would raise
    KeyError and take the whole board down — a blank page, not a wrong pill.
    """
    raw = (fm.get("type") or "").strip().lower()
    if not raw:
        return folder_type
    plural = raw + "s" if raw in ("bug", "enhancement", "task") else raw
    return plural if plural in TYPES else folder_type


_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(T\d{2}:\d{2}:\d{2}Z)?$")


def _norm_ts(v) -> str:
    """A frontmatter date/timestamp -> a fixed-width `YYYY-MM-DDTHH:MM:SSZ`, or
    "" if it is not one (ADT-172).

    The recency sort compares these values reverse-lexicographically, which is
    only correct when every value has the SAME SHAPE. Frontmatter admits three:
    a full ISO-Z stamp (what the sync writes), a bare `YYYY-MM-DD` (what a
    hand-written brief carries), and the literal string `null` — `parse_frontmatter`
    is a flat text reader, so a YAML `null` arrives as `"null"`, which the
    `or`-chain below then accepts as truthy. Unnormalised, a bare date sorts
    AHEAD of a same-day timestamp (its tuple is shorter, so it compares first)
    and `"null"` sorts near the top (`-ord('n')` is very negative).

    A bare date means start-of-day, so it pads to `T00:00:00Z` and correctly
    follows a timestamped brief from the same day. Anything unparseable is ""
    and sorts last, which is where a brief with no usable date belongs."""
    m = _TS_RE.match((v or "").strip())
    if not m:
        return ""
    return m.group(1) + (m.group(2) or "T00:00:00Z")


class Item:
    def __init__(self, path: Path, type_: str, status: str):
        self.path = path
        self.status = status
        self.text = path.read_text()
        self.fm = parse_frontmatter(self.text)
        # ADT-155: `type:` frontmatter decides the type, not the folder — the
        # REVERSE of the `stage` convention (see sync_stage_frontmatter, and the
        # schema doc's two sections on this). Stage can be folder-derived
        # because stage HAS a mover: every playbook `mv`s the file across
        # lanes on every transition, so the folder is always current. Type never
        # had one — nothing in adt_sync relocates a ticket between type folders
        # (_cache_path_for runs only on the pull's reconstruct branch), so a
        # backlog adopted from type-less Issues collapsed into tasks/ and could
        # never be corrected no matter how the operator edited the frontmatter.
        # The folder is now storage; _cache_path_for still buckets NEW files by
        # this same field, so the two agree for anything created after adoption.
        self.type = _type_from_fm(self.fm, type_)
        self.slug = self.fm.get("slug", path.stem)
        self.priority = (self.fm.get("priority") or "").upper()
        self.size = (self.fm.get("size") or "").upper()
        # Impact tier (the impact-tier work): which lifecycle path the ticket takes.
        #   fast     — copy/doc/rename-no-callsite/config: brief→build→done
        #   standard — normal feature/bug: plan→build→qa→done (default)
        #   full     — guarded path (trade/money, schema, ui_review): no gate skipped
        # Unknown/blank values fall back to standard (never crash the board).
        track = (self.fm.get("track") or "standard").strip().lower()
        self.track = track if track in ("fast", "standard", "full") else "standard"
        self.title = extract_title(self.text)
        self.hook = extract_one_line_hook(self.text)
        self.closed = self.fm.get("closed", "")
        # When the brief last changed. Prefer an explicit `updated:`
        # field; fall back to closed date, then created date. Used to
        # sort each lane most-recently-touched first.
        #
        # ADT-172: each candidate is normalised BEFORE the fallback chain, not
        # after. Normalising after would let a literal `"null"` (or any other
        # non-date) win the `or` and resolve to "", masking a real `created:`
        # further down the chain. Per-candidate, an unusable value falls
        # through to the next one, which is what the chain is for.
        # ADT-322: a repaired date replaces `updated:` for a ticket nobody has
        # touched since the backlog was adopted. The adoption stamped
        # `updatedAt` on every Issue with the install time, so for an adopted
        # backlog `updated:` says "today" for tickets nobody has touched in
        # months and the recency sort means nothing. Absent a table entry the
        # chain is exactly what it was.
        #
        # ADT-381: but only until the ticket is edited again. The table records
        # the stamp it replaced, and the sync writes the same GitHub field into
        # `updated:`, so the two are the same clock at two moments: `updated:`
        # LATER than the stamp is an edit the reconstruction never saw, and the
        # repaired date is then the stale one. Equal means untouched, so the
        # repaired date stands — which is why this is not "the later of the
        # two": for an untouched ticket `updated:` IS the stamp, later than the
        # repaired date, and preferring it would undo the whole repair. No
        # usable stamp (ticket absent from the table, empty or unparseable
        # column) behaves as it did before.
        issue_no = _parse_int(self.fm.get("issue_number"))
        repaired = _norm_ts(_last_activity_table().get(issue_no))
        stamp = _norm_ts(_adoption_stamps().get(issue_no))
        updated = _norm_ts(self.fm.get("updated"))
        if repaired and stamp and updated > stamp:
            repaired = ""
        self.updated = (
            repaired
            or updated
            or _norm_ts(self.fm.get("closed"))
            or _norm_ts(self.fm.get("created"))
            or ""
        )
        self.commits = [
            c.strip() for c in (self.fm.get("commits") or "").split(",")
            if c.strip()
        ]
        self.id = (self.fm.get("id") or "").strip().upper()
        # Cost-of-work in tokens (input+output). the cost-of-work work, phase 2 precedence:
        # a frontmatter `tokens:` value (stamped by /adt-close at done-
        # time) WINS — it's the durable, git-tracked snapshot that survives a
        # ledger prune or a fresh clone. Only when no such field is present
        # (active tickets, or pre-phase-2 done tickets) do we fall back to the
        # live ledger sum. None when neither exists → renders "🪙 —".
        stamped = _parse_int(self.fm.get("tokens"))
        if stamped is not None:
            self.tokens: int | None = stamped
        else:
            ledger = TOKEN_USAGE.get(canon_tix(self.id)) if self.id else None
            # ADT-100: fold in per-machine register comments (pulled into the
            # cache file by the sync's reconstruct pass). Own machine's
            # register and the local ledger cover the SAME spend at different
            # freshness — max() dedupes them (register == last checkpointed
            # sum ≤ ledger normally; ≥ ledger after a prune). Other machines'
            # registers are spend this ledger never saw — they add.
            registers = _register_totals(self.text)
            if registers:
                own = registers.pop(OWN_MACHINE, 0)
                self.tokens = max(ledger or 0, own) + sum(registers.values())
            else:
                self.tokens = ledger
        # ADT-115: the same cost-of-work, in money. Precedence mirrors .tokens
        # exactly — a stamped `cost_usd:` (written by /adt-close) wins, because
        # it is the durable git-tracked snapshot that survives a ledger prune;
        # otherwise price the live ledger and fold in other machines' cost
        # registers. `cost_tier` travels with the number so the badge can mark
        # an estimate; it is NEVER inferred from the value.
        stamped_cost = self.fm.get("cost_usd")
        self.cost_tier: str = str(self.fm.get("cost_tier") or "measured")
        self.cost: int | None = None
        if stamped_cost is not None and str(stamped_cost).strip() not in (
                "", "unattributed", "None"):
            try:
                self.cost = int(round(float(str(stamped_cost).replace(
                    "$", "").replace(",", "")) * 1_000_000))
            except (TypeError, ValueError):
                self.cost = None
        else:
            entry = COST_USAGE.get(canon_tix(self.id)) if self.id else None
            local = entry.get("micros") if entry else None
            if entry:
                self.cost_tier = entry.get("tier") or "measured"
            cregs = _cost_totals(self.text)
            if cregs:
                own = cregs.pop(OWN_MACHINE, None)
                own_micros = own[0] if own else 0
                total = max(local or 0, own_micros)
                for micros, tier in cregs.values():
                    total += micros
                    if tier != "measured":
                        self.cost_tier = tier
                # A register total of 0 with no local figure is not "free" —
                # it is "nothing recorded". Collapse it back to None so the
                # badge shows "—" (ADT-72), the same rule the ledger path uses.
                self.cost = total if (total or local is not None) else None
            else:
                self.cost = local
        # A ticket with no cost is NOT "measured" — leaving the default tier on
        # an unpriced card let the header Σ report a confident total over a
        # board containing unpriced cards. The tier has to describe the number
        # that is actually there, and when there is no number that is
        # `unattributed` (ADT-72).
        if self.cost is None:
            self.cost_tier = "unattributed"
        # GitHub Issue number (the cache-first migration): set by the sync. Card links to it.
        self.issue_number = _parse_int(self.fm.get("issue_number"))

    @property
    def rel_path(self) -> str:
        return str(self.path.relative_to(BL))

    @property
    def sort_key(self) -> tuple:
        return (PRIORITY_ORDER.get(self.priority, 5), self.slug)

    @property
    def recency_ts(self) -> str:
        """The timestamp the lane orders by, and the one the card chip shows.

        Everywhere but `done` that is `updated` — the lane is a work queue, so
        "last touched" is the useful order. In `done` it is `closed`, because
        the lane is a record of when things were FINISHED and `updated` moves
        for reasons that have nothing to do with the ticket: ADT-174 added a
        slug trailer to every Issue body, GitHub bumped every `updated_at`, and
        the whole done lane re-ordered itself to that afternoon (tickets closed
        in June sat at the top). `closed` is written once, by the close, and no
        later edit of the text can move it.

        Falls back to `updated` when `closed` is absent so a done card is never
        undated; the sync now pulls `closed` onto existing files, so that
        fallback is for a ticket parked in `done/` whose Issue is still open.
        """
        if self.status == "done":
            return _norm_ts(self.fm.get("closed")) or self.updated
        return self.updated

    @property
    def recency_sort_key(self) -> tuple:
        # Most-recently-touched first within a lane (most-recently-CLOSED in
        # `done` — see recency_ts). Values are normalised fixed-width ISO, so
        # reverse-lexicographic == reverse-chronological. Undated briefs sort
        # last; ties break by priority then slug for a stable order.
        ts = self.recency_ts
        return (
            ts == "",                      # dated briefs (False) before undated (True)
            tuple(-ord(c) for c in ts),    # newer date first
            PRIORITY_ORDER.get(self.priority, 5),
            self.slug,
        )


def load_items() -> list[Item]:
    items: list[Item] = []
    for t in TYPES:
        for s in STATUSES:
            d = BL / t / s
            if not d.exists():
                continue
            for f in sorted(d.glob("*.md")):
                items.append(Item(f, t, s))
    return items


def _parse_int(v) -> int | None:
    """Coerce a frontmatter value to a non-negative int, or None if absent/
    unparseable. Accepts an int, or a string like "62167" / "62,167". A blank,
    None, or garbage value returns None so the caller falls back to the ledger.
    Used for the stamped `tokens:` field (the cost-of-work work, phase 2)."""
    if v is None:
        return None
    if isinstance(v, bool):  # guard: bool is an int subclass, not a token count
        return None
    if isinstance(v, int):
        return v if v >= 0 else None
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        n = int(s)
    except ValueError:
        return None
    return n if n >= 0 else None


def _canonical_root() -> Path:
    """The checkout that owns the gitignored runtime ledger.

    In a linked worktree, `git rev-parse --git-common-dir` points at the main
    repo's `.git`, whose parent is the canonical tree; in a plain checkout it
    resolves to that same tree. Mirrors the resolution in adt-token-log.sh /
    adt-token-sum.sh (the canonical-ledger fix) so the engine reads the *same* ledger the hook
    writes. Falls back to REPO on any error or if the resolved dir has no
    .adt/ (so a non-git build still renders). See the cost-of-work work, phase 3.

    When TOKEN_LEDGER_ROOT is set (adt_watch renders from the cache dir, so REPO
    is NOT a code tree), resolve from that code checkout instead of REPO — the
    cache dir sits under an unrelated parent git repo with no .adt/,
    which would otherwise fall back to the cache dir where the ledger is absent.
    See the kanban-token-counts-dark fix."""
    base = Path(TOKEN_LEDGER_ROOT).resolve() if TOKEN_LEDGER_ROOT else REPO
    try:
        common = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if common:
            common_path = Path(common)
            if not common_path.is_absolute():
                common_path = base / common_path
            canonical = common_path.parent.resolve()
            if (canonical / ".adt").is_dir():
                return canonical
    except (subprocess.SubprocessError, OSError):
        pass
    # With an explicit code root that itself holds the ledger dir but isn't git
    # (or git-common-dir didn't resolve), prefer it over REPO.
    if TOKEN_LEDGER_ROOT and (base / ".adt").is_dir():
        return base
    return REPO


# ADT-100: the per-machine token register — ONE Issue comment per (machine,
# ticket), written by adt_sync.checkpoint_tokens and pulled into cache files
# by its reconstruct pass. Writer template + parser regex are co-located here
# so a format tweak can't silently orphan the parser (adt_sync imports both —
# same direction as canon_tix). SINGLE-LINE by contract (the frontmatter
# comment scalar is one line), so a raw-text grep is sufficient —
# parse_frontmatter() is flat-only and never sees dictlist entries.
TOKENS_MARKER_RE = re.compile(r"<!-- adt:tokens machine=(\S+) total=(\d+) -->")


# ADT-115: the COST register is a SECOND, adjacent marker — deliberately NOT a
# widened adt:tokens one. TOKENS_MARKER_RE above anchors `-->` immediately after
# `total=(\d+)`, so a field added inside that marker makes every machine still
# running the old parser stop matching it entirely — the register would go dark
# cross-machine. Two markers is strictly additive: old parsers keep reading the
# first, new parsers read both. `tier` rides along so a machine reading someone
# else's register knows whether that number was measured or estimated.
COST_MARKER_RE = re.compile(
    r"<!-- adt:cost machine=(\S+) micros=(\d+) tier=(\w+) -->")


def register_body(machine: str, total: int, micros=None,
                  tier: str = "measured") -> str:
    """The register comment — SINGLE line (see block comment above).

    `micros`/`tier` are optional so an older caller that passes only a token
    total still produces exactly the pre-ADT-115 string, byte for byte."""
    body = (f"<!-- adt:tokens machine={machine} total={total} --> "
            f"\U0001fa99 {total:,} tokens (input+output) spent on machine "
            f"`{machine}` — approximate; maintained by adt watch (ADT-100).")
    if micros is not None:
        approx = "" if tier == "measured" else "~"
        body += (f" <!-- adt:cost machine={machine} micros={micros} "
                 f"tier={tier} --> \U0001f4b5 {approx}"
                 f"${micros / 1_000_000.0:,.2f} at list rates (ADT-115).")
    return body


def load_cost_usage(root=None) -> dict:
    """The ledger, priced -> {canon_tix: {"micros": int|None, "tier": str}}.

    Reads the SAME two ledger paths load_token_usage() reads, so tokens and
    cost can never disagree about which rows exist. Returns {} when no ledger
    is present, which renders "$ —" rather than "$0.00" (ADT-72)."""
    root = Path(root) if root else _canonical_root()
    lines: list[str] = []
    for rel in (TOKEN_LEDGER_REL, TOKEN_LEDGER_REL_PREV):
        try:
            lines.extend((root / rel).read_text().splitlines())
        except (FileNotFoundError, OSError):
            continue
    if not lines:
        return {}
    return adt_cost.price_ledger(lines, adt_cost.load_prices(),
                                 adt_cost.load_estimator(root))


def _cost_totals(text: str) -> dict:
    """{machine: (micros, tier)} from cost markers in a ticket FRONTMATTER
    block — same scoping rule and same duplicate-keeps-max rule as
    _register_totals, for the same reasons."""
    fm_block = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm_block = text[:end]
    out: dict = {}
    for m, micros, tier in COST_MARKER_RE.findall(fm_block):
        n = int(micros)
        if n > out.get(m, (-1, ""))[0]:
            out[m] = (n, tier)
    return out


def canonical_root(start=None) -> Path:
    """The CANONICAL checkout, not a linked worktree.

    `.adt-state/` is gitignored, so it exists only in the canonical tree — a
    worktree has none. Every consumer of machine-local state has to agree on
    this or they read different files; the hooks already resolve it this way
    (`adt-token-log.sh`: `--git-common-dir`, then its parent), and this is the
    python half of the same rule.
    """
    import subprocess
    base = Path(start) if start else REPO
    try:
        common = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        if common:
            p = Path(common)
            if not p.is_absolute():
                p = base / p
            root = p.parent.resolve()
            if (root / ".adt").is_dir():
                return root
    except Exception:
        pass
    return base


def machine_id(start=None) -> str:
    """The install's ANONYMOUS id — the register PARTITION KEY (ADT-100/ADT-254).

    A random UUID READ from `.adt/state/install-id`, minted
    once by whoever gets there first. NOT a hostname: `platform.node()` routinely
    carries a person's name, and `adt_phone_home.install_id`'s docstring already
    rejected that precedent for telemetry — "a fingerprint is what makes
    telemetry read as tracking". The cost ledger was the precedent it was
    rejecting; this closes it.

    READ, not derived, and that is the point. Three places used to compute the
    hostname independently — this function, `adt_sync._machine_id` (which imports
    it), and an embedded python mirror inside `adt-token-total.sh` — and the old
    docstring warned that if writer and reader ever diverged, own spend
    double-counts. A shared file cannot diverge, so the hazard stops existing
    rather than being duplicated into a third form.

    Resolved against the CANONICAL checkout: `.adt-state/` is gitignored and
    absent from every linked worktree, so keying off this module's own path
    would mint a fresh identity per worktree and shatter one machine into many.
    """
    path = canonical_root(start) / ".adt" / "state" / "install-id"
    try:
        existing = path.read_text().strip()
        if existing:
            return existing
    except OSError:
        pass
    import uuid
    new_id = str(uuid.uuid4())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_id + "\n")
    except OSError:
        pass          # fail-open: an unwritable state dir must not break a render
    return new_id


OWN_MACHINE = machine_id()


def _register_totals(text: str) -> dict[str, int]:
    """{machine: total} from register markers in a ticket's FRONTMATTER block
    (where pulled comments live). Scoped there on purpose: a ticket BODY may
    quote a register comment (build logs quote Issue comments) and a body-wide
    grep would add phantom machines. A machine appearing twice (shouldn't
    happen — the sync PATCHes one comment in place) keeps the larger value,
    never double-counts."""
    fm_block = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm_block = text[:end]
    out: dict[str, int] = {}
    for machine, total in TOKENS_MARKER_RE.findall(fm_block):
        n = int(total)
        if n > out.get(machine, -1):
            out[machine] = n
    return out


def canon_tix(tix: str) -> str:
    """Canonicalise a ticket id so spelling variants key to ONE bucket.

    The number is normalised free of leading zeros (`ADT-058` → `ADT-58`) and
    the whole id upper-cased. This is the single point that makes the ledger
    padding-insensitive: writers have stored both `ADT-58` (from a prompt that
    typed it short) and `ADT-058` (zero-padded canonical id) for the SAME
    ticket, and an exact string match billed them to different cards / showed
    `🪙 —` on the one the close-helper queried (ADT-58 incident, 2026-06-26 —
    the recurring token-attribution failure class). Normalising at every read
    AND write retroactively heals already-split rows without rewriting the
    ledger. Non-`PREFIX-NNN` ids (e.g. `__unassigned__`) pass through upper-cased
    unchanged."""
    s = tix.strip().upper()
    m = re.fullmatch(r"([A-Z]+)-0*([0-9]+)", s)
    return f"{m.group(1)}-{m.group(2)}" if m else s


def load_token_usage(root=None) -> dict[str, int]:
    """Parse the append-only token ledger → {tix_id_upper: input+output}.

    `root`: explicit project root owning the ledger (str or Path). Default
    resolves via _canonical_root() for the render path; adt_sync passes its
    project_root so the checkpoint pass reuses THIS parser instead of a
    fourth copy (ADT-100), scanning the ledger once per pass.

    Ledger format (TSV, written by adt-token-log.sh) — 11 columns since
    ADT-115, one row per (turn, model, speed) group:
        ISO8601 <TAB> tix-id <TAB> input <TAB> output <TAB> session_id
          <TAB> model <TAB> speed <TAB> cache_read <TAB> cw_5m <TAB> cw_1h
          <TAB> tier
    This function reads columns 3 and 4 only, and the ADT-115 columns were
    APPENDED, so it is unchanged by the widening and still parses a 5-column
    row. The money lives in load_cost_usage(); tokens and cost read the same
    two files so they can never disagree about which rows exist.

    Rows are summed by tix-id (a ticket worked over many turns/sessions
    accumulates). Malformed rows are skipped, never fatal — the board must
    render even if the hook wrote garbage. Unattributed turns carry the literal
    tix-id `__unassigned__` and are summed under that key like any other (the
    render decides how to surface them). Returns {} when the ledger is absent
    (no hook installed yet), so every card shows "🪙 —". See the cost-of-work notes.

    Ledger location: the hook (adt-token-log.sh) and the close-helper
    (adt-token-sum.sh) both write/read it under the *canonical* checkout —
    `git-common-dir`'s parent — because the ledger is gitignored runtime
    state that exists only there, never in a linked worktree. The engine
    must resolve it the same way, or a board generated from a worktree reads
    an empty/absent ledger and every card shows "🪙 —" / "Σ 0" even though
    the canonical ledger is full. See the cost-of-work work, phase 3 / the canonical-ledger fix."""
    root = Path(root) if root else _canonical_root()
    totals: dict[str, int] = defaultdict(int)
    found = False
    # Read the new ledger and (the .adt-state consolidation) the legacy one, summing both.
    for rel in (TOKEN_LEDGER_REL, TOKEN_LEDGER_REL_PREV):
        try:
            text = (root / rel).read_text()
        except (FileNotFoundError, OSError):
            continue
        found = True
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue  # malformed — skip, don't crash
            tix = canon_tix(parts[1])
            if not tix:
                continue
            try:
                totals[tix] += int(parts[2]) + int(parts[3])
            except (ValueError, IndexError):
                continue  # non-numeric token counts — skip the row
    if not found:
        return {}
    return dict(totals)


def _offboard_spend(items) -> dict:
    """Ledger spend that no card claims, for the reconciliation invariant.

    Counts every priced ledger id that no Item claims. `__unassigned__` is the
    bulk of it by design (the attribution hooks pool there when no signal
    resolves); the rest are ids whose cache file has since gone. Both are real
    money. The board does not display them (ADT-172 removed that badge), so
    this figure exists to keep them measurable rather than visible."""
    carded = {canon_tix(it.id) for it in items if it.id}
    micros, tokens, ids, tier = 0, 0, [], "measured"
    RANK = {"measured": 0, "estimated": 1, "legacy": 2}
    for tix, acc in COST_USAGE.items():
        if tix in carded:
            continue
        tokens += acc.get("tokens") or 0
        ids.append(tix)
        if acc.get("micros"):
            micros += acc["micros"]
        t = acc.get("tier") or "measured"
        if RANK.get(t, 1) > RANK[tier]:
            tier = t
    return {"micros": micros or None, "tokens": tokens, "tier": tier,
            "ids": sorted(ids)}


def fmt_tokens(n: int | None) -> str:
    """Compact token count for the kanban badge: None → "—", <1000 → "820",
    <1e6 → "1.2k" / "184k", else "1.3M". One decimal only when it adds signal
    (1.2k, not 12.0k → "12k"). See the cost-of-work notes."""
    if n is None:
        return "—"
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        v = n / 1000
        return (f"{v:.1f}k" if v < 10 else f"{round(v)}k")
    v = n / 1_000_000
    return (f"{v:.1f}M" if v < 10 else f"{round(v)}M")


# ID_PREFIX is set at top + rebound by configure(); ID_RE follows it.
ID_RE = re.compile(r"^" + re.escape(ID_PREFIX) + r"-(\d+)$")


# NOTE: the old `_next_id_number` (highest-existing + 1) is gone. It minted ids
# from a LOCAL counter, which forced `#N == <PREFIX>-N` and a virgin repo (see
# ADT-9). The id is now DERIVED from the GitHub issue number, so a ticket filed
# as issue #31 is <PREFIX>-31 regardless of how many tickets exist — letting ADT
# drop into any repo whose issue numbers are already past 1.


def _write_id_into_frontmatter(item: Item, new_id: str) -> None:
    """Set `id: TIX-NNN` in the frontmatter of `item`'s file and update
    in-memory state. REPLACES an existing `id:` line in place if present
    (e.g. a `<PREFIX>-PENDING` placeholder, issue #72) — otherwise inserts
    one before the closing fence. Replacing rather than appending is
    load-bearing: appending alongside a placeholder id would leave TWO
    `id:` lines, which is the duplicate-id failure class the frontmatter
    parser mints from."""
    text = item.text
    lines = text.splitlines(keepends=False)
    # We require fenced frontmatter (--- ... ---). If absent we'd be
    # editing the body, so refuse.
    if not lines or lines[0].strip() != "---":
        raise ValueError(
            f"Cannot assign id: {item.path} has no fenced frontmatter."
        )
    # Find the closing fence.
    close_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            close_idx = i
            break
    if close_idx is None:
        raise ValueError(
            f"Cannot assign id: {item.path} frontmatter has no closing fence."
        )
    # If an `id:` line already exists within the frontmatter, replace it in
    # place (a placeholder being upgraded to the canonical id). Otherwise
    # insert just before the closing fence.
    id_idx = None
    for i in range(1, close_idx):
        if re.match(r"^id:\s", lines[i]):
            id_idx = i
            break
    if id_idx is not None:
        new_lines = lines[:]
        new_lines[id_idx] = f"id: {new_id}"
    else:
        new_lines = lines[:close_idx] + [f"id: {new_id}"] + lines[close_idx:]
    new_text = "\n".join(new_lines)
    if text.endswith("\n"):
        new_text += "\n"
    item.path.write_text(new_text)
    # Re-read so the Item is internally consistent.
    item.text = new_text
    item.fm["id"] = new_id
    item.id = new_id


def assign_missing_ids(items: list[Item]) -> int:
    """Derive `id` from the GitHub issue number for any synced brief missing one
    (ADT-9). `id := <PREFIX>-<issue_number>` — the issue number is the single
    source of identity, so a ticket filed as issue #31 is <PREFIX>-31. Writes
    back to disk. Returns the count of newly-derived ids.

    A ticket with NO issue_number yet (filed locally, not synced) is left
    unlabelled — its id is stamped by the sync (reconcile_one) the moment its
    Issue is created. The board shows such a ticket without a number until then.

    "No id yet" includes a non-canonical PLACEHOLDER id, not only an absent
    field (issue #72): a brief filed with `id: <PREFIX>-PENDING` (or any value
    that isn't the canonical `PREFIX-<int>`) is truthy, so the original
    `if it.id` guard skipped it forever — the issue number was synced but the id
    was never derived, stranding it at the placeholder. Any id that doesn't match
    `PREFIX-<int>` is treated as missing and backfilled from issue_number.
    """
    derived = 0
    canonical_id = re.compile(rf"^{re.escape(ID_PREFIX)}-\d+$")
    for it in items:
        if (it.id and canonical_id.match(it.id)) or not it.issue_number:
            continue
        new_id = f"{ID_PREFIX}-{it.issue_number:03d}"
        _write_id_into_frontmatter(it, new_id)
        derived += 1
    return derived


def sync_stage_frontmatter(items: list["Item"]) -> int:
    """Folder is the single source of truth for status; `stage:` frontmatter is
    a derived mirror (queryable + portable). For each brief this:
      - sets `stage:` to the folder value (insert if missing, **correct** a
        stale value — e.g. after you `mv` a brief between cache folders),
      - keeps `state:` consistent with the stage: `done`/`cancelled` -> closed,
        everything else -> open. (cache-first: `state:` is no longer a legacy
        orchestrator alias to drop — it is the open/closed GitHub-Issue state in
        the field-map, so it is preserved and kept in lockstep with the stage.)
    Folder always wins — moving the file is how you change status, and the
    generator brings `stage:`/`state:` into line. (No fail-loud: a stage `mv`
    legitimately leaves them stale until the next run, indistinguishable from a
    hand-edit, so silently correcting to the folder is the safe rule.)
    Returns the count of briefs whose frontmatter changed."""
    changed = 0
    for it in items:
        lines = it.text.splitlines(keepends=False)
        if not lines or lines[0].strip() != "---":
            raise ValueError(f"{it.path}: no fenced frontmatter.")
        close_idx = next((i for i, l in enumerate(lines[1:], start=1)
                          if l.strip() == "---"), None)
        if close_idx is None:
            raise ValueError(f"{it.path}: frontmatter has no closing fence.")

        # Desired state from the folder (+ explicit cancelled override).
        is_cancelled = (it.fm.get("status") == "cancelled")
        want_state = "closed" if (it.status == "done" or is_cancelled) else "open"

        new_fm = []
        saw_stage = False
        saw_state = False
        for l in lines[1:close_idx]:
            key = l.split(":", 1)[0].strip() if ":" in l else ""
            if key == "stage":
                new_fm.append(f"stage: {it.status}")
                saw_stage = True
            elif key == "state":
                new_fm.append(f"state: {want_state}")
                saw_state = True
            else:
                new_fm.append(l)
        if not saw_stage:
            new_fm.append(f"stage: {it.status}")
        # Only insert state: if the file already participates in the field-map
        # (has issue_number/state keys) — don't force it onto pre-migration
        # tickets that have neither, to avoid churn until they're migrated.
        if not saw_state and ("issue_number" in it.fm or "state_reason" in it.fm):
            new_fm.append(f"state: {want_state}")
            saw_state = True

        rebuilt = ["---"] + new_fm + ["---"] + lines[close_idx + 1:]
        new_text = "\n".join(rebuilt) + ("\n" if it.text.endswith("\n") else "")
        if new_text != it.text:
            it.path.write_text(new_text)
            it.text = new_text
            it.fm["stage"] = it.status
            if saw_state:
                it.fm["state"] = want_state
            changed += 1
    return changed


# ── Markdown index ────────────────────────────────────────────────────


def render_markdown(items: list[Item]) -> str:
    """README.md is intentionally minimal: how the system works,
    nothing more. The HTML kanban and per-ticket pages are the
    actual data surface — duplicating them here is overkill."""
    by_type = defaultdict(int)
    for it in items:
        by_type[it.type] += 1
    counts = " · ".join(
        f"{TYPE_LABEL[t]} ({by_type.get(t, 0)})" for t in TYPES
    )
    out = [
        "# Backlog",
        "",
        f"{len(items)} tickets — {counts}.",
        "",
        "## How it works",
        "",
        "- **Source of truth**: `<type>/<status>/<slug>.md` under this",
        "  directory. `<type>` ∈ {`bugs`, `enhancements`, `tasks`},",
        "  `<status>` ∈ {`ideas`, `planned`, `building`, `qa`, `blocked`,",
        "  `ready-to-release`, `done`}.",
        "- **Visual board**: open [`kanban.html`](kanban.html). Cards",
        "  carry each ticket's full detail inline — click a card to open it.",
        "- **Workflow**:",
        "  1. Create a brief under `<type>/ideas/` with frontmatter",
        "     (`slug`, `priority`, `size`, `type`).",
        "  2. `mv` between status dirs as work progresses (a plain `mv` — the",
        "     cache is outside every git checkout, so git has no business",
        "     moving it).",
        "  3. Run the kanban generator (`/adt-regen-kanban`) to regenerate the",
        f"     board + ticket pages. Ids (`{ID_PREFIX}-NNN`), kanban.html, and",
        "     kanban.html is generator-managed — don't hand-edit.",
        "  4. Nothing to commit: `adt watch` syncs the move to the Issue and",
        "     re-renders the board. Only your CODE goes through a PR.",
        "- **Type classification**:",
        "  - `bug` — something is currently wrong / broken / leaking.",
        "  - `enhancement` — a missing capability to add.",
        "  - `task` — housekeeping / reviews / audits / cleanup.",
        "",
    ]
    return "\n".join(out)


# ── HTML kanban view ──────────────────────────────────────────────────


HTML_CSS = """
:root {
  --bg: #fafafa; --card: #fff; --muted: #6b7280; --line: #e5e7eb;
  --bug: #fee2e2; --bug-strong: #b91c1c;
  --enh: #dbeafe; --enh-strong: #1e40af;
  --tsk: #f3e8ff; --tsk-strong: #6b21a8;
  --p0: #ef4444; --p1: #f59e0b; --p2: #10b981;
  --text: #111827; --code-bg: #f3f4f6;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111827; --card: #1f2937; --muted: #9ca3af; --line: #374151;
    --bug: #7f1d1d; --bug-strong: #fecaca;
    --enh: #1e3a8a; --enh-strong: #bfdbfe;
    --tsk: #581c87; --tsk-strong: #e9d5ff;
    --text: #f9fafb; --code-bg: #111827;
  }
}
* { box-sizing: border-box; }
body {
  font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg); color: var(--text); margin: 0; padding: 16px;
}
h1 { font-size: 18px; margin: 0 0 4px; }
.meta { color: var(--muted); margin: 0 0 16px; font-size: 12px; }
.machines { border-top: 1px solid var(--line); margin-top: 24px; padding-top: 8px; }
.machines p { margin: 2px 0; }
.machines .machines-title { font-weight: 600; }
/* ADT-153 header. flex-wrap drops .hdr-right to its own row when the header is
   too narrow for one line. Scope note: this fixes the HEADER row only. The
   .board grid below sets repeat(7, minmax(220px, 1fr)) with no breakpoint, so
   the board itself still scrolls horizontally under ~1540px — pre-existing and
   out of this ticket's scope. An earlier version of this comment claimed the
   wrap prevented that; it does not. */
.hdr { display: flex; justify-content: space-between; align-items: baseline;
  flex-wrap: wrap; gap: 8px; margin: 0 0 16px; }
.hdr-left { margin: 0; }
/* 11px, matching .filter-group .label — deliberately smaller than .meta's 12px
   rather than a new size invented for this one element. */
.hdr-right { margin-left: auto; font-size: 11px; }
.gen-at { font-size: 12px; font-weight: 400; color: var(--muted); margin-left: 8px; }
.cmd-link { color: var(--p1, #4a9); text-decoration: none; font-weight: 600;
  margin-left: 6px; }
.cmd-link:hover { text-decoration: underline; }
/* AO-006: .sync-pill::after is a 44px hit area centred on a ~21px pill inside
   the h1, so it overhangs ~11.5px below it, and .hdr sits 4px under that. Lift
   these links above it, the same way .card a:not(.title) is lifted above the
   card's own full-bleed ::after overlay. */
.hdr-left .cmd-link { position: relative; z-index: 1; }
.controls { margin: 0 0 16px; display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }
.filter-group { display: flex; gap: 6px; align-items: center; }
/* Right cluster: token total + Columns toggle as one unit, pinned to the
 * right edge via margin-left:auto. Being a single flex child, the pair wraps
 * together on narrow viewports instead of splitting across lines. (the cost-of-work work) */
.right-cluster {
  margin-left: auto; display: flex; gap: 16px; align-items: center;
}
.token-total {
  color: var(--muted); font-size: 12px;
  white-space: nowrap; font-variant-numeric: tabular-nums;
}
/* GitHub API rate-limit counter in the header (📊 used / limit API). */
.rate-badge {
  /* ADT-153: NO font-size here. It is the only instance in the DOM and it lives
     in .hdr-right, so it inherits 11px from that. A font-size declared on this
     element wins over the inherited value regardless of selector specificity —
     which is exactly how the badge kept rendering at .meta's 12px while the
     stylesheet "said" 11px, and two tests asserting the DECLARED text of
     .hdr-right passed on it. */
  font-weight: 600; white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
/* Below 480px the nowrap badge is wider than the row it sits in, so it clipped
   off the right edge and scrolled the page sideways. Wrapping the flex ITEM was
   not enough — the nowrap child still overflowed its shrunk parent. */
@media (max-width: 480px) {
  .rate-badge { white-space: normal; }
  .hdr-right { margin-left: 0; min-width: 0; }
  /* Same reason as .rate-badge: a nowrap child overflows its shrunk parent and
     scrolls the page sideways. The pill wraps WITH .gen-at, never away from it. */
  .sync-pill { white-space: normal; }
}
/* ADT-165: same shape as .rate-badge — no font-size, so it inherits 11px from
   .hdr-right (see the note above; a font-size here would win over the
   inherited value and silently resize it). */
.prot-badge {
  font-weight: 600; white-space: nowrap;
}
@media (max-width: 480px) { .prot-badge { white-space: normal; } }
.prot-badge.prot-ok { color: var(--muted); }
.prot-badge.prot-warn { color: #d44; }
/* AO-006 sync pill. Deliberately shaped like .rate-badge/.prot-badge: 11px/600,
   nowrap, no declared font-size beyond the one it needs (it sits in the h1, not
   .hdr-right, so it must state its own — see the ADT-153 note above for why a
   declared size wins over an inherited one). */
/* The UA stylesheet's [hidden]{display:none} is an AUTHOR-beatable default: the
   `display` below wins, so `hidden` alone left an empty capsule on the page
   whenever JS did not run — a broken HTML_JS, or JS off. Restore it explicitly.
   test_hidden_pill_is_not_displayed asserts the computed style, not the
   attribute, because the attribute was never the thing that was wrong. */
.sync-pill[hidden] { display: none; }
.sync-pill {
  display: inline-flex; align-items: center; gap: 5px;
  margin-left: 8px; padding: 3px 9px;
  font-family: inherit; font-size: 11px; font-weight: 600; line-height: 1;
  color: var(--text); background: var(--card);
  border: 1px solid var(--line); border-radius: 999px;
  white-space: nowrap; cursor: pointer; position: relative;
  vertical-align: baseline;
}
.sync-pill:hover { background: var(--code-bg); border-color: var(--muted); }
/* Disabled = the board was opened as a file:// URL, so there is no watcher to
   POST to. It still SHOWS the state; it just cannot change it. */
.sync-pill[disabled] { cursor: default; opacity: 0.75; }
.sync-pill:focus-visible {
  /* --text, not --p1: --p1 is the P1-priority colour used by the priority
     badges, and reusing it here conflates "priority one" with "has focus". It
     also measured 2.06:1 against --bg in light mode, under the 3:1 WCAG
     minimum for a non-text indicator. --text is 17:1 in both themes. */
  outline: 2px solid var(--text); outline-offset: 2px;
}
/* The visible pill is ~21px tall, well under the 44px touch minimum. A
   transparent ::after grows the HIT area without moving anything on the line. */
.sync-pill::after {
  content: ""; position: absolute; left: 50%; top: 50%;
  transform: translate(-50%, -50%);
  min-width: 44px; min-height: 44px;
}
.sync-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--muted); flex: none;
}
/* The dot carries the state; the label says it in words, so colour is never the
   only channel. The label colour is a TEXT colour that clears 4.5:1 at 11px —
   the dot's green does not, which is why it is never used on text. */
.sync-pill.sync-on .sync-dot {
  background: var(--p2);
  /* Derived from the token, not a copy of its value: a literal rgba here would
     drift the moment --p2 changed, and nothing would report it. */
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--p2) 18%, transparent);
}
.sync-pill.sync-off {
  color: var(--bug-strong); background: var(--bug); border-color: var(--bug-strong);
}
.sync-pill.sync-off .sync-dot { background: var(--bug-strong); }
/* Hover, per state. The base .sync-pill:hover above ties on specificity with
   .sync-pill.sync-off and loses to it on source order, so the red pill had no
   hover feedback at all. These are (0,3,0) and win regardless of order. */
.sync-pill.sync-on:hover { background: var(--code-bg); border-color: var(--muted); }
.sync-pill.sync-off:hover { border-color: var(--text); }
.rate-badge.rate-ok { color: var(--muted); }
.rate-badge.rate-warn { color: #c77; }
.rate-badge.rate-crit { color: #d44; }
.filter-group .label {
  color: var(--muted); font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.04em; margin-right: 2px;
}
.controls button {
  background: var(--card); color: var(--text); border: 1px solid var(--line);
  padding: 4px 10px; border-radius: 4px; cursor: pointer; font: inherit;
}
.controls button.active {
  background: var(--text); color: var(--bg); border-color: var(--text);
}
.type-section {
  border-top: 2px solid var(--line); padding-top: 16px;
  margin-top: 24px;
}
.type-section:first-of-type { border-top: none; padding-top: 0; margin-top: 0; }
.section-title {
  margin: 0 0 12px; font-size: 18px; font-weight: 700;
  display: flex; align-items: center; gap: 10px;
}
.section-title .count-pill {
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
  background: var(--line); color: var(--muted); font-weight: 600;
}
.section-title.bugs { color: var(--bug-strong); }
.section-title.enhancements { color: var(--enh-strong); }
.section-title.tasks { color: var(--tsk-strong); }
.section-title.bugs .count-pill { background: var(--bug); color: var(--bug-strong); }
.section-title.enhancements .count-pill { background: var(--enh); color: var(--enh-strong); }
.section-title.tasks .count-pill { background: var(--tsk); color: var(--tsk-strong); }
.quick-jump {
  display: flex; gap: 6px; margin: 0 0 16px; flex-wrap: wrap;
}
.quick-jump a {
  font-size: 12px; padding: 4px 10px; border-radius: 4px;
  background: var(--card); border: 1px solid var(--line);
  color: var(--text); text-decoration: none;
}
.quick-jump a:hover { border-color: var(--text); }
.quick-jump a.bugs:hover { border-color: var(--bug-strong); color: var(--bug-strong); }
.quick-jump a.enhancements:hover { border-color: var(--enh-strong); color: var(--enh-strong); }
.quick-jump a.tasks:hover { border-color: var(--tsk-strong); color: var(--tsk-strong); }
.board {
  display: grid;
  grid-template-columns: repeat(7, minmax(220px, 1fr));
  gap: 12px; margin-bottom: 24px;
}
/* When the "hide blank" toggle is on, the grid reflows to only as many
 * columns as there are visible lanes. Empty lanes are hidden via the JS
 * setting display:none on .lane[data-count="0"]. */
body.hide-blank .board {
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}
body.hide-blank .lane[data-count="0"] { display: none; }
.lane {
  background: var(--card); border: 1px solid var(--line); border-radius: 6px;
  padding: 8px; min-height: 80px;
}
.lane h3 {
  margin: 0 0 8px; font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.04em; color: var(--muted); font-weight: 600;
}
.lane h3 .count {
  background: var(--line); color: var(--text); padding: 1px 6px;
  border-radius: 8px; font-size: 10px; margin-left: 4px;
}
.lane-tools {
  margin: -4px 0 8px; padding: 4px 6px;
  background: var(--bg); border: 1px dashed var(--line); border-radius: 4px;
  font-size: 10px; line-height: 1.6; color: var(--muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
/* Lane tooling: one uniform token. Every entry is something a human invokes, so
   there is no asset-type taxonomy left to colour-code (ADT-214). */
.tok { border-radius: 3px; padding: 0 4px; white-space: nowrap;
       color: #1e40af; background: #dbeafe; }
@media (prefers-color-scheme: dark) {
  .tok { color: #93c5fd; background: #1e3a5f; }
}
.card {
  background: var(--bg); border: 1px solid var(--line); border-radius: 4px;
  padding: 8px; margin-bottom: 6px; font-size: 12px;
}
.card .card-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 6px; margin-bottom: 4px;
}
.card .card-id {
  font: 10px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--muted); letter-spacing: 0.05em;
}
.card .updated {
  font: 10px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--muted); opacity: 0.75; white-space: nowrap;
}
.card .title {
  display: block; color: var(--text); text-decoration: none;
  font-weight: 600; margin-bottom: 4px; line-height: 1.3;
}
.card .title:hover { text-decoration: underline; }
.card .badges { display: flex; gap: 4px; margin-bottom: 4px; }
.badge {
  font-size: 10px; padding: 1px 5px; border-radius: 3px;
  color: white; font-weight: 600;
}
.badge.P0 { background: var(--p0); }
.badge.P1 { background: var(--p1); }
.badge.P2 { background: var(--p2); }
.badge.size {
  background: transparent; color: var(--muted); padding-left: 0;
  border: none; font-weight: 400;
}
/* Cost-of-work token count — quiet, same weight as size. Muted so it reads
 * as metadata, not a status pill. Dark-mode safe (var(--muted)). the cost-of-work work. */
.badge.tokens {
  background: transparent; color: var(--muted); padding-left: 0;
  border: none; font-weight: 400; white-space: nowrap;
}
/* ADT-115: cost sits beside the token badge and takes the SAME treatment —
   no new colour, so it works in both themes with no extra token. The
   approximation mark is the literal "~" in the text, never a colour or an
   opacity: those are invisible to a colour-blind reader and vanish in a
   screenshot. */
.badge.cost {
  background: transparent; color: var(--muted); padding-left: 0;
  border: none; font-weight: 400; white-space: nowrap;
}
/* (Impact-tier chip removed the cost-of-work work, phase 3 — `track:` is internal lifecycle
 * metadata, not rendered on the card. Frontmatter still carries it.) */
.card .hook { color: var(--muted); line-height: 1.35; }
.card .commits {
  display: flex; gap: 4px; flex-wrap: wrap; margin-top: 6px;
}
.card .commit {
  font: 10px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--line); color: var(--text); text-decoration: none;
  padding: 2px 5px; border-radius: 3px;
}
.card .commit:hover { text-decoration: underline; }
.lane.bugs h3 { color: var(--bug-strong); }
.lane.enhancements h3 { color: var(--enh-strong); }
.lane.tasks h3 { color: var(--tsk-strong); }
.empty { color: var(--muted); font-style: italic; font-size: 11px; }
"""

HTML_JS = """
// Default to showing all columns (every stage lane visible). Toggle with the
// right-pinned "Columns" buttons to hide blank lanes.
// ADT-153: persisted, because the board now auto-refreshes every 60s and the
// whole point of that is a long-lived open tab — which is precisely the tab
// that would silently lose its filters. localStorage only; no network call, so
// this does not reopen the file:// CORS problem that ruled out a JS poller.
const STATE_KEY = 'adt-kanban-state';
const state = (() => {
  const d = { type: 'all', priority: 'all', columns: 'all', query: '' };
  try { return Object.assign(d, JSON.parse(localStorage.getItem(STATE_KEY) || '{}')); }
  catch (e) { return d; }   // private mode / disabled storage → defaults
})();
function saveState() {
  try { localStorage.setItem(STATE_KEY, JSON.stringify(state)); } catch (e) {}
}

// Compact token formatter — mirrors fmt_tokens() in build_kanban.py so the
// header Σ reads the same as the per-card badges. (the cost-of-work work)
function fmtTokens(n) {
  if (!n) return '0';
  if (n < 1000) return String(n);
  if (n < 1000000) { const v = n / 1000; return (v < 10 ? v.toFixed(1) : Math.round(v)) + 'k'; }
  const v = n / 1000000; return (v < 10 ? v.toFixed(1) : Math.round(v)) + 'M';
}

// Compact money formatter — mirrors adt_cost.fmt_cost() so the header Σ reads
// the same as the per-card badges. Abbreviates above four figures for the same
// reason fmtTokens does: the badge row must not wrap on mobile. (ADT-115)
function fmtCost(micros, approx) {
  if (micros === null || micros === undefined) return '$ —';
  const d = micros / 1000000;
  const pre = approx ? '~' : '';
  if (d >= 10000) return pre + '$' + Math.round(d / 1000) + 'k';
  if (d >= 1000) return pre + '$' + (d / 1000).toFixed(1) + 'k';
  if (d >= 1) return pre + '$' + d.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
  if (d > 0) return d >= 0.005 ? pre + '$' + d.toFixed(2) : pre + '<$0.01';
  return pre + '$0.00';
}

function applyFilters() {
  const q = state.query.trim().toLowerCase();
  // Single unified board: filter cards by type + priority + (optional) search.
  // Sum tokens of the cards left visible so the header Σ matches the screen.
  let tokenSum = 0;
  // ADT-115: the money Σ is accumulated in the SAME pass over the SAME visible
  // set, so Σ$ and Σ🪙 can never disagree about which cards they counted. One
  // approximate card makes the whole total approximate — a total containing an
  // estimate IS an estimate.
  let costSum = 0, costApprox = false;
  document.querySelectorAll('.card').forEach(c => {
    const tOk = (state.type === 'all' || c.dataset.type === state.type);
    const pOk = (state.priority === 'all' || c.dataset.priority === state.priority);
    const qOk = !q || (c.dataset.id || '').toLowerCase().includes(q);
    const visible = tOk && pOk && qOk;
    c.style.display = visible ? '' : 'none';
    if (visible) {
      tokenSum += parseInt(c.dataset.tokens || '0', 10) || 0;
      costSum += parseInt(c.dataset.cost || '0', 10) || 0;
      if ((c.dataset.costTier || 'measured') !== 'measured') costApprox = true;
    }
  });
  // Recount visible cards per lane AND update data-count so the CSS rule
  // (body.hide-blank .lane[data-count="0"] { display: none }) picks up
  // dynamically-emptied lanes — e.g. when a priority filter removes the
  // last visible card from a column.
  document.querySelectorAll('.lane').forEach(lane => {
    const visible = lane.querySelectorAll('.card:not([style*=\"none\"])').length;
    lane.dataset.count = String(visible);
    const countEl = lane.querySelector('.count');
    if (countEl) countEl.textContent = visible;
  });
  // The columns toggle hides whole lanes via CSS but doesn't change which
  // cards match the filters — type/priority/search already determined the
  // visible set above, so tokenSum is correct for either columns mode.
  const totalEl = document.getElementById('token-total');
  if (totalEl) totalEl.textContent = '🪙 Σ ' + fmtTokens(tokenSum);
  const costEl = document.getElementById('cost-total');
  if (costEl) costEl.textContent = '$ Σ ' + fmtCost(costSum, costApprox);
}

function applyColumnsMode() {
  document.body.classList.toggle('hide-blank', state.columns === 'compact');
}

document.querySelectorAll('.filter-group[data-dim]').forEach(group => {
  const dim = group.dataset.dim; // 'type' | 'priority' | 'columns'
  group.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', () => {
      group.querySelectorAll('button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state[dim] = btn.dataset.filter;
      saveState();
      if (dim === 'columns') applyColumnsMode();
      applyFilters();
    });
  });
});

const searchEl = document.getElementById('search');
if (searchEl) {
  searchEl.addEventListener('input', () => {
    state.query = searchEl.value;
    saveState();
    applyFilters();
  });
}

// ADT-153: reflect the restored state in the controls BEFORE the first render,
// or the board would filter by a state the buttons don't show — worse than not
// persisting at all, because the user cannot see why cards are missing.
document.querySelectorAll('.filter-group[data-dim]').forEach(group => {
  const dim = group.dataset.dim;
  group.querySelectorAll('button').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.filter === state[dim]);
  });
});
if (searchEl) searchEl.value = state.query || '';

applyColumnsMode();
applyFilters();

// Two reload paths, and the honest limit of both. In Chrome on file:// they
// work — measured, three document loads exactly 60s apart. In Cursor's built-in
// browser on file:// NEITHER works: that viewer silently ignores every form of
// document self-navigation — location.reload / href / replace / assign,
// history.go(0), a synthetic anchor click, an injected meta refresh — raising
// no exception, and it refuses nested file:// frames so a wrapper cannot
// re-point a child either. The same page over http://127.0.0.1 reloads normally
// in that same viewer, so a live board there means serving the file over
// loopback http: ADT-160. Do not read this timer as covering webviews.
// It earns its place anyway: it needs no fetch, so the file:// CORS constraint
// that ruled out a *polling* refresh never applied to it — that rejection
// conflated reading the file with reloading the document — and a host that
// drops the meta tag may still run a timer. If both fire, the later one lands
// on an already-navigating document and is a no-op.
setTimeout(function () { location.reload(); }, __REFRESH_MS__);

// AO-006 — the header sync button. The page carries the watcher's published
// validity window and nothing about state; the colour is decided HERE, on every
// load, because the watcher is the only thing that renders this page.
//
// The click POSTs to the watcher itself, which serves this board. That is why
// the watcher is resident: a 60s --once tick has no process alive to take it.
// Opened as a file:// URL the board still renders; the button disables itself,
// because there is nothing to POST to.
const SYNC_LABEL_MS = 1500;

function syncPill() {
  const el = document.getElementById('board-sync');
  if (!el) return;
  const until = parseInt(el.dataset.healthyUntil || '0', 10);
  if (!until) return;
  const live = Date.now() / 1000 <= until;
  const lateMin = Math.max(0, Math.round((Date.now() / 1000 - until) / 60));
  const served = location.protocol === 'http:' || location.protocol === 'https:';

  el.classList.toggle('sync-on', live);
  el.classList.toggle('sync-off', !live);
  el.querySelector('.sync-label').textContent =
    live ? 'sync on' : (lateMin ? 'sync off \u00b7 ' + lateMin + 'm late' : 'sync off');
  el.disabled = !served;
  el.setAttribute('aria-label', served
    ? (live ? 'Sync is running. Click to stop it.'
            : 'Sync is stopped. Click to start it.')
    : 'Sync status. Open the board from the watcher to start or stop it.');
  el.title = served
    ? (live ? 'click to stop' : 'click to start')
    : 'open the board from the watcher to start or stop sync';
  el.hidden = false;
}

// Ask the watcher to flip, then show what it reports — never what we assumed.
// The pill reads its state from the next render either way, so a failed POST
// leaves the truth on screen rather than a guess.
function syncPillToggle(el) {
  const label = el.querySelector('.sync-label');
  const restore = label.textContent;
  el.disabled = true;
  label.textContent = '\u2026';
  fetch('/sync/toggle', {method: 'POST'})
    .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function (s) {
      label.textContent = s.paused ? 'stopped' : 'started';
      setTimeout(function () { location.reload(); }, SYNC_LABEL_MS);
    })
    .catch(function () {
      label.textContent = 'failed';
      setTimeout(function () { label.textContent = restore; el.disabled = false; },
                 SYNC_LABEL_MS);
    });
}

document.addEventListener('click', function (e) {
  const el = e.target && e.target.closest ? e.target.closest('#board-sync') : null;
  if (el && !el.disabled) syncPillToggle(el);
});

syncPill();

// Keep the reader's place in an open ticket across that reload. The panel is a
// fixed overlay with its own scrollbar, and a browser restores only the page's
// scroll position on reload, never an element's, so without this every 60s tick
// threw a half-read ticket back to the top. sessionStorage is per tab and
// survives a reload. The saved position is dropped when the fragment changes, so
// opening a different panel (or reopening this one) starts at the top.
const PANEL_SCROLL_KEY = 'adt-panel-scroll';
document.addEventListener('scroll', function (e) {
  const t = e.target;
  if (!(t instanceof Element) || !t.matches('.ticket-detail:target')) return;
  try {
    sessionStorage.setItem(PANEL_SCROLL_KEY,
      JSON.stringify({ hash: location.hash, top: t.scrollTop }));
  } catch (err) {}
}, true);   // capture: scroll events do not bubble
window.addEventListener('hashchange', function () {
  try { sessionStorage.removeItem(PANEL_SCROLL_KEY); } catch (err) {}
});
// Wait for `load`, not DOMContentLoaded: measured in Chrome, :target does not
// match yet at DOMContentLoaded, so the open panel cannot be found then.
window.addEventListener('load', function () {
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(PANEL_SCROLL_KEY) || 'null'); }
  catch (err) {}
  if (!saved || !location.hash || saved.hash !== location.hash) return;
  const panel = document.querySelector('.ticket-detail:target');
  if (panel) panel.scrollTop = saved.top;
});
"""


def _rate_limit_badge() -> str:
    """Header counter for GitHub API usage: 📊 GraphQL a / b · REST c / d API.
    A point-in-time snapshot the caller captured this render. ADT-153: it is NOT
    from `gh api rate_limit` — that endpoint reports used:0 for every resource
    whatever the real consumption, which is why this badge read 0 / 5,000
    forever. GraphQL now comes from the free `rateLimit` query, core from
    `X-RateLimit-*` on a real response. Empty string when no snapshot was
    passed — the counter degrades to ABSENT, never to a confident 0. Shows BOTH pools (they're separate 5000/hr budgets;
    since ADT-109 moved the command layer onto REST, GraphQL staying flat while
    REST moves is the healthy shape — a single binding-pool number would hide
    that). Colour tracks the worse pool: amber past 80% used, red past 95%."""
    worst = 0.0
    parts = []
    for label, used, limit in RATE_POOLS:
        if used is None or not limit:
            continue
        worst = max(worst, used / limit)
        parts.append(f"{html.escape(str(label))} {used:,} / {limit:,}")
    if not parts:
        return ""
    cls = "rate-ok"
    if worst >= 0.95:
        cls = "rate-crit"
    elif worst >= 0.80:
        cls = "rate-warn"
    return (
        f' <span class="rate-badge {cls}" '
        f'title="GitHub API requests used this hour, per pool. REST (core) and '
        f'GraphQL are separate 5000/hr pools; whichever fills first blocks its '
        f'ops. Resets hourly.">'
        f'📊 {" · ".join(parts)} API</span>'
    )


def _protection_badge() -> str:
    """Header badge saying whether the project's main branch is protected.

    ADT ships a rule stating main is server-protected; until ADT-165 nothing
    applied or checked it, so a project installed BEFORE that ticket had no way
    to learn its main was unprotected short of re-running setup. This badge is
    that way: the board is rendered every watch tick and is the surface a human
    already opens.

    Renders in BOTH states deliberately. A warn-only badge is invisible on a
    healthy repo, which makes "the check is present" and "the check works"
    indistinguishable — the confusion this ticket is written against. An
    UNREADABLE state renders nothing at all: absent, never a confident
    "protected".

    The branch name is html.escape()d. It comes from the operator-typed
    `main_branch`, and git ref names do not exclude <, >, & or " — the same
    reason REPO_NAME and every card field are escaped.
    """
    if not BRANCH_PROTECTION:
        return ""
    branch, state = BRANCH_PROTECTION
    if state not in ("protected", "unprotected"):
        return ""
    protected = state == "protected"
    cls = "prot-ok" if protected else "prot-warn"
    icon = "\U0001F512" if protected else "\U0001F513"
    title = ("Branch protection on this repo's main branch, read from the "
             "GitHub API this render. Protected means: changes land via pull "
             "request, no force-pushes, no branch deletion. "
             "Enable it with ./setup.sh --init-github.")
    return (
        f' <span id="board-protection" class="prot-badge {cls}" '
        f'title="{html.escape(title)}">'
        f'{icon} {html.escape(branch)} {html.escape(state)}</span>'
    )


def _machines_footer() -> str:
    """Footer listing each machine that reported its ADT in the last 7 days,
    newest report first (ADT-384). Each machine's sync runs its own ADT clone
    against these Issues, so a machine on an old version is worth seeing."""
    if not MACHINES:
        return ""
    import adt_machines
    rows = []
    for m in sorted(MACHINES, key=lambda m: str(m.get("reported_at", "")), reverse=True):
        ago = adt_machines.age(str(m.get("reported_at", "")))
        if ago is None:
            continue
        mine = " (this machine)" if m.get("this_machine") else ""
        rows.append(
            f'<p class="machine">{html.escape(str(m.get("login", "")))} · '
            f'{html.escape(str(m.get("os", "")))} · '
            f'v{html.escape(str(m.get("version", "")))} '
            f'@{html.escape(str(m.get("commit", ""))[:7])} · '
            f'reported {html.escape(ago)} ago{mine}</p>')
    if not rows:
        return ""
    return ('<footer id="board-machines" class="machines meta">'
            '<p class="machines-title">Machines syncing this repo</p>'
            + "".join(rows) + "</footer>")


def _sync_pill() -> str:
    """The header's sync button, or "" when there is no window to report.

    Carries the published window and NOTHING about state: the colour is decided
    in the browser, because the watcher is the only thing that renders this page
    and anything it wrote about its own health would freeze at its last value
    the moment it stopped.

    `hidden` plus the [hidden] rule in HTML_CSS, so a page whose JS never ran
    shows nothing rather than an undecided capsule.
    """
    if HEALTHY_UNTIL is None:
        return ""
    return (
        '<button type="button" id="board-sync" class="sync-pill" hidden '
        f'data-healthy-until="{int(HEALTHY_UNTIL)}">'
        '<span class="sync-dot"></span><span class="sync-label"></span>'
        "</button>")


def render_html(items: list[Item]) -> str:
    by_bucket: dict[tuple[str, str], list[Item]] = defaultdict(list)
    for it in items:
        by_bucket[(it.type, it.status)].append(it)
    for k in by_bucket:
        by_bucket[k].sort(key=lambda i: i.sort_key)

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        # ADT-153. Without this, a phone browser lays the page out at a default
        # ~980px and shrinks it to fit, so EVERY `max-width` media query in this
        # stylesheet is dead code for actual mobile visitors — the pre-existing
        # 640px rule and this ticket's new 480px badge rule alike. The design
        # gate caught it by rendering with mobile emulation: matchMedia
        # '(max-width: 480px)' returned false at a physical 375px viewport, and
        # injecting this tag flipped it to true. A media query that can never
        # match is the same defect class as the breaker that could never fire.
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        # ADT-153: the board was regenerated every watcher tick and an open tab
        # never learned — the page carried NO refresh mechanism at all (zero
        # matches for http-equiv=refresh / setInterval / EventSource /
        # WebSocket / location.reload), so a newly filed ticket stayed invisible
        # to anyone watching. A JS poller is not an option: the board is opened
        # as a file:// URL (board_out is a filesystem path), where fetch/HEAD is
        # CORS-blocked and would silently never fire — which is this ticket's own
        # defect class. Meta refresh works on file://. The `content` value is what
        # makes it work; the tag without it is inert.
        f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">',
        "<title>Project backlog</title>",
        f"<style>{HTML_CSS}\n{TICKET_CSS}\n{DETAIL_CSS}</style>",
        "</head><body>",
        # ADT-153: the generated-at time rides on the h1 instead of owning a
        # line; the old generator-attribution + click-hint sentence is deleted
        # (it told a returning reader nothing); links sit at the left edge and
        # the API badge at the right. NB the deleted wording is deliberately not
        # quoted here — a done-lane DoD condition greps this file for its
        # absence, and a comment reciting it would keep that check red forever.
        # ADT-172: name the repo. With REPO_NAME set the title is
        # "<owner/repo> issues"; without it the generic title stands, so a
        # caller that does not pass repo_name renders exactly as before. The
        # id is the anchor the unit test and the done-lane DoD condition both
        # assert on — inlined ticket prose is HTML-escaped, so a raw
        # id="board-repo" cannot be produced by a card body.
        ('<h1><span id="board-repo">'
         f'{html.escape(REPO_NAME)} issues</span>' if REPO_NAME
         else '<h1>Project backlog')
        # ADT-172: say whose clock this is. "2026-09-03 13:20" alone reads as a
        # GitHub timestamp; it is ADT's own render time.
        + f' <span class="gen-at">updated at '
          f'{datetime.now().strftime("%Y-%m-%d %H:%M")} by ADT</span>'
        # AO-006: the pill rides the same baseline, 8px after the timestamp it
        # qualifies. Inside the h1 so the two wrap together on a narrow screen —
        # they are one fact, and a stale time with its indicator on another line
        # is the ambiguity this ticket exists to remove.
        + _sync_pill()
        + "</h1>",
        '<div class="hdr">'
        + '<p class="meta hdr-left">'
        + (
            f'<a class="cmd-link" href="{html.escape(COMMANDS_DOC_URL)}">'
            "📖 references</a>"
            if (REPO / COMMANDS_DOC_SRC).exists() else ""   # now #references
        )
        + (
            f'<a class="cmd-link" href="{html.escape(BOARD_URL)}" '
            'target="_blank">📋 GitHub board</a>'
            if BOARD_URL else ""
        )
        + "</p>"
        + '<span class="hdr-right">' + _protection_badge().strip()
        + _rate_limit_badge() + "</span>"
        + "</div>",
        '<div class="controls">',
        '<div class="filter-group" data-dim="type">',
        '<span class="label">Type</span>',
        '<button class="active" data-filter="all">All</button>',
        '<button data-filter="bugs">Bugs</button>',
        '<button data-filter="enhancements">Enhancements</button>',
        '<button data-filter="tasks">Tasks</button>',
        "</div>",
        '<div class="filter-group" data-dim="priority">',
        '<span class="label">Priority</span>',
        '<button class="active" data-filter="all">All</button>',
        '<button data-filter="P0">P0</button>',
        '<button data-filter="P1">P1</button>',
        '<button data-filter="P2">P2</button>',
        "</div>",
        '<div class="filter-group search">',
        '<span class="label">Find</span>',
        '<input id="search" type="search" inputmode="numeric" '
        'placeholder="TIX # e.g. 90" '
        'style="background:var(--card);color:var(--text);'
        'border:1px solid var(--line);padding:4px 8px;border-radius:4px;'
        'font:inherit;width:140px;">',
        "</div>",
        # Right cluster: board-wide token total + Columns toggle, wrapped in
        # one flex container with margin-left:auto so the pair stays together
        # at the right edge and wraps as a unit on narrow viewports (the cost-of-work work).
        '<div class="right-cluster">',
        '<span id="token-total" class="token-total" '
        'title="Total tokens across visible cards (input+output)">'
        '🪙 Σ 0</span>',
        # ADT-115: money total. Sums the SAME visible-card set as the token Σ
        # in the same JS pass, so the two can never disagree about which cards
        # they counted.
        '<span id="cost-total" class="token-total" '
        'title="Total cost across visible cards, at Anthropic list rates">'
        '$ Σ 0</span>',
        '<div class="filter-group columns-group" data-dim="columns">',
        '<span class="label">Columns</span>',
        '<button data-filter="compact">Hide blank</button>',
        '<button class="active" data-filter="all">Show all</button>',
        "</div>",
        "</div>",
        "</div>",
    ]

    # Single unified board — one set of lanes, every ticket regardless of type.
    # Cards carry data-type so the Type chip still filters them in place.
    parts.append('<div class="board">')
    for s in STATUSES:
        bucket = [it for it in items if it.status == s]
        bucket.sort(key=lambda i: i.recency_sort_key)
        label_s = stage_label(s)
        parts.append(f'<div class="lane" data-count="{len(bucket)}">')
        parts.append(
            f'<h3>{label_s} <span class="count">{len(bucket)}</span></h3>'
        )
        tools = STAGE_TOOLS.get(s)
        if tools:
            parts.append(
                f'<div class="lane-tools" title="the commands you can invoke in this lane — see the 📖 references link">'
                f'{render_stage_tools(tools)}</div>'
            )
        if not bucket:
            parts.append('<div class="empty">—</div>')
        for it in bucket:
            title = html.escape(it.title)
            hook = html.escape(it.hook)
            href = html.escape(f"#t-{it.slug}")
            pr = it.priority or ""
            size = it.size or ""
            badge_p = (
                f'<span class="badge {pr}">{pr}</span>' if pr else ""
            )
            badge_s = (
                f'<span class="badge size">{size}</span>' if size else ""
            )
            # The `track:` impact tier (the impact-tier work) is internal lifecycle metadata
            # — which review gates a ticket must pass — not something a board
            # reader needs. It stays in frontmatter (the gate logic reads it);
            # no chip is rendered on the card. Removed the cost-of-work work, phase 3.
            # Cost-of-work token badge — "🪙 184k", or "🪙 —" when no ledger
            # data yet. data-tokens (int, 0 when unknown) feeds the header Σ
            # that recomputes from visible cards. the cost-of-work work.
            badge_tok = (
                f'<span class="badge tokens" title="Tokens spent working this '
                f'ticket (input+output)">🪙 {fmt_tokens(it.tokens)}</span>'
            )
            data_tokens = it.tokens if it.tokens is not None else 0
            # ADT-115: the same work in money, at Anthropic list rates. A "~"
            # prefix marks a figure that is not fully measured — the estimator
            # ratio has a 2.5-2.8x observed spread, so an estimate must never be
            # able to read as exact. "$ —" means no ledger rows at all, never $0.
            _cost_txt = adt_cost.fmt_cost(it.cost, it.cost_tier)
            _cost_title = {
                "measured": "Cost at Anthropic list rates, priced per row at "
                            "that row's own model and speed",
                "estimated": "ESTIMATE — no transcript survived for some rows; "
                             "derived ratio, not a measurement",
                "legacy": "ESTIMATE — pre-ADT-115 ledger rows carry no model; "
                          "derived ratio, not a measurement",
            }.get(it.cost_tier, "Cost at Anthropic list rates")
            badge_cost = (
                f'<span class="badge cost" data-tier="{html.escape(it.cost_tier)}" '
                f'title="{html.escape(_cost_title)}">{html.escape(_cost_txt)}</span>'
            )
            data_cost = it.cost if it.cost is not None else 0
            if it.commits:
                if REPO_URL:
                    chips = "".join(
                        f'<a class="commit" href="{REPO_URL}/commit/{c}" '
                        f'target="_blank" rel="noopener">{c}</a>'
                        for c in it.commits
                    )
                else:
                    chips = "".join(
                        f'<span class="commit">{c}</span>'
                        for c in it.commits
                    )
                commit_html = f'<div class="commits">{chips}</div>'
            else:
                commit_html = ""
            # card id; links to its GitHub Issue when synced + issues_url known.
            if it.id and it.issue_number and ISSUES_URL:
                id_html = (
                    f'<a class="card-id" href="{ISSUES_URL}/{it.issue_number}" '
                    f'title="GitHub Issue #{it.issue_number}" target="_blank">'
                    f'{html.escape(it.id)}</a>'
                )
            elif it.id:
                id_html = f'<span class="card-id">{html.escape(it.id)}</span>'
            else:
                id_html = ""
            # Display date-only; the value itself is a full ISO timestamp so
            # the recency sort can order same-day edits (ADT-54 follow-up).
            # Split on 'T' for the visible chip; the full value rides in
            # data-updated. The chip shows the SAME timestamp the lane sorts on
            # (recency_ts) — in `done` that is the close date, so the card the
            # user sees agrees with the position it sits in.
            card_ts = it.recency_ts
            ts_title = "Closed" if it.status == "done" else "Last updated"
            updated_disp = card_ts.split("T", 1)[0] if card_ts else ""
            updated_html = (
                f'<span class="updated" title="{ts_title}">'
                f'{html.escape(updated_disp)}</span>'
                if card_ts else ""
            )
            parts.append(
                f'<div class="card" data-type="{it.type}" '
                f'data-priority="{pr}" data-id="{html.escape(it.id)}" '
                f'data-tokens="{data_tokens}" '
                f'data-cost="{data_cost}" '
                f'data-cost-tier="{html.escape(it.cost_tier)}" '
                f'data-updated="{html.escape(card_ts)}">'
                f'<div class="card-head">{id_html}{updated_html}</div>'
                f'<a class="title" href="{href}">{title}</a>'
                f'<div class="badges">{badge_p}{badge_s}{badge_tok}{badge_cost}</div>'
                f'<div class="hook">{hook}</div>'
                f"{commit_html}"
                f"</div>"
            )
        parts.append("</div>")  # .lane
    parts.append("</div>")  # .board
    parts.append(_machines_footer())

    parts.append(
        "<script>"
        + HTML_JS.replace("__REFRESH_MS__", str(REFRESH_SECONDS * 1000))
        + "</script>"
    )
    # ADT-143: build the link map BEFORE anything renders markdown, so a
    # ticket body that links a catalogue doc resolves to its panel too — not
    # just the catalogue's own rows. Populating it after the ticket loop
    # silently degraded those to plain text.
    docs = catalogue_docs()
    DOC_FRAGMENTS.clear()
    for raw, rel, _ in docs:
        DOC_FRAGMENTS[raw] = "#" + doc_slug(rel)

    for it in items:
        parts.append(render_ticket_detail(it))

    # ADT-143: the references catalogue and every document it links to, inlined
    # as sibling :target panels. Sibling, not nested: DETAIL_CSS is pure CSS
    # (.ticket-detail{display:none} / :target{display:block}) with no :has(), so
    # a nested anchor would move the fragment off #references, un-:target the
    # containing article, and hide the content being navigated to.
    refs_panel = render_references_panel()
    if refs_panel:
        parts.append(refs_panel)
    for _, rel, text in docs:
        parts.append(render_doc_panel(rel, text))
    parts.append("</body></html>")
    return "\n".join(parts)


# ── Per-ticket HTML page ─────────────────────────────────────────────


TICKET_CSS = """
.wrap { max-width: 760px; margin: 0 auto; }
.back {
  display: inline-block; color: var(--muted); text-decoration: none;
  font-size: 13px; margin-bottom: 16px;
}
.back:hover { color: var(--text); text-decoration: underline; }

.ticket-header {
  background: var(--card); border: 1px solid var(--line);
  border-left: 4px solid var(--line);
  border-radius: 8px; padding: 20px 24px; margin-bottom: 24px;
  /* ADT-386: the title and slug sit outside .ticket-body, so they need their
     own wrap rule or a long unbroken title token widens the panel on a phone. */
  overflow-wrap: break-word;
}
.ticket-header.bug { border-left-color: var(--bug-strong); }
.ticket-header.enhancement { border-left-color: var(--enh-strong); }
.ticket-header.task { border-left-color: var(--tsk-strong); }

.ticket-header .slug {
  font: 11px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--muted); text-transform: uppercase;
  letter-spacing: 0.05em; margin-bottom: 6px;
}
.ticket-header h1 {
  font-size: 22px; font-weight: 700; margin: 0 0 12px;
  line-height: 1.3;
}
.ticket-meta {
  display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
  margin-bottom: 12px;
}
.pill {
  font-size: 11px; padding: 3px 8px; border-radius: 4px;
  font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em;
}
.pill.type-bug { background: var(--bug); color: var(--bug-strong); }
.pill.type-enhancement { background: var(--enh); color: var(--enh-strong); }
.pill.type-task { background: var(--tsk); color: var(--tsk-strong); }
.pill.priority {
  color: white;
}
.pill.priority.P0 { background: var(--p0); }
.pill.priority.P1 { background: var(--p1); }
.pill.priority.P2 { background: var(--p2); }
.pill.size, .pill.status {
  background: transparent; color: var(--muted); border: 1px solid var(--line);
}
.pill.status.blocked { color: var(--bug-strong); border-color: var(--bug-strong); }
.pill.status.done { color: var(--p2); border-color: var(--p2); }
.pill.status.building, .pill.status.qa, .pill.status.ready-to-release {
  color: var(--enh-strong); border-color: var(--enh-strong);
}

.ticket-commits {
  display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px;
}
.ticket-commits .label {
  color: var(--muted); font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.04em; line-height: 22px;
}
.ticket-commits a {
  font: 11px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--line); color: var(--text); text-decoration: none;
  padding: 4px 7px; border-radius: 3px;
}
.ticket-commits a:hover { text-decoration: underline; }

/* break-word lets a long unbroken token in prose (a path, `a+b+c+d`) wrap
   instead of widening the panel. It does not change a table's minimum width,
   so table cells still keep whole words; inline code gets `anywhere` below. */
.ticket-body { font-size: 15px; overflow-wrap: break-word; }
.ticket-body h1, .ticket-body h2, .ticket-body h3,
.ticket-body h4, .ticket-body h5, .ticket-body h6 {
  margin: 28px 0 10px; line-height: 1.3; font-weight: 600;
}
.ticket-body h1 { display: none; } /* hide duplicate of header */
.ticket-body h2 { font-size: 18px; padding-top: 8px; }
.ticket-body h3 { font-size: 15px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
.ticket-body p { margin: 0 0 12px; }
.ticket-body ul, .ticket-body ol { margin: 0 0 14px; padding-left: 24px; }
.ticket-body li { margin-bottom: 4px; }
.ticket-body code {
  background: var(--code-bg); padding: 1px 5px; border-radius: 3px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
}
.ticket-body pre {
  background: var(--code-bg); padding: 12px 14px; border-radius: 6px;
  overflow-x: auto; margin: 0 0 14px;
  border: 1px solid var(--line);
}
.ticket-body pre code { background: none; padding: 0; font-size: 13px; }
.ticket-body blockquote {
  border-left: 3px solid var(--line); padding: 4px 14px;
  color: var(--muted); margin: 0 0 14px;
  background: var(--card); border-radius: 0 4px 4px 0;
}
.ticket-body blockquote p:last-child { margin-bottom: 0; }
/* A table wider than the panel scrolls inside this wrapper at every width. It
   used to do so only below 640px, so on a desktop a table holding a long file
   path pushed its last column out past the panel's right edge. */
.ticket-body .table-wrap { overflow-x: auto; margin: 0 0 18px; }
.ticket-body table {
  border-collapse: collapse; width: 100%; margin: 0;
  font-size: 13px;
}
/* Inline code is usually a file path with no spaces, which cannot wrap: in a
   paragraph it runs past the panel edge on a phone, and in a table cell it sets
   the table's minimum width. Letting it break keeps both inside the panel, so
   the wrapper above rarely has to scroll. Code blocks are unaffected: <pre>
   does not wrap and scrolls on its own. */
.ticket-body code { overflow-wrap: anywhere; }
.ticket-body th, .ticket-body td {
  text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line);
  vertical-align: top;
}
.ticket-body th {
  color: var(--muted); font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.03em; font-size: 11px;
}
.ticket-body a { color: var(--enh-strong); }
.ticket-body hr { border: none; border-top: 1px solid var(--line); margin: 24px 0; }

.ticket-footer {
  margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--line);
  color: var(--muted); font-size: 12px;
}
.ticket-footer .raw-link { color: var(--muted); }
"""


DETAIL_CSS = """
/* ADT-136: ticket detail is inlined, not navigated to. The whole card is the
   click target via a stretched ::after on the title anchor; every OTHER anchor
   in the card is lifted above that overlay so it stays independently clickable
   (.card-id -> the GitHub Issue, .commit -> a commit). .title is deliberately
   excluded from the lift: a stacking context on it would re-base its own
   ::after to the title's box and collapse the whole-card target. */
.card { position: relative; }
.card .title::after { content: ""; position: absolute; inset: 0; }
.card a:not(.title) { position: relative; z-index: 1; }

/* The panel is a sibling of the board, shown by :target — a fragment is not a
   navigation, so no viewer can decline to follow it, and no JS is involved. */
.ticket-detail { display: none; }
.ticket-detail:target {
  display: block; position: fixed; inset: 0; z-index: 100;
  overflow: auto; background: rgba(17, 24, 39, 0.55); padding: 24px 16px;
}
.ticket-detail .wrap {
  max-width: 820px; margin: 0 auto; background: var(--card);
  border: 1px solid var(--line); border-radius: 6px; padding: 28px 32px;
}
@media (max-width: 640px) {
  .ticket-detail:target { padding: 0; }
  .ticket-detail .wrap { border-radius: 0; padding: 20px 16px; min-height: 100%; }
}
"""


def render_ticket_detail(it: Item) -> str:
    body_md = strip_frontmatter(it.text)
    body_html = md_to_html(body_md)

    type_singular = {"bugs": "bug", "enhancements": "enhancement", "tasks": "task"}[it.type]
    type_label = {"bug": "Bug", "enhancement": "Enhancement", "task": "Task"}[type_singular]

    pills: list[str] = [
        f'<span class="pill type-{type_singular}">{type_label}</span>',
    ]
    if it.priority:
        pills.append(f'<span class="pill priority {it.priority}">{it.priority}</span>')
    if it.size:
        pills.append(f'<span class="pill size">Size {it.size}</span>')
    pills.append(
        f'<span class="pill status {it.status}">{stage_label(it.status)}</span>'
    )

    if it.commits:
        if REPO_URL:
            chips = "".join(
                f'<a href="{REPO_URL}/commit/{c}" target="_blank" rel="noopener">{c}</a>'
                for c in it.commits
            )
        else:
            chips = "".join(f'<a>{c}</a>' for c in it.commits)
        commits_block = (
            '<div class="ticket-commits">'
            '<span class="label">Commits</span>'
            f"{chips}"
            "</div>"
        )
    else:
        commits_block = ""

    # Link to the GitHub Issue (the durable backing store), NOT a repo blob —
    # the cache .md lives outside the repo and is never committed there, so a
    # /blob/ URL would 404. Empty when the ticket isn't synced yet.
    repo_link = (
        f"{ISSUES_URL}/{it.issue_number}"
        if ISSUES_URL and it.issue_number else ""
    )

    return f"""<article class="ticket-detail" id="t-{html.escape(it.slug)}">
<div class="wrap">
  <a class="back" href="#">← Back to board</a>
  <div class="ticket-header {type_singular}">
    <div class="slug">{html.escape(it.id + ' · ' if it.id else '')}{html.escape(it.slug)}</div>
    <h1>{html.escape(it.title)}</h1>
    <div class="ticket-meta">{''.join(pills)}</div>
    {commits_block}
  </div>
  <div class="ticket-body">{body_html}</div>
  <div class="ticket-footer">
    {'<a class="raw-link" href="' + repo_link + '" target="_blank" rel="noopener">View Issue on GitHub</a>' if repo_link else ''}
  </div>
</div>
</article>
"""


# ── Main ──────────────────────────────────────────────────────────────


def _project_state_dir():
    """`~/.adt/<project>`, the project's own machine-local ADT state.

    adt_watch renders with `project_root=<cache dir>`, so REPO is that cache and
    the project dir is its parent. When REPO is not a cache (a direct run, the
    tests) it IS the base, and the lookup simply finds no table."""
    r = Path(REPO)
    return r.parent if r.name == "cache" else r


_LAST_ACTIVITY = None
_ADOPTION_STAMPS = None


def _last_activity_column(fn_name: str) -> dict:
    """One column of the last-activity table, read through `last_activity.py`.

    Loaded by path, not `import last_activity`: the renderer is imported from
    several roots (adt_watch, the tests, a direct run) and only some of them
    have tools/ on sys.path. Mutating sys.path on the render path to find a
    sibling would be a side effect for every later import in the process.

    Fails open to {} — a missing or malformed table must render as "no repaired
    dates", never as a broken board."""
    try:
        import importlib.util  # noqa: PLC0415
        src = Path(__file__).resolve().parent / "last_activity.py"
        spec = importlib.util.spec_from_file_location("last_activity", src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, fn_name)(_project_state_dir(), REPO_NAME or "")
    except Exception:  # noqa: BLE001 - never break a render
        return {}


def _adoption_stamps() -> dict:
    """{issue number: the `updatedAt` the adoption stamped}, cached per render.

    ADT-381. Read beside `_last_activity_table()` and from the same table. It is
    what tells an untouched ticket (its `updated:` still equals this stamp) from
    one edited after the table was built (`updated:` is later), which decides
    whether the repaired date is still the better one. See `Item.__init__`."""
    global _ADOPTION_STAMPS
    if _ADOPTION_STAMPS is None:
        _ADOPTION_STAMPS = _last_activity_column("load_stamps")
    return _ADOPTION_STAMPS


def _last_activity_table() -> dict:
    """{issue number: ISO ts} recovered after an adoption rewrote `updatedAt`.

    ADT-322. Read once per render from `data/last-activity/<owner>-<repo>.tsv`
    in the ADT checkout, NOT from the cache: the sync owns `updated:` and pulls
    it back from GitHub every tick, and a cache rebuild would drop a repaired
    value entirely. Keeping the table outside the cache means the sync needs no
    change and an uninstall cannot take the correction with it.

    The table lives with the PROJECT (`~/.adt/<project>/last-activity/`), never
    in the ADT checkout: ADT is the portable team definition, and a recovered
    backlog is one consumer's private history. Committing it here would ship
    that project's data to every clone of the team repo.

    Fails open to {} — a missing or malformed table must render as "no repaired
    dates", never as a broken board."""
    global _LAST_ACTIVITY
    if _LAST_ACTIVITY is None:
        _LAST_ACTIVITY = _last_activity_column("load")
    return _LAST_ACTIVITY


def _adt_dir():
    """The ADT checkout the RENDERER is running from.

    Module-relative, never `commands_doc_src` (ADT-143): a consumer's two
    configs can name different checkouts — one the standalone clone, one the
    vendored submodule — and deriving from that field would inline docs from a
    different commit than the code inlining them. `__file__` cannot skew."""
    return Path(__file__).resolve().parent.parent


def catalogue_docs() -> list:
    """(rel_path, text) for every local .md the references doc links to.

    Read from the renderer's own checkout. A link whose file is absent is
    skipped — it degrades to plain text via _resolve_doc_fragment returning
    None, never an empty panel."""
    src = REPO / COMMANDS_DOC_SRC
    if not src.exists():
        return []
    adt = _adt_dir()
    out, seen = [], set()
    for raw in re.findall(r"\]\(([^)]+\.md)\)", src.read_text()):
        if not _is_local(raw):
            continue
        parts = [p for p in raw.split("/") if p not in ("", ".", "..")]
        rel = "/".join(parts)
        # Strip a leading agent-dev-team/ segment: the catalogue writes
        # ../../agent-dev-team/commands/plan.md, which is commands/plan.md
        # inside the checkout.
        if parts and parts[0] == "agent-dev-team":
            rel = "/".join(parts[1:])
        cand = adt / rel
        if not cand.is_file():
            cand = adt / "docs" / rel          # bare `loops.md` form
            rel = f"docs/{rel}"
        if cand.is_file() and rel not in seen:
            seen.add(rel)
            # strip_frontmatter, same as render_ticket_detail: 16 of the 18
            # catalogued docs open with a YAML fence, and without this the panel
            # renders it as body copy (`<hr><p>name: adt-plan description: …`).
            out.append((raw, rel, strip_frontmatter(cand.read_text())))
    return sorted(out, key=lambda t: t[1])


def render_doc_panel(rel: str, text: str) -> str:
    """One inlined document, same shape as render_ticket_detail.

    The <h1> in the header is load-bearing, not decoration: TICKET_CSS carries
    `.ticket-body h1 { display: none; }`, written for ticket panels whose header
    shows the title separately. A doc panel with no header title would have that
    rule suppress the document's own and only heading, so every panel would ship
    untitled."""
    return f"""<article class="ticket-detail" id="{html.escape(doc_slug(rel))}">
<div class="wrap">
  <a class="back" href="#references">← Back to references</a>
  <div class="ticket-header">
    <div class="slug">{html.escape(rel)}</div>
    <h1>{html.escape(extract_title(text))}</h1>
  </div>
  <div class="ticket-body">{md_to_html(text)}</div>
</div>
</article>
"""


def render_references_panel() -> str | None:
    """The references catalogue, inlined into the board (ADT-143).

    Was a standalone references.html reached by a relative file:// link — the
    last scheme-dependent local navigation on the board, and the one ADT-136
    named as out of its scope. Now a :target panel like ticket detail: no
    navigation, so nothing for a viewer's URL handling to break."""
    src = REPO / COMMANDS_DOC_SRC
    if not src.exists():
        return None
    return f"""<article class="ticket-detail" id="references">
<div class="wrap">
  <a class="back" href="#">← Back to board</a>
  <div class="ticket-body">{md_to_html(src.read_text())}</div>
</div>
</article>
"""


def run(project_root=None, *, backlog_root=".adt/backlog",
        id_prefix="TIX", stage_tools=None, commands_doc_src=None,
        issues_url="", board_url="", rate_pools=None,
        token_ledger_root="", repo_name="", branch_protection=None,
        quiet=False, machines=None, healthy_until=None):
    """Generate the board for a project. The entry point a shim calls.
    If project_root is None, uses whatever configure() last set (or the
    module defaults).

    quiet suppresses the three unconditional "Wrote …" lines — for the
    adt_watch --once tick, which prints them 1,440 times a day into a launchd
    log nothing rotates (ADT-119). The CONDITIONAL lines below ("Assigned N new
    ids", "Synced stage: …") are NOT suppressed: they report a state change,
    which is exactly what a quiet log should still carry."""
    if project_root is not None:
        configure(project_root, backlog_root=backlog_root,
                  id_prefix=id_prefix, stage_tools=stage_tools,
                  commands_doc_src=commands_doc_src,
                  issues_url=issues_url, board_url=board_url,
                  rate_pools=rate_pools, token_ledger_root=token_ledger_root,
                  repo_name=repo_name, branch_protection=branch_protection,
                  machines=machines, healthy_until=healthy_until)

    # Load the token ledger BEFORE items — Item.__init__ reads TOKEN_USAGE
    # to set per-ticket .tokens. Empty dict when no ledger exists. the cost-of-work work.
    global TOKEN_USAGE, COST_USAGE
    TOKEN_USAGE = load_token_usage()
    COST_USAGE = load_cost_usage()

    items = load_items()
    OFFBOARD.clear()
    OFFBOARD.update(_offboard_spend(items))
    new = assign_missing_ids(items)
    if new:
        print(f"Assigned {new} new {ID_PREFIX}-N ids")
    staged = sync_stage_frontmatter(items)
    if staged:
        print(f"Synced stage: frontmatter on {staged} briefs (folder=truth; state: kept in lockstep)")
    (BL / "BACKLOG-README.md").write_text(render_markdown(items))
    (BL / "kanban.html").write_text(render_html(items))

    # Styled references page next to the board (if the source doc exists).


    # ADT-136: ticket detail is inlined into kanban.html, so there are no
    # per-ticket pages any more. Remove a tree left by an older render — an
    # orphan here still opens in a browser and looks current.
    stale_tickets = BL / "tickets"
    if stale_tickets.is_dir():
        shutil.rmtree(stale_tickets, ignore_errors=True)

    if not quiet:
        print(f"Wrote BACKLOG-README.md ({len(items)} items)")
        print("Wrote kanban.html")
        print(f"Inlined {len(items)} ticket details into kanban.html")


def main(argv=None):
    ap = argparse.ArgumentParser(description="ADT kanban generator")
    ap.add_argument("--project-root", default=str(REPO),
                    help="project repo root (default: this tool's grandparent)")
    ap.add_argument("--backlog-root", default=".adt/backlog")
    ap.add_argument("--id-prefix", default="TIX")
    args = ap.parse_args(argv)
    run(args.project_root, backlog_root=args.backlog_root,
        id_prefix=args.id_prefix)


if __name__ == "__main__":
    main()
