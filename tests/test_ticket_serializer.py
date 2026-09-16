# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests the ticket <-> GitHub Issue serialiser in tools/ticket_serializer.py:
frontmatter parsing, round trips in both directions, and the generated
Definition of Done block."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from ticket_serializer import (  # noqa: E402
    parse_md, emit_md, to_issue, from_issue, _DOD_MARKER,
)


SAMPLE_MD = """---
slug: example-ticket
id: TIX-999
title: An example ticket with every field shape
type: bug
priority: P0
track: full
size: L
stage: building
state: open
state_reason: null
created: 2026-06-14
updated: 2026-06-15
closed: null
created_by: claude
assignees:
  - alice
  - bob
milestone: v2.0
tags:
  - infra
  - sync
ui_review_required: true
security_review_required: false
issue_number: 222
issue_node_id: I_kwDOABCD123
comments:
  - author: claude
    at: 2026-06-14T10:00:00Z
    body: First closure note.
  - author: alice
    at: 2026-06-15T09:00:00Z
    body: Second comment.
---

# An example ticket with every field shape

## Why
Body text below the frontmatter, with **markdown** and a list:
- one
- two
"""


class TestFrontmatterRoundTrip(unittest.TestCase):
    def test_parse_then_emit_then_parse_is_stable(self):
        once = parse_md(SAMPLE_MD)
        twice = parse_md(emit_md(once))
        self.assertEqual(once, twice, "parse->emit->parse must be a fixed point")

    def test_scalars_parsed(self):
        d = parse_md(SAMPLE_MD)
        self.assertEqual(d["id"], "TIX-999")
        self.assertEqual(d["priority"], "P0")
        self.assertIs(d["ui_review_required"], True)
        self.assertIs(d["security_review_required"], False)
        self.assertIsNone(d["closed"])

    def test_string_lists_parsed(self):
        d = parse_md(SAMPLE_MD)
        self.assertEqual(d["assignees"], ["alice", "bob"])
        self.assertEqual(d["tags"], ["infra", "sync"])

    def test_comments_dictlist_parsed(self):
        d = parse_md(SAMPLE_MD)
        self.assertEqual(len(d["comments"]), 2)
        self.assertEqual(d["comments"][0]["author"], "claude")
        self.assertEqual(d["comments"][1]["body"], "Second comment.")

    def test_body_preserved(self):
        d = parse_md(SAMPLE_MD)
        self.assertIn("# An example ticket", d["body"])
        self.assertIn("- one", d["body"])

    def test_scalar_value_on_a_list_key_parses_to_a_one_item_list(self):
        md = "---\nslug: x\ncommits: 360031a\n---\n\n# X\n"
        d = parse_md(md)
        self.assertEqual(d["commits"], ["360031a"])
        self.assertEqual(parse_md(emit_md(d)), d)  # fixed point


class TestIssueRoundTrip(unittest.TestCase):
    def test_md_to_issue_to_md_zero_field_loss(self):
        original = parse_md(SAMPLE_MD)
        issue = to_issue(original)
        # Merge onto the original, because keys like slug and size are not
        # Issue fields.
        recovered = from_issue(issue, base=original)
        for key in ("title", "priority", "track", "stage", "state",
                    "assignees", "milestone", "created", "closed",
                    "created_by", "issue_number", "issue_node_id"):
            self.assertEqual(recovered.get(key), original.get(key),
                             f"field '{key}' lost on round-trip")
        self.assertEqual(sorted(recovered["tags"]), sorted(original["tags"]))
        self.assertEqual(len(recovered["comments"]), len(original["comments"]))
        self.assertEqual(recovered["comments"][0]["author"], "claude")

    def test_gates_map_to_labels_and_back(self):
        d = parse_md(SAMPLE_MD)
        issue = to_issue(d)
        names = {l["name"] for l in issue["labels"]}
        self.assertIn("gate:ui", names)          # ui_review_required: true
        self.assertNotIn("gate:security", names)  # security_review_required: false
        back = from_issue(issue, base={})
        self.assertIs(back.get("ui_review_required"), True)
        self.assertNotIn("security_review_required", back)

    def test_priority_and_track_labels(self):
        issue = to_issue(parse_md(SAMPLE_MD))
        names = {l["name"] for l in issue["labels"]}
        self.assertIn("P0", names)
        self.assertIn("track:full", names)
        self.assertIn("stage:building", names)

    def test_issue_to_md_from_scratch(self):
        # Simulate a `gh issue view --json` dump with no prior .md.
        issue = {
            "title": "Filed via gh",
            "body": "## Why\nfiled directly",
            "number": 300,
            "id": "I_node300",
            "state": "open",
            "stateReason": None,
            "labels": [{"name": "P1"}, {"name": "track:standard"},
                       {"name": "stage:planned"}, {"name": "needs-design"}],
            "assignees": [{"login": "alice"}],
            "milestone": None,
            "createdAt": "2026-06-15",
            "closedAt": None,
            "author": {"login": "alice"},
            "comments": [],
        }
        d = from_issue(issue, base={})
        self.assertEqual(d["priority"], "P1")
        self.assertEqual(d["track"], "standard")
        self.assertEqual(d["stage"], "planned")
        self.assertEqual(d["tags"], ["needs-design"])
        self.assertEqual(d["assignees"], ["alice"])
        # And it emits a valid .md that re-parses identically.
        self.assertEqual(parse_md(emit_md(d)), d)


