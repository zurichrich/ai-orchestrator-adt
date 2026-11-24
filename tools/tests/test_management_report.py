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


def _ticket(cache: Path, slug: str, stage: str = "done", **fm) -> None:
    d = cache / "enhancements" / stage
    d.mkdir(parents=True, exist_ok=True)
    fields = {"slug": slug, "id": slug.upper(), "type": "enhancement",
              "stage": stage, "created": "2026-11-01", **fm}
    head = "\n".join(f"{k}: {v}" for k, v in fields.items())
    (d / f"{slug}.md").write_text(f"---\n{head}\n---\n\n# Title of {slug}\n")


def _render(cache: Path) -> str:
    """Render through the live entry point and return the written report."""
    build_kanban.run(str(cache), backlog_root="", id_prefix="T", quiet=True)
    return (cache / "management-report.html").read_text()


def _data(html: str) -> dict:
    m = re.search(r'const PROJECTS = \{"p": (.*?)\};\n', html, re.S)
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
    for s in ("a", "b", "c"):
        _ticket(tmp_path, s, epic="Billing", feature="Invoices")
    _ticket(tmp_path, "d", stage="ideas")
    ids = [t[0] for t in _data(_render(tmp_path))["tickets"]]
    assert sorted(ids) == ["A", "B", "C", "D"]


def test_report_contains_only_its_own_project(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    _ticket(one, "alpha-only", epic="E", feature="F")
    _ticket(two, "beta-only", epic="E", feature="F")
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
    _ticket(cache, "one", epic="E", feature="F")
    return cache


def test_slip_and_cost_variance():
    done = management_report.rollup(
        [_row("a", finish="2026-11-10", pf="2026-11-07", cost=5.0, pc=3.0),
         _row("b", finish="2026-11-05", pf="2026-11-06", cost=1.0)], TODAY)
    assert done["slip"] == 3 and done["slipOpen"] is False
    assert done["cvN"] == 1 and done["cv"] == 2.0
    open_ = management_report.rollup([_row("a", pf="2026-11-20")], TODAY)
    assert open_["slip"] == 4 and open_["slipOpen"] is True
    early = management_report.rollup([_row("a", pf="2026-12-20")], TODAY)
    assert early["slip"] is None


def test_feature_inherits_epic_owner(tmp_path):
    _ticket(tmp_path, "a", epic="E", feature="One", product_owner="alice")
    _ticket(tmp_path, "b", epic="E", feature="One", product_owner="alice")
    _ticket(tmp_path, "c", epic="E", feature="Two")
    _ticket(tmp_path, "d", epic="E", feature="Three", product_owner="bob")
    d = _data(_render(tmp_path))
    e = d["epics"].index("E")
    assert d["epicPO"][e] == "alice"
    po = dict(zip(d["features"][e], d["featPO"][e]))
    assert po["One"] == ["alice", True]
    assert po["Two"] == ["alice", True]
    assert po["Three"] == ["bob", False]
    assert 'inh: P.featPO[ctx.e][ctx.f][1]' in _render(tmp_path)


def test_owner_tie_goes_to_earliest_ticket():
    rows = [_row("late", start="2026-11-09", owner="zed"),
            _row("early", start="2026-11-02", owner="amy")]
    assert management_report._owner(rows) == "amy"
    assert management_report._owner(list(reversed(rows))) == "amy"


def test_feature_is_keyed_by_epic_and_name(tmp_path):
    _ticket(tmp_path, "a", epic="Billing", feature="Search")
    _ticket(tmp_path, "b", epic="Catalogue", feature="Search")
    d = _data(_render(tmp_path))
    assert d["features"][d["epics"].index("Billing")] == ["Search"]
    assert d["features"][d["epics"].index("Catalogue")] == ["Search"]
    rows = {t[0]: (t[2], t[3]) for t in d["tickets"]}
    assert rows["A"][0] != rows["B"][0]


def test_no_plan_means_no_plan_values(tmp_path):
    _ticket(tmp_path, "a", epic="E", feature="F")
    _ticket(tmp_path, "b", epic="E", feature="F", planned_start="2026-11-02",
            planned_finish="2026-11-09", planned_cost=12.5)
    d = _data(_render(tmp_path))
    t = {r[0]: r for r in d["tickets"]}
    assert t["A"][13:16] == [None, None, None]
    assert t["B"][13:16] == ["2026-11-02", "2026-11-09", 12.5]
    assert d["rollups"]["all"]["nPlan"] == 1
    assert "examplePlan" not in _render(tmp_path)


def test_unassigned_tickets_get_their_own_lane(tmp_path):
    _ticket(tmp_path, "a", epic="E", feature="F")
    _ticket(tmp_path, "b")
    d = _data(_render(tmp_path))
    assert d["epics"][-1] == "Unassigned"
    assert d["features"][-1] == ["Unassigned"]
    assert d["unassigned"] == 1


def test_embedded_data_cannot_close_the_script(tmp_path):
    _ticket(tmp_path, "x", epic="</script><script>alert(1)</script>", feature="<!--")
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
    _ticket(tmp_path, "a", epic="E", feature="F", product_owner="amy")
    assert management_report.main(["--check-assigned", str(tmp_path)]) == 0
    _ticket(tmp_path, "b", epic="E")
    assert management_report.main(["--check-assigned", str(tmp_path)]) == 1
    assert "B: no epic or feature" in capsys.readouterr().out


def test_check_assigned_fails_on_an_epic_with_no_owner(tmp_path, capsys):
    _ticket(tmp_path, "a", epic="E", feature="F")
    assert management_report.main(["--check-assigned", str(tmp_path)]) == 1
    assert "epic 'E': no product owner" in capsys.readouterr().out


def test_list_groups_prints_epics_features_and_owners(tmp_path, capsys):
    _ticket(tmp_path, "a", epic="Billing", feature="Invoices", product_owner="amy")
    _ticket(tmp_path, "b", epic="Billing", feature="Refunds", product_owner="bo")
    assert management_report.main(["--list-groups", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Billing  (owner: amy)" in out
    assert "  Invoices  (owner: amy, inherited)" in out
    assert "  Refunds  (owner: bo)" in out
