# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests the plan-quality check in `adt_dod.py --gate`.

A missing, FLAWED or UNKNOWN plan review refuses the ticket only when the
calibration record says `gating: ENABLED` and the ticket's track is `full` (or
unset). Otherwise the verdict is reported on stderr and the ticket stays
approvable. No state turns a FLAWED verdict into a pass.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import adt_dod  # noqa: E402

CLEAN_DOD = "done_evidence:\n  - must_run: 'true'\n"
COVERAGE_OK = "\n### DoD-coverage review\n**Verdict:** COVERED\n**Gaps:** none\n"


def _ticket(tmp_path, plan_review_body="", tix="T-1"):
    """A ticket that passes every earlier gate, so only the plan-quality check decides."""
    md = tmp_path / "ticket.md"
    md.write_text("---\nid: %s\n%s---\n\n# body\n%s%s"
                  % (tix, CLEAN_DOD, COVERAGE_OK, plan_review_body))
    return str(md)


def _review(verdict):
    return "\n### Plan-quality review\n**Verdict:** %s\n**Defects:** none\n" % verdict


def _calibration(tmp_path, state):
    # One file per state, so a test can hold an ENABLED and a DISABLED record at once.
    rec = tmp_path / ("calib-%s.md" % state.lower())
    rec.write_text("# record\n\ngating: %s\n\ncaught: 6/6\n" % state)
    return str(rec)


def _gate(ticket, calibration):
    return adt_dod._cli([ticket, "--gate", "--plan-calibration", calibration])


# ── the reader ────────────────────────────────────────────────────────────
def test_plan_review_returns_none_when_no_block(tmp_path):
    assert adt_dod.plan_review(_ticket(tmp_path)) is None


def test_plan_review_reads_the_recorded_verdict(tmp_path):
    assert adt_dod.plan_review(_ticket(tmp_path, _review("SOUND"))) == "SOUND"
    assert adt_dod.plan_review(_ticket(tmp_path, _review("FLAWED"))) == "FLAWED"


def test_plan_review_does_not_confuse_the_two_review_blocks(tmp_path):
    """The coverage block is COVERED and the plan block is FLAWED; each reader returns its own."""
    md = _ticket(tmp_path, _review("FLAWED"))
    assert adt_dod.coverage_review(md)[0] == "COVERED"
    assert adt_dod.plan_review(md) == "FLAWED"


# ── the conditionality ────────────────────────────────────────────────────
def test_gating_enabled_only_on_an_explicit_enabled_line(tmp_path):
    assert adt_dod.plan_gating_enabled(_calibration(tmp_path, "ENABLED")) is True
    assert adt_dod.plan_gating_enabled(_calibration(tmp_path, "DISABLED")) is False


def test_gating_fails_toward_not_gating_when_the_record_is_missing(tmp_path):
    assert adt_dod.plan_gating_enabled(str(tmp_path / "absent.md")) is False


def test_refuses_when_calibrated(tmp_path, capsys):
    """With gating enabled, a FLAWED plan is refused."""
    rc = _gate(_ticket(tmp_path, _review("FLAWED")), _calibration(tmp_path, "ENABLED"))
    assert rc == 1
    assert "REFUSE" in capsys.readouterr().out


def test_records_only_when_bar_unmet(tmp_path, capsys):
    """With gating disabled, a FLAWED plan is approvable and the verdict is printed to stderr."""
    rc = _gate(_ticket(tmp_path, _review("FLAWED")), _calibration(tmp_path, "DISABLED"))
    out = capsys.readouterr()
    assert rc == 0
    assert "APPROVABLE" in out.out
    assert "not calibrated above its declared bar" in out.err
    assert "FLAWED" in out.err


def test_unknown_is_never_a_pass_when_gated(tmp_path, capsys):
    rc = _gate(_ticket(tmp_path, _review("UNKNOWN")), _calibration(tmp_path, "ENABLED"))
    assert rc == 1
    assert "UNKNOWN" in capsys.readouterr().out


def test_missing_plan_review_is_refused_when_gated(tmp_path, capsys):
    rc = _gate(_ticket(tmp_path), _calibration(tmp_path, "ENABLED"))
    assert rc == 1
    assert "Plan-quality review" in capsys.readouterr().out


def test_missing_plan_review_is_not_refused_when_ungated(tmp_path, capsys):
    rc = _gate(_ticket(tmp_path), _calibration(tmp_path, "DISABLED"))
    assert rc == 0
    assert "APPROVABLE" in capsys.readouterr().out


def test_sound_passes_in_both_states(tmp_path):
    for state in ("ENABLED", "DISABLED"):
        assert _gate(_ticket(tmp_path, _review("SOUND")),
                     _calibration(tmp_path, state)) == 0


def test_disabled_does_not_convert_flawed_into_sound(tmp_path):
    md = _ticket(tmp_path, _review("FLAWED"))
    _gate(md, _calibration(tmp_path, "DISABLED"))
    assert adt_dod.plan_review(md) == "FLAWED"


