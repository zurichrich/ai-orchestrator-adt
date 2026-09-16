# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests tools/calibration/dod-coverage/score.py, which scores the DoD-coverage
reviewer against a set of labelled cases."""
from __future__ import annotations

import importlib.util
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCORE = os.path.join(HERE, "calibration", "dod-coverage", "score.py")


def _load():
    spec = importlib.util.spec_from_file_location("score", SCORE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_score_module_loads():
    assert os.path.isfile(SCORE)
    _load()


def test_it_writes_to_the_repo_docs_dir_not_tools_docs():
    mod = _load()
    assert mod.OUT == os.path.join(os.path.dirname(HERE), "docs",
                                   "dod-coverage-calibration.md"), mod.OUT


def test_verdict_is_parsed_from_the_block(tmp_path):
    mod = _load()
    p = tmp_path / "x.verdict"
    p.write_text("### DoD-coverage review\n**Verdict:** GAP\n**Gaps:**\n- a\n")
    assert mod.read_verdict(str(p))[0] == "GAP"


def test_a_missing_verdict_file_scores_as_not_run(tmp_path):
    mod = _load()
    assert mod.read_verdict(str(tmp_path / "nope.verdict")) == (None, "")


def test_every_case_has_an_expected_entry_and_a_file():
    d = os.path.join(HERE, "calibration", "dod-coverage")
    expected = json.load(open(os.path.join(d, "expected.json"), encoding="utf-8"))
    cases = {f[:-3] for f in os.listdir(os.path.join(d, "cases")) if f.endswith(".md")}
    assert cases == set(expected), (cases ^ set(expected))


def test_the_cases_include_both_covered_and_gap():
    d = os.path.join(HERE, "calibration", "dod-coverage")
    expected = json.load(open(os.path.join(d, "expected.json"), encoding="utf-8"))
    verdicts = {v["expect"] for v in expected.values()}
    assert "COVERED" in verdicts and "GAP" in verdicts
