#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""The delivery dashboard: what the lifecycle costs, and what each number covers.

ADT-254 Phase E, criteria 4-8. The operator sets `track:` against numbers ADT
produces about itself. Those numbers now exist per lane, per command and per
install — but a dashboard that shows them without saying what they COVER is the
defect this ticket was filed about, one layer up: per-ticket money reconciles
across machines, per-turn detail does not, and both would sit on the page
looking equally complete.

SO COVERAGE IS A VALUE, NOT A FOOTNOTE. Every panel carries
`data-coverage="<n> of <n>"` and renders it beside the number. A single-install
run reads `1 of 1` through the SAME code path a multi-install run uses; there is
no single-machine mode to rot, which is the point of criterion 5.

NO NETWORK CALLS. Everything comes from files: the local ledger and whatever
rollups are visible. That is what lets the lane split work offline — commands map
to lanes through `adt_lane_cost.COMMAND_LANE` at read time rather than being
resolved against GitHub label events at write time.

TWO BUCKETS ARE NAMED, NEVER MERGED. Rows with no install id (everything written
before ADT-254) are `unattributed`; commands that map to no lane (`decide`,
`unblock`, the three `review-*`, `build-todone`, and every subagent row) are
`unmapped`. Folding either into a total would produce exactly the kind of number
that gets quoted later without its caveat.

