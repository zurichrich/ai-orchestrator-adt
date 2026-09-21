# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests adt_dod's gate_effects: recording review verdicts on a ticket and
deriving whether each gate ran, caused an edit, or went unrecorded. Also covers
the serializer round trip and the Issue body size cap.
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import adt_dod  # noqa: E402
import ticket_serializer  # noqa: E402

FRONT = """---
slug: t
id: ADT-999
title: A ticket
type: enhancement
priority: P1
size: M
stage: planned
state: open
created: 2026-09-01
created_by: po
done_evidence:
  - must_run: 'true'
    lane: build
---

# A ticket

## Plan (PM)

### Problem & goal
{goal}

### Design
{design}

### Impact / ripple analysis
none

### Sub-steps
- 1a [backend] — do the thing

### Risks
- none

### Test plan
- a test

{blocks}
"""

COVERAGE = "### DoD-coverage review\n**Verdict:** {v}\nprose\n"
QUALITY = "### Plan-quality review\n**Verdict:** {v}\nprose\n"


def ticket(tmp, goal="solve it", design="do X", blocks=""):
    path = os.path.join(tmp, "t.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(FRONT.format(goal=goal, design=design, blocks=blocks))
    return path


class RecordTest(unittest.TestCase):
    def test_appends_with_round_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="FLAWED"))
            row = adt_dod.record_verdict(t, "plan-quality")
            self.assertEqual(row["round"], 1)
            self.assertEqual(row["verdict"], "FLAWED")
            self.assertTrue(row["hash"])

    def test_verdict_parsed_from_block_not_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="FLAWED"))
            self.assertEqual(adt_dod.record_verdict(t, "plan-quality")["verdict"],
                             "FLAWED")
        import inspect
        params = list(inspect.signature(adt_dod.record_verdict).parameters)
        self.assertEqual(params, ["ticket_md_path", "gate"],
                         "record_verdict must not accept a verdict argument")

    def test_ran_counts_multiple_rounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="FLAWED"))
            adt_dod.record_verdict(t, "plan-quality")
            adt_dod.record_verdict(t, "plan-quality")
            adt_dod.record_verdict(t, "plan-quality")
            adt_dod.derive_gate_effects(t)
            self.assertEqual(adt_dod.gate_effects(t)["plan-quality"]["ran"], 3)

    def test_ran_counts_record_rows_not_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="SOUND"))
            adt_dod.record_verdict(t, "plan-quality")
            adt_dod.derive_gate_effects(t)
            rows = adt_dod._read_frontmatter_list(t, "gate_effects")
            records = [r for r in rows if r.get("kind") == "record"]
            self.assertEqual(adt_dod.gate_effects(t)["plan-quality"]["ran"],
                             len(records))

    def test_record_verdict_needs_a_review_block_to_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks="")
            self.assertIsNone(adt_dod.record_verdict(t, "plan-quality"),
                              "recorded a verdict with no block to read")
            t2 = ticket(tmp, blocks=QUALITY.format(v="SOUND"))
            self.assertIsNotNone(adt_dod.record_verdict(t2, "plan-quality"))

    def test_record_lives_in_frontmatter_not_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="SOUND"))
            adt_dod.record_verdict(t, "plan-quality")
            text = open(t, encoding="utf-8").read()
            body = text.split("---", 2)[2]
            self.assertNotIn("kind: record", body)
            self.assertIn("kind: record", text.split("---", 2)[1])

    def test_coerces_round_and_ran_from_strings(self):
        """Dict-list values parse back as strings, so readers must convert them."""
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="SOUND"))
            adt_dod.record_verdict(t, "plan-quality")
            adt_dod.derive_gate_effects(t)
            self.assertIsInstance(adt_dod.gate_effects(t)["plan-quality"]["ran"], int)
            self.assertEqual(adt_dod._as_int("3"), 3)
            self.assertEqual(adt_dod._as_int(None), 0)


