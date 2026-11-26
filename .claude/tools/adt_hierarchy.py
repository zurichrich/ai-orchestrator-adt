# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Epic and Feature tickets, and the grouping that creates them (AO-028).

An Epic and a Feature are tickets of `type: epic` and `type: feature`. A ticket
names its Feature with `parent: <ID>`, and a Feature names its Epic the same
way. resolve() follows that chain; the done-guard's plan gate and
management_report's --check-assigned both use it.

  --status  <cache>            print how many tickets are not in an Epic and
                               Feature yet (nothing when every one is)
  --propose <cache>            print an outline of the grouping: existing Epic
                               and Feature tickets, phase 1 `epic:`/`feature:`
                               names, and every other ticket under # Ungrouped
  --apply <outline> <cache>    show what the outline would create; with --yes,
                               create the Epics and Features and set parents

--apply --yes holds two locks. This machine's sync lock (the same
.adt-sync.lock file adt_sync's pass_lock takes) keeps a watch tick from pushing
half-written files. The `adt:grouping` label keeps two machines from grouping at
once: creating a label is atomic on GitHub, so exactly one caller gets a 201.
Before any create it lists every `type:epic` and `type:feature` Issue straight
from REST, because the pull skips Issues still carrying `adt:filing`, which is
the state of an Epic another machine has just made.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adt_devs import config_value  # noqa: E402
from ticket_serializer import (FILING_LABEL, _strip_slug_trailer,  # noqa: E402
                               _with_slug_trailer, meta_from_body, parse_md)

LANES = ("ideas", "planned", "building", "qa", "blocked", "ready-to-release", "done")
PARENT_TYPES = ("epic", "feature")
LOCK_LABEL = "adt:grouping"
LOCK_STALE_SECONDS = 3600
SYNC_LOCK = ".adt-sync.lock"   # adt_sync.LOCK_FILENAME; adt_sync is not installed in projects


class HierarchyError(RuntimeError):
    pass


# ── Reading the cache ─────────────────────────────────────────────────

def canon(tid) -> str:
    """`AO-028`, `ao-28` and `AO-28` are one ticket: the number is the id."""
    s = str(tid or "").strip().strip("'\"").upper()
    m = re.match(r"^([A-Z]+)-0*(\d+)$", s)
    return f"{m.group(1)}-{m.group(2)}" if m else s


def _field(fm: dict, key: str) -> str:
    v = str(fm.get(key) or "").strip().strip("'\"")
    return "" if v.lower() in ("null", "none", "~") else v


def load(cache) -> dict:
    """Every ticket in the cache, keyed by canonical id."""
    out = {}
    for p in sorted(Path(cache).glob("*/*/*.md")):
        if p.parent.name not in LANES:
            continue
        data = parse_md(p.read_text(encoding="utf-8"))
        body = data.get("body") or ""
        title = _field(data, "title") or next(
            (ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), p.stem)
        typ = _field(data, "type").lower()
        rec = {"id": canon(_field(data, "id") or p.stem), "type": typ, "title": title,
               "parent": canon(_field(data, "parent")), "lane": p.parent.name,
               "path": p, "fm": data}
        out[rec["id"]] = rec
    return out


def resolve(tickets: dict, tid: str):
    """(feature id, epic id) for a ticket, or None when its chain does not
    reach a Feature whose own parent is an Epic. A Feature resolves to
    (itself, its Epic)."""
    t = tickets.get(canon(tid))
    if not t:
        return None
    feat = t if t["type"] == "feature" else tickets.get(t["parent"])
    if not feat or feat["type"] != "feature":
        return None
    epic = tickets.get(feat["parent"])
    if not epic or epic["type"] != "epic":
        return None
    return feat["id"], epic["id"]


def resolve_path(md_path) -> bool:
    """True when the ticket at md_path resolves. The cache root is the
    grandparent of its lane folder (<cache>/<type>/<lane>/<slug>.md)."""
    p = Path(md_path)
    tickets = load(p.parent.parent.parent)
    tid = _field(parse_md(p.read_text(encoding="utf-8")), "id") or p.stem
    return resolve(tickets, tid) is not None


