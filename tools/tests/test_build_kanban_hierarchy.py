# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""AO-028: Epic and Feature tickets on the board. Their cards say what they
are, and their lanes follow their children through build_kanban.run()."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build_kanban  # noqa: E402


def _t(cache: Path, folder: str, lane: str, tid: str, typ: str, **fm) -> Path:
    d = cache / folder / lane
    d.mkdir(parents=True, exist_ok=True)
    head = "\n".join(f"{k}: {v}" for k, v in
                     {"slug": tid.lower(), "id": tid, "type": typ, "stage": lane, "state": "open", **fm}.items())
    p = d / f"{tid.lower()}.md"
    p.write_text(f"---\n{head}\n---\n\n# {fm.get('title', tid)}\n")
    return p


def _tree(cache: Path, *ticket_lanes: str) -> None:
    _t(cache, "epics", "ideas", "T-1", "epic", title="Billing")
    _t(cache, "features", "ideas", "T-2", "feature", title="Invoices", parent="T-1")
    for n, lane in enumerate(ticket_lanes, start=10):
        _t(cache, "enhancements", lane, f"T-{n}", "enhancement", parent="T-2")


def _run(cache: Path) -> str:
    build_kanban.run(str(cache), backlog_root="", id_prefix="T", quiet=True)
    return (cache / "kanban.html").read_text()


def _lane(cache: Path, folder: str, tid: str) -> str:
    return next((cache / folder).glob(f"*/{tid.lower()}.md")).parent.name


def test_epic_and_feature_cards_are_titled(tmp_path):
    _tree(tmp_path, "ideas")
    html = _run(tmp_path)
    assert '<div class="card" data-type="epics"' in html
    assert '<div class="card" data-type="features"' in html
    assert ">Epic: Billing</a>" in html and ">Feature: Invoices</a>" in html
    assert '<button data-filter="epics">Epics</button>' in html


def test_feature_lane_follows_its_tickets(tmp_path):
    _tree(tmp_path, "ideas", "ideas")
    _run(tmp_path)
    assert _lane(tmp_path, "features", "T-2") == "ideas"
    (tmp_path / "enhancements/qa").mkdir()
    (tmp_path / "enhancements/ideas/t-10.md").rename(tmp_path / "enhancements/qa/t-10.md")
    _run(tmp_path)
    assert _lane(tmp_path, "features", "T-2") == "building"
    for n in (10, 11):
        src = next((tmp_path / "enhancements").glob(f"*/t-{n}.md"))
        (tmp_path / "enhancements/done").mkdir(exist_ok=True)
        src.rename(tmp_path / "enhancements/done" / src.name)
    _run(tmp_path)
    p = tmp_path / "features/done/t-2.md"
    assert p.exists() and "stage: done" in p.read_text() and "state: closed" in p.read_text()


def test_epic_lane_follows_its_features(tmp_path):
    _tree(tmp_path, "done")
    _t(tmp_path, "features", "ideas", "T-3", "feature", title="Refunds", parent="T-1")
    _t(tmp_path, "enhancements", "ideas", "T-20", "enhancement", parent="T-3")
    _run(tmp_path)
    # One Feature done, the other still in ideas: the Epic is under way.
    assert _lane(tmp_path, "features", "T-2") == "done"
    assert _lane(tmp_path, "features", "T-3") == "ideas"
    assert _lane(tmp_path, "epics", "T-1") == "building"
    (tmp_path / "enhancements/ideas/t-20.md").rename(tmp_path / "enhancements/done/t-20.md")
    _run(tmp_path)
    assert _lane(tmp_path, "epics", "T-1") == "done"


def test_a_hand_moved_epic_goes_back(tmp_path):
    _tree(tmp_path, "ideas")
    (tmp_path / "epics/done").mkdir()
    (tmp_path / "epics/ideas/t-1.md").rename(tmp_path / "epics/done/t-1.md")
    _run(tmp_path)
    assert _lane(tmp_path, "epics", "T-1") == "ideas"
    assert "stage: ideas" in (tmp_path / "epics/ideas/t-1.md").read_text()
