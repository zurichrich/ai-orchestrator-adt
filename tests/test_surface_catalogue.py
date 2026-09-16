# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that the commands, agents, hooks and skills on disk match the documents
that describe them: docs/references.md, README.md's review-agent roster,
`STAGE_TOOLS` in tools/build_kanban.py, operating-model.md's lane table,
docs/loops.md and docs/surface-review.md.

Each check compares the set of names on disk with the set in the document and
reports the names missing from each side.
"""
from __future__ import annotations

import json
import os
import re
import sys

ADT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ADT, "tools"))

import build_kanban  # noqa: E402  (needs the tools/ path above)

# ── Recorded exemptions ──────────────────────────────────────────────────────
# A command that no lane header names passes only if it is listed here with a
# reason.
BOARD_EXEMPT: dict[str, str] = {
    "decide": "cross-cutting — an ADR can be recorded from any stage, so no "
              "single lane owns it. Surfaced via docs/references.md's "
              "cross-cutting section instead of a lane header.",
}

# Hooks shipped as templates: a project copies one, fills in its own patterns
# and wires it in its own settings.json, so it is not in
# defaults/settings.hooks.json.
TEMPLATE_HOOKS = {"adt-deploy-guard"}

# Test files for the surface checks.
NEW_TEST_FILES = (
    "tests/test_surface_catalogue.py",
    "defaults/hooks/tests/test_surface_log.sh",
)

HOOK_CLASSES = ("wired", "template", "helper")


def _read(rel: str) -> str:
    with open(os.path.join(ADT, rel), encoding="utf-8") as fh:
        return fh.read()


def _names(rel_glob_dir: str, suffix: str) -> set[str]:
    d = os.path.join(ADT, rel_glob_dir)
    if not os.path.isdir(d):
        return set()
    return {f[: -len(suffix)] for f in os.listdir(d) if f.endswith(suffix)}


def commands_on_disk() -> set[str]:
    return _names("commands", ".md")


def agents_on_disk() -> set[str]:
    return _names(".claude/agents", ".md")


def hooks_on_disk() -> set[str]:
    return _names("defaults/hooks", ".sh")


def skills_on_disk() -> set[str]:
    d = os.path.join(ADT, "defaults/skills")
    if not os.path.isdir(d):
        return set()
    return {n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n))}


def skill_identity(dirname: str) -> str:
    """Return the `/adt-*` name of a skill directory, without doubling an
    existing `adt-` prefix."""
    return "/" + dirname if dirname.startswith("adt-") else f"/adt-{dirname}"


def surface_identities() -> set[str]:
    """Every operator-facing surface, named as the docs name it."""
    return ({f"/adt-{c}" for c in commands_on_disk()}
            | agents_on_disk() | hooks_on_disk()
            | {skill_identity(s) for s in skills_on_disk()})


def wired_hooks() -> set[str]:
    """Return the hooks wired in defaults/settings.hooks.json."""
    cfg = json.loads(_read("defaults/settings.hooks.json"))
    out = set()
    for event in cfg.get("hooks", {}).values():
        for block in event:
            for hook in block.get("hooks", []):
                cmd = hook.get("command", "")
                out.add(os.path.basename(cmd)[: -len(".sh")]
                        if cmd.endswith(".sh") else os.path.basename(cmd))
    return out


def stage_tools_command_tokens() -> dict[str, set[str]]:
    """Per lane, the `/adt-*` tokens named in the board's lane header."""
    out = {}
    for lane, value in build_kanban.STAGE_TOOLS.items():
        out[lane] = {t.strip() for t in value.split("·")
                     if t.strip().startswith("/")}
    return out


def _diff(label_a: str, a: set, label_b: str, b: set) -> str:
    only_a = sorted(a - b)
    only_b = sorted(b - a)
    parts = []
    if only_a:
        parts.append(f"only in {label_a}: {only_a}")
    if only_b:
        parts.append(f"only in {label_b}: {only_b}")
    return "; ".join(parts)