def unresolved(tickets: dict) -> list:
    """Tickets and Features whose chain does not resolve, done ones included."""
    return [t for t in tickets.values()
            if t["type"] != "epic" and resolve(tickets, t["id"]) is None]


# ── The outline ───────────────────────────────────────────────────────

def propose(tickets: dict) -> str:
    epics = {}          # epic title -> {"owner", "features": {title: [ids]}}

    def slot(etitle, ftitle, owner=""):
        e = epics.setdefault(etitle, {"owner": owner, "features": {}})
        if owner and not e["owner"]:
            e["owner"] = owner
        return e["features"].setdefault(ftitle, []) if ftitle else None

    for t in tickets.values():
        if t["type"] == "epic":
            slot(t["title"], None, _field(t["fm"], "product_owner"))
    for t in tickets.values():
        if t["type"] == "feature":
            e = tickets.get(t["parent"])
            if e and e["type"] == "epic":
                slot(e["title"], t["title"])
    ungrouped = []
    for t in sorted(tickets.values(), key=lambda t: t["id"]):
        if t["type"] in PARENT_TYPES:
            continue
        chain = resolve(tickets, t["id"])
        if chain:
            slot(tickets[chain[1]]["title"], tickets[chain[0]]["title"]).append(t)
            continue
        en, fn = _field(t["fm"], "epic"), _field(t["fm"], "feature")
        if en and fn:
            slot(en, fn, _field(t["fm"], "product_owner")).append(t)
        else:
            ungrouped.append(t)

    lines = []
    for et, e in epics.items():
        lines += [f"# Epic: {et}", f"owner: {e['owner']}"]
        for ft, members in e["features"].items():
            lines.append(f"## Feature: {ft}")
            lines += [f"- {t['id']} {t['title']}" for t in members]
        lines.append("")
    lines.append("# Ungrouped")
    lines += [f"- {t['id']} {t['title']}" for t in ungrouped]
    return "\n".join(lines) + "\n"


def parse_outline(text: str) -> list:
    """[{title, owner, features: [{title, owner, tickets: [id]}]}]; the
    # Ungrouped section is read and left out."""
    epics, epic, feat = [], None, None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("# Epic:"):
            epic = {"title": line[7:].strip(), "owner": "", "features": []}
            epics.append(epic)
            feat = None
        elif line.startswith("# "):
            epic = feat = None
        elif line.startswith("## Feature:") and epic is not None:
            feat = {"title": line[11:].strip(), "owner": "", "tickets": []}
            epic["features"].append(feat)
        elif line.startswith("owner:") and epic is not None:
            (feat or epic)["owner"] = line[6:].strip()
        elif line.startswith("- ") and feat is not None:
            feat["tickets"].append(canon(line[2:].split()[0]))
    return epics


# ── GitHub ────────────────────────────────────────────────────────────

def _gh(args: list) -> str:
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise HierarchyError(p.stderr.strip() or f"gh {' '.join(args[:3])} failed")
    return p.stdout


def _label_path(repo: str, name: str) -> str:
    return f"repos/{repo}/labels/{quote(name, safe='')}"


def _lock_holder(repo: str) -> str:
    return _gh(["api", _label_path(repo, LOCK_LABEL), "--jq", ".description"]).strip()


def take_lock(repo: str, me: str, now: float | None = None) -> bool:
    """True when this run now holds the `adt:grouping` label."""
    now = time.time() if now is None else now

    def create() -> bool:
        try:
            _gh(["api", "-X", "POST", f"repos/{repo}/labels", "-f", f"name={LOCK_LABEL}",
                 "-f", "color=ededed", "-f", f"description={me} {int(now)}"])
            return True
        except HierarchyError as e:
            if "already_exists" not in str(e) and "422" not in str(e):
                raise
            return False

    if create():
        return True
    held_since = _lock_holder(repo).rpartition(" ")[2]
    if not held_since.isdigit() or now - int(held_since) < LOCK_STALE_SECONDS:
        return False
    # An hour old: its run died. Take it over; still_mine() catches the case
    # where another machine took it over at the same moment.
    _gh(["api", "-X", "DELETE", _label_path(repo, LOCK_LABEL)])
    return create()


