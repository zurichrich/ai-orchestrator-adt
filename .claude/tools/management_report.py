# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""The management report: a project's tickets grouped Epic > Feature > Ticket
on a timeline, with owners, devs, plan and actual dates, and cost (AO-028).

build_kanban.run() calls render() with the Item list it already loaded and
writes the page beside kanban.html; adt_watch copies it into the project's
.adt/. The page is self-contained and loads nothing from the network, because
it is opened from disk, often offline.

Epics and Features are tickets of `type: epic` and `type: feature`. A ticket
names its Feature with `parent: <ID>` and a Feature names its Epic the same way
(adt_hierarchy.py). Each Epic and Feature row is titled by its ticket's title,
and holds the tickets whose chain resolves to it; a ticket whose chain does not
resolve goes under "Unassigned". Epic and Feature tickets are rows, never
counted as tickets.

An Epic's owner is its own `product_owner:`. A Feature shows its own, or its
Epic's marked inherited. An Epic or Feature with its own plan dates or budget
is measured against them, and the children's roll-up is kept beside it as
`kids` so the page can show both.

CLI, against a backlog cache directory:
  --list-groups <cache>     print each epic, its owner and its features
  --check-assigned <cache>  exit 1 if a ticket has no epic or feature, or an
                            epic has no product owner
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adt_hierarchy import LANES, PARENT_TYPES, _field, canon  # noqa: E402

UNASSIGNED = "Unassigned"
_TEMPLATE = Path(__file__).with_name("management_report_template.html")


def _day(value) -> str | None:
    """The YYYY-MM-DD part of a frontmatter date or timestamp, else None."""
    s = str(value or "").strip().strip("'\"")
    if len(s) >= 10 and s[4] == "-" and s[7] == "-" and s[:4].isdigit():
        return s[:10]
    return None


def _money(value) -> float | None:
    s = str(value or "").strip().strip("'\"").replace("$", "").replace(",", "")
    if not s or s.lower() in ("null", "none"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def ticket_row(item, built: str) -> dict:
    """One ticket's fields, read from the board's Item."""
    fm = item.fm
    start = _day(fm.get("created")) or _day(item.closed) or _day(fm.get("updated")) or built
    return {
        "id": item.id or item.slug,
        "title": item.title,
        "kind": _field(fm, "type").lower(),
        "parent": canon(_field(fm, "parent")),
        "number": _field(fm, "issue_number") or None,
        "refused": _field(fm, "sub_issue_refused"),
        "stage": item.status,
        "priority": item.priority,
        "type": _field(fm, "type"),
        "start": start,
        "finish": _day(item.closed),
        "size": item.size,
        "cost": None if item.cost is None else round(item.cost / 1_000_000, 4),
        "tier": item.cost_tier,
        "devs": [d.strip().lstrip("@") for d in _field(fm, "devs").split(",") if d.strip()],
        "owner": _field(fm, "product_owner") or None,
        "planned_start": _day(fm.get("planned_start")),
        "planned_finish": _day(fm.get("planned_finish")),
        "planned_cost": _money(fm.get("planned_cost")),
    }


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(a) - date.fromisoformat(b)).days


def rollup(rows: list[dict], today: str) -> dict:
    """Plan and actual fields rolled up over a set of tickets.

    Earliest start, latest finish, summed costs, the finish slip (actual
    finish, or today while any ticket is open, against the latest plan finish)
    and the cost variance (actual minus plan, over tickets that have both).
    Each figure carries how many tickets it covers."""
    planned = [r for r in rows if r["planned_finish"]]
    starts = [r["planned_start"] for r in planned if r["planned_start"]]
    out = {
        "n": len(rows),
        "nPlan": len(planned),
        "ps": min(starts) if starts else None,
        "pf": max(r["planned_finish"] for r in planned) if planned else None,
        "as": min(r["start"] for r in rows) if rows else None,
        "allDone": bool(rows) and all(r["finish"] for r in rows),
    }
    out["af"] = max(r["finish"] for r in rows) if out["allDone"] else None
    out["slip"], out["slipOpen"] = None, False
    if out["pf"]:
        if out["allDone"]:
            out["slip"] = _days(out["af"], out["pf"])
        elif today > out["pf"]:
            out["slip"], out["slipOpen"] = _days(today, out["pf"]), True
    out["pc"] = round(sum(r["planned_cost"] or 0 for r in planned), 4)
    costed = [r for r in rows if r["cost"] is not None]
    out["ac"] = {
        "sum": round(sum(r["cost"] for r in costed), 4),
        "n": len(costed),
        "of": len(rows),
        "approx": any(r["tier"] and r["tier"] != "measured" for r in costed),
    }
    both = [r for r in rows if r["cost"] is not None and r["planned_cost"] is not None]
    out["cvN"] = len(both)
    out["cv"] = round(sum(r["cost"] - r["planned_cost"] for r in both), 4)
    return out


