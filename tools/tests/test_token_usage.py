# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests token counting in build_kanban: fmt_tokens() formatting, ledger
parsing in load_token_usage(), how Item.tokens chooses between frontmatter,
ledger and per-machine registers, and where the ledger is found."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402


# ── fmt_tokens ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("n,expected", [
    (None, "—"),
    (0, "0"),
    (1, "1"),
    (999, "999"),
    (1000, "1.0k"),
    (1234, "1.2k"),
    (9999, "10.0k"),       # still <10 by the v=9.999 path → one decimal
    (10000, "10k"),        # v=10.0 → no decimal
    (184000, "184k"),
    (999999, "1000k"),     # rounds to 1000k just below the 1M boundary
    (1_000_000, "1.0M"),
    (1_300_000, "1.3M"),
    (12_000_000, "12M"),
])
def test_fmt_tokens_boundaries(n, expected):
    assert build_kanban.fmt_tokens(n) == expected


# ── load_token_usage ────────────────────────────────────────────────────────


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Return a writer for a ledger under a tmp project root, with
    build_kanban.REPO pointed at that root."""
    monkeypatch.setattr(build_kanban, "REPO", tmp_path)

    def write(rows: list[str]) -> Path:
        path = tmp_path / build_kanban.TOKEN_LEDGER_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(rows) + ("\n" if rows else ""))
        return path

    return write


def test_absent_ledger_returns_empty(ledger):
    assert build_kanban.load_token_usage() == {}


def test_empty_ledger_returns_empty(ledger):
    ledger([])
    assert build_kanban.load_token_usage() == {}


def test_sums_by_tix_across_rows(ledger):
    ledger([
        "2026-06-09T10:00:00Z\tTIX-188\t100\t50\tsess-a",
        "2026-06-09T10:05:00Z\tTIX-188\t200\t30\tsess-a",
        "2026-06-09T11:00:00Z\tTIX-090\t1000\t40\tsess-b",
    ])
    # TIX-090 and TIX-90 are the same key: leading zeros are dropped.
    assert build_kanban.load_token_usage() == {"TIX-188": 380, "TIX-90": 1040}


def test_padding_variants_sum_to_one_bucket(ledger):
    ledger([
        "2026-06-26T16:55:38Z\tADT-58\t1118\t19252\tsess-build",
        "2026-06-26T17:05:27Z\tADT-058\t6748\t18126\tsess-close",
    ])
    assert build_kanban.load_token_usage() == {"ADT-58": 1118 + 19252 + 6748 + 18126}


def test_canon_tix_strips_padding_and_uppercases():
    assert build_kanban.canon_tix("ADT-058") == "ADT-58"
    assert build_kanban.canon_tix("adt-58") == "ADT-58"
    assert build_kanban.canon_tix("TIX-007") == "TIX-7"
    assert build_kanban.canon_tix("__unassigned__") == "__UNASSIGNED__"


def test_tix_id_uppercased(ledger):
    ledger(["2026-06-09T10:00:00Z\ttix-188\t10\t5\ts"])
    assert build_kanban.load_token_usage() == {"TIX-188": 15}


def test_unassigned_kept_as_its_own_key(ledger):
    ledger([
        "2026-06-09T10:00:00Z\t__unassigned__\t100\t50\ts",
        "2026-06-09T10:00:00Z\tTIX-188\t10\t5\ts",
    ])
    assert build_kanban.load_token_usage() == {"__UNASSIGNED__": 150, "TIX-188": 15}


def test_malformed_rows_skipped_not_fatal(ledger):
    ledger([
        "2026-06-09T10:00:00Z\tTIX-188\t100\t50\ts",   # good
        "garbage line with no tabs",                     # too few fields
        "2026-06-09\tTIX-188\tnotanumber\t50\ts",        # non-numeric input
        "2026-06-09\t\t10\t5\ts",                        # empty tix
        "2026-06-09\tTIX-188\t7\t3\ts",                   # good
        "",                                               # blank
    ])
    # Only the two good rows count: 150 + 10 = 160.
    assert build_kanban.load_token_usage() == {"TIX-188": 160}


def test_extra_trailing_columns_tolerated(ledger):
    # A row with MORE than 5 columns (future fields) still parses fields 2-4.
    ledger(["2026-06-09\tTIX-188\t10\t5\ts\textra\tcols"])
    assert build_kanban.load_token_usage() == {"TIX-188": 15}


# ── _parse_int (frontmatter `tokens:` coercion) ─────────────────────────────


@pytest.mark.parametrize("v,expected", [
    (None, None),
    ("", None),
    ("   ", None),
    (0, 0),
    (62167, 62167),
    ("62167", 62167),
    ("62,167", 62167),       # comma-grouped string
    ("  62167  ", 62167),    # surrounding whitespace
    (-5, None),              # negative is nonsense for a token count
    ("-5", None),
    ("notanumber", None),
    (True, None),            # bool is an int subclass; it must not count as 1
    (False, None),
])
def test_parse_int(v, expected):
    assert build_kanban._parse_int(v) == expected


# ── Item.tokens precedence (frontmatter wins over the ledger) ───────────────


def _make_ticket(tmp_path, fm_lines: list[str]) -> Path:
    """Write a minimal ticket .md with the given frontmatter lines, return its path."""
    body = "---\n" + "\n".join(fm_lines) + "\n---\n\n# A ticket\n\nhook line.\n"
    p = tmp_path / "ticket.md"
    p.write_text(body)
    return p


def test_item_tokens_prefers_frontmatter_over_ledger(tmp_path, monkeypatch):
    # Ledger says 999; frontmatter `tokens:` says 62167, and frontmatter wins.
    monkeypatch.setattr(build_kanban, "TOKEN_USAGE", {"TIX-216": 999})
    path = _make_ticket(tmp_path, ["id: TIX-216", "tokens: 62167"])
    item = build_kanban.Item(path, "bugs", "done")
    assert item.tokens == 62167


def test_item_tokens_falls_back_to_ledger_when_no_frontmatter(tmp_path, monkeypatch):
    # No `tokens:` field, so the ledger sum is used.
    monkeypatch.setattr(build_kanban, "TOKEN_USAGE", {"TIX-216": 999})
    path = _make_ticket(tmp_path, ["id: TIX-216"])
    item = build_kanban.Item(path, "bugs", "building")
    assert item.tokens == 999


def test_item_tokens_none_when_neither_present(tmp_path, monkeypatch):
    # No `tokens:` field and no ledger row gives None.
    monkeypatch.setattr(build_kanban, "TOKEN_USAGE", {})
    path = _make_ticket(tmp_path, ["id: TIX-999"])
    item = build_kanban.Item(path, "bugs", "ideas")
    assert item.tokens is None


def test_item_tokens_stamped_zero_wins_over_ledger(tmp_path, monkeypatch):
    # A stamped `tokens: 0` is a value and wins over the ledger.
    monkeypatch.setattr(build_kanban, "TOKEN_USAGE", {"TIX-216": 999})
    path = _make_ticket(tmp_path, ["id: TIX-216", "tokens: 0"])
    item = build_kanban.Item(path, "bugs", "done")
    assert item.tokens == 0


# ── per-machine register comments count toward Item.tokens ──────────────────


def _reg(machine: str, total: int) -> str:
    # A register marker as it appears in the cache file's `comments:` frontmatter.
    return (f"  - author: someone\n    at: 2026-07-03T10:00:00Z\n"
            f"    body: <!-- adt:tokens machine={machine} total={total} --> "
            f"tokens on {machine}")


def _make_ticket_with_comments(tmp_path, fm_lines, comment_lines) -> Path:
    body = ("---\n" + "\n".join(fm_lines) + "\ncomments:\n"
            + "\n".join(comment_lines)
            + "\n---\n\n# A ticket\n\nhook line.\n")
    p = tmp_path / "ticket.md"
    p.write_text(body)
    return p


def test_item_tokens_with_empty_ledger_sums_other_machines_registers(tmp_path, monkeypatch):
    # Empty local ledger and registers from two other machines: their sum is used.
    monkeypatch.setattr(build_kanban, "TOKEN_USAGE", {})
    monkeypatch.setattr(build_kanban, "OWN_MACHINE", "machine-c")
    path = _make_ticket_with_comments(
        tmp_path, ["id: ADT-100"], [_reg("machine-a", 40_000),
                                    _reg("machine-b", 2_000)])
    item = build_kanban.Item(path, "bugs", "building")
    assert item.tokens == 42_000


def test_item_tokens_sums_ledger_plus_other_machines(tmp_path, monkeypatch):
    # Own spend is max(ledger, own register); other machines' registers are added.
    monkeypatch.setattr(build_kanban, "TOKEN_USAGE", {"ADT-100": 10_000})
    monkeypatch.setattr(build_kanban, "OWN_MACHINE", "machine-a")
    path = _make_ticket_with_comments(
        tmp_path, ["id: ADT-100"],
        [_reg("machine-a", 8_000),      # own register, stale vs ledger 10k
         _reg("machine-b", 5_000)])
    item = build_kanban.Item(path, "bugs", "building")
    assert item.tokens == 15_000        # max(10k, 8k) + 5k


def test_item_tokens_own_register_floors_pruned_ledger(tmp_path, monkeypatch):
    # When the own register is larger than the ledger, the register is used.
    monkeypatch.setattr(build_kanban, "TOKEN_USAGE", {"ADT-100": 200})
    monkeypatch.setattr(build_kanban, "OWN_MACHINE", "machine-a")
    path = _make_ticket_with_comments(
        tmp_path, ["id: ADT-100"], [_reg("machine-a", 40_000)])
    item = build_kanban.Item(path, "bugs", "building")
    assert item.tokens == 40_000


def test_item_tokens_stamped_still_wins_over_registers(tmp_path, monkeypatch):
    # A stamped `tokens:` value wins over registers.
    monkeypatch.setattr(build_kanban, "TOKEN_USAGE", {})
    monkeypatch.setattr(build_kanban, "OWN_MACHINE", "machine-c")
    path = _make_ticket_with_comments(
        tmp_path, ["id: ADT-100", "tokens: 62167"], [_reg("machine-a", 999)])
    item = build_kanban.Item(path, "bugs", "done")
    assert item.tokens == 62167


def test_register_totals_duplicate_machine_keeps_max():
    # A machine that appears twice keeps its larger value.
    text = _reg("m1", 100) + "\n" + _reg("m1", 300) + "\n" + _reg("m2", 50)
    assert build_kanban._register_totals(text) == {"m1": 300, "m2": 50}


# ── _canonical_root: the ledger is read from the canonical checkout ─────────
# The ledger is gitignored, so a linked worktree has none. _canonical_root()
# resolves to the parent of git-common-dir.


def test_canonical_root_falls_back_to_repo_when_not_git(tmp_path, monkeypatch):
    # In a directory that is not a git repo, REPO is returned unchanged.
    monkeypatch.setattr(build_kanban, "REPO", tmp_path)
    assert build_kanban._canonical_root() == tmp_path


def test_canonical_root_resolves_worktree_to_canonical(tmp_path, monkeypatch):
    # Simulate a linked worktree: a canonical checkout with .adt/,
    # and a worktree whose git-common-dir points back at the canonical .git.
    import subprocess as sp
    canonical = tmp_path / "canonical"
    canonical.mkdir(exist_ok=True)
    sp.run(["git", "init", "-q"], cwd=canonical, check=True)
    sp.run(["git", "config", "user.email", "t@t"], cwd=canonical, check=True)
    sp.run(["git", "config", "user.name", "t"], cwd=canonical, check=True)
    (canonical / ".adt").mkdir(exist_ok=True)
    (canonical / "f").write_text("x")
    sp.run(["git", "add", "-A"], cwd=canonical, check=True)
    sp.run(["git", "commit", "-qm", "init"], cwd=canonical, check=True)
    wt = tmp_path / "wt"
    sp.run(["git", "worktree", "add", "-q", str(wt), "-b", "b"], cwd=canonical, check=True)

    # REPO points at the worktree; resolution returns the canonical checkout.
    monkeypatch.setattr(build_kanban, "REPO", wt)
    assert build_kanban._canonical_root() == canonical.resolve()


# ── TOKEN_LEDGER_ROOT override ──────────────────────────────────────────────
# adt_watch renders with REPO set to the cache dir, which has no ledger, so it
# passes the code checkout as TOKEN_LEDGER_ROOT.


def test_ledger_root_override_used_over_repo(tmp_path, monkeypatch):
    # REPO is the cache dir; the override points at a code tree with .adt/.
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    code = tmp_path / "code"
    (code / ".adt").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(build_kanban, "REPO", cache)
    monkeypatch.setattr(build_kanban, "TOKEN_LEDGER_ROOT", str(code))
    assert build_kanban._canonical_root() == code.resolve()


def test_ledger_root_override_loads_real_ledger(tmp_path, monkeypatch):
    # With REPO at the empty cache, load_token_usage() reads the override's ledger.
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    code = tmp_path / "code"
    (code / ".adt").mkdir(parents=True, exist_ok=True)
    ledger = code / build_kanban.TOKEN_LEDGER_REL
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "2026-06-17T09:00:00Z\tTIX-222\t100\t200\tsess\n"
    )
    monkeypatch.setattr(build_kanban, "REPO", cache)
    monkeypatch.setattr(build_kanban, "TOKEN_LEDGER_ROOT", str(code))
    assert build_kanban.load_token_usage() == {"TIX-222": 300}


def test_load_token_usage_sums_both_ledger_names(tmp_path, monkeypatch):
    # load_token_usage reads both the current and the previous ledger file and
    # sums them per ticket.
    dt = tmp_path / ".adt"
    dt.mkdir(parents=True, exist_ok=True)
    new = tmp_path / build_kanban.TOKEN_LEDGER_REL
    new.parent.mkdir(parents=True, exist_ok=True)
    new.write_text("2026-06-25T10:00:00Z\tTIX-234\t10\t20\ts1\n")
    legacy = tmp_path / build_kanban.TOKEN_LEDGER_REL_PREV
    legacy.write_text("2026-06-24T10:00:00Z\tTIX-234\t1\t2\ts0\n")
    monkeypatch.setattr(build_kanban, "REPO", tmp_path)
    monkeypatch.setattr(build_kanban, "TOKEN_LEDGER_ROOT", "")
    # 10+20 (new) + 1+2 (legacy) = 33
    assert build_kanban.load_token_usage() == {"TIX-234": 33}


def test_empty_ledger_root_falls_back_to_repo(tmp_path, monkeypatch):
    # An empty override resolves from REPO.
    monkeypatch.setattr(build_kanban, "REPO", tmp_path)
    monkeypatch.setattr(build_kanban, "TOKEN_LEDGER_ROOT", "")
    assert build_kanban._canonical_root() == tmp_path