# ── (i) the board names every command, or records why it does not ────────────
def test_stage_tools_covers_every_command():
    named = {t.lstrip("/").replace("adt-", "", 1)
             for toks in stage_tools_command_tokens().values() for t in toks}
    missing = commands_on_disk() - named - set(BOARD_EXEMPT)
    assert not missing, (
        "commands on disk that no lane header names and BOARD_EXEMPT does not "
        f"list: {sorted(missing)}. Add them to a STAGE_TOOLS lane, or to "
        "BOARD_EXEMPT with a reason.")


# ── (ii) the catalogue names every surface ───────────────────────────────────
def test_references_covers_every_surface():
    refs = _read("docs/references.md")
    missing = {}
    for kind, names in (("command", {f"/adt-{c}" for c in commands_on_disk()}),
                        ("agent", agents_on_disk()),
                        ("hook", hooks_on_disk()),
                        ("skill", {skill_identity(s)
                                   for s in skills_on_disk()})):
        absent = sorted(n for n in names if f"`{n}`" not in refs)
        if absent:
            missing[kind] = absent
    assert not missing, (
        f"surfaces on disk with no row in docs/references.md: {missing}")


# ── (iii) no skill symlink points at a deleted target ────────────────────────
def test_no_dangling_skill_symlink():
    d = os.path.join(ADT, ".claude/skills")
    broken = []
    if os.path.isdir(d):
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if os.path.islink(p) and not os.path.exists(p):
                broken.append(f"{name} -> {os.readlink(p)}")
    assert not broken, (
        f"dangling skill symlinks (target deleted, link left behind): {broken}")


# ── every shipped agent carries the adt- prefix (ADT-371) ────────────────────
def test_every_shipped_agent_has_the_adt_prefix():
    # Telemetry counts agents by name, and a project can have its own agent with
    # a plain name such as security-reviewer. The prefix is what tells ADT's
    # agents apart, so a new agent cannot ship without it.
    shipped = _names("defaults/agents", ".md")
    assert shipped, "defaults/agents/ has no agents; the check would pass on nothing"
    plain = sorted(n for n in shipped if not n.startswith("adt-"))
    assert not plain, f"shipped agents without the adt- prefix: {plain}"


# ── (iv) README's roster lists every review agent ────────────────────────────
def test_readme_agent_roster_complete():
    readme = _read("README.md")
    # Accept "**Review agents** —", "**Review agents.**" and "**Review agents:**".
    m = re.search(r"^- \*\*Review agents[.:]?\*\*(.*?)(?=^- \*\*)", readme,
                  re.M | re.S)
    # Only the roster bullet counts; agents named elsewhere in README do not.
    assert m, ("README.md has no '- **Review agents**' bullet; the roster is "
               "missing or was renamed.")
    roster = set(re.findall(r"`([a-z-]+-(?:reviewer|investigator|triage))`",
                            m.group(1)))
    on_disk = agents_on_disk()
    assert roster == on_disk, (
        "README's review-agent roster does not match .claude/agents/ — "
        + _diff("roster", roster, "disk", on_disk))


# ── (v) the surface review gives every surface a verdict ─────────────────────
def test_review_doc_classifies_every_surface():
    rel = "docs/surface-review.md"
    path = os.path.join(ADT, rel)
    assert os.path.exists(path), (
        f"{rel} does not exist")
    doc = _read(rel)
    verdicts = dict(re.findall(
        r"^\|\s*`([^`]+)`\s*\|[^|]*\|\s*(keep|integrate better|drop)\s*\|",
        doc, re.M | re.I))
    expected = surface_identities()
    classified = set(verdicts)
    assert classified == expected, (
        "docs/surface-review.md must carry exactly one keep/integrate better/"
        "drop verdict per surface on disk — "
        + _diff("review", classified, "disk", expected))