def build(items, project: str = "", today: str | None = None, issues_url: str = "") -> dict:
    """The page's data: epics, features, owners, ticket rows and roll-ups."""
    today = today or date.today().isoformat()
    return assemble([ticket_row(it, today) for it in items], project, today, issues_url)


def _own_plan(r: dict, parent: dict | None, today: str) -> dict:
    """A roll-up measured against an Epic's or Feature's own plan when it has
    one. The children's plan is kept as `kids` for the page to show beside it."""
    if not parent or not (parent["planned_start"] or parent["planned_finish"]
                          or parent["planned_cost"] is not None):
        return r
    r = dict(r, kids={"ps": r["ps"], "pf": r["pf"], "pc": r["pc"], "nPlan": r["nPlan"]})
    if parent["planned_start"]:
        r["ps"] = parent["planned_start"]
    if parent["planned_finish"]:
        r["pf"], r["slip"], r["slipOpen"] = parent["planned_finish"], None, False
        if r["allDone"]:
            r["slip"] = _days(r["af"], r["pf"])
        elif today > r["pf"]:
            r["slip"], r["slipOpen"] = _days(today, r["pf"]), True
    if parent["planned_cost"] is not None:
        r["pc"] = parent["planned_cost"]
        r["cv"], r["cvN"] = round(r["ac"]["sum"] - r["pc"], 4), r["ac"]["n"]
    return r


def assemble(rows: list[dict], project: str, today: str, issues_url: str = "") -> dict:
    """build() over rows already read, so the CLI reads each ticket once."""
    by_id = {canon(r["id"]): r for r in rows}
    epic_rows = sorted((r for r in rows if r["kind"] == "epic"), key=lambda r: r["title"].lower())
    feat_rows = sorted((r for r in rows if r["kind"] == "feature"), key=lambda r: r["title"].lower())
    tickets_ = [r for r in rows if r["kind"] not in PARENT_TYPES]

    epics = [r["title"] for r in epic_rows]
    epic_of = {canon(r["id"]): i for i, r in enumerate(epic_rows)}
    features = [[] for _ in epics]
    feat_tickets: list = [[] for _ in epics]
    index = {}
    for f in feat_rows:
        i = epic_of.get(f["parent"])
        if i is None:
            continue            # a Feature with no Epic is an orphan: its tickets are Unassigned
        index[canon(f["id"])] = (i, len(features[i]))
        features[i].append(f)
        feat_tickets[i].append([])

    placed = []
    unassigned = []
    for r in tickets_:
        at = index.get(r["parent"])
        if at is None:
            unassigned.append(r)
        else:
            feat_tickets[at[0]][at[1]].append(r)
            placed.append((r, at))
    if unassigned:
        epics.append(UNASSIGNED)
        epic_rows.append(None)
        features.append([None])
        feat_tickets.append([unassigned])
        u = (len(epics) - 1, 0)
        placed += [(r, u) for r in unassigned]

    epic_po = [e["owner"] if e else None for e in epic_rows]
    feat_po = []
    rollups = {"all": rollup(tickets_, today)}
    for i, e in enumerate(epic_rows):
        members = [r for fl in feat_tickets[i] for r in fl]
        rollups[f"e{i}"] = _own_plan(rollup(members, today), e, today)
        owners = []
        for j, f in enumerate(features[i]):
            rollups[f"e{i}-f{j}"] = _own_plan(rollup(feat_tickets[i][j], today), f, today)
            own = f["owner"] if f else None
            owners.append([own or epic_po[i], own is None or own == epic_po[i]])
        feat_po.append(owners)

    tickets = [[r["id"], r["title"], *at,
                r["stage"], r["priority"], r["type"], r["start"], r["finish"],
                r["size"], r["cost"], r["tier"], r["devs"],
                r["planned_start"], r["planned_finish"], r["planned_cost"]]
               for r, at in placed]
    num = lambda r: int(r["number"]) if r and str(r["number"] or "").isdigit() else None  # noqa: E731
    return {
        "name": project or "Project",
        "built": today,
        "epics": epics,
        "features": [[f["title"] if f else UNASSIGNED for f in fl] for fl in features],
        "epicPO": epic_po,
        "featPO": feat_po,
        "epicIssues": [num(e) for e in epic_rows],
        "featIssues": [[num(f) for f in fl] for fl in features],
        "issuesUrl": issues_url,
        "rollups": rollups,
        "tickets": tickets,
        "unassigned": len(unassigned),
    }


