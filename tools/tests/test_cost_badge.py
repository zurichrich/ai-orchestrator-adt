# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests the cost badge on each kanban card and the cost total in the board
header, by reading the rendered board HTML."""

from __future__ import annotations

import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402

TICKET = """---
slug: {slug}
id: {tid}
title: {slug}
type: enhancement
priority: P1
size: S
stage: ideas
state: open
---

# {slug}

## Problem (user)
x
"""


def _board(tmp_path, rows, estimator=None):
    root = tmp_path / "proj"
    (root / ".adt" / "state").mkdir(parents=True, exist_ok=True)
    (root / ".adt" / "state" / "token-usage.log").write_text(
        "".join(r + "\n" for r in rows))
    if estimator:
        import json
        (root / ".adt" / "state"
         / "cost-estimator.json").write_text(json.dumps(estimator))
    bl = root / ".adt" / "backlog" / "enhancements" / "ideas"
    bl.mkdir(parents=True, exist_ok=True)
    for tid, slug in (("ADT-1", "measured-one"), ("ADT-2", "legacy-one"),
                      ("ADT-3", "no-rows-at-all")):
        (bl / f"{slug}.md").write_text(TICKET.format(slug=slug, tid=tid))
    build_kanban.run(str(root), backlog_root=".adt/backlog",
                     id_prefix="ADT")
    return (root / ".adt" / "backlog" / "kanban.html").read_text()


WIDE = ("2026-01-01T00:00:00Z\tADT-1\t1000\t2000\ts\tclaude-opus-5\tstandard\t"
        "500000\t0\t100000\tmeasured")
LEGACY = "2026-01-01T00:00:00Z\tADT-2\t400\t600\ts2"


def test_measured_card_shows_a_plain_dollar_figure(tmp_path):
    html = _board(tmp_path, [WIDE])
    assert 'class="badge cost" data-tier="measured"' in html
    # 1000*5 + 2000*25 + 500000*0.5 + 100000*10 = 1,305,000 micros, which
    # renders as "$1.30" because the float is just under the half.
    assert ">$1.30<" in html
    assert "~$1.30" not in html, "expected no ~ mark on a measured figure"


def test_card_with_no_ledger_rows_shows_a_dollar_dash_not_zero(tmp_path):
    html = _board(tmp_path, [WIDE])
    assert 'data-cost-tier="unattributed"' in html
    # Search only the badges: the board's JS contains '$0.00' elsewhere.
    badges = re.findall(r'<span class="badge cost".*?</span>', html)
    assert badges and all("$0.00" not in b for b in badges), \
        "expected no $0.00 badge for a card with no ledger rows"
    # "$ —" rather than a bare dash, so it cannot merge with the token badge's dash.
    assert any(">$ —<" in b for b in badges)


def test_data_cost_is_an_integer_for_the_js_sum(tmp_path):
    html = _board(tmp_path, [WIDE])
    assert 'data-cost="1305000"' in html, "expected data-cost as integer micro-dollars"


def test_header_carries_a_cost_total_element(tmp_path):
    html = _board(tmp_path, [WIDE])
    assert 'id="cost-total"' in html
    assert "fmtCost" in html, "expected the JS fmtCost function in the board"


def test_header_sums_the_same_visible_set_as_the_token_total(tmp_path):
    """The cost and token totals are summed in the same loop over visible cards."""
    html = _board(tmp_path, [WIDE])
    js = html[html.index("function applyFilters"):]
    body = js[:js.index("document.querySelectorAll('.lane')")]
    assert "tokenSum +=" in body and "costSum +=" in body


def test_estimated_card_is_visually_distinct(tmp_path):
    """An estimated cost carries a literal ~ mark. The estimator is needed to
    price the 5-column row at all."""
    html = _board(tmp_path, [WIDE, LEGACY],
                  estimator={"usd_per_mtok": 152.0, "derivation_id": "t",
                             "p10": 66.0, "p90": 544.0, "n_samples": 35})
    badges = re.findall(r'<span class="badge cost".*?</span>', html)
    approx = [b for b in badges if 'data-tier="legacy"' in b]
    assert approx, "expected the 5-column row to be priced by the estimator"
    assert ">~$" in approx[0], "expected the ~ mark on the estimated badge"


def test_badge_tier_is_exposed_to_the_header_for_the_approximation_mark(tmp_path):
    html = _board(tmp_path, [WIDE])
    assert "data-cost-tier=" in html
    assert "costApprox" in html