class TestLegacyTicketDerivations(unittest.TestCase):
    """A ticket with no title: or state: field, only stage and an H1."""

    LEGACY = """---
slug: legacy
id: TIX-100
priority: P1
stage: ideas
type: bug
---

# The real heading lives in the body

## Why
text
"""

    def test_title_falls_back_to_h1(self):
        issue = to_issue(parse_md(self.LEGACY))
        self.assertEqual(issue["title"], "The real heading lives in the body")

    def test_state_derives_from_stage(self):
        self.assertEqual(to_issue(parse_md(self.LEGACY))["state"], "open")

    def test_done_stage_is_closed_completed(self):
        d = parse_md(self.LEGACY.replace("stage: ideas", "stage: done"))
        issue = to_issue(d)
        self.assertEqual(issue["state"], "closed")
        self.assertEqual(issue["stateReason"], "completed")

    def test_cancelled_status_is_closed_not_planned(self):
        d = parse_md(self.LEGACY.replace("stage: ideas",
                                         "stage: ideas\nstatus: cancelled"))
        issue = to_issue(d)
        self.assertEqual(issue["state"], "closed")
        self.assertEqual(issue["stateReason"], "not_planned")

    # ADT-354: the board render writes `state: closed` into done and cancelled
    # files but never a reason, which used to skip the derivation entirely.
    def test_cancelled_closed_without_reason_is_not_planned(self):
        d = parse_md(self.LEGACY.replace(
            "stage: ideas",
            "stage: done\nstatus: cancelled\nstate: closed\nstate_reason: null"))
        issue = to_issue(d)
        self.assertEqual(issue["state"], "closed")
        self.assertEqual(issue["stateReason"], "not_planned")

    def test_done_closed_without_reason_is_completed(self):
        d = parse_md(self.LEGACY.replace(
            "stage: ideas", "stage: done\nstate: closed\nstate_reason: null"))
        self.assertEqual(to_issue(d)["stateReason"], "completed")

    def test_an_explicit_reason_on_a_closed_ticket_is_kept(self):
        d = parse_md(self.LEGACY.replace(
            "stage: ideas", "stage: done\nstate: closed\nstate_reason: duplicate"))
        self.assertEqual(to_issue(d)["stateReason"], "duplicate")


