# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that adt_migrate_ordered reads the ticket id prefix from config and
uses it in its id regex and placeholder titles. No real `gh` call is made."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_migrate_ordered as mig  # noqa: E402


def test_id_prefix_defaults_to_tix():
    assert mig._id_prefix({}) == "TIX"
    assert mig._id_prefix({"id_prefix": "ADT"}) == "ADT"
    assert mig._id_prefix({"id_prefix": "  TIX  "}) == "TIX"


def test_id_re_matches_configured_prefix():
    adt = mig._id_re("ADT")
    assert adt.search("id: ADT-7")
    assert adt.search("id: ADT-7").group(1) == "7"
    # A different prefix does not match.
    assert not adt.search("id: TIX-7")
    tix = mig._id_re("TIX")
    assert tix.search("id: TIX-12").group(1) == "12"
    assert not tix.search("id: ADT-12")


def test_index_by_num_uses_config_prefix(tmp_path, monkeypatch):
    # Two fake cache files; only the one matching the configured prefix is indexed.
    a = tmp_path / "a.md"; a.write_text("---\nid: ADT-3\n---\nbody\n")
    b = tmp_path / "b.md"; b.write_text("---\nid: TIX-9\n---\nbody\n")
    monkeypatch.setattr(
        mig.adt_sync, "iter_cache_files",
        lambda cfg: [(str(a), {}), (str(b), {})],
    )
    idx = mig._index_by_num({"id_prefix": "ADT"})
    assert set(idx) == {3}          # only ADT-3, not TIX-9
    assert idx[3][0] == str(a)


def test_placeholder_title_uses_prefix(monkeypatch):
    # _create_placeholder builds its title/body from the prefix; capture every gh
    # call (it issues two: create then close) and find the create's --title.
    calls = []

    def fake_gh(args):
        calls.append(args)
        return "https://github.com/o/r/issues/5\n"

    monkeypatch.setattr(mig.adt_sync, "_gh", fake_gh)
    n = mig._create_placeholder({"repo": "o/r", "id_prefix": "ADT"}, 5)
    assert n == 5
    create = next(a for a in calls if "--title" in a)
    title = create[create.index("--title") + 1]
    assert title.startswith("ADT-5")