def still_mine(repo: str, me: str) -> bool:
    try:
        return _lock_holder(repo).startswith(me + " ")
    except HierarchyError:
        return False


def release_lock(repo: str) -> None:
    try:
        _gh(["api", "-X", "DELETE", _label_path(repo, LOCK_LABEL)])
    except HierarchyError:
        pass


def list_remote(repo: str) -> list:
    """Every Epic and Feature Issue, closed ones included, from REST. Not the
    pull: the pull skips Issues still labelled `adt:filing`."""
    out = []
    for typ in PARENT_TYPES:
        page = 1
        while True:
            items = json.loads(_gh(["api", f"repos/{repo}/issues?labels=type:{typ}"
                                           f"&state=all&per_page=100&page={page}"]) or "[]")
            for it in items:
                if "pull_request" in it:
                    continue
                meta = meta_from_body(_strip_slug_trailer(it.get("body") or "")) or {}
                out.append({"number": it["number"], "title": it["title"], "type": typ,
                            "parent": canon(meta.get("parent"))})
            if len(items) < 100:
                break
            page += 1
    return out


def _ensure_labels(repo: str) -> None:
    for name in ("type:epic", "type:feature", "stage:ideas", FILING_LABEL):
        try:
            _gh(["api", "-X", "POST", f"repos/{repo}/labels", "-f", f"name={name}",
                 "-f", "color=ededed"])
        except HierarchyError:
            pass        # already exists


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "untitled"


def create(repo: str, prefix: str, cache, typ: str, title: str,
           parent: str = "", owner: str = "") -> str:
    """File one Epic or Feature the way /adt-brief files a ticket: the Issue
    first, claimed with adt:filing, then the cache file. Returns its id."""
    base = _slug(title)
    d = Path(cache) / f"{typ}s" / "ideas"
    d.mkdir(parents=True, exist_ok=True)
    slug, n = f"{typ}-{base}", 2
    while any((Path(cache) / f"{typ}s" / lane / f"{slug}.md").exists() for lane in LANES):
        slug, n = f"{typ}-{base}-{n}", n + 1
    issue = json.loads(_gh([
        "api", "-X", "POST", f"repos/{repo}/issues", "-f", f"title={title}",
        "-f", "body=" + _with_slug_trailer(f"{typ.capitalize()} ticket, created by /adt-group.", slug),
        "-f", f"labels[]=type:{typ}", "-f", "labels[]=stage:ideas",
        "-f", f"labels[]={FILING_LABEL}"]))
    num = issue["number"]
    tid = f"{prefix}-{num}"
    fm = [f"slug: {slug}", f"id: {tid}", f"title: {title}", f"type: {typ}",
          f"created: {date.today().isoformat()}", "created_by: po", "stage: ideas",
          "state: open", "state_reason: null", "priority: P2"]
    if owner:
        fm.append(f"product_owner: {owner}")
    if parent:
        fm.append(f"parent: {parent}")
    fm += [f"issue_number: {num}", f"issue_node_id: {issue.get('node_id', '')}", "comments: []"]
    (d / f"{slug}.md").write_text(
        "---\n" + "\n".join(fm) + f"\n---\n\n# {title}\n\n## Description\n(empty)\n",
        encoding="utf-8")
    return canon(tid)