class CausedEditTest(unittest.TestCase):
    def test_caused_edit_true_after_a_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, design="do X", blocks=QUALITY.format(v="FLAWED"))
            adt_dod.record_verdict(t, "plan-quality")
            # the design moves in response, then the gate re-runs
            body = open(t, encoding="utf-8").read().replace("do X", "do Y instead")
            open(t, "w", encoding="utf-8").write(body)
            adt_dod.record_verdict(t, "plan-quality")
            adt_dod.derive_gate_effects(t)
            self.assertTrue(adt_dod.gate_effects(t)["plan-quality"]["caused_edit"])

    def test_caused_edit_false_when_hash_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="FLAWED"))
            adt_dod.record_verdict(t, "plan-quality")
            adt_dod.record_verdict(t, "plan-quality")
            adt_dod.derive_gate_effects(t)
            self.assertFalse(adt_dod.gate_effects(t)["plan-quality"]["caused_edit"])

    def test_a_gate_that_caused_no_edit_still_reports_ran_and_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="SOUND"))
            adt_dod.record_verdict(t, "plan-quality")
            adt_dod.derive_gate_effects(t)
            eff = adt_dod.gate_effects(t)["plan-quality"]
            self.assertEqual(eff["ran"], 1)
            self.assertFalse(eff["caused_edit"])
            self.assertFalse(eff["under_recorded"])

    def test_a_reflow_is_not_an_edit(self):
        """Whitespace changes do not change the graded-text hash."""
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, design="do X", blocks=QUALITY.format(v="FLAWED"))
            before = adt_dod.graded_text_hash(t)
            body = open(t, encoding="utf-8").read().replace("do X", "do\n   X")
            open(t, "w", encoding="utf-8").write(body)
            self.assertEqual(before, adt_dod.graded_text_hash(t))

    def test_derives_gate_effects_and_writes_no_follow_ons(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="FLAWED"))
            adt_dod.record_verdict(t, "plan-quality")
            adt_dod.derive_gate_effects(t)
            data = ticket_serializer.parse_md(open(t, encoding="utf-8").read())
            self.assertNotIn("follow_ons", data)
            self.assertTrue(data["gate_effects"])

    def test_derive_pass_never_clobbers_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="FLAWED"))
            adt_dod.record_verdict(t, "plan-quality")
            adt_dod.record_verdict(t, "plan-quality")
            adt_dod.derive_gate_effects(t)
            adt_dod.derive_gate_effects(t)
            rows = adt_dod._read_frontmatter_list(t, "gate_effects")
            self.assertEqual(len([r for r in rows if r.get("kind") == "record"]), 2)
            self.assertEqual(len([r for r in rows if r.get("kind") == "derived"]),
                             len(adt_dod.GATES))


class UnderRecordedTest(unittest.TestCase):
    def test_under_recorded_from_block_without_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="SOUND"))
            adt_dod.derive_gate_effects(t)          # block present, no record
            self.assertTrue(adt_dod.gate_effects(t)["plan-quality"]["under_recorded"])

    def test_under_recorded_differs_from_ran_without_an_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="SOUND"))
            adt_dod.derive_gate_effects(t)
            eff = adt_dod.gate_effects(t)["plan-quality"]
            self.assertTrue(eff["under_recorded"])
            self.assertEqual(eff["ran"], 0)

            t2 = ticket(tmp, blocks=QUALITY.format(v="SOUND"))
            adt_dod.record_verdict(t2, "plan-quality")
            adt_dod.derive_gate_effects(t2)
            eff2 = adt_dod.gate_effects(t2)["plan-quality"]
            self.assertFalse(eff2["under_recorded"])
            self.assertFalse(eff2["caused_edit"])

    def test_under_recorded_does_not_refuse(self):
        """An under-recorded gate is reported, and `adt_dod.py --gate` still approves."""
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=COVERAGE.format(v="COVERED") + QUALITY.format(v="SOUND"))
            adt_dod.derive_gate_effects(t)
            self.assertTrue(adt_dod.gate_effects(t)["plan-quality"]["under_recorded"])
            r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "adt_dod.py"),
                                t, "--gate"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("APPROVABLE", r.stdout)

    def test_under_recorded_when_plan_gating_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            cal = os.path.join(tmp, "cal.md")
            open(cal, "w", encoding="utf-8").write("gating: DISABLED\n")
            t = ticket(tmp, blocks=QUALITY.format(v="SOUND"))
            adt_dod.derive_gate_effects(t, cal)
            eff = adt_dod.gate_effects(t)["plan-quality"]
            self.assertTrue(eff["under_recorded"])
            self.assertFalse(eff["gating_enabled"])

    def test_gating_state_recorded_not_reevaluated(self):
        """Changing the calibration file later does not change the stored value."""
        with tempfile.TemporaryDirectory() as tmp:
            cal = os.path.join(tmp, "cal.md")
            open(cal, "w", encoding="utf-8").write("gating: ENABLED\n")
            t = ticket(tmp, blocks=QUALITY.format(v="SOUND"))
            adt_dod.record_verdict(t, "plan-quality")
            adt_dod.derive_gate_effects(t, cal)
            self.assertTrue(adt_dod.gate_effects(t)["plan-quality"]["gating_enabled"])
            # flip the flag; the recorded value must stay the same
            open(cal, "w", encoding="utf-8").write("gating: DISABLED\n")
            self.assertTrue(adt_dod.gate_effects(t)["plan-quality"]["gating_enabled"])


