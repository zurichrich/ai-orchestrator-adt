# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that the sync posts a ticket's stamped cost to its GitHub Issue as a
structured comment, and only re-posts when the stamp changes."""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import adt_sync  # noqa: E402


def test_stamp_body_states_the_cost_and_the_tier():
    b = adt_sync._stamp_body("ADT-115", 244.31, "legacy", 978224)
    assert "$244.31" in b and "legacy" in b and "978,224 tokens" in b


def test_stamp_body_is_machine_readable():
    b = adt_sync._stamp_body("ADT-115", 244.31, "measured", 978224)
    assert "<!-- adt:stamp tix=ADT-115 cost_usd=244.31 tier=measured" in b


def test_stamp_says_it_is_authoritative_and_the_registers_are_not():
    b = adt_sync._stamp_body("ADT-115", 244.31, "legacy", 978224)
    assert "number of record" in b
    assert "not the final cost" in b
    # The figure is re-stamped for a while after close, so the comment says so.
    assert "RECOMPUTED" in b


def test_estimated_stamp_carries_the_approximation_mark():
    assert "~$244.31" in adt_sync._stamp_body("ADT-115", 244.31, "legacy", 1)
    assert "~" not in adt_sync._stamp_body(
        "ADT-115", 244.31, "measured", 1).split("Stamped")[0].replace("🪙", "")


def test_unattributed_cost_reads_not_captured_rather_than_zero():
    b = adt_sync._stamp_body("ADT-9", "unattributed", "unattributed",
                             "unattributed")
    assert "not captured" in b
    assert "$0.00" not in b


def test_a_ticket_with_no_cost_usd_is_skipped(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(adt_sync, "iter_cache_files",
                        lambda cfg: [("p.md", {"issue_number": 1, "id": "ADT-1"})])
    monkeypatch.setattr(adt_sync, "_upsert_comment",
                        lambda *a, **k: calls.append(a) or "1")
    (tmp_path / ".adt").mkdir(exist_ok=True)
    out = adt_sync.checkpoint_stamp(str(tmp_path), {"repo": "o/r"})
    assert out == [] and calls == []


def test_unchanged_stamp_makes_no_api_call(tmp_path, monkeypatch):
    data = {"issue_number": 1, "id": "ADT-1", "cost_usd": 12.34,
            "cost_tier": "measured", "tokens": 100}
    monkeypatch.setattr(adt_sync, "iter_cache_files",
                        lambda cfg: [("p.md", data)])
    calls = []
    monkeypatch.setattr(adt_sync, "_upsert_comment",
                        lambda *a, **k: (calls.append(a), "77")[1])
    (tmp_path / ".adt").mkdir(exist_ok=True)
    first = adt_sync.checkpoint_stamp(str(tmp_path), {"repo": "o/r"})
    second = adt_sync.checkpoint_stamp(str(tmp_path), {"repo": "o/r"})
    assert len(first) == 1 and second == []
    assert len(calls) == 1, "second pass must make no API call"


def test_a_changed_cost_repushes(tmp_path, monkeypatch):
    data = {"issue_number": 1, "id": "ADT-1", "cost_usd": 12.34,
            "cost_tier": "measured", "tokens": 100}
    monkeypatch.setattr(adt_sync, "iter_cache_files", lambda cfg: [("p.md", data)])
    calls = []
    monkeypatch.setattr(adt_sync, "_upsert_comment",
                        lambda *a, **k: (calls.append(a), "77")[1])
    (tmp_path / ".adt").mkdir(exist_ok=True)
    adt_sync.checkpoint_stamp(str(tmp_path), {"repo": "o/r"})
    data["cost_usd"] = 244.31              # re-stamped at a later close
    adt_sync.checkpoint_stamp(str(tmp_path), {"repo": "o/r"})
    assert len(calls) == 2
    assert calls[1][3] == "77", "expected a PATCH of the existing comment"


def test_tier_change_alone_repushes(tmp_path, monkeypatch):
    """The whole payload is hashed, so a tier change alone is re-posted."""
    data = {"issue_number": 1, "id": "ADT-1", "cost_usd": 12.34,
            "cost_tier": "measured", "tokens": 100}
    monkeypatch.setattr(adt_sync, "iter_cache_files", lambda cfg: [("p.md", data)])
    calls = []
    monkeypatch.setattr(adt_sync, "_upsert_comment",
                        lambda *a, **k: (calls.append(a), "77")[1])
    (tmp_path / ".adt").mkdir(exist_ok=True)
    adt_sync.checkpoint_stamp(str(tmp_path), {"repo": "o/r"})
    data["cost_tier"] = "legacy"
    adt_sync.checkpoint_stamp(str(tmp_path), {"repo": "o/r"})
    assert len(calls) == 2


# --- ADT-354: a reinstall has no checkpoint, but the stamp is on the Issue ----

def _fake_github(monkeypatch, pages):
    """Stub the two gh wrappers. `pages` is the Issue's comments, one list per
    REST page. Returns the list of (method, endpoint) calls made."""
    calls = []

    def gh_json(args):
        method = args[2] if args[1] == "-X" else "GET"
        endpoint = args[3] if args[1] == "-X" else args[1]
        calls.append((method, endpoint))
        if method == "POST":
            return {"id": 999}
        page = int(endpoint.rsplit("page=", 1)[1])
        return pages[page - 1] if page <= len(pages) else []

    def gh(args):
        calls.append((args[2], args[3]))
        return ""

    monkeypatch.setattr(adt_sync, "_gh_json", gh_json)
    monkeypatch.setattr(adt_sync, "_gh", gh)
    return calls


def _stamped(monkeypatch, tmp_path):
    data = {"issue_number": 7, "id": "ADT-7", "cost_usd": 12.34,
            "cost_tier": "measured", "tokens": 100}
    monkeypatch.setattr(adt_sync, "iter_cache_files", lambda cfg: [("p.md", data)])
    (tmp_path / ".adt").mkdir(exist_ok=True)    # no stamp-checkpoint dir yet


def test_existing_stamp_on_a_later_page_is_patched_not_reposted(tmp_path, monkeypatch):
    _stamped(monkeypatch, tmp_path)
    filler = [{"id": i, "body": "a conversation comment"} for i in range(100)]
    older = adt_sync._stamp_body("ADT-70", 1.0, "measured", 1)   # another ticket
    pages = [filler, [{"id": 554, "body": older},
                      {"id": 555, "body": adt_sync._stamp_body(
                          "ADT-7", 12.34, "measured", 100)}]]
    calls = _fake_github(monkeypatch, pages)

    adt_sync.checkpoint_stamp(str(tmp_path), {"repo": "o/r"})

    assert ("PATCH", "repos/o/r/issues/comments/555") in calls
    assert not [c for c in calls if c[0] == "POST"], calls
    ck = (tmp_path / ".adt" / "state" / "stamp-checkpoint" / "ADT-7").read_text()
    assert ck.strip().endswith("\t555"), "the found id must be checkpointed"


def test_existing_stamp_absent_posts_once(tmp_path, monkeypatch):
    _stamped(monkeypatch, tmp_path)
    calls = _fake_github(monkeypatch, [[{"id": 1, "body": "hello"}]])

    adt_sync.checkpoint_stamp(str(tmp_path), {"repo": "o/r"})

    assert [c for c in calls if c[0] == "POST"] == [
        ("POST", "repos/o/r/issues/7/comments")]
    assert not [c for c in calls if c[0] == "PATCH"]