def set_parent(path: Path, parent: str) -> bool:
    """Write `parent:` and drop phase 1's `epic:`/`feature:` lines. One-line
    splices, not emit_md, which would reorder every key. True if it changed."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return False
    head, sep, rest = text[4:].partition("\n---")
    lines = [ln for ln in head.split("\n") if not re.match(r"^(epic|feature):", ln)]
    if any(ln.startswith("parent:") for ln in lines):
        lines = [f"parent: {parent}" if ln.startswith("parent:") else ln for ln in lines]
    else:
        lines.append(f"parent: {parent}")
    new = "---\n" + "\n".join(lines) + sep + rest
    if new == text:
        return False
    path.write_text(new, encoding="utf-8")
    return True


# ── Apply ─────────────────────────────────────────────────────────────

def _local(tickets: dict, typ: str, title: str, parent: str = ""):
    for t in tickets.values():
        if t["type"] == typ and t["title"] == title and (typ == "epic" or t["parent"] == parent):
            return t["id"]
    return None


def plan(epics: list, tickets: dict) -> list:
    """What --apply would create, from the local cache alone."""
    out = []
    for e in epics:
        eid = _local(tickets, "epic", e["title"])
        if not eid:
            out.append(f"create Epic: {e['title']} (owner {e['owner'] or '-'}, "
                       f"{len(e['features'])} feature(s))")
        for f in e["features"]:
            if not (eid and _local(tickets, "feature", f["title"], eid)):
                out.append(f"create Feature: {f['title']} under {e['title']} "
                           f"({len(f['tickets'])} ticket(s))")
    n = sum(len(f["tickets"]) for e in epics for f in e["features"])
    out.append(f"set parent on {n} ticket(s)")
    return out


class _SyncLock:
    """This machine's sync lock, taken the way adt_sync.pass_lock takes it:
    contention stops the run, any other failure proceeds unlocked."""

    def __init__(self, cache):
        self.path, self.fd = os.path.join(cache, SYNC_LOCK), None

    def __enter__(self) -> bool:
        try:
            import fcntl
            self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        except (ImportError, OSError):
            pass
        return True

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)


def apply(epics: list, cache, repo: str, prefix: str, me: str | None = None) -> list:
    me = me or f"{socket.gethostname()}:{os.getpid()}"
    done = []
    with _SyncLock(cache) as ours:
        if not ours:
            raise HierarchyError("a sync pass is running on this machine; try again in a minute")
        if not take_lock(repo, me):
            raise HierarchyError("another machine is grouping (the adt:grouping label is held)")
        try:
            _ensure_labels(repo)
            remote = list_remote(repo)
            tickets = load(cache)

            def find(typ, title, parent=""):
                hit = _local(tickets, typ, title, parent)
                if hit:
                    return hit
                for r in remote:
                    if r["type"] == typ and r["title"] == title \
                            and (typ == "epic" or r["parent"] == parent):
                        return canon(f"{prefix}-{r['number']}")
                return None

            def made(typ, title, parent="", owner=""):
                tid = create(repo, prefix, cache, typ, title, parent, owner)
                done.append(f"created {typ.capitalize()}: {title} ({tid})")
                if not still_mine(repo, me):
                    raise HierarchyError("the adt:grouping lock passed to another machine; stopped")
                return tid

            for e in epics:
                eid = find("epic", e["title"]) or made("epic", e["title"], owner=e["owner"])
                for f in e["features"]:
                    fid = find("feature", f["title"], eid) or \
                        made("feature", f["title"], eid, f["owner"])
                    for tid in f["tickets"]:
                        t = tickets.get(tid)
                        if t and set_parent(t["path"], fid):
                            done.append(f"{tid} -> {fid}")
        finally:
            release_lock(repo)
    return done


# ── CLI ───────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Epic and Feature tickets")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", metavar="CACHE")
    g.add_argument("--propose", metavar="CACHE")
    g.add_argument("--apply", nargs=2, metavar=("OUTLINE", "CACHE"))
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--repo")
    ap.add_argument("--prefix")
    a = ap.parse_args(argv)

    if a.status:
        n = len(unresolved(load(a.status)))
        if n:
            print(f"{n} ticket(s) are not in an Epic and a Feature yet. "
                  f"In Claude Code, run /adt-group to group them.")
        return 0
    if a.propose:
        sys.stdout.write(propose(load(a.propose)))
        return 0

    outline, cache = a.apply
    epics = parse_outline(Path(outline).read_text(encoding="utf-8"))
    if not a.yes:
        for line in plan(epics, load(cache)):
            print(line)
        print("Nothing changed. Run again with --yes to apply.")
        return 0
    repo, prefix = a.repo or config_value("repo"), a.prefix or config_value("id_prefix") or "TIX"
    if not repo:
        print("no repo: pass --repo or set repo: in .adt/config.yaml", file=sys.stderr)
        return 2
    try:
        for line in apply(epics, cache, repo, prefix):
            print(line)
    except HierarchyError as e:
        print(f"adt_hierarchy: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

# adt-bundle: v0.5.0
