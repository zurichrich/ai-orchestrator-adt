# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests the summary line adt_sync prints after adopting Issues on a pull,
including the count of Issues it deferred."""

import os
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))

from adt_sync import summarise_adopt, RECONSTRUCT_GRACE_SECONDS  # noqa: E402


class AdoptSummary(unittest.TestCase):

    def test_reports_what_it_adopted(self):
        line = summarise_adopt([{"action": "reconstructed"},
                                {"action": "reconstructed"}])
        self.assertIn("adopted 2 issue(s)", line)

    def test_says_nothing_about_deferral_when_none_deferred(self):
        self.assertNotIn("deferred", summarise_adopt([{"action": "reconstructed"}]))

    def test_names_the_deferred_count(self):
        line = summarise_adopt([{"action": "reconstructed"},
                                {"action": "deferred"},
                                {"action": "deferred"}])
        self.assertIn("adopted 1 issue(s)", line)
        self.assertIn("2 deferred", line)

    def test_explains_the_wait_and_that_they_are_picked_up_next_sync(self):
        line = summarise_adopt([{"action": "deferred"}])
        self.assertIn("%d min" % (RECONSTRUCT_GRACE_SECONDS // 60), line)
        self.assertIn("next sync", line)

    def test_empty_pull_is_not_reported_as_a_failure(self):
        self.assertIn("adopted 0 issue(s)", summarise_adopt([]))

    def test_other_actions_are_not_counted_as_adoptions(self):
        line = summarise_adopt([{"action": "noop"}, {"action": "updated"}])
        self.assertIn("adopted 0 issue(s)", line)


class WiredUp(unittest.TestCase):
    """Runs the real adt_sync CLI to check it prints the summary."""

    def test_adt_sync_emits_the_line_on_a_pull(self):
        """Only `gh` is stubbed."""
        import tempfile, textwrap
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".adt"))
            open(os.path.join(tmp, ".adt", "config.yaml"), "w").write(
                textwrap.dedent("""\
                    project: stub
                    repo: me/stub
                    owner: me
                    id_prefix: TIX
                    backlog_root: development-team/backlog
                    cache_dir: %s/cache
                    """) % tmp)
            os.makedirs(os.path.join(tmp, "cache"))
            binp = os.path.join(tmp, "bin")
            os.makedirs(binp)
            gh = os.path.join(binp, "gh")
            # Every gh call on this path expects a JSON list; an empty one is a
            # repo with no Issues.
            open(gh, "w").write("#!/bin/sh\necho '[]'\n")
            os.chmod(gh, 0o755)
            env = dict(os.environ, PATH=binp + os.pathsep + os.environ["PATH"])
            r = subprocess.run(
                [sys.executable, os.path.join(ROOT, "tools", "adt_sync.py"),
                 "--root", tmp, "--pull"],
                capture_output=True, text=True, env=env, cwd=ROOT)
            self.assertIn(
                "adopted", r.stdout,
                "expected an adopt summary from adt_sync --pull. "
                "stdout=%r stderr=%r" % (r.stdout, r.stderr))

    def test_a_push_only_run_does_not_print_the_adopt_line(self):
        import tempfile, textwrap
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".adt"))
            open(os.path.join(tmp, ".adt", "config.yaml"), "w").write(
                textwrap.dedent("""\
                    project: stub
                    repo: me/stub
                    owner: me
                    id_prefix: TIX
                    backlog_root: development-team/backlog
                    cache_dir: %s/cache
                    """) % tmp)
            os.makedirs(os.path.join(tmp, "cache"))
            r = subprocess.run(
                [sys.executable, os.path.join(ROOT, "tools", "adt_sync.py"),
                 "--root", tmp],
                capture_output=True, text=True, cwd=ROOT)
            self.assertNotIn("adopted", r.stdout)

if __name__ == "__main__":
    unittest.main()