Self-contained HTML with inline SVG, matching `build_kanban.render_html`: this
repo ships no JS dependencies and the page is opened from disk, so a charting
library is neither available nor wanted. A histogram is `<rect>` elements.
"""
from __future__ import annotations

import argparse
import html
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt_cost  # noqa: E402
import adt_lane_cost  # noqa: E402
import adt_rollup  # noqa: E402
import build_kanban as bk  # noqa: E402

UNATTRIBUTED = "unattributed"
UNMAPPED = "unmapped"


class Panel:
    """A number and what it covers. The pairing is the whole design.

    `covered`/`total` are tickets or rows; `installs`/`installs_total` are
    machines. A panel cannot be rendered without them, which is what stops
    coverage becoming a footnote somebody trims.
    """

    def __init__(self, title, rows, covered, total, installs, note=""):
        self.title, self.rows = title, rows
        self.covered, self.total = covered, total
        self.installs, self.note = installs, note

    @property
    def coverage(self) -> str:
        return "%d of %d" % (self.covered, self.total)


def load(project_root: str, rollup_dir: str):
    """-> (rows, installs). Local ledger plus every rollup we can see.

    The ledger gives per-turn detail for THIS install; the rollups give the same
    shape for every install that has written one, including this one. Rows are
    normalised to the rollup shape so the two paths cannot diverge.
    """
    rows = []
    own = bk.machine_id(project_root)
    ledger = os.path.join(project_root, ".adt", "state",
                          "cost-ledger.log")
    prices = adt_cost.load_prices()
    estimator = adt_cost.load_estimator(project_root)
    seen_turn = set()
    try:
        with open(ledger, encoding="utf-8") as fh:
            for line in fh:
                r = adt_cost.parse_row(line)
                if not r:
                    continue
                micros, _ = adt_cost.price_row(r, prices, estimator)
                key = (r["ts"], r["tix"])
                rows.append({"date": (r["ts"] or "")[:10],
                             "install": r.get("install") or UNATTRIBUTED,
                             "command": r.get("command") or "",
                             "tix": r["tix"],
                             "turns": 0 if key in seen_turn else 1,
                             "tokens": r["input"] + r["output"],
                             "micros": micros or 0})
                seen_turn.add(key)
    except OSError:
        pass

    for r in adt_rollup.read_all(rollup_dir):
        if r["install"] == own:
            continue          # already have it from the ledger, in more detail
        r = dict(r)
        r["tix"] = None       # rollups carry no ticket id, by design
        rows.append(r)

    installs = sorted({r["install"] for r in rows}) or [own]
    return rows, installs


def by_key(rows, keyfn):
    out = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        acc = out[keyfn(r)]
        acc[0] += r["turns"]
        acc[1] += r["tokens"]
        acc[2] += r["micros"]
    return dict(out)


def lane_of(command: str) -> str:
    return adt_lane_cost.command_lane(command) or UNMAPPED


def histogram(values, bins=12):
    """-> [(lo, hi, count)]. Bars, not a smoothed curve.

    At this n a density plot implies a confidence the data does not support; a
    histogram shows `n=1` as one bar, which is the honest picture the gate-tax
    report's own caveat asks for.
    """
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return []
    lo, hi = vals[0], vals[-1]
    if hi == lo:
        return [(lo, hi, len(vals))]
    width = (hi - lo) / bins
    out = []
    for i in range(bins):
        a, b = lo + i * width, lo + (i + 1) * width
        n = sum(1 for v in vals if (a <= v < b) or (i == bins - 1 and v == hi))
        out.append((a, b, n))
    return out


# ------------------------------------------------------------------ rendering

def _bar_panel(p: Panel, fmt) -> str:
    if not p.rows:
        body = '<p class="empty">no data yet</p>'
    else:
        top = max(v[2] for v in p.rows.values()) or 1
        lines = []
        for name, v in sorted(p.rows.items(), key=lambda kv: -kv[1][2]):
            pct = 100.0 * v[2] / top
            cls = " muted" if name in (UNATTRIBUTED, UNMAPPED) else ""
            lines.append(
                '<div class="row%s"><span class="k">%s</span>'
                '<span class="bar"><i style="width:%.1f%%"></i></span>'
                '<span class="v">%s</span></div>'
                % (cls, html.escape(str(name)), pct, fmt(v)))
        body = "".join(lines)
    return ('<section class="panel" data-coverage="%s">'
            '<h2>%s</h2><div class="cov">%s tickets · %d install%s</div>%s%s</section>'
            % (html.escape(p.coverage), html.escape(p.title),
               html.escape(p.coverage), p.installs, "" if p.installs == 1 else "s",
               body,
               '<p class="note">%s</p>' % html.escape(p.note) if p.note else ""))


def _hist_panel(p: Panel, bins, fmt) -> str:
    if not bins:
        inner = '<p class="empty">no data yet</p>'
    else:
        top = max(b[2] for b in bins) or 1
        w, h = 260, 90
        step = w / len(bins)
        rects = "".join(
            '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f"></rect>'
            % (i * step, h - (h * b[2] / top), step - 2, h * b[2] / top)
            for i, b in enumerate(bins))
        inner = ('<svg viewBox="0 0 %d %d" role="img" aria-label="%s">%s</svg>'
                 '<div class="axis"><span>%s</span><span>%s</span></div>'
                 % (w, h, html.escape(p.title), rects,
                    html.escape(fmt(bins[0][0])), html.escape(fmt(bins[-1][1]))))
    return ('<section class="panel" data-coverage="%s">'
            '<h2>%s</h2><div class="cov">%s tickets · %d install%s</div>%s</section>'
            % (html.escape(p.coverage), html.escape(p.title),
               html.escape(p.coverage), p.installs, "" if p.installs == 1 else "s",
               inner))


CSS = """
:root{--fg:#111827;--muted:#6b7280;--line:#e5e7eb;--bar:#1e40af;--bg:#fafafa}
*{box-sizing:border-box}
body{margin:0;padding:24px;font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:var(--fg);background:var(--bg)}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 20px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
.panel{background:#fff;border:1px solid var(--line);border-radius:8px;padding:14px}
.panel h2{font-size:13px;margin:0;text-transform:uppercase;letter-spacing:.04em}
.cov{color:var(--muted);font-size:12px;margin:2px 0 10px}
.row{display:grid;grid-template-columns:130px 1fr 90px;gap:8px;align-items:center;margin:3px 0}
.row.muted .k{color:var(--muted);font-style:italic}
.k{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar{background:var(--line);height:9px;border-radius:5px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--bar)}
.v{font-size:12px;text-align:right;font-variant-numeric:tabular-nums}
svg{width:100%;height:auto}rect{fill:var(--bar)}
.axis{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;margin-top:4px}
.note,.empty{color:var(--muted);font-size:12px;margin:8px 0 0}
"""


def render(rows, installs, tickets_covered, tickets_total) -> str:
    money = lambda v: adt_cost.fmt_cost(v[2]) if v[2] else "—"
    n = len(installs)

    per_lane = by_key(rows, lambda r: lane_of(r["command"]))
    per_cmd = by_key(rows, lambda r: r["command"] or UNMAPPED)
    per_install = by_key(rows, lambda r: r["install"])

    # Distributions. Per-ticket cost only counts rows that HAVE a ticket —
    # rollups from other installs carry none, which is why the panel says so.
    by_tix = defaultdict(int)
    for r in rows:
        if r.get("tix"):
            by_tix[r["tix"]] += r["micros"]
    per_turn = [r["micros"] for r in rows if r["turns"]]
    turns_per_tix = defaultdict(int)
    for r in rows:
        if r.get("tix"):
            turns_per_tix[r["tix"]] += r["turns"]

    panels = [
        _bar_panel(Panel("cost by lane", per_lane, tickets_covered, tickets_total, n,
                         "commands that name no lane are shown as `unmapped`, "
                         "not folded into a total"), money),
        _bar_panel(Panel("cost by command", per_cmd, tickets_covered, tickets_total, n), money),
        _bar_panel(Panel("cost by install", per_install, tickets_covered, tickets_total, n,
                         "rows written before ADT-254 carry no id and are shown "
                         "as `unattributed`"), money),
        _hist_panel(Panel("cost per ticket", {}, len(by_tix), tickets_total, n),
                    histogram(list(by_tix.values())),
                    lambda v: adt_cost.fmt_cost(int(v))),
        _hist_panel(Panel("cost per turn", {}, len(per_turn), len(per_turn), n),
                    histogram(per_turn), lambda v: adt_cost.fmt_cost(int(v))),
        _hist_panel(Panel("turns per ticket", {}, len(turns_per_tix), tickets_total, n),
                    histogram(list(turns_per_tix.values())), lambda v: "%d" % v),
    ]
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>ADT delivery</title><style>%s</style></head><body>"
            "<h1>ADT delivery</h1>"
            "<p class=\"sub\">%d install%s · %d ticket%s · every panel states what it covers</p>"
            "<div class=\"grid\">%s</div></body></html>\n"
            % (CSS, n, "" if n == 1 else "s", tickets_total,
               "" if tickets_total == 1 else "s", "".join(panels)))


def _invoking_repo() -> str:
    """The checkout this was run from — a worktree included."""
    import subprocess
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        if top:
            return top
    except Exception:
        pass
    return os.getcwd()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Render the ADT delivery dashboard from local files only.")
    ap.add_argument("--project-root", default=None)
    ap.add_argument("--rollup-dir", default=None)
    ap.add_argument("--out", default=None,
                    help="default: <project>/.adt/dashboard.html")
    args = ap.parse_args(argv)

    # TWO ROOTS, deliberately. Machine-local STATE (.adt-state: the ledger, the
    # install id, the rollups) lives only in the canonical checkout, because it
    # is gitignored and no worktree has it. A generated ARTIFACT is a tracked
    # file and belongs in the tree you invoked from — writing it to the
    # canonical tree from a worktree would leave the artifact outside the diff
    # that produced it, and outside the DoD's `file:` check, which resolves
    # against the invoking repo.
    state_root = args.project_root or str(bk.canonical_root())
    artifact_root = args.project_root or _invoking_repo()
    rollup_dir = args.rollup_dir or adt_rollup.default_rollup_dir(state_root)
    rows, installs = load(state_root, rollup_dir)

    tix = {r["tix"] for r in rows if r.get("tix")}
    out = args.out or os.path.join(artifact_root, ".adt", "dashboard.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(render(rows, installs, len(tix), len(tix)))
    print("wrote %s — %d install(s), %d ticket(s), %d row(s)"
          % (out, len(installs), len(tix), len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
