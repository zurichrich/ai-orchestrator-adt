# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for adt_register_migrate: it re-keys only this machine's register
comments from hostname to install id, keeps every total, and leaves other
machines' registers alone."""
import os
import subprocess
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import adt_register_migrate as m  # noqa: E402
import build_kanban as bk  # noqa: E402

OWN = "mac"
NEW = "af787b39-3760-46f9-a0d5-640ab98fdae1"
OTHER = "r-mac-mini"


def comment(cid, machine, total, micros=None, tier="measured"):
    return {"id": cid, "body": bk.register_body(machine, total, micros, tier)}


def fixture():
    return [
        comment(1, OWN, 966638, 147765743),
        comment(2, OWN, 10000),
        comment(3, OTHER, 500000, 80000000),
        comment(4, OTHER, 250),
        {"id": 5, "body": "just a normal comment, no register here"},
    ]


class ScopeTest(unittest.TestCase):
    def test_only_own_hostname_registers_are_rewritten(self):
        work = m.plan(fixture(), OWN, NEW)
        self.assertEqual(sorted(c for c, _, _ in work), [1, 2])

    def test_another_machines_registers_are_left_untouched(self):
        work = m.plan(fixture(), OWN, NEW)
        touched = {c for c, _, _ in work}
        self.assertNotIn(3, touched)
        self.assertNotIn(4, touched)

    def test_a_non_register_comment_is_never_touched(self):
        self.assertNotIn(5, {c for c, _, _ in m.plan(fixture(), OWN, NEW)})

    def test_no_hostname_to_uuid_map_is_written(self):
        with open(os.path.join(ROOT, "tools", "adt_register_migrate.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("machine-map", src)
        self.assertNotIn("open(", src.split("def main")[0].replace(
            "open(os.path", ""), "the planning half must not open any file")


class PreservationTest(unittest.TestCase):
    def test_totals_survive_the_migration(self):
        """Re-key, then check every number is identical."""
        before = m.snapshot(fixture())
        migrated = []
        rewritten = {c: new for c, _, new in m.plan(fixture(), OWN, NEW)}
        for c in fixture():
            migrated.append({"id": c["id"],
                             "body": rewritten.get(c["id"], c["body"])})
        after = m.snapshot(migrated)
        self.assertEqual(m.verify(before, after, OWN, NEW), [])
        self.assertEqual(after[1][1], 966638)
        self.assertEqual(after[1][2], 147765743)
        self.assertEqual(after[1][0], NEW, "ours must be re-keyed")
        self.assertEqual(after[3][0], OTHER, "theirs must not be")

    def test_deleting_every_register_fails_verification(self):
        """verify() reports deleted registers, so a wipe cannot pass as a migration."""
        before = m.snapshot(fixture())
        problems = m.verify(before, {}, OWN, NEW)
        self.assertEqual(len(problems), 4)
        self.assertTrue(all("disappeared" in p for p in problems))

    def test_other_machines_registers_match_the_snapshot(self):
        """verify() also catches another machine's row being changed or re-keyed."""
        before = m.snapshot(fixture())
        after = dict(before)
        after[3] = (OTHER, 1, None)          # amounts mangled
        problems = m.verify(before, after, OWN, NEW)
        self.assertTrue(any("amounts changed" in p for p in problems), problems)

        after = dict(before)
        after[3] = (NEW, 500000, 80000000)   # another machine's row re-keyed to us
        problems = m.verify(before, after, OWN, NEW)
        self.assertTrue(any("belongs to" in p for p in problems), problems)

    def test_a_partial_rekey_is_caught(self):
        before = m.snapshot(fixture())
        after = dict(before)
        after[1] = (NEW, 966638, 147765743)  # done
        # comment 2 left on the old key
        problems = m.verify(before, after, OWN, NEW)
        self.assertTrue(any("did not re-key" in p for p in problems), problems)


class BodyTest(unittest.TestCase):
    def test_the_hostname_leaves_the_prose_too(self):
        """The hostname is replaced in the readable sentence as well as the marker."""
        body = bk.register_body(OWN, 12345, 6789)
        self.assertIn("`%s`" % OWN, body)
        new = m.rewrite_body(body, OWN, NEW)
        # "mac" is inside "machine", so check the two places the hostname
        # appeared rather than a bare substring.
        self.assertNotIn("machine=%s" % OWN, new)
        self.assertNotIn("`%s`" % OWN, new)
        self.assertIn("machine=%s" % NEW, new)
        self.assertIn("`%s`" % NEW, new)
        self.assertIn("12,345", new)

    def test_a_token_only_register_survives_rewriting(self):
        """A register with no cost marker gets none added when rewritten."""
        body = bk.register_body(OWN, 500)
        new = m.rewrite_body(body, OWN, NEW)
        self.assertNotIn("adt:cost", new)
        self.assertEqual(m.register_of(new), (NEW, 500, None, None))


class DryRunTest(unittest.TestCase):
    def test_help_names_the_dry_run_default(self):
        env = dict(os.environ)
        env["PATH"] = os.path.join(ROOT, "tools", "tests") + os.pathsep + env["PATH"]
        r = subprocess.run([sys.executable,
                            os.path.join(ROOT, "tools", "adt_register_migrate.py"),
                            "--help"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--dry-run", r.stdout + r.stderr,
                      "the help must name the dry-run default")

    def test_apply_is_opt_in_not_default(self):
        import argparse
        import io as _io
        from contextlib import redirect_stderr
        ap = None
        for line in open(os.path.join(ROOT, "tools", "adt_register_migrate.py"),
                         encoding="utf-8"):
            if 'add_argument("--apply"' in line:
                ap = line
        self.assertIsNotNone(ap, "--apply must exist")
        self.assertIn("store_true", ap,
                      "--apply must be a flag that defaults to off")


if __name__ == "__main__":
    unittest.main()
