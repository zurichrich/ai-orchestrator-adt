# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests adt_usage_report, the reader for usage counts in Cloudflare Analytics
Engine: column order matches the collector, sampling weights are applied only
to row counts, a missing token exits cleanly, and the install figure is
labelled an estimate.
"""
import io
import os
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import adt_usage_report as rep  # noqa: E402

COLLECTOR = os.path.join(ROOT, "collector", "collector.js")


class CmdsetTest(unittest.TestCase):
    def test_cmdset_order_matches_writer(self):
        """Counts are positional, so the reader's order must match collector.js exactly."""
        import re
        with open(COLLECTOR, encoding="utf-8") as fh:
            js = fh.read()
        m = re.search(r"export const CMDSET_V1 = \[(.*?)\];", js, re.S)
        self.assertIsNotNone(m, "CMDSET_V1 not found in collector.js")
        writer = re.findall(r'"([a-z0-9-]+)"', m.group(1))
        self.assertEqual(writer, rep.CMDSET_V1,
                         "reader and writer disagree on cmdset-v1 column order")

    def test_cmdset_covers_the_shipped_commands(self):
        shipped = sorted(os.path.basename(p)[:-3]
                         for p in os.listdir(os.path.join(ROOT, "commands"))
                         if p.endswith(".md"))
        self.assertEqual(sorted(rep.CMDSET_V1), shipped)


class SamplingTest(unittest.TestCase):
    def test_weights_where_rows_are_counted_and_nowhere_else(self):
        """Every double is a cumulative total read with argMax/MAX/MIN, so only
        the ping count is weighted by _sample_interval."""
        for sql in (rep._sql(7), rep._sql_v1(7)):
            self.assertNotIn("SUM(_sample_interval * double", sql,
                             "a raw double is still summed")
            for fn in ("argMax", "MAX", "MIN"):
                for expr in re.findall(re.escape(fn) + r"\(([^)]*)\)", sql):
                    self.assertNotIn("_sample_interval", expr,
                                     f"sample weight inside {fn}({expr})")
        # The weight is applied when counting pings.
        self.assertIn("SUM(_sample_interval) AS pings", rep._sql(7))

    def test_the_reader_and_the_dashboard_read_the_same_totals_v1_rows(self):
        """The SQL this module generates and the SQL collector.js generates
        share the same filters, aggregates and table."""
        import shutil
        import subprocess
        node = shutil.which("node")
        if node is None:
            self.skipTest("node not installed")
        r = subprocess.run(
            [node, "--input-type=module", "-e",
             "import {dashboardQueries} from './collector/collector.js';"
             "process.stdout.write(dashboardQueries().v1Commands);"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        js_sql, py_sql = r.stdout, rep._sql_v1(30)
        for fragment in ("blob3 = 'totals-v1'", "blob4 = 'command'",
                         "argMax(double1, timestamp) AS total",
                         "MAX(double1) - MIN(double1) AS total_win",
                         "GROUP BY blob5, index1"):
            self.assertIn(fragment, py_sql, f"reader lost {fragment!r}")
            self.assertIn(fragment, js_sql, f"dashboard lost {fragment!r}")
        self.assertIn("FROM adt_usage_v2", py_sql)
        self.assertIn("FROM adt_usage_v2", js_sql)

    def test_the_query_is_pinned_to_the_declared_cmdset(self):
        self.assertIn("blob3 = 'cmdset-v1'", rep._sql(7))


class TokenHandlingTest(unittest.TestCase):
    def test_exits_cleanly_when_token_unset(self):
        env = dict(os.environ)
        env.pop("CLOUDFLARE_API_TOKEN", None)
        env.pop("CLOUDFLARE_ACCOUNT_ID", None)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "adt_usage_report.py")],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, f"expected a clean exit, got {r.returncode}")
        self.assertIn("CLOUDFLARE_API_TOKEN", r.stderr)

    def test_no_token_is_stored_in_the_repo(self):
        with open(os.path.join(ROOT, "tools", "adt_usage_report.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("Bearer sk", src)
        self.assertIn('os.environ.get("CLOUDFLARE_API_TOKEN"', src)

    def test_print_sql_needs_no_credentials(self):
        env = dict(os.environ)
        env.pop("CLOUDFLARE_API_TOKEN", None)
        env.pop("CLOUDFLARE_ACCOUNT_ID", None)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "adt_usage_report.py"),
                            "--print-sql"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0)
        self.assertIn("adt_usage_v2", r.stdout)


class InstallCountEstimateTest(unittest.TestCase):
    """The install count from Analytics Engine is an estimate and is labelled as one.
    The exact count lives in KV."""

    def test_the_query_does_not_call_it_installs(self):
        sql = rep._sql(30)
        self.assertIn("installs_estimate", sql)
        self.assertNotIn(" AS installs\n", sql)
        self.assertNotIn(" AS installs,", sql)

    def test_the_render_labels_it_an_estimate(self):
        row = {"pings": 12, "installs_estimate": 3}
        for name in rep.CMDSET_V1:
            row[name.replace("-", "_")] = 0
        out = rep.render([row])
        self.assertIn("estimate", out.lower())
        self.assertNotRegex(out, r"^installs:", )

    def test_the_render_names_the_exact_source(self):
        """The output says where the exact install count is kept."""
        row = {"pings": 1, "installs_estimate": 1}
        for name in rep.CMDSET_V1:
            row[name.replace("-", "_")] = 0
        out = rep.render([row])
        self.assertIn("install:", out)
        self.assertIn("kv key list", out)


class RenderTest(unittest.TestCase):
    def test_renders_a_row_into_named_commands(self):
        row = {"pings": 12, "installs_estimate": 3}
        for i, name in enumerate(rep.CMDSET_V1):
            row[name.replace("-", "_")] = i
        out = rep.render([row])
        self.assertIn("plan", out)
        self.assertIn("3", out)

    def test_empty_result_is_not_an_error(self):
        self.assertIn("no usage rows", rep.render([]))


if __name__ == "__main__":
    unittest.main()
