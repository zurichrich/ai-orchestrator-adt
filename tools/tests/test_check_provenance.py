# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for check_provenance.py: counted claims in the docs need a source command and a date.

Includes a negative control that strips the dates and expects the checker to fail.
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(ROOT, "tools", "check_provenance.py")
sys.path.insert(0, os.path.join(ROOT, "tools"))

import check_provenance as cp  # noqa: E402

DOCS = [os.path.join(ROOT, "docs", d) for d in
        ("gate-tax-report.md", "self-verification.md", "security-posture.md")]


def run(*args):
    return subprocess.run([sys.executable, TOOL, *args],
                          capture_output=True, text=True)


class ProvenanceTest(unittest.TestCase):
    def test_the_shipped_docs_pass(self):
        r = run(*DOCS)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_shipped_docs_contain_at_least_one_counted_claim(self):
        r = run(*DOCS)
        self.assertRegex(r.stdout, r"checked ([1-9][0-9]*) counted claim")

    def test_a_doc_with_its_dates_stripped_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(ROOT, "docs", "gate-tax-report.md")
            with open(src, encoding="utf-8") as fh:
                text = fh.read()
            probe = os.path.join(tmp, "stripped.md")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write(cp.DATE.sub("(date removed)", text))
            r = run(probe)
            self.assertEqual(r.returncode, 1,
                             "expected a failure with every date stripped:\n"
                             + r.stdout)

    def test_the_control_mode_reports_a_zero_claim_file_as_skipped(self):
        r = run("--control", os.path.join(ROOT, "docs", "security-posture.md"))
        self.assertIn("CONTROL SKIPPED", r.stdout)

    def test_a_number_in_a_code_fence_is_not_a_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "d.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("# T\n\n```\ntotal 550,652 tokens $67.81\n```\n")
            self.assertEqual(run(p).returncode, 0)

    def test_a_version_or_ticket_id_is_not_a_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "d.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("# T\n\nADT-224 shipped in v0.1.0 — see line 1234.\n")
            self.assertEqual(run(p).returncode, 0)

    def test_an_unsourced_claim_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "d.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("# T\n\nThe stamp understates by 76%.\n")
            self.assertEqual(run(p).returncode, 1)

    def test_a_sourced_claim_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "d.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("# T\n\n*Source: `awk -F'\\t' ... cost-ledger.log`, "
                         "2026-09-05.*\n\nThe stamp understates by 76%.\n")
            self.assertEqual(run(p).returncode, 0)

    def test_a_date_without_a_command_is_not_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "d.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("# T\n\nMeasured 2026-09-05.\n\nUnderstates by 76%.\n")
            self.assertEqual(run(p).returncode, 1)


if __name__ == "__main__":
    unittest.main()
