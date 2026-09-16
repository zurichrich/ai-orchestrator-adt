# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests the install id: a UUID read from .adt/state/install-id, shared by
worktrees, read the same way by the Python and bash readers, and used to count
a machine's own spend once."""
import os
import re
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_kanban as bk  # noqa: E402

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def project(tmp, install_id=None):
    """A project root with an .adt-state, optionally carrying an install id."""
    state = os.path.join(tmp, ".adt", "state")
    os.makedirs(state, exist_ok=True)
    subprocess.run(["git", "-C", tmp, "init", "-q"], check=False)
    # An empty commit, so a worktree can be added to the repo.
    for cmd in (["git", "-C", tmp, "config", "user.email", "t@t"],
                ["git", "-C", tmp, "config", "user.name", "t"],
                ["git", "-C", tmp, "commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(cmd, check=False, capture_output=True)
    if install_id:
        with open(os.path.join(state, "install-id"), "w", encoding="utf-8") as fh:
            fh.write(install_id + "\n")
    return tmp


class IdentityShapeTest(unittest.TestCase):
    def test_the_id_is_a_uuid_not_the_hostname(self):
        import platform
        with tempfile.TemporaryDirectory() as tmp:
            known = str(uuid.uuid4())
            got = bk.machine_id(project(tmp, known))
            self.assertEqual(got, known)
            self.assertRegex(got, UUID_RE)
            host = (platform.node().split(".")[0] or "").lower()
            self.assertNotEqual(got, host)
            self.assertNotIn(host, got, "expected no hostname in the id")

    def test_a_missing_id_file_gets_a_new_stable_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            first = bk.machine_id(root)
            self.assertRegex(first, UUID_RE)
            self.assertEqual(bk.machine_id(root), first, "expected the same id twice")

    def test_the_id_is_read_from_the_canonical_checkout(self):
        """A linked worktree has no state dir of its own and uses the canonical one."""
        with tempfile.TemporaryDirectory() as tmp:
            known = str(uuid.uuid4())
            canonical = project(os.path.join(tmp, "canon"), known)
            wt = os.path.join(tmp, "wt")
            subprocess.run(["git", "-C", canonical, "worktree", "add", "-q",
                            "--detach", wt], check=False,
                           capture_output=True)
            if not os.path.isdir(wt):
                self.skipTest("git worktree unavailable")
            self.assertFalse(
                os.path.exists(os.path.join(wt, ".adt", "state",
                                            "install-id")),
                "fixture invalid: the worktree must not have the id file")
            self.assertEqual(bk.machine_id(wt), known,
                             "expected the worktree to read the canonical id")


class ReadersAgreeTest(unittest.TestCase):
    def test_python_and_bash_readers_agree(self):
        """machine_id() and a bash read of the file return the same id."""
        with tempfile.TemporaryDirectory() as tmp:
            known = str(uuid.uuid4())
            root = project(tmp, known)
            path = os.path.join(root, ".adt", "state", "install-id")
            from_bash = subprocess.run(
                ["bash", "-c", 'cat "$1" 2>/dev/null | tr -d "\\n"', "_", path],
                capture_output=True, text=True).stdout.strip()
            self.assertEqual(bk.machine_id(root), from_bash)
            self.assertEqual(from_bash, known)

    def test_no_reader_still_derives_a_hostname(self):
        """No reader of the machine id calls platform.node()."""
        import ast

        offenders = []
        for rel in ("tools/build_kanban.py", "tools/adt_sync.py",
                    "defaults/hooks/adt-token-total.sh",
                    ".claude/hooks/adt-token-total.sh"):
            path = os.path.join(ROOT, rel)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            if rel.endswith(".py"):
                # Parse for real calls so a mention in a docstring does not match.
                for node in ast.walk(ast.parse(body)):
                    if (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "node"
                            and isinstance(node.func.value, ast.Name)
                            and node.func.value.id == "platform"):
                        offenders.append("%s:%d" % (rel, node.lineno))
            else:
                for n, line in enumerate(body.splitlines(), 1):
                    if "platform.node()" in line and not line.lstrip().startswith("#"):
                        offenders.append("%s:%d" % (rel, n))
        self.assertEqual(offenders, [],
                         "expected no platform.node() calls, found: %s"
                         % offenders)


class RegisterDedupeTest(unittest.TestCase):
    """How own spend combines with registers keyed by UUID or by hostname."""

    @staticmethod
    def combine(local, registers, own):
        """adt-token-total.sh's rule: max(own ledger, own register) + sum of
        every other register."""
        regs = dict(registers)
        own_reg = regs.pop(own, 0)
        return max(local, own_reg) + sum(regs.values())

    def test_own_register_is_not_added_to_own_ledger(self):
        own = str(uuid.uuid4())
        self.assertEqual(self.combine(10000, {own: 8000}, own), 10000)
        self.assertEqual(self.combine(6000, {own: 8000}, own), 8000)

    def test_a_stale_hostname_register_is_summed_as_another_machine(self):
        """combine() cannot tell an old hostname register is this machine's, so
        the migration has to remove it."""
        own = str(uuid.uuid4())
        stale = "mac"
        total = self.combine(10000, {own: 8000, stale: 8000}, own)
        self.assertEqual(total, 18000,
                         "expected the stale hostname register to be summed "
                         "as another machine's")

    def test_another_machines_register_is_still_counted(self):
        own = str(uuid.uuid4())
        self.assertEqual(self.combine(10000, {own: 8000, "other": 5000}, own),
                         15000)


if __name__ == "__main__":
    unittest.main()