# ── where the calibration record is found ─────────────────────────────────
# Installed, adt_dod.py runs from `.claude/tools/`; in the source tree it runs
# from `tools/`. The record is looked for beside the tool first, then in ../docs/.
def test_calibration_resolves_next_to_the_tool_first(tmp_path, monkeypatch):
    tools = tmp_path / ".claude" / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "plan-quality-calibration.md").write_text("gating: ENABLED\n")
    monkeypatch.setattr(adt_dod, "__file__", str(tools / "adt_dod.py"))
    assert adt_dod.plan_gating_enabled() is True, (
        "expected the record beside the tool to be found")


def test_calibration_falls_back_to_the_source_tree(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True, exist_ok=True)
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "plan-quality-calibration.md").write_text("gating: ENABLED\n")
    monkeypatch.setattr(adt_dod, "__file__", str(repo / "tools" / "adt_dod.py"))
    assert adt_dod.plan_gating_enabled() is True, (
        "expected the record in ../docs/ to be found"
    )


def test_calibration_absent_everywhere_fails_toward_not_gating(tmp_path, monkeypatch):
    tools = tmp_path / "nowhere" / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(adt_dod, "__file__", str(tools / "adt_dod.py"))
    assert adt_dod.plan_gating_enabled() is False


# ── the ticket's track ──────────────────────────────────────────────────────
# A missing plan review is refused only on a `full` ticket. An absent `track:`
# is treated as `full`. Both calibration and track must allow a refusal.

def _tiered(tmp_path, tier, plan_review_body="", tix="T-1"):
    """Like _ticket(), plus a `track:` line. One file per tier so calls don't overwrite each other."""
    md = tmp_path / ("ticket-%s.md" % tier.lower())
    md.write_text("---\nid: %s\ntrack: %s\n%s---\n\n# body\n%s%s"
                  % (tix, tier, CLEAN_DOD, COVERAGE_OK, plan_review_body))
    return str(md)


def test_track_is_read_from_frontmatter(tmp_path):
    assert adt_dod.ticket_track(_tiered(tmp_path, "standard")) == "standard"
    assert adt_dod.ticket_track(_tiered(tmp_path, "FULL")) == "full"
    assert adt_dod.ticket_track(_ticket(tmp_path)) is None


def test_missing_plan_review_is_not_refused_below_full(tmp_path, capsys):
    for tier in ("standard", "fast"):
        rc = _gate(_tiered(tmp_path, tier), _calibration(tmp_path, "ENABLED"))
        assert rc == 0, tier
        assert "APPROVABLE" in capsys.readouterr().out


def test_full_still_refuses_a_missing_plan_review(tmp_path, capsys):
    rc = _gate(_tiered(tmp_path, "full"), _calibration(tmp_path, "ENABLED"))
    assert rc == 1
    assert "Plan-quality review" in capsys.readouterr().out


def test_absent_track_fails_closed(tmp_path, capsys):
    """No `track:` is treated as `full`, so a missing review is refused."""
    rc = _gate(_ticket(tmp_path), _calibration(tmp_path, "ENABLED"))
    assert rc == 1
    assert "REFUSE" in capsys.readouterr().out


def test_flawed_below_full_is_recorded_with_the_tier_as_the_reason(tmp_path, capsys):
    """Below `full`, the stderr note names the track as the reason, not calibration."""
    rc = _gate(_tiered(tmp_path, "standard", _review("FLAWED")),
               _calibration(tmp_path, "ENABLED"))
    out = capsys.readouterr()
    assert rc == 0
    assert "APPROVABLE" in out.out
    assert "FLAWED" in out.err
    assert "track: standard" in out.err
    assert "not calibrated" not in out.err


def test_both_halves_are_required(tmp_path):
    """plan_gate_applies needs both a `full` track and gating enabled."""
    cal_on = _calibration(tmp_path, "ENABLED")
    cal_off = _calibration(tmp_path, "DISABLED")
    assert adt_dod.plan_gate_applies(_tiered(tmp_path, "full"), cal_on) is True
    assert adt_dod.plan_gate_applies(_tiered(tmp_path, "standard"), cal_on) is False
    assert adt_dod.plan_gate_applies(_tiered(tmp_path, "full"), cal_off) is False
    assert adt_dod.plan_gate_applies(_tiered(tmp_path, "standard"), cal_off) is False


def test_ticket_track_fails_closed_when_the_serializer_is_missing(monkeypatch):
    """If ticket_serializer cannot be imported, ticket_track returns None instead of raising."""
    import builtins
    real_import = builtins.__import__

    def no_serializer(name, *a, **kw):
        if name == "ticket_serializer":
            raise ImportError("simulated: module not installed beside adt_dod.py")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_serializer)
    assert adt_dod.ticket_track("/any/path.md") is None


def test_absent_track_and_missing_serializer_both_mean_full(tmp_path):
    """A ticket_track of None is treated as `full`, so the gate still applies."""
    cal = _calibration(tmp_path, "ENABLED")
    assert adt_dod.plan_gate_applies(_ticket(tmp_path), cal) is True