class ReaderTest(unittest.TestCase):
    def test_reads_latest_verdict_not_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=(QUALITY.format(v="FLAWED")
                                    + "\n" + QUALITY.format(v="SOUND")))
            self.assertEqual(adt_dod.plan_review(t), "SOUND")
            t2 = ticket(tmp, blocks=(COVERAGE.format(v="GAP")
                                     + "\n" + COVERAGE.format(v="COVERED")))
            self.assertEqual(adt_dod.coverage_review(t2)[0], "COVERED")

    def test_records_keep_the_order_the_gates_ran_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=COVERAGE.format(v="COVERED") + QUALITY.format(v="FLAWED"))
            adt_dod.record_verdict(t, "coverage")
            adt_dod.record_verdict(t, "plan-quality")
            rows = [r for r in adt_dod._read_frontmatter_list(t, "gate_effects")
                    if r.get("kind") == "record"]
            self.assertEqual([r["gate"] for r in rows], ["coverage", "plan-quality"])


class SerializerTest(unittest.TestCase):
    def test_DICTLIST_KEYS_membership(self):
        """Keys missing from DICTLIST_KEYS lose all but their first line on a round trip."""
        self.assertIn("gate_effects", ticket_serializer.DICTLIST_KEYS)
        self.assertIn("dod_snapshot", ticket_serializer.DICTLIST_KEYS)

    def test_gate_effects_roundtrips_through_serializer(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="FLAWED"))
            adt_dod.record_verdict(t, "plan-quality")
            adt_dod.derive_gate_effects(t)
            before = adt_dod._read_frontmatter_list(t, "gate_effects")
            data = ticket_serializer.parse_md(open(t, encoding="utf-8").read())
            open(t, "w", encoding="utf-8").write(ticket_serializer.emit_md(data))
            self.assertEqual(adt_dod._read_frontmatter_list(t, "gate_effects"), before)

    def test_gate_effects_is_not_pushed_to_the_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="FLAWED"))
            adt_dod.record_verdict(t, "plan-quality")
            data = ticket_serializer.parse_md(open(t, encoding="utf-8").read())
            issue = ticket_serializer.to_issue(data)
            self.assertNotIn("gate_effects", issue.get("body", ""))


class BodyCapTest(unittest.TestCase):
    def test_raises_on_a_body_over_the_cap(self):
        import adt_sync
        adt_sync.check_body_cap("x" * 100)          # under cap: silent
        over = "x" * (adt_sync.ISSUE_BODY_CAP_BYTES + 1)
        with self.assertRaises(adt_sync.GhError):
            adt_sync.check_body_cap(over)

    def test_cap_counts_utf8_bytes_not_characters(self):
        """The cap is 262,144 bytes, so a long ASCII body over 65,536 characters passes."""
        import adt_sync
        ascii_body = "x" * 71640                    # 71,640 chars / 71,640 bytes
        self.assertEqual(len(ascii_body.encode("utf-8")), 71640)
        adt_sync.check_body_cap(ascii_body)         # accepted

        # 4-byte characters: 65,536 of them is exactly the byte cap.
        wide = "\U0001F600" * 65537                 # 65,537 chars, 262,148 bytes
        self.assertGreater(len(wide.encode("utf-8")), adt_sync.ISSUE_BODY_CAP_BYTES)
        with self.assertRaises(adt_sync.GhError):
            adt_sync.check_body_cap(wide)

        # ...and just under it, the same character count passes.
        ok = "\U0001F600" * 65536                   # 65,536 chars, 262,144 bytes
        self.assertEqual(len(ok.encode("utf-8")), adt_sync.ISSUE_BODY_CAP_BYTES)
        adt_sync.check_body_cap(ok)


