# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""The management report: a project's tickets grouped Epic > Feature > Ticket
on a timeline, with owners, devs, plan and actual dates, and cost (AO-028).

build_kanban.run() calls render() with the Item list it already loaded and
writes the page beside kanban.html; adt_watch copies it into the project's
.adt/. The page is self-contained and loads nothing from the network, because
it is opened from disk, often offline.

Each ticket's group comes from its own frontmatter (`epic:`, `feature:`), so
there is no registry of epics. A feature is the pair (epic, feature name): the
same name under two epics is two features. A ticket missing either field goes
under "Unassigned".

Owners are derived from the tickets. An epic's product owner is the most
common `product_owner:` among its tickets, a tie going to the value on the
earliest-created ticket. A feature's is the same over its own tickets; when it
has none, or it equals the epic's, it is shown as inherited.

CLI, against a backlog cache directory:
  --list-groups <cache>     print each epic, its owner and its features
  --check-assigned <cache>  exit 1 if a ticket has no epic or feature, or an
                            epic has no product owner
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

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


def _field(fm: dict, key: str) -> str:
    v = str(fm.get(key) or "").strip().strip("'\"")
    return "" if v.lower() in ("null", "none", "~") else v


def ticket_row(item, built: str) -> dict:
    """One ticket's fields, read from the board's Item."""
    fm = item.fm
    start = _day(fm.get("created")) or _day(item.closed) or _day(fm.get("updated")) or built
    return {
        "id": item.id or item.slug,
        "title": item.title,
        "epic": _field(fm, "epic") or UNASSIGNED,
        "feature": _field(fm, "feature") or UNASSIGNED,
        "assigned": bool(_field(fm, "epic") and _field(fm, "feature")),
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


def _owner(rows: list[dict]) -> str | None:
    """Most common owner over rows; a tie goes to the earliest-created ticket's."""
    counts = Counter(r["owner"] for r in rows if r["owner"])
    if not counts:
        return None
    top = max(counts.values())
    tied = {o for o, n in counts.items() if n == top}
    for r in sorted(rows, key=lambda r: (r["start"], r["id"])):
        if r["owner"] in tied:
            return r["owner"]
    return None


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


def build(items, project: str = "", today: str | None = None) -> dict:
    """The page's data: epics, features, owners, ticket rows and roll-ups."""
    today = today or date.today().isoformat()
    return assemble([ticket_row(it, today) for it in items], project, today)


def assemble(rows: list[dict], project: str, today: str) -> dict:
    """build() over rows already read, so the CLI reads each ticket once."""
    by_epic: dict[str, list[dict]] = {}
    by_feature: dict[str, dict[str, list[dict]]] = {}
    last = lambda name: (name == UNASSIGNED, name.lower())  # noqa: E731
    for r in sorted(rows, key=lambda r: (last(r["epic"]), last(r["feature"]))):
        by_epic.setdefault(r["epic"], []).append(r)
        by_feature.setdefault(r["epic"], {}).setdefault(r["feature"], []).append(r)
    epics = list(by_epic)
    features = [list(by_feature[e]) for e in epics]
    index = {(e, f): (i, j) for i, e in enumerate(epics)
             for j, f in enumerate(features[i])}

    epic_po = [_owner(by_epic[e]) for e in epics]
    feat_po = []
    rollups = {"all": rollup(rows, today)}
    for i, e in enumerate(epics):
        rollups[f"e{i}"] = rollup(by_epic[e], today)
        owners = []
        for j, f in enumerate(features[i]):
            members = by_feature[e][f]
            rollups[f"e{i}-f{j}"] = rollup(members, today)
            own = _owner(members)
            owners.append([own or epic_po[i], own is None or own == epic_po[i]])
        feat_po.append(owners)

    tickets = [[r["id"], r["title"], *index[(r["epic"], r["feature"])],
                r["stage"], r["priority"], r["type"], r["start"], r["finish"],
                r["size"], r["cost"], r["tier"], r["devs"],
                r["planned_start"], r["planned_finish"], r["planned_cost"]]
               for r in rows]
    return {
        "name": project or "Project",
        "built": today,
        "epics": epics,
        "features": features,
        "epicPO": epic_po,
        "featPO": feat_po,
        "rollups": rollups,
        "tickets": tickets,
        "unassigned": sum(1 for r in rows if not r["assigned"]),
    }


def _embed(data: dict) -> str:
    """JSON that is safe inside a <script> element: no `<` survives, so ticket
    text cannot close the script or open a comment."""
    return (json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def render(items, project: str = "", today: str | None = None) -> str:
    """The management report page for a list of build_kanban Items."""
    data = build(items, project, today)
    title = (data["name"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return (_TEMPLATE.read_text(encoding="utf-8")
            .replace("__TITLE__", title)
            .replace("__DATA__", _embed(data)))


# ── CLI ───────────────────────────────────────────────────────────────

_LANES = ("ideas", "planned", "building", "qa", "blocked", "ready-to-release", "done")


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
        self.title = next((ln[2:].strip() for ln in body.splitlines()
                           if ln.startswith("# ")), self.slug)
        self.status = path.parent.name
        self.priority = _field(self.fm, "priority").upper()
        self.size = _field(self.fm, "size").upper()
        self.closed = _field(self.fm, "closed")
        self.cost, self.cost_tier = None, "unattributed"


def _load(cache: str):
    return [_Ticket(p) for p in sorted(Path(cache).glob("*/*/*.md"))
            if p.parent.name in _LANES]


def problems(rows: list[dict], data: dict) -> list[str]:
    """Every ticket with no epic or feature, and every epic with no owner."""
    out = [f"{r['id']}: no epic or feature" for r in rows if not r["assigned"]]
    out += [f"epic '{name}': no product owner"
            for name, po in zip(data["epics"], data["epicPO"])
            if name != UNASSIGNED and not po]
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
        for e, name in enumerate(data["epics"]):
            print(f"{name}  (owner: {data['epicPO'][e] or '-'})")
            for f, feat in enumerate(data["features"][e]):
                po, inh = data["featPO"][e][f]
                print(f"  {feat}  (owner: {po or '-'}{', inherited' if inh and po else ''})")
        return 0
    found = problems(rows, data)
    for p in found:
        print(p)
    if found:
        print(f"{len(found)} problem(s)", file=sys.stderr)
        return 1
    print(f"all {len(items)} tickets have an epic and a feature, and every epic has an owner")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# adt-bundle: v0.4.0
