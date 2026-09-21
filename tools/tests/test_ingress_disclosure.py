# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that every listening socket in tools/, lib/ and collector/ is described
in docs/security-posture.md.

The egress half of that document has been enforced since it was written; before
AO-006 there was no ingress to enforce, because ADT opened no sockets. It opens
one now — serving the whole backlog and accepting a state change — so the same
rule applies: open a port and forget to document it, and this goes red.
"""
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOC = os.path.join(ROOT, "docs", "security-posture.md")
SCAN = ("tools", "lib", "collector")

# A server binding an address: socketserver/http.server subclasses instantiated
# with an (host, port) tuple, and raw socket binds.
BIND = re.compile(r'''\b\w+\(\(\s*["']([0-9a-zA-Z.:]*)["']\s*,\s*([A-Za-z0-9_.]+)\s*\)''')
# Ports named as a module constant, so the doc can be checked against the value.
PORT_CONST = re.compile(r'^([A-Z_]*PORT)\s*=\s*(\d+)\s*$', re.M)


def _sources():
    for top in SCAN:
        base = os.path.join(ROOT, top)
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules")]
            for fn in files:
                if fn.endswith((".py", ".sh")):
                    yield os.path.join(dirpath, fn)


class IngressDisclosure(unittest.TestCase):
    def test_every_bind_is_documented(self):
        doc = open(DOC, encoding="utf-8").read()
        self.assertIn("## Network ingress", doc,
                      "docs/security-posture.md has no ingress section")
        ingress = doc.split("## Network ingress", 1)[1]

        found = []
        for path in _sources():
            src = open(path, encoding="utf-8").read()
            if "test_ingress_disclosure" in os.path.basename(path):
                continue
            consts = dict(PORT_CONST.findall(src))
            for host, port in BIND.findall(src):
                if not host:                       # "" = all interfaces
                    host = "0.0.0.0"
                value = consts.get(port, port)
                found.append((os.path.relpath(path, ROOT), host, value))

        undocumented = []
        for rel, host, port in found:
            # The doc must name the interface, and the port where it is a literal.
            if host not in ingress:
                undocumented.append(f"{rel}: binds {host} — not in the ingress section")
            elif port.isdigit() and port not in ingress:
                undocumented.append(f"{rel}: binds port {port} — not in the ingress section")
        self.assertFalse(undocumented, "undocumented listener(s):\n" + "\n".join(undocumented))

    def test_the_check_can_see_the_known_listener(self):
        """A disclosure test that finds nothing passes for the wrong reason."""
        binds = [f for f in _sources()
                 if "_serve_board" in open(f, encoding="utf-8").read()]
        self.assertTrue(binds, "the scanner found no listener at all; it is not looking")


if __name__ == "__main__":
    unittest.main()
