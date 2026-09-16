# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the token pricer in tools/adt_cost.py.

Covers derived cache rates, fast-mode pricing, unknown models, row parsing at
both widths, ticket totals, and fmt_cost output."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import adt_cost  # noqa: E402

PRICES = adt_cost.load_prices()


# ── the table itself ──────────────────────────────────────────────────────

def test_price_table_loads_and_is_versioned():
    assert PRICES is not None, "defaults/pricing.json must resolve from tools/"
    assert PRICES["version"], "the price table must have a version"
    assert PRICES["models"]["claude-opus-5"]["standard"]["input"] == 5.0


def test_cache_rates_are_derived_and_match_the_published_table():
    """Cache rates are computed as multiples of the base input rate."""
    r = adt_cost.rates_for(PRICES, "claude-opus-5", "standard")
    assert r["input"] == 5.0 and r["output"] == 25.0
    assert r["cache_read"] == 0.5          # 0.1x
    assert r["cache_write_5m"] == 6.25     # 1.25x
    assert r["cache_write_1h"] == 10.0     # 2x


def test_fast_mode_doubles_every_token_class():
    std = adt_cost.rates_for(PRICES, "claude-opus-5", "standard")
    fast = adt_cost.rates_for(PRICES, "claude-opus-5", "fast")
    for cls in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h"):
        assert fast[cls] == pytest.approx(std[cls] * 2), cls
    assert fast["cache_read"] == 1.0 and fast["cache_write_1h"] == 20.0


def test_unknown_model_has_no_rates():
    assert adt_cost.rates_for(PRICES, "claude-not-a-model", "standard") is None


def test_unknown_speed_falls_back_to_standard():
    r = adt_cost.rates_for(PRICES, "claude-opus-5", "warp-speed")
    assert r is not None and r["input"] == 5.0


# ── row parsing at both widths ────────────────────────────────────────────

def _row11(tix="ADT-1", inp=100, out=50, model="claude-opus-5", speed="standard",
           cread=1000, cw5=0, cw1=200, tier="measured"):
    return "\t".join(["2026-08-20T10:00:00Z", tix, str(inp), str(out), "sess",
                      model, speed, str(cread), str(cw5), str(cw1), tier])


def test_legacy_five_column_row_still_parses():
    r = adt_cost.parse_row("2026-08-20T10:00:00Z\tADT-1\t100\t50\tsess")
    assert r["input"] == 100 and r["output"] == 50
    assert r["model"] is None
    assert r["tier"] == "legacy", "a 5-column row should have tier legacy"


def test_eleven_column_row_parses_all_classes():
    r = adt_cost.parse_row(_row11())
    assert (r["cache_read"], r["cache_write_5m"], r["cache_write_1h"]) == (1000, 0, 200)
    assert r["model"] == "claude-opus-5" and r["tier"] == "measured"


def test_canon_tix_strips_leading_zeros():
    assert adt_cost.parse_row(_row11(tix="ADT-058"))["tix"] == "ADT-58"


# ── pricing a row ─────────────────────────────────────────────────────────

def test_measured_row_prices_every_class():
    """micros = tokens * ($/MTok): 100*5 + 50*25 + 1000*0.5 + 200*10 = 4250."""
    micros, tier = adt_cost.price_row(adt_cost.parse_row(_row11()), PRICES, None)
    assert tier == "measured"
    assert micros == 4250


def test_cache_heavy_row_costs_far_more_than_its_input_and_output():
    row = adt_cost.parse_row(_row11(inp=1, out=100, cread=1_000_000, cw1=0))
    micros, _ = adt_cost.price_row(row, PRICES, None)
    in_out_only = 1 * 5 + 100 * 25
    assert micros > in_out_only * 100


def test_fast_row_costs_double_a_standard_row():
    a, _ = adt_cost.price_row(adt_cost.parse_row(_row11()), PRICES, None)
    b, _ = adt_cost.price_row(adt_cost.parse_row(_row11(speed="fast")), PRICES, None)
    assert b == a * 2


def test_no_estimator_and_no_model_prices_to_none_not_zero():
    row = adt_cost.parse_row("2026-08-20T10:00:00Z\tADT-1\t100\t50\tsess")
    micros, tier = adt_cost.price_row(row, PRICES, None)
    assert micros is None and tier == "legacy"


# ── ticket totals ─────────────────────────────────────────────────────────

def test_ticket_total_sums_rows_priced_at_their_own_rates():
    """Rows on different models and speeds each price at their own rate."""
    lines = [_row11(model="claude-opus-5", speed="standard"),
             _row11(model="claude-opus-4-8", speed="fast")]
    totals = adt_cost.price_ledger(lines, PRICES, None)
    assert totals["ADT-1"]["micros"] == 4250 + 8500
    assert totals["ADT-1"]["tier"] == "measured"


def test_one_unmeasured_row_makes_the_whole_ticket_approximate():
    est = {"usd_per_mtok": 200.0, "derivation_id": "t"}
    lines = [_row11(), "2026-08-20T10:00:00Z\tADT-1\t10\t20\tsess"]
    totals = adt_cost.price_ledger(lines, PRICES, est)
    assert totals["ADT-1"]["tier"] != "measured", \
        "a total that includes an estimate should not be measured"


def test_ticket_with_no_rows_is_absent_not_zero():
    totals = adt_cost.price_ledger([_row11(tix="ADT-1")], PRICES, None)
    assert "ADT-999" not in totals


# ── formatting ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("micros,tier,expected", [
    (None, "measured", "$ —"),        # not "$0.00" and not a bare dash
    (4_250_000, "measured", "$4.25"),
    (4_250_000, "estimated", "~$4.25"),
    (4_250_000, "legacy", "~$4.25"),
    (12_340_000, "measured", "$12.34"),
    (1_500_000_000, "measured", "$1.5k"),
    (25_000_000_000, "measured", "$25k"),
    (1_000, "measured", "<$0.01"),
])
def test_fmt_cost(micros, tier, expected):
    assert adt_cost.fmt_cost(micros, tier) == expected


def test_estimates_are_prefixed_with_a_tilde_and_measured_costs_are_not():
    for micros in (1_000, 4_250_000, 1_500_000_000, 25_000_000_000):
        assert adt_cost.fmt_cost(micros, "estimated").startswith("~")
        assert not adt_cost.fmt_cost(micros, "measured").startswith("~")
