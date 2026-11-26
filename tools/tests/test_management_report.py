# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""The management report (AO-028): the page build_kanban.run() writes beside
kanban.html, its roll-ups and owners, and its CLI."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402
import management_report  # noqa: E402

TODAY = "2026-11-24"


def _ticket(cache: Path, slug: str, stage: str = "done", folder: str = "enhancements",
            **fm) -> None:
    d = cache / folder / stage
    d.mkdir(parents=True, exist_ok=True)
    fields = {"slug": slug, "id": slug.upper(), "type": "enhancement",
              "stage": stage, "created": "2026-11-01", **fm}
    head = "\n".join(f"{k}: {v}" for k, v in fields.items())
    (d / f"{slug}.md").write_text(f"---\n{head}\n---\n\n# {fm.get('title', 'Title of ' + slug)}\n")


def _epic(cache: Path, slug: str, title: str, **fm) -> None:
    _ticket(cache, slug, "ideas", "epics", type="epic", title=title, **fm)


def _feature(cache: Path, slug: str, title: str, parent: str, **fm) -> None:
    _ticket(cache, slug, "ideas", "features", type="feature", title=title, parent=parent, **fm)


def _grouped(cache: Path, owner="amy") -> None:
    """Epic E1 (owner amy) > Feature F1, the parent most tests hang tickets on."""
    _epic(cache, "e1", "E", product_owner=owner)
    _feature(cache, "f1", "F", "E1")


def _render(cache: Path) -> str:
    """Render through the live entry point and return the written report."""
    build_kanban.run(str(cache), backlog_root="", id_prefix="T", quiet=True)
    return (cache / "management-report.html").read_text()


def _data(html: str) -> dict:
    m = re.search(r'const P = (.*?);\n', html, re.S)
    assert m, "no embedded data"
    return json.loads(m.group(1))


def _items(cache: Path):
    build_kanban.configure(str(cache), backlog_root="")
    return build_kanban.load_items()


def _row(id_, cost=None, tier="measured", start="2026-11-01", finish=None,
         ps=None, pf=None, pc=None, owner=None):
    return {"id": id_, "start": start, "finish": finish, "cost": cost, "tier": tier,
            "planned_start": ps, "planned_finish": pf, "planned_cost": pc, "owner": owner}


def test_every_ticket_is_embedded(tmp_path):
    _grouped(tmp_path)
    for s in ("a", "b", "c"):
        _ticket(tmp_path, s, parent="F1")
    _ticket(tmp_path, "d", stage="ideas", cost_usd="2.5", cost_tier="measured")
    rows = {t[0]: t for t in _data(_render(tmp_path))["tickets"]}
    assert sorted(rows) == ["A", "B", "C", "D"]
    # A stamped cost_usd arrives in dollars: the board holds micro-dollars.
    assert rows["D"][10] == 2.5 and rows["A"][10] is None


