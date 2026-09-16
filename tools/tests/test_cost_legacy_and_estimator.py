# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests how cost is priced for 5-column (legacy) ledger rows and unknown
models, and that the cost estimator records which derivation produced it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import adt_backfill_cost as bf  # noqa: E402
import adt_cost  # noqa: E402

PRICES = adt_cost.load_prices()
EST = {"usd_per_mtok": 152.0, "p10": 66.0, "p90": 544.0,
       "derivation_id": "d35-n254-m152", "n_samples": 35}
LEGACY = "2026-01-01T00:00:00Z\tADT-7\t400\t600\tsess"


# ── the legacy-row rule ───────────────────────────────────────────────────

def test_legacy_row_prices_through_the_estimator():
    micros, tier = adt_cost.price_row(adt_cost.parse_row(LEGACY), PRICES, EST)
    assert tier == "legacy"
    assert micros == round(1000 * 152.0)


def test_legacy_row_is_never_zero_and_never_dropped_from_the_total():
    totals = adt_cost.price_ledger([LEGACY], PRICES, EST)
    assert totals["ADT-7"]["micros"] > 0
    assert totals["ADT-7"]["tokens"] == 1000


def test_legacy_row_renders_with_the_approximation_mark():
    micros, tier = adt_cost.price_row(adt_cost.parse_row(LEGACY), PRICES, EST)
    assert adt_cost.fmt_cost(micros, tier).startswith("~")


def test_dollar_dash_is_only_for_a_ticket_with_no_rows():
    assert adt_cost.fmt_cost(None, "unattributed") == "$ —"
    micros, tier = adt_cost.price_row(adt_cost.parse_row(LEGACY), PRICES, EST)
    assert adt_cost.fmt_cost(micros, tier) != "—"


def test_unknown_model_on_a_wide_row_falls_back_to_the_estimator():
    row = "2026-01-01T00:00:00Z\tADT-8\t100\t200\tsess\tclaude-from-2030\t" \
          "standard\t50\t0\t0\tmeasured"
    micros, tier = adt_cost.price_row(adt_cost.parse_row(row), PRICES, EST)
    assert tier == "estimated" and micros == round(300 * 152.0)


def test_a_ticket_mixing_legacy_and_measured_rows_is_approximate_overall():
    wide = ("2026-01-01T00:00:00Z\tADT-7\t100\t200\tsess\tclaude-opus-5\t"
            "standard\t1000\t0\t0\tmeasured")
    totals = adt_cost.price_ledger([LEGACY, wide], PRICES, EST)
    assert totals["ADT-7"]["tier"] != "measured"


# ── the versioned estimator ───────────────────────────────────────────────

def test_estimator_carries_a_derivation_id_and_its_spread(tmp_path):
    root = tmp_path / "proj"
    (root / ".adt" / "state").mkdir(parents=True, exist_ok=True)
    ledger = root / ".adt" / "state" / "token-usage.log"
    msgs, rows = [], []
    for n in range(6):
        msgs.append({"type": "assistant", "message": {
            "role": "assistant", "model": "claude-opus-5",
            "usage": {"input_tokens": 100, "output_tokens": 2000 + n * 500,
                      "cache_read_input_tokens": 100000, "speed": "standard",
                      "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                         "ephemeral_1h_input_tokens": 1000}}}})
        rows.append(f"2026-01-0{n+1}T00:00:00Z\tADT-{n}\t100\t{2000 + n * 500}\tsessE")
    ledger.write_text("".join(r + "\n" for r in rows))
    t = tmp_path / "sessE.jsonl"
    t.write_text("".join(json.dumps(m) + "\n" for m in msgs))
    bf.run(str(ledger), str(root), apply=True, transcripts={"sessE": str(t)})
    est = adt_cost.load_estimator(str(root))
    assert est is not None
    assert est["derivation_id"], "expected the estimator to name its derivation"
    assert est["p10"] <= est["usd_per_mtok"] <= est["p90"]
    assert est["n_samples"] >= 3


def test_two_different_derivations_are_distinguishable():
    a = bf.derive_estimator([adt_cost.parse_row(
        f"2026-01-01T00:00:00Z\tADT-1\t100\t{n}\ts\tclaude-opus-5\tstandard\t"
        f"{n * 10}\t0\t0\tmeasured") for n in (2000, 3000, 4000)], PRICES)
    b = bf.derive_estimator([adt_cost.parse_row(
        f"2026-01-01T00:00:00Z\tADT-1\t100\t{n}\ts\tclaude-opus-5\tstandard\t"
        f"{n * 90}\t0\t0\tmeasured") for n in (2000, 3000, 4000)], PRICES)
    assert a and b and a["derivation_id"] != b["derivation_id"] or \
        a["usd_per_mtok"] != b["usd_per_mtok"]


def test_estimator_is_not_derived_from_too_few_samples():
    assert bf.derive_estimator([], PRICES) is None


# ── ticket tier ───────────────────────────────────────────────────────────

def test_ticket_tier_is_its_worst_row_and_legacy_is_worse_than_estimated():
    wide = ("2026-01-01T00:00:00Z\tADT-9\t100\t200\ts\tclaude-opus-5\t"
            "standard\t1000\t0\t0\tmeasured")
    est_row = ("2026-01-01T00:00:00Z\tADT-9\t10\t20\ts\tclaude-opus-5\t"
               "standard\t0\t0\t0\testimated")
    legacy = "2026-01-01T00:00:00Z\tADT-9\t400\t600\ts"
    assert adt_cost.price_ledger([wide], PRICES, EST)["ADT-9"]["tier"] == "measured"
    assert adt_cost.price_ledger([wide, est_row], PRICES, EST)["ADT-9"]["tier"] == "estimated"
    assert adt_cost.price_ledger([wide, est_row, legacy], PRICES, EST)["ADT-9"]["tier"] == "legacy"


def test_unpriceable_row_keeps_the_other_rows_cost_but_is_not_measured():
    wide = ("2026-01-01T00:00:00Z\tADT-9\t100\t200\ts\tclaude-opus-5\t"
            "standard\t1000\t0\t0\tmeasured")
    legacy = "2026-01-01T00:00:00Z\tADT-9\t400\t600\ts"
    got = adt_cost.price_ledger([wide, legacy], PRICES, None)   # no estimator
    assert got["ADT-9"]["micros"] > 0, "priced rows still count"
    assert got["ADT-9"]["tier"] != "measured"