class TestDoneEvidenceRoundTrip(unittest.TestCase):
    """done_evidence is a list of dicts. It survives parse_md -> emit_md, and
    adt_dod's own parser reads emit_md's output."""

    # must_run with shell metacharacters and a leading `!`, a
    # file/must_contain_regex entry with a `(?i)` regex, and a was_red_at ref.
    DONE_EVIDENCE = [
        {"must_run": 'test -x defaults/hooks/adt-dod.sh && grep -q "adt_dod.py" defaults/hooks/adt-dod.sh',
         "lane": "build"},
        {"must_run": '! grep -rn "python3 tools/adt_dod.py" commands/', "lane": "build"},
        {"file": "README.md",
         "must_contain_regex": "(?i)updating an existing|update an existing install",
         "lane": "build"},
        {"must_run": "bash tests/test_uninstall.sh", "was_red_at": "plan", "lane": "qa"},
    ]

    def _ticket(self, done_evidence):
        return {"id": "ADT-090", "slug": "x", "stage": "building",
                "done_evidence": done_evidence, "body": "# Title\n\nbody\n"}

    def test_done_evidence_survives_round_trip(self):
        out = emit_md(self._ticket(self.DONE_EVIDENCE))
        self.assertEqual(parse_md(out)["done_evidence"], self.DONE_EVIDENCE)

    def test_not_flattened_to_null(self):
        out = emit_md(self._ticket(self.DONE_EVIDENCE))
        self.assertNotIn("done_evidence: null", out)
        self.assertIn("must_contain_regex:", out)
        self.assertIn("was_red_at: plan", out)

    def test_quote_wrapped_value_survives_reparse(self):
        """A value that starts and ends with the same quote character, such as
        `"a" && "b"`, reads back unchanged in both parsers."""
        import importlib
        import tempfile
        adt_dod = importlib.import_module("adt_dod")
        evidence = [
            {"must_run": '"a" && "b"', "lane": "build"},
            {"must_run": "'single wrapped'", "lane": "build"},
            {"must_run": 'grep -qE "^x" f', "lane": "done"},
        ]
        out = emit_md(self._ticket(evidence))
        # the serializer's parser
        self.assertEqual(parse_md(out)["done_evidence"], evidence)
        # adt_dod's parser
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(out)
            path = fh.name
        try:
            got = [e.get("must_run") for e in adt_dod.parse_done_evidence(path)]
        finally:
            os.unlink(path)
        self.assertEqual(got, [e["must_run"] for e in evidence])

    def test_empty_round_trips_as_list_not_null(self):
        out = emit_md(self._ticket([]))
        self.assertIn("done_evidence: []", out)
        self.assertEqual(parse_md(out)["done_evidence"], [])

    def test_emitted_form_is_what_adt_dod_reads(self):
        # adt_dod has its own parser, separate from the serializer's.
        import importlib
        adt_dod = importlib.import_module("adt_dod")
        import tempfile
        out = emit_md(self._ticket(self.DONE_EVIDENCE))
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(out)
            path = fh.name
        try:
            evs = adt_dod.parse_done_evidence(path)
        finally:
            os.unlink(path)
        self.assertEqual(len(evs), len(self.DONE_EVIDENCE))
        file_ev = next(e for e in evs if e.get("file") == "README.md")
        self.assertEqual(file_ev["regex"],
                         "(?i)updating an existing|update an existing install")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestGeneratedDoDBlock(unittest.TestCase):
    """The `### Definition of Done` block in the Issue body is generated from
    the `done_evidence:` frontmatter on push and parsed back on reconstruct."""

    EV = [
        {"must_run": "python3 -m pytest tests/x.py -q",
         "was_red_at": "2337ba6", "lane": "build"},
        {"must_run": '"a" && "b"', "lane": "done"},
        {"file": "kanban.html", "must_contain_regex": "ADT-273", "lane": "done"},
    ]
    WITH_FENCE = ("# T\n\n### Definition of Done (machine-checkable)\n"
                  "```yaml\ndone_evidence:\n  - must_run: 'stale hand copy'\n"
                  "    lane: build\n```\n\n### Sub-steps\n- 1a\n")
    NO_FENCE = ("# T\n\n### Definition of Done (machine-checkable)\n\n"
                "### Sub-steps\n- 1a\n")
    NO_HEADING = "# T\n\nnothing here\n"

    def _body(self, body, evidence, slug="t"):
        return to_issue({"slug": slug, "done_evidence": evidence,
                         "body": body})["body"]

    def test_dod_block_generated_from_frontmatter(self):
        """The frontmatter replaces whatever block the body held."""
        out = self._body(self.WITH_FENCE, self.EV)
        self.assertIn(_DOD_MARKER, out)
        self.assertNotIn("stale hand copy", out)
        self.assertIn("was_red_at: 2337ba6", out)
        self.assertEqual(out.count(_DOD_MARKER), 1)
        self.assertEqual(out.count("done_evidence:"), 1)

    def test_dod_block_is_idempotent(self):
        once = self._body(self.WITH_FENCE, self.EV)
        twice = self._body(once, self.EV)
        self.assertEqual(once, twice)

    def test_dod_inserted_under_empty_heading(self):
        """A body with the heading but no block gains one; a body with no
        heading is left alone."""
        out = self._body(self.NO_FENCE, self.EV)
        self.assertIn(_DOD_MARKER, out)
        self.assertIn("```\n\n### Sub-steps", out)   # next heading stays spaced
        untouched = self._body(self.NO_HEADING, self.EV)
        self.assertTrue(untouched.startswith(self.NO_HEADING))
        self.assertNotIn(_DOD_MARKER, untouched)

    def test_empty_done_evidence_leaves_body_untouched(self):
        for empty in ([], None):
            out = self._body(self.WITH_FENCE, empty)
            self.assertTrue(out.startswith(self.WITH_FENCE))
            self.assertIn("stale hand copy", out)
            self.assertNotIn(_DOD_MARKER, out)

    def test_malformed_done_evidence_does_not_break_the_push(self):
        """A malformed `done_evidence:` renders no block and leaves the body
        alone instead of raising."""
        for bad in ("some string", ["not", "dicts"], 7, {"a": 1}):
            out = self._body(self.WITH_FENCE, bad)
            self.assertTrue(out.startswith(self.WITH_FENCE))
            self.assertNotIn(_DOD_MARKER, out)

    def test_dod_block_round_trips_on_reconstruct(self):
        """A ticket rebuilt from the Issue alone, with base={} as adt_sync
        passes it, keeps its done_evidence."""
        issue = to_issue({"slug": "t", "issue_number": 9,
                          "done_evidence": self.EV, "body": self.WITH_FENCE})
        self.assertEqual(from_issue(issue, base={}).get("done_evidence"), self.EV)

    def test_unmarked_block_is_not_parsed_back(self):
        """Only a block carrying the generated marker is parsed back; a
        hand-written block is ignored."""
        self.assertIsNone(
            from_issue({"body": self.WITH_FENCE, "number": 1},
                       base={}).get("done_evidence"))
        marked = to_issue({"slug": "t", "done_evidence": self.EV,
                           "body": self.WITH_FENCE})["body"]
        self.assertIsNotNone(
            from_issue({"body": marked, "number": 1},
                       base={}).get("done_evidence"))