def test_report_contains_only_its_own_project(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    _grouped(one)
    _grouped(two)
    _ticket(one, "alpha-only", parent="F1")
    _ticket(two, "beta-only", parent="F1")
    a, b = _render(one), _render(two)
    assert "ALPHA-ONLY" in a and "BETA-ONLY" not in a
    assert "BETA-ONLY" in b and "ALPHA-ONLY" not in b


def test_rollup_sums_cost_and_counts_coverage():
    r = management_report.rollup(
        [_row("a", cost=2.5), _row("b", cost=1.5), _row("c")], TODAY)
    assert r["ac"] == {"sum": 4.0, "n": 2, "of": 3, "approx": False}
    assert r["n"] == 3


def test_non_measured_cost_is_marked(tmp_path):
    r = management_report.rollup([_row("a", cost=2.0), _row("b", cost=1.0, tier="estimated")], TODAY)
    assert r["ac"]["approx"] is True
    # The page prefixes an approximate roll-up with "~".
    html = _render(_seed_one(tmp_path))
    assert '(a.ac.approx?"~":"")' in html


def _seed_one(cache: Path) -> Path:
    _grouped(cache)
    _ticket(cache, "one", parent="F1")
    return cache


def test_slip_and_cost_variance():
    done = management_report.rollup(
        [_row("a", finish="2026-11-10", ps="2026-11-04", pf="2026-11-07", cost=5.0, pc=3.0),
         _row("b", finish="2026-11-05", ps="2026-11-02", pf="2026-11-06", cost=1.0, pc=1.5),
         # Costed but unplanned: in the actual cost, out of the variance.
         _row("c", finish="2026-11-08", cost=2.0)], TODAY)
    assert done["ps"] == "2026-11-02" and done["pf"] == "2026-11-07"
    assert done["nPlan"] == 2 and done["pc"] == 4.5
    assert done["slip"] == 3 and done["slipOpen"] is False
    assert done["cvN"] == 2 and done["cv"] == 1.5
    open_ = management_report.rollup([_row("a", pf="2026-11-20")], TODAY)
    assert open_["slip"] == 4 and open_["slipOpen"] is True
    early = management_report.rollup([_row("a", pf="2026-12-20")], TODAY)
    assert early["slip"] is None


def test_feature_inherits_epic_owner(tmp_path):
    _epic(tmp_path, "e1", "E", product_owner="alice")
    _feature(tmp_path, "f1", "One", "E1")
    _feature(tmp_path, "f2", "Two", "E1", product_owner="alice")
    _feature(tmp_path, "f3", "Three", "E1", product_owner="bob")
    for s, f in (("a", "F1"), ("b", "F2"), ("c", "F3")):
        _ticket(tmp_path, s, parent=f)
    d = _data(_render(tmp_path))
    e = d["epics"].index("E")
    assert d["epicPO"][e] == "alice"
    po = dict(zip(d["features"][e], d["featPO"][e]))
    assert po["One"] == ["alice", True]
    assert po["Two"] == ["alice", True]
    assert po["Three"] == ["bob", False]
    assert 'inh: P.featPO[ctx.e][ctx.f][1]' in _render(tmp_path)


def test_epic_owner_is_the_epic_tickets(tmp_path):
    _epic(tmp_path, "e1", "E", product_owner="erin")
    _feature(tmp_path, "f1", "F", "E1")
    _ticket(tmp_path, "a", parent="F1", product_owner="zed")
    _ticket(tmp_path, "b", parent="F1", product_owner="zed")
    d = _data(_render(tmp_path))
    assert d["epicPO"][d["epics"].index("E")] == "erin"


def test_feature_is_found_by_parent_id(tmp_path):
    _epic(tmp_path, "e1", "Billing", product_owner="amy")
    _epic(tmp_path, "e2", "Catalogue", product_owner="amy")
    _feature(tmp_path, "f1", "Search", "E1")
    _feature(tmp_path, "f2", "Search", "E2", id="F-2")
    _ticket(tmp_path, "a", parent="F1")
    _ticket(tmp_path, "b", parent="f-02")
    d = _data(_render(tmp_path))
    assert d["features"][d["epics"].index("Billing")] == ["Search"]
    assert d["features"][d["epics"].index("Catalogue")] == ["Search"]
    rows = {t[0]: t[2] for t in d["tickets"]}
    assert d["epics"][rows["A"]] == "Billing" and d["epics"][rows["B"]] == "Catalogue"


def test_no_plan_means_no_plan_values(tmp_path):
    _grouped(tmp_path)
    _ticket(tmp_path, "a", parent="F1")
    _ticket(tmp_path, "b", parent="F1", planned_start="2026-11-02",
            planned_finish="2026-11-09", planned_cost=12.5)
    d = _data(_render(tmp_path))
    t = {r[0]: r for r in d["tickets"]}
    assert t["A"][13:16] == [None, None, None]
    assert t["B"][13:16] == ["2026-11-02", "2026-11-09", 12.5]
    assert d["rollups"]["all"]["nPlan"] == 1
    assert "examplePlan" not in _render(tmp_path)


def test_unassigned_tickets_get_their_own_lane(tmp_path):
    _grouped(tmp_path)
    _feature(tmp_path, "f9", "Orphan", "E99")
    _ticket(tmp_path, "a", parent="F1")
    _ticket(tmp_path, "b")
    _ticket(tmp_path, "c", parent="A")       # a plain ticket is not a Feature
    _ticket(tmp_path, "d", parent="F9")      # a Feature with no Epic
    d = _data(_render(tmp_path))
    assert d["epics"][-1] == "Unassigned"
    assert d["features"][-1] == ["Unassigned"]
    assert d["unassigned"] == 3
    assert "Orphan" not in sum(d["features"], [])


def test_embedded_data_cannot_close_the_script(tmp_path):
    _epic(tmp_path, "e1", "</script><script>alert(1)</script>", product_owner="a")
    _feature(tmp_path, "f1", "<!--", "E1")
    _ticket(tmp_path, "x", parent="F1")
    html = _render(tmp_path)
    script = html.split("<script>", 1)[1]
    assert script.count("</script>") == 1
    assert "<!--" not in script
    assert _data(html)["epics"][0] == "</script><script>alert(1)</script>"


def test_source_names_no_project():
    for f in ("management_report.py", "management_report_template.html"):
        text = (TOOLS / f).read_text().lower()
        for name in ("agenticz", "ai-orchestrator", "zurichrich/"):
            assert name not in text, f"{f} names {name}"
        assert "googleapis" not in text and "https://" not in text, f"{f} loads from the network"


def test_page_carries_every_control_and_state(tmp_path):
    html = _render(_seed_one(tmp_path))
    for el in ('id="zoomCtl"', 'data-z="quarter"', 'data-z="month"', 'data-z="week"',
               'id="dfCtl"', 'data-df="week"', 'data-df="date"',
               'id="layerCtl"', 'data-l="actual"', 'data-l="plan"', 'data-l="both"',
               'id="colBtn"', 'id="colMenu"', 'id="crumbs"', 'class="up-btn"',
               'class="open-btn"', "dblclick"):
        assert el in html, el
    for preset in ("People & cost", "Schedule", "Budget", "Everything"):
        assert f'["{preset}",' in html, preset
    assert 'class="lv"' in html, "column level notes"
    assert 'ev.key!=="Escape"' in html
    for key in ("adt-report-zoom", "adt-report-df", "adt-report-layer", "adt-report-cols"):
        assert f'"{key}"' in html, key
    for state in ("done", "flight", "idea", "blocked"):
        assert f".tb.{state}" in html, state
    assert ".who.inh" in html, "inherited-owner marker"
    assert 'cols:["po","dev","ac"]' in html, "default columns"


def test_board_still_renders_when_the_report_fails(tmp_path, monkeypatch, capsys):
    _seed_one(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("report exploded")
    monkeypatch.setattr(management_report, "render", boom)
    build_kanban.run(str(tmp_path), backlog_root="", id_prefix="T", quiet=True)
    assert (tmp_path / "kanban.html").stat().st_size > 0
    assert not (tmp_path / "management-report.html").exists()
    assert "report exploded" in capsys.readouterr().err


def test_check_assigned_fails_on_an_ungrouped_ticket(tmp_path, capsys):
    _grouped(tmp_path)
    _ticket(tmp_path, "a", parent="F1")
    assert management_report.main(["--check-assigned", str(tmp_path)]) == 0
    _ticket(tmp_path, "b", stage="done")        # a done ticket counts too
    assert management_report.main(["--check-assigned", str(tmp_path)]) == 1
    assert "B: not in a Feature and an Epic" in capsys.readouterr().out


def test_check_assigned_fails_on_an_epic_with_no_owner(tmp_path, capsys):
    _epic(tmp_path, "e1", "E")
    _feature(tmp_path, "f1", "F", "E1")
    _ticket(tmp_path, "a", parent="F1")
    assert management_report.main(["--check-assigned", str(tmp_path)]) == 1
    assert "epic 'E': no product owner" in capsys.readouterr().out


def test_list_groups_prints_epics_features_and_owners(tmp_path, capsys):
    _epic(tmp_path, "e1", "Billing", product_owner="amy", issue_number=40, id="T-40")
    _feature(tmp_path, "f1", "Invoices", "T-40", issue_number=41, id="T-41")
    _feature(tmp_path, "f2", "Refunds", "T-40", product_owner="bo", issue_number=42, id="T-42")
    assert management_report.main(["--list-groups", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "T-40 Billing  (owner: amy)" in out
    assert "  T-41 Invoices  (owner: amy, inherited)" in out
    assert "  T-42 Refunds  (owner: bo)" in out


def test_groups_come_from_parent_tickets(tmp_path):
    _epic(tmp_path, "e1", "Renamed epic", product_owner="amy")
    _feature(tmp_path, "f1", "Renamed feature", "E1")
    _ticket(tmp_path, "a", parent="F1", epic="Old name", feature="Old feature")
    d = _data(_render(tmp_path))
    assert d["epics"] == ["Renamed epic"] and d["features"] == [["Renamed feature"]]
    assert d["tickets"][0][2:4] == [0, 0]


def test_epic_and_feature_tickets_are_not_counted_as_tickets(tmp_path):
    _grouped(tmp_path)
    _ticket(tmp_path, "a", parent="F1", cost_usd="1.5", cost_tier="measured")
    d = _data(_render(tmp_path))
    assert [t[0] for t in d["tickets"]] == ["A"]
    assert d["rollups"]["all"]["n"] == 1 and d["rollups"]["e0"]["n"] == 1
    assert d["rollups"]["e0-f0"]["ac"]["sum"] == 1.5


def test_own_plan_is_compared_with_children(tmp_path):
    _epic(tmp_path, "e1", "E", product_owner="amy", planned_finish="2026-11-20",
          planned_cost="10")
    _feature(tmp_path, "f1", "F", "E1")
    _ticket(tmp_path, "a", parent="F1", closed="2026-11-22", planned_finish="2026-11-25",
            planned_cost="4", cost_usd="6", cost_tier="measured")
    d = _data(_render(tmp_path))
    e, f = d["rollups"]["e0"], d["rollups"]["e0-f0"]
    assert e["pf"] == "2026-11-20" and e["pc"] == 10.0
    assert e["kids"]["pf"] == "2026-11-25" and e["kids"]["pc"] == 4.0
    assert e["slip"] == 2                       # finished 22nd against its own 20th
    assert e["cv"] == -4.0 and e["cvN"] == 1    # 6 spent against its own 10
    assert "kids" not in f and f["slip"] == -3  # the Feature has no plan of its own


def test_epic_and_feature_rows_link_to_their_issues(tmp_path):
    # The render derives an id from issue_number, so the parents are T-40, T-41.
    _epic(tmp_path, "e1", "E", product_owner="amy", issue_number=40)
    _feature(tmp_path, "f1", "F", "T-40", issue_number=41)
    _ticket(tmp_path, "a", parent="T-41")
    _ticket(tmp_path, "b")
    build_kanban.run(str(tmp_path), backlog_root="", id_prefix="T", quiet=True,
                     issues_url="https://example.test/o/r/issues")
    d = _data((tmp_path / "management-report.html").read_text())
    assert d["epicIssues"] == [40, None] and d["featIssues"] == [[41], [None]]
    assert d["issuesUrl"] == "https://example.test/o/r/issues"


def test_check_assigned_names_a_refused_link(tmp_path, capsys):
    _grouped(tmp_path)
    _ticket(tmp_path, "a", parent="F1", sub_issue_refused="422 limit reached")
    assert management_report.main(["--check-assigned", str(tmp_path)]) == 1
    assert "A: GitHub refused its sub-issue link (422 limit reached)" in capsys.readouterr().out


def test_page_shows_children_plan_and_issue_links(tmp_path):
    html = _render(_seed_one(tmp_path))
    assert '<small class="kids">children: ' in html
    assert "P.epicIssues[e]" in html and "P.featIssues[e]" in html
    assert '<a class="name" href=' in html