class DodSnapshotTest(unittest.TestCase):
    def test_dod_snapshot_records_the_condition_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp)
            row = adt_dod.snapshot_dod(t)
            self.assertEqual(row["n"], 1)
            self.assertIsNone(adt_dod.snapshot_dod(t), "an unchanged DoD should add no snapshot")


# ── AO-013 gap 2: what a verdict rests on, and when it reopens ──────────────
DEP = ("### DoD-coverage review\n**Verdict:** COVERED\n"
       "**Depends-on-unmodified:** tools/adt_watch.py:_save_watch_state\n")
# A block that passes something ungraded and declares nothing: the AO-006
# round-3 wording, verbatim in shape.
UNDECLARED = ("### DoD-coverage review\n**Verdict:** COVERED\n"
              "The Key decision needs no test because it is a property of "
              "existing, unmodified code.\n")


def _lane_ticket(tmp, lane, **kw):
    """The same ticket, written into a lane folder, since the gate reads the
    folder rather than `stage:`."""
    d = os.path.join(tmp, lane)
    os.makedirs(d, exist_ok=True)
    src = ticket(tmp, **kw)
    dst = os.path.join(d, "t.md")
    os.replace(src, dst)
    return dst


class DeclaredDependencyTest(unittest.TestCase):
    def test_a_recorded_verdict_carries_its_declared_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=DEP)
            row = adt_dod.record_verdict(t, "coverage")
            self.assertEqual(row["rests_on"],
                             "tools/adt_watch.py:_save_watch_state")
            # and it survives the serializer round trip, which is where an
            # unlisted dict key silently becomes a string (ADT-090).
            rows = adt_dod._read_frontmatter_list(t, "gate_effects")
            self.assertEqual(rows[0]["rests_on"],
                             "tools/adt_watch.py:_save_watch_state")

    def test_a_verdict_with_no_declaration_stores_no_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=COVERAGE.format(v="COVERED"))
            self.assertNotIn("rests_on", adt_dod.record_verdict(t, "coverage"))

    def test_a_justification_phrase_with_no_declaration_prints_a_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=UNDECLARED)
            # The fixture carries both markers, and the earliest one in the text
            # is what gets named — asserted exactly, so a regex that started
            # matching something else would not slip through.
            self.assertIn("needs no test",
                          adt_dod.undeclared_justification(t, "coverage"))
            # The other arm, on its own, so both markers are proven rather than
            # one shadowing the other.
            only = ticket(tmp, blocks="### DoD-coverage review\n"
                                      "**Verdict:** COVERED\n"
                                      "A property of existing, unmodified code.\n")
            self.assertIn("existing, unmodified code",
                          adt_dod.undeclared_justification(only, "coverage"))
            # The STRUCTURAL half: the reviewer's format requires the line on
            # every block, so a block with no phrase and no line is still a NOTE.
            plain = ticket(tmp, blocks=COVERAGE.format(v="COVERED"))
            self.assertIn("no `**Depends-on-unmodified:**` line",
                          adt_dod.undeclared_justification(plain, "coverage"))
            r = subprocess.run(
                [sys.executable, os.path.join(ROOT, "tools", "adt_dod.py"),
                 t, "--record-verdict", "coverage"],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("RECORDED", r.stdout, "the row is written first")
            self.assertIn("declared no dependency", r.stderr)

    def test_a_declared_dependency_silences_the_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=UNDECLARED.replace(
                "**Verdict:** COVERED\n",
                "**Verdict:** COVERED\n**Depends-on-unmodified:** "
                "tools/adt_watch.py:_save_watch_state\n"))
            self.assertIsNone(adt_dod.undeclared_justification(t, "coverage"))

    def test_an_unmoved_spec_reopens_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=DEP)
            adt_dod.record_verdict(t, "coverage")
            self.assertEqual(adt_dod.reopened_dependencies(t), [])

    def test_a_moved_spec_reopens_a_dependency_its_sub_steps_still_touch(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=DEP)
            adt_dod.record_verdict(t, "coverage")
            body = open(t, encoding="utf-8").read().replace(
                "- 1a [backend] — do the thing",
                "- 1a [backend] — edit tools/adt_watch.py")
            open(t, "w", encoding="utf-8").write(body)
            out = adt_dod.reopened_dependencies(t)
            self.assertEqual(
                out, [("coverage", 1, "tools/adt_watch.py:_save_watch_state")])

    def test_a_moved_spec_that_stopped_touching_the_file_reopens_nothing(self):
        """Both halves are required: the text moved, but the sub-steps no longer
        name that file, so the dependency still holds."""
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=DEP)
            adt_dod.record_verdict(t, "coverage")
            body = open(t, encoding="utf-8").read().replace(
                "- 1a [backend] — do the thing",
                "- 1a [backend] — edit tools/build_kanban.py")
            open(t, "w", encoding="utf-8").write(body)
            self.assertEqual(adt_dod.reopened_dependencies(t), [])

    def test_a_reopened_dependency_refuses_a_planned_ticket(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = _lane_ticket(tmp, "planned", blocks=DEP)
            adt_dod.record_verdict(t, "coverage")
            body = open(t, encoding="utf-8").read().replace(
                "- 1a [backend] — do the thing",
                "- 1a [backend] — edit tools/adt_watch.py")
            open(t, "w", encoding="utf-8").write(body)
            self.assertTrue(adt_dod.in_plan_lane(t))
            r = subprocess.run(
                [sys.executable, os.path.join(ROOT, "tools", "adt_dod.py"),
                 t, "--gate", "--plan-calibration", "/nonexistent.md"],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("REFUSE", r.stdout)
            self.assertIn("moved past", r.stdout)

            # The same ticket in `building/` reports and approves.
            moved = os.path.join(tmp, "building")
            os.makedirs(moved)
            dst = os.path.join(moved, "t.md")
            os.replace(t, dst)
            self.assertFalse(adt_dod.in_plan_lane(dst))
            r2 = subprocess.run(
                [sys.executable, os.path.join(ROOT, "tools", "adt_dod.py"),
                 dst, "--gate", "--plan-calibration", "/nonexistent.md"],
                capture_output=True, text=True)
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            self.assertIn("APPROVABLE", r2.stdout)
            self.assertIn("recorded and not enforced", r2.stderr)


    def test_a_later_none_does_not_erase_an_earlier_declaration(self):
        """AO-006's actual sequence, and the one the last-row rule missed: a
        declaration, then a delta re-review that rests on nothing and writes
        `none` as its own template instructs, then the spec starts editing that
        file."""
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=DEP)
            adt_dod.record_verdict(t, "coverage")
            body = open(t, encoding="utf-8").read() + (
                "### DoD-coverage review\n**Verdict:** COVERED\n"
                "**Depends-on-unmodified:** none\n")
            open(t, "w", encoding="utf-8").write(body)
            adt_dod.record_verdict(t, "coverage")
            body = open(t, encoding="utf-8").read().replace(
                "- 1a [backend] — do the thing",
                "- 1a [backend] — edit tools/adt_watch.py")
            open(t, "w", encoding="utf-8").write(body)
            out = adt_dod.reopened_dependencies(t)
            self.assertEqual(
                out, [("coverage", 1, "tools/adt_watch.py:_save_watch_state")],
                "the round-1 declaration still stands; round 2 rested on nothing")


class UngradedSectionTest(unittest.TestCase):
    def test_a_plan_missing_some_graded_sections_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp)
            body = open(t, encoding="utf-8").read().replace("### Risks", "### Hazards")
            open(t, "w", encoding="utf-8").write(body)
            out = adt_dod.ungraded_sections(t)
            self.assertEqual(len(out), 1, out)
            self.assertIn("`### Risks`", out[0][1])

    def test_a_plan_with_no_graded_sections_at_all_is_not_drift(self):
        """A fast-track plan legitimately has a `## Plan (PM)` and no `###`
        subsections. Reporting `0 of 6` on every one of those is how a check
        teaches people to ignore it."""
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp)
            head = open(t, encoding="utf-8").read().split("## Plan (PM)")[0]
            open(t, "w", encoding="utf-8").write(
                head + "## Plan (PM)\n\nA one-line fix, no sections.\n")
            self.assertEqual(adt_dod.ungraded_sections(t), [])

    def test_a_ticket_with_no_plan_is_not_missing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp)
            head = open(t, encoding="utf-8").read().split("## Plan (PM)")[0]
            open(t, "w", encoding="utf-8").write(head)
            self.assertEqual(adt_dod.ungraded_sections(t), [])


