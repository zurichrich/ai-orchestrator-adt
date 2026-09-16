# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests the slug trailer: to_issue appends `<!-- adt: slug=... -->` to the
Issue body, and from_issue reads the slug back and strips the trailer."""

import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))

from ticket_serializer import parse_md, to_issue, from_issue  # noqa: E402

# Bodies have no trailing newline, matching what parse_md returns.
TICKET = {
    "slug": "hello-script",
    "id": "TIX-1",
    "title": "Add a hello script",
    "type": "task",
    "stage": "ideas",
    "body": "# Add a hello script\n\n## Problem (user)\nNo way to check it runs.",
}
TRAILER = "<!-- adt: slug=hello-script -->"


class SlugTrailerPush(unittest.TestCase):

    def test_body_on_the_issue_carries_the_slug(self):
        self.assertIn(TRAILER, to_issue(TICKET)["body"])

    def test_the_ticket_body_itself_is_unchanged(self):
        to_issue(TICKET)
        self.assertNotIn("adt: slug=", TICKET["body"])

    def test_appending_is_idempotent(self):
        once = to_issue(TICKET)["body"]
        twice = to_issue(dict(TICKET, body=once))["body"]
        self.assertEqual(once, twice)
        self.assertEqual(twice.count("adt: slug="), 1)

    def test_a_slug_that_could_break_the_comment_is_refused(self):
        for bad in ("a -->b", "a\nb", "../escape", "x" * 300):
            with self.subTest(slug=bad):
                self.assertNotIn(
                    "adt: slug=", to_issue(dict(TICKET, slug=bad))["body"])

    def test_no_slug_emits_no_trailer(self):
        self.assertNotIn("adt: slug=", to_issue(dict(TICKET, slug=None))["body"])

    def test_a_body_we_add_no_trailer_to_is_returned_byte_identical(self):
        for slug in (None, "", "a -->b", "../escape", "x" * 300):
            for body in ("# T\n\nruns.", "", "# T\n\n## Section\n\n- a\n- b"):
                with self.subTest(slug=slug, body=body):
                    issue = to_issue(dict(TICKET, slug=slug, body=body))
                    self.assertEqual(issue["body"], body)
                    self.assertEqual(from_issue(issue, base={})["body"], body)


class SlugTrailerPull(unittest.TestCase):

    def test_reconstruct_recovers_the_slug(self):
        # base={} means there is no local file to merge onto.
        back = from_issue(to_issue(TICKET), base={})
        self.assertEqual(back["slug"], "hello-script")

    def test_trailer_is_stripped_from_the_recovered_body(self):
        back = from_issue(to_issue(TICKET), base={})
        self.assertNotIn("adt: slug=", back["body"])
        self.assertEqual(back["body"], TICKET["body"])

    def test_round_trip_is_byte_identical(self):
        self.assertEqual(from_issue(to_issue(TICKET), base={})["body"],
                         TICKET["body"])

    def test_a_local_slug_wins_over_the_trailer(self):
        issue = to_issue(TICKET)
        back = from_issue(issue, base={"slug": "renamed-locally"})
        self.assertEqual(back["slug"], "renamed-locally")

    def test_an_issue_without_a_trailer_is_untouched(self):
        legacy = {"number": 7, "title": "T", "body": "# T\n\nold body"}
        back = from_issue(legacy, base={})
        self.assertEqual(back["body"], "# T\n\nold body")
        self.assertIsNone(back.get("slug"))

    def test_a_body_that_mentions_the_pattern_mid_text_is_not_a_trailer(self):
        prose = {"number": 7, "title": "T",
                 "body": "# T\n\nwe write <!-- adt: slug=x --> in the docs\n\nmore text"}
        back = from_issue(prose, base={})
        self.assertIsNone(back.get("slug"))
        self.assertEqual(back["body"], prose["body"])

    def test_a_body_whose_last_line_contains_the_pattern_is_not_truncated(self):
        """Only a comment on its own line, after a blank line, is a trailer."""
        for body in (
            "# Doc\n\nExample: <!-- adt: slug=example -->",
            "trailing text <!-- adt: slug=nope -->",
            "# Doc\n\nsee <!-- adt: slug=a --> and <!-- adt: slug=b -->",
        ):
            with self.subTest(body=body):
                back = from_issue({"number": 7, "title": "T", "body": body}, base={})
                self.assertEqual(back["body"], body, "authored body was truncated")
                self.assertIsNone(back.get("slug"), "slug invented from prose")


class ParsedTicketRoundTrip(unittest.TestCase):

    def test_a_body_ending_in_a_blank_line_survives(self):
        """A ticket file ending in a blank line gives a body ending in "\n"."""
        md = ("---\nslug: s\ntype: task\nstage: ideas\ntitle: T\n---\n\n"
              "# T\n\nbody\n\n")
        d = parse_md(md)
        self.assertTrue(d["body"].endswith("\n"),
                        "fixture no longer exercises the trailing-newline shape")
        self.assertEqual(from_issue(to_issue(d), base={})["body"], d["body"])

    def test_a_parsed_ticket_body_round_trips_unchanged(self):
        md = ("---\nslug: fixture-ticket\ntype: task\nstage: ideas\n"
              "title: Fixture\n---\n\n# Fixture\n\nbody\n")
        d = parse_md(md)
        self.assertFalse(d["body"].endswith("\n"), "fixture must use parse_md's shape")
        self.assertEqual(from_issue(to_issue(d), base={})["body"], d["body"])


if __name__ == "__main__":
    unittest.main()