# ── (vi) the workflow doc's lane table agrees with the board ─────────────────
def test_operating_model_matches_stage_tools():
    om = _read("operating-model.md")
    board = stage_tools_command_tokens()
    mismatches = {}
    for lane, board_cmds in board.items():
        m = re.search(r"^\|\s*%s\s*\|(.+?)\|" % re.escape(lane), om, re.M)
        if not m:
            continue  # lanes missing from the workflow doc's table are skipped
        doc_cmds = set(re.findall(r"`(/adt-[a-z-]+)`", m.group(1)))
        if doc_cmds != board_cmds:
            mismatches[lane] = _diff("operating-model", doc_cmds,
                                     "STAGE_TOOLS", board_cmds)
    assert not mismatches, (
        f"operating-model.md's lane table contradicts STAGE_TOOLS: {mismatches}")


# ── (vii) the loops doc covers every loop-tagged surface ─────────────────────
def test_loops_doc_covers_loop_tagged():
    tagged = set(re.findall(r"`(/adt-[a-z-]+)` \| (?:command|skill) · loop",
                            _read("docs/references.md")))
    assert tagged, ("no rows in docs/references.md carry the '· loop' tag; the "
                    "tag or this pattern changed.")
    listed = set(re.findall(r"^\| `(/adt-[a-z-]+)`", _read("docs/loops.md"),
                            re.M))
    assert tagged == listed, (
        "docs/loops.md's table does not match the loop-tagged rows in "
        "docs/references.md — " + _diff("references", tagged, "loops.md",
                                        listed))


# ── (viii) the catalogue's hook classes match the actual wiring ──────────────
def test_hook_classification_matches_wiring():
    refs = _read("docs/references.md")
    wired = wired_hooks()
    wrong = {}
    for hook in sorted(hooks_on_disk()):
        expected = ("wired" if hook in wired
                    else "template" if hook in TEMPLATE_HOOKS else "helper")
        row = re.search(r"^\|\s*`%s`\s*\|.*$" % re.escape(hook), refs, re.M)
        if not row:
            wrong[hook] = "no row in docs/references.md"
            continue
        declared = [c for c in HOOK_CLASSES
                    if re.search(r"\b%s\b" % c, row.group(0), re.I)]
        if declared != [expected]:
            wrong[hook] = f"declared {declared or 'nothing'}, wiring says {expected!r}"
    assert not wrong, (
        f"docs/references.md's hook classification disagrees with "
        f"defaults/settings.hooks.json: {wrong}")


# ── (ix) the catalogue's summary counts match disk ──────────────────────────
def test_references_header_counts_match_disk():
    header = re.search(r"^\*\*.*?\bcommands\b.*$", _read("docs/references.md"), re.M)
    assert header, "docs/references.md has no '**N commands**' summary line"
    line = header.group(0)
    expected = {"commands": len(commands_on_disk()),
                "subagents": len(agents_on_disk()),
                "hooks": len(hooks_on_disk())}
    wrong = {}
    for noun, n in expected.items():
        m = re.search(r"\*\*(\d+) %s\*\*" % noun, line)
        if not m:
            wrong[noun] = "not stated"
        elif int(m.group(1)) != n:
            wrong[noun] = f"says {m.group(1)}, disk has {n}"
    assert not wrong, (
        f"docs/references.md's summary line disagrees with disk: {wrong}. "
        f"Line: {line!r}")


# ── (x) every `/adt-*` token on the board resolves to a file ─────────────────
def test_stage_tools_tokens_resolve():
    unresolved = []
    for lane, toks in stage_tools_command_tokens().items():
        for tok in toks:
            name = tok.lstrip("/")
            bare = name.replace("adt-", "", 1)
            if (os.path.exists(os.path.join(ADT, "commands", f"{bare}.md"))
                    or os.path.exists(os.path.join(ADT, "defaults/skills",
                                                   name, "SKILL.md"))):
                continue
            unresolved.append(f"{lane}:{tok}")
    assert not unresolved, (
        f"STAGE_TOOLS names surfaces with no file: {unresolved}")