class DivergenceTest(unittest.TestCase):
    def test_two_consecutive_negative_plan_quality_verdicts_print_escalate(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="FLAWED"))
            adt_dod.record_verdict(t, "plan-quality")
            self.assertIsNone(adt_dod.divergence_escalation(t),
                              "one negative verdict is a finding, not divergence")
            r = subprocess.run(
                [sys.executable, os.path.join(ROOT, "tools", "adt_dod.py"),
                 t, "--record-verdict", "plan-quality"],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("ESCALATE", r.stdout)
            self.assertIn("rounds 1 and 2", r.stdout)

    def test_a_negative_then_positive_verdict_is_converging(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=QUALITY.format(v="FLAWED"))
            adt_dod.record_verdict(t, "plan-quality")
            body = open(t, encoding="utf-8").read() + QUALITY.format(v="SOUND")
            open(t, "w", encoding="utf-8").write(body)
            adt_dod.record_verdict(t, "plan-quality")
            self.assertIsNone(adt_dod.divergence_escalation(t))

    def test_two_coverage_gaps_do_not_escalate(self):
        """Coverage keeps its tier round limits: a GAP says the grading is
        incomplete, not that the design is being replaced."""
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks=COVERAGE.format(v="GAP"))
            adt_dod.record_verdict(t, "coverage")
            adt_dod.record_verdict(t, "coverage")
            self.assertIsNone(adt_dod.divergence_escalation(t))

    def test_a_block_with_no_parseable_verdict_cannot_escalate(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, blocks="### Plan-quality review\nno verdict here\n")
            adt_dod.record_verdict(t, "plan-quality")
            adt_dod.record_verdict(t, "plan-quality")
            self.assertIsNone(adt_dod.divergence_escalation(t))


