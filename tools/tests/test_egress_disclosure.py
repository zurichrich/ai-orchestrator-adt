# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that every outbound host in tools/, lib/ and collector/ is listed in
docs/security-posture.md.

Hosts are found two ways: https:// literals in the source, and `gh`/`curl`
subprocess calls, which reach api.github.com without naming it.
"""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOC = os.path.join(ROOT, "docs", "security-posture.md")
SCAN = ("tools", "lib", "collector")

URL = re.compile(r'https://([A-Za-z0-9.\-]+)')
# `gh` and `curl` invoked as a subprocess, in python or shell.
GH = re.compile(r'''(\[\s*["']gh["']|\bgh\s+api\b|^\s*gh\s|["']curl["']|\bcurl\s+-)''', re.M)

# Hosts a subprocess tool reaches that never appear as a literal.
IMPLIED = {"gh": "api.github.com"}
# Hosts that appear in the source but are never contacted. github.com is not
# here because the advisory fetch does contact it.
IGNORE = {"example.com", "x.example",
          # A test fixture origin on the reserved .example TLD, and the URL a
          # Durable Object stub is addressed with, which never leaves the isolate.
          "collector.example", "ip-budget"}


def _sources():
    for sub in SCAN:
        for dirpath, _, names in os.walk(os.path.join(ROOT, sub)):
            if "tests" in dirpath.split(os.sep):
                continue
            for n in names:
                if n.endswith((".py", ".sh", ".js", ".mjs")):
                    yield os.path.join(dirpath, n)


class EgressDisclosureTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(os.path.exists(DOC), f"{DOC} missing")
        with open(DOC) as fh:
            self.doc = fh.read()
        self.assertIn("## Network egress", self.doc)

    def _hosts(self):
        hosts = set()
        for path in _sources():
            with open(path, errors="replace") as fh:
                text = fh.read()
            for h in URL.findall(text):
                if h not in IGNORE:
                    hosts.add(h)
            if GH.search(text):
                hosts.add(IMPLIED["gh"])
        return hosts

    def test_every_outbound_host_is_documented(self):
        missing = sorted(h for h in self._hosts() if h not in self.doc)
        self.assertEqual(missing, [], f"undocumented egress hosts: {missing}")

    def test_gh_calls_are_detected_as_api_github_com(self):
        self.assertIn("api.github.com", self._hosts(),
                      "expected gh calls to add api.github.com")

    def test_removing_any_host_from_the_doc_reports_it_missing(self):
        for host in sorted(self._hosts()):
            with self.subTest(host=host):
                stripped = self.doc.replace(host, "REDACTED")
                missing = sorted(h for h in self._hosts() if h not in stripped)
                self.assertIn(host, missing,
                              f"expected {host} to be reported missing once "
                              f"removed from the doc")


if __name__ == "__main__":
    unittest.main()