def _embed(data: dict) -> str:
    """JSON that is safe inside a <script> element: no `<` survives, so ticket
    text cannot close the script or open a comment."""
    return (json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def render(items, project: str = "", today: str | None = None, issues_url: str = "") -> str:
    """The management report page for a list of build_kanban Items."""
    data = build(items, project, today, issues_url)
    title = (data["name"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return (_TEMPLATE.read_text(encoding="utf-8")
            .replace("__TITLE__", title)
            .replace("__DATA__", _embed(data)))


# ── CLI ───────────────────────────────────────────────────────────────



class _Ticket:
    """The few Item fields ticket_row reads, from a cache file.

    The CLI runs from a project's installed .claude/tools/, where build_kanban
    is not installed, so it reads the frontmatter with ticket_serializer, which
    is. Cost is not needed to check or list groups."""

    def __init__(self, path: Path):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from ticket_serializer import parse_md
        data = parse_md(path.read_text(encoding="utf-8"))
        body = data.pop("body", "")
        self.fm = {k: v for k, v in data.items() if v is not None}
        self.slug = _field(self.fm, "slug") or path.stem
        self.id = _field(self.fm, "id").upper()
        self.title = _field(self.fm, "title") or next(
            (ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), self.slug)
        self.status = path.parent.name
        self.priority = _field(self.fm, "priority").upper()
        self.size = _field(self.fm, "size").upper()
        self.closed = _field(self.fm, "closed")
        self.cost, self.cost_tier = None, "unattributed"


def _load(cache: str):
    return [_Ticket(p) for p in sorted(Path(cache).glob("*/*/*.md"))
            if p.parent.name in LANES]


def problems(rows: list[dict], data: dict) -> list[str]:
    """Every ticket not in a Feature and Epic, every Feature with no Epic,
    every Epic with no owner, and every sub-issue link GitHub refused."""
    placed = {canon(t[0]) for t in data["tickets"]
              if data["epics"][t[2]] != UNASSIGNED}
    epics = {canon(r["id"]) for r in rows if r["kind"] == "epic"}
    out = [f"{r['id']}: not in a Feature and an Epic" for r in rows
           if r["kind"] not in PARENT_TYPES and canon(r["id"]) not in placed]
    out += [f"{r['id']}: Feature with no Epic" for r in rows
            if r["kind"] == "feature" and r["parent"] not in epics]
    out += [f"epic '{name}': no product owner"
            for name, po in zip(data["epics"], data["epicPO"])
            if name != UNASSIGNED and not po]
    out += [f"{r['id']}: GitHub refused its sub-issue link ({r['refused']})"
            for r in rows if r["refused"]]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ADT management report")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list-groups", metavar="CACHE")
    g.add_argument("--check-assigned", metavar="CACHE")
    a = ap.parse_args(argv)
    cache = a.list_groups or a.check_assigned
    if not Path(cache).is_dir():
        print(f"not a directory: {cache}", file=sys.stderr)
        return 2
    items = _load(cache)
    today = date.today().isoformat()
    rows = [ticket_row(it, today) for it in items]
    data = assemble(rows, "", today)
    if a.list_groups:
        ids = {r["number"]: r["id"] for r in rows if r["number"]}
        tag = lambda n: (ids.get(str(n)) or "") + " " if n else ""  # noqa: E731
        for e, name in enumerate(data["epics"]):
            print(f"{tag(data['epicIssues'][e])}{name}  (owner: {data['epicPO'][e] or '-'})")
            for f, feat in enumerate(data["features"][e]):
                po, inh = data["featPO"][e][f]
                print(f"  {tag(data['featIssues'][e][f])}{feat}  "
                      f"(owner: {po or '-'}{', inherited' if inh and po else ''})")
        return 0
    found = problems(rows, data)
    for p in found:
        print(p)
    if found:
        print(f"{len(found)} problem(s)", file=sys.stderr)
        return 1
    print(f"all {len(items)} tickets are in a Feature and an Epic, and every Epic has an owner")
    return 0


if __name__ == "__main__":
    sys.exit(main())