class GradedHeadingTest(unittest.TestCase):
    def test_the_graded_hash_covers_a_suffixed_sub_steps_heading(self):
        """The template writes `### Sub-steps  (DERIVED from ...)`, and the
        matcher used to require the bare heading, so no sub-step change ever
        moved the hash."""
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp)
            body = open(t, encoding="utf-8").read().replace(
                "### Sub-steps",
                "### Sub-steps  (DERIVED from Design + Impact — not invented)")
            open(t, "w", encoding="utf-8").write(body)
            before = adt_dod.graded_text_hash(t)
            open(t, "w", encoding="utf-8").write(body.replace(
                "- 1a [backend] — do the thing",
                "- 1a [backend] — do something else entirely"))
            self.assertNotEqual(before, adt_dod.graded_text_hash(t))

    def test_a_design_notes_heading_is_not_read_as_the_design(self):
        """A prefix match would take the LAST `Design*` heading, so a UI-review
        note would be hashed as the Design."""
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, design="the real design")
            body = open(t, encoding="utf-8").read() + (
                "\n### Design notes\nswatches and tokens\n")
            open(t, "w", encoding="utf-8").write(body)
            self.assertIn("the real design", adt_dod.graded_section(body, "Design"))
            self.assertNotIn("swatches", adt_dod.graded_section(body, "Design"))

    def test_an_appended_build_log_line_does_not_move_the_hash(self):
        """Otherwise every build-log append reopens every declared dependency."""
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp)
            before = adt_dod.graded_text_hash(t)
            open(t, "a", encoding="utf-8").write(
                "\n## Build log (Dev)\n- 1a done. Tests: PASS.\n")
            self.assertEqual(before, adt_dod.graded_text_hash(t))


if __name__ == "__main__":
    unittest.main()
