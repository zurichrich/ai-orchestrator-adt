# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests adt_cost's ledger pricing against the checked-in fixture in
fixtures/cost-ledger.

The fixture has a multi-model ticket, a fast-mode row, a legacy 5-column row,
a 5-minute-TTL cache write, and an unattributed ticket."""

from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import adt_cost  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "cost-ledger"
LINES = (FIX / "ledger.tsv").read_text().splitlines()
EXPECTED = json.loads((FIX / "expected.json").read_text())
PRICES = adt_cost.load_prices()


def _totals(estimator=None):
    return adt_cost.price_ledger(LINES, PRICES, estimator)


def test_price_table_version_is_the_one_the_fixture_was_computed_at():
    """A change to the price table fails here instead of shifting the expected figures."""
    assert PRICES["version"] == EXPECTED["price_table_version"]


def test_multi_model_multi_speed_ticket_prices_to_the_hand_computed_total():
    got = _totals()["ADT-900"]
    assert got["tier"] == "measured"
    assert abs(got["micros"] - EXPECTED["ADT_900_micros"]) <= \
        EXPECTED["ADT_900_micros"] * 0.01, "within 1%"


def test_full_pipeline_is_at_least_5x_the_input_output_only_total():
    """Fails if the cache token classes are dropped from pricing."""
    got = _totals()["ADT-900"]["micros"]
    assert got >= 5 * EXPECTED["ADT_900_in_out_only_micros"], (
        "cache classes appear to have been dropped from the pipeline: "
        f"{got} vs in+out-only {EXPECTED['ADT_900_in_out_only_micros']}")


def test_fixture_contains_a_5m_ttl_cache_write():
    assert any(adt_cost.parse_row(l) and adt_cost.parse_row(l)["cache_write_5m"]
               for l in LINES)


def test_legacy_five_column_row_is_priced_when_an_estimator_exists():
    est = {"usd_per_mtok": 152.0, "derivation_id": "fixture"}
    got = _totals(est)["ADT-901"]
    assert got["micros"] == round((500 + 1000) * 152.0)
    assert got["tier"] == "legacy", "a 5-column row should have tier legacy"


def test_legacy_row_without_an_estimator_is_none_never_zero():
    assert _totals()["ADT-901"]["micros"] is None


def test_unattributed_ticket_is_absent_from_totals_not_zero():
    assert "ADT-999" not in _totals()
