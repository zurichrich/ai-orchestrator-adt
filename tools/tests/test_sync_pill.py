# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""AO-006 — the header sync pill.

The board renders a validity window published by the watcher and nothing about
state; the browser decides the colour. These tests cover the four things that
can go wrong with that: the published value must never be a sentinel, the
renderer must not bake state in, the comparison must actually run, and the
watcher's own state must survive the extra write.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402


# ── 1a: the slug mirror must not drift from lib/watcher.sh ──────────────────

# Awkward on purpose: case, dots, spaces, runs of separators, leading/trailing
# punctuation, and a name that is nothing but separators.
_SLUG_NAMES = [
    "ai-orchestrator-adt",
    "My.Project",
    "Foo  Bar",
    "--weird--",
    "A_B.C",
    "UPPER",
    "trailing-",
    "-leading",
    "dots...everywhere",
    "mixed_-_separators",
    "a",
    "___",
]


def _shell_slug(name: str) -> str:
    """Run the REAL _watcher_slug from lib/watcher.sh, not a restatement of it."""
    script = REPO / "lib" / "watcher.sh"
    assert script.is_file(), f"lib/watcher.sh not found at {script}"
    # `set -euo pipefail` at the top of watcher.sh is fine under `bash -c`.
    out = subprocess.run(
        ["bash", "-c", f'source "{script}"; _watcher_slug "$1"', "_", name],
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, f"shell slug failed: {out.stderr}"
    return out.stdout.rstrip("\n")


def test_slug_matches_shell():
    mismatches = []
    for name in _SLUG_NAMES:
        got, want = build_kanban.watcher_slug(name), _shell_slug(name)
        if got != want:
            mismatches.append(f"{name!r}: python={got!r} shell={want!r}")
    assert not mismatches, "watcher_slug drifted from lib/watcher.sh:\n" + "\n".join(mismatches)


# ── 1b: both platform branches, including the one this machine never runs ────

def test_commands_both_platforms(monkeypatch):
    monkeypatch.setattr(build_kanban.sys, "platform", "darwin")
    stop, start = build_kanban.watcher_commands("my-proj")
    assert stop == "launchctl bootout gui/$(id -u)/com.adt.my-proj.watch"
    assert start == (
        "launchctl bootstrap gui/$(id -u) "
        "~/Library/LaunchAgents/com.adt.my-proj.watch.plist")

    monkeypatch.setattr(build_kanban.sys, "platform", "linux")
    stop, start = build_kanban.watcher_commands("my-proj")
    assert stop == "systemctl --user stop adt-watch-my-proj.timer"
    assert start == "systemctl --user start adt-watch-my-proj.timer"

    # Neither supervisor: no commands, so 1d renders no button rather than one
    # whose click copies nothing.
    monkeypatch.setattr(build_kanban.sys, "platform", "win32")
    assert build_kanban.watcher_commands("my-proj") == ("", "")

    # No slug is the same case.
    monkeypatch.setattr(build_kanban.sys, "platform", "darwin")
    assert build_kanban.watcher_commands("") == ("", "")


# ── 1c: the watcher publishes the window ────────────────────────────────────

import adt_sync  # noqa: E402
import adt_watch  # noqa: E402


def _project(tmp_path):
    """A project root adt_sync.load_config can read, with an empty cache."""
    cache = tmp_path / "cache"
    (cache / "enhancements" / "ideas").mkdir(parents=True)
    # A real ticket, so _cache_fingerprint returns a real mtime rather than the
    # 0.0 an empty tree gives — a fixture that leaves the value at its zero
    # default can satisfy an assertion by accident.
    (cache / "enhancements" / "ideas" / "probe.md").write_text(
        "---\nslug: probe\nid: PR-001\ntitle: probe\ntype: enhancement\n"
        "stage: ideas\nstate: open\n---\n\n# probe\n")
    adt = tmp_path / ".adt"
    adt.mkdir()
    (adt / "config.yaml").write_text(
        "project: probe\n"
        "repo: owner/probe\n"
        "owner: owner\n"
        "id_prefix: PR\n"
        "backlog_root: \n"
        f"cache_dir: {cache}\n"
        "main_branch: main\n"
    )
    return str(tmp_path)


def _run_tick(tmp_path, monkeypatch, *, breached=False, moved=False, on_sync=None):
    """One real tick of adt_watch.watch(once=True) with the network stubbed.

    Only the calls that reach GitHub or write HTML are replaced. The state
    plumbing under test — _stamp_health, _save_watch_state, _watch_state — runs
    for real against a temp sidecar.
    """
    root = _project(tmp_path)

    def fake_sync(project_root, pull=True):
        if on_sync is not None:
            on_sync(root)
        return moved

    monkeypatch.setattr(adt_watch, "_sync", fake_sync)
    monkeypatch.setattr(adt_watch, "_rate_limit_snapshot", lambda: [])
    monkeypatch.setattr(adt_watch, "_pool_floor_breached",
                        lambda pools: "core" if breached else "")
    monkeypatch.setattr(adt_watch, "_branch_protection_snapshot",
                        lambda *a, **k: None)
    monkeypatch.setattr(adt_watch, "_render", lambda *a, **k: None)
    monkeypatch.setattr(adt_watch.adt_machines, "report", lambda *a, **k: None)
    monkeypatch.setattr(adt_watch.adt_phone_home, "run_once", lambda *a, **k: None)

    adt_watch.watch(root, once=True)
    return root


def test_health_window_never_sentinel():
    """The published value is a real stamp on BOTH sides of QUIET_GRACE.

    _backoff_due returns 0.0 while quiet < QUIET_GRACE — gate 1's "run eagerly"
    marker, not a time. Published to the page it reads as long expired and
    paints an actively-syncing board red. _health_window must never do that, at
    any quiet count, which is why it takes no quiet argument at all.
    """
    now = 1_700_000_000.0
    for quiet in (0, 1, 2, 3, 10, 1000):
        # The sentinel this function exists NOT to be, at the same quiet count:
        if quiet < adt_watch.QUIET_GRACE:
            assert adt_watch._backoff_due(quiet, now) == 0.0
        assert adt_watch._health_window(now) == now + 360.0
    assert adt_watch.HEALTH_WINDOW == adt_watch.BACKOFF_CAP + adt_watch.BACKOFF_BASE


def test_window_stamped_before_sync(tmp_path, monkeypatch):
    """Stamped BEFORE the long call, and again after it.

    Placement, not presence: a build that writes both stamps at completion
    fails the first assertion, and one that writes only the pre-sync stamp
    fails the second.
    """
    seen = {}

    def during_sync(root):
        cfg = adt_sync.load_config(root)
        seen["mid"] = adt_watch._health_stamp(cfg)

    before = adt_watch._health_window(__import__("time").time())
    root = _run_tick(tmp_path, monkeypatch, on_sync=during_sync)
    cfg = adt_sync.load_config(root)
    after = adt_watch._health_stamp(cfg)

    assert seen["mid"] is not None, "no window published before _sync ran"
    assert seen["mid"] >= before - 5, "the pre-sync stamp is not fresh"
    assert after is not None
    assert after >= seen["mid"], "no second stamp after the pass completed"


def test_breached_tick_not_stamped(tmp_path, monkeypatch):
    """A rate-limited tick must not refresh its own green light.

    Both stamps sit inside the non-breached `else`, so forcing a breach means
    neither runs. A build that stamps at lock acquisition — before the breach
    check — fails this.
    """
    root = _run_tick(tmp_path, monkeypatch, breached=True)
    cfg = adt_sync.load_config(root)
    assert adt_watch._health_stamp(cfg) is None, (
        "a breached tick published a window; the pill would read green on a "
        "watcher that is deliberately not syncing")


def test_watch_fields_untouched_by_stamp(tmp_path, monkeypatch):
    """quiet/next_due/fp survive the healthy_until write, bit for bit.

    healthy_until is a top-level key, so _update_state_doc's shallow merge
    cannot reach into `watch`. A _health_stamp implemented as a wrapper over the
    nested blob would pass every other condition while reintroducing the
    shared-payload hazard that freezes the backoff ladder; this is what catches
    it. Asserted by comparing the whole dict across a stamp rather than by
    predicting each field's type.
    """
    root = _run_tick(tmp_path, monkeypatch, moved=False)
    cfg = adt_sync.load_config(root)

    before = dict(adt_watch._watch_state(cfg))
    assert before, "the tick wrote no watch state; the fixture proves nothing"
    assert "healthy_until" not in before, "the window leaked into the watch blob"

    # The tick already stamped twice. Stamp again, directly: the write under
    # test, in isolation, against state a real tick produced.
    adt_watch._stamp_health(cfg, 1_700_000_000.0)

    after = dict(adt_watch._watch_state(cfg))
    assert after == before, (
        f"the healthy_until write disturbed the watch blob:\n"
        f"  before={before}\n  after ={after}")
    assert adt_watch._health_stamp(cfg) == 1_700_000_000.0 + 360.0


# ── 1d: the renderer emits the window and nothing about state ───────────────

def _page(tmp_path, *, healthy_until, project="ai-orchestrator-adt"):
    (tmp_path / "enhancements" / "ideas").mkdir(parents=True, exist_ok=True)
    build_kanban.configure(str(tmp_path), project_name=project,
                           healthy_until=healthy_until)
    return build_kanban.render_html(build_kanban.load_items())


def test_render_independent_of_window(tmp_path, monkeypatch):
    """Two windows straddling the decision boundary render the same bytes.

    The straddle is what gives this power: two windows on the same side would
    render identically even from a server-side computation, so the test would
    pass while the regression it exists to catch was present.
    """
    monkeypatch.setattr(build_kanban.sys, "platform", "darwin")
    now = 1_700_000_000
    fresh = _page(tmp_path, healthy_until=now + 3600)      # comfortably valid
    stale = _page(tmp_path, healthy_until=now - 3600)      # an hour expired

    fresh_n = fresh.replace(str(now + 3600), "EPOCH")
    stale_n = stale.replace(str(now - 3600), "EPOCH")
    # The generated-at minute can tick between renders; it is not state.
    fresh_n = re.sub(r"updated at [\d-]+ [\d:]+", "updated at T", fresh_n)
    stale_n = re.sub(r"updated at [\d-]+ [\d:]+", "updated at T", stale_n)
    assert fresh_n == stale_n, (
        "the page differs by more than the epoch: the renderer is deciding "
        "state that only the browser may decide")
    # The BUTTON must carry no state class. The stylesheet legitimately defines
    # .sync-on/.sync-off, so assert on the element, not on the page.
    for page in (fresh, stale):
        tag = re.search(r'<button[^>]*id="board-sync"[^>]*>', page)
        assert tag, "the pill was not rendered"
        cls = re.search(r'class="([^"]*)"', tag.group(0)).group(1)
        assert cls.split() == ["sync-pill"], (
            f"a state class was baked into the HTML: class={cls!r}")


def test_rendered_board_carries_the_window(tmp_path, monkeypatch):
    monkeypatch.setattr(build_kanban.sys, "platform", "darwin")
    page = _page(tmp_path, healthy_until=1_700_000_000)
    assert re.search(r'data-healthy-until="\d{10}"', page)
    assert 'id="board-sync"' in page and "hidden" in page
    # The commands are rendered, so the click has something to copy.
    assert "launchctl bootout" in page and "launchctl bootstrap" in page


def test_missing_window_hides_pill(tmp_path, monkeypatch):
    """No published window -> no button at all, never a window of zero.

    Zero would read as long expired and paint a board red that has simply never
    been stamped — a first-ever tick, or a state file predating this field.
    """
    monkeypatch.setattr(build_kanban.sys, "platform", "darwin")
    page = _page(tmp_path, healthy_until=None)
    assert 'id="board-sync"' not in page
    assert "data-healthy-until" not in page
    # And the same when the platform has no supervisor to drive.
    monkeypatch.setattr(build_kanban.sys, "platform", "win32")
    page = _page(tmp_path, healthy_until=1_700_000_000)
    assert 'id="board-sync"' not in page


# ── 1e: the pill's CSS, in both themes and at the breakpoint ────────────────

def test_pill_css_uses_tokens_and_both_themes():
    css = build_kanban.HTML_CSS
    for sel in (".sync-pill {", ".sync-pill.sync-on", ".sync-pill.sync-off",
                ".sync-pill::after", ".sync-dot", ".sync-pill:focus-visible"):
        assert sel in css, f"missing rule: {sel}"

    block = css[css.index("/* AO-006 sync pill"):
                css.index(".sync-pill.sync-off .sync-dot")]
    # Theme tokens only: a literal colour here would be light-mode-only, and the
    # board has a dark theme that redefines --card/--line/--text/--bug.
    assert not re.findall(r"#[0-9a-fA-F]{3,6}", block), "hardcoded hex in the pill rules"
    assert not re.findall(r"\brgba?\(", block), "hardcoded rgb/rgba in the pill rules"
    for tok in ("var(--card)", "var(--line)", "var(--text)", "var(--p2)",
                "var(--bug)", "var(--bug-strong)"):
        assert tok in block, f"pill does not use {tok}"

    # The 44px hit area, and the breakpoint the other nowrap badges already use.
    assert "min-width: 44px" in css and "min-height: 44px" in css
    narrow = css[css.index("@media (max-width: 480px)"):]
    assert ".sync-pill { white-space: normal; }" in narrow


# ── 1f: the comparison, actually executed ───────────────────────────────────

import json  # noqa: E402
import shutil  # noqa: E402

import pytest  # noqa: E402

_PILL_HARNESS = r"""
const vm = require('vm');
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');

function page(healthyUntil) {
  const label = { textContent: '' };
  const attrs = {};
  const classes = new Set();
  const el = {
    dataset: {
      healthyUntil: healthyUntil === null ? '' : String(healthyUntil),
      stop: 'STOP-CMD',
      start: 'START-CMD',
    },
    classList: {
      toggle: (c, on) => { on ? classes.add(c) : classes.delete(c); },
      contains: c => classes.has(c),
    },
    querySelector: sel => (sel === '.sync-label' ? label : null),
    setAttribute: (k, v) => { attrs[k] = v; },
    title: '',
    hidden: true,
  };
  const ctx = {
    document: {
      addEventListener() {},
      querySelectorAll: () => [],
      querySelector: () => null,
      getElementById: id => (id === 'board-sync' ? el : null),
      body: { classList: { toggle() {} }, appendChild() {}, removeChild() {} },
      createElement: () => ({ style: {}, setAttribute() {}, select() {} }),
    },
    location: { hash: '', reload() {} },
    localStorage: { getItem: () => null, setItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    navigator: {},
    setTimeout() {},
    JSON: JSON,
    Date: Date,
    Math: Math,
    parseInt: parseInt,
  };
  ctx.window = ctx;
  ctx.addEventListener = () => {};
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  return { on: classes.has('sync-on'), off: classes.has('sync-off'),
           label: label.textContent, hidden: el.hidden, attrs: attrs,
           title: el.title };
}

const result = {};
__SCENARIO__
process.stdout.write(JSON.stringify(result));
"""


def _run_pill(tmp_path, scenario: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    js = tmp_path / "page.js"
    js.write_text(build_kanban.HTML_JS.replace(
        "__REFRESH_MS__", str(build_kanban.REFRESH_SECONDS * 1000)))
    harness = tmp_path / "pill-harness.js"
    harness.write_text(_PILL_HARNESS.replace("__SCENARIO__", scenario))
    r = subprocess.run([node, str(harness), str(js)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"harness failed:\n{r.stdout}\n{r.stderr}"
    return json.loads(r.stdout)


def test_window_decides_colour(tmp_path):
    """The three cases every fixed-threshold draft of this got wrong.

    300s out is a converged board on BACKOFF_CAP — healthy, and three earlier
    designs reddened it. 10s out is an ordinary tick. 10s past is the only one
    that may be red.
    """
    out = _run_pill(tmp_path, """
      const now = Math.floor(Date.now() / 1000);
      result.converged = page(now + 300);
      result.ordinary  = page(now + 10);
      result.expired   = page(now - 10);
      result.absent    = page(null);
    """)
    assert out["converged"]["on"] and not out["converged"]["off"]
    assert out["converged"]["label"] == "sync on"
    assert out["ordinary"]["on"] and not out["ordinary"]["off"]
    assert out["expired"]["off"] and not out["expired"]["on"]
    assert out["expired"]["label"].startswith("sync off")
    # Unhidden only once a state has actually been decided.
    assert out["converged"]["hidden"] is False and out["expired"]["hidden"] is False
    # A missing window decides nothing and stays hidden.
    assert out["absent"]["hidden"] is True
    assert not out["absent"]["on"] and not out["absent"]["off"]
    # State is in words as well as colour.
    assert out["converged"]["attrs"]["aria-pressed"] == "true"
    assert out["expired"]["attrs"]["aria-pressed"] == "false"
    assert out["expired"]["attrs"]["aria-label"]
    # The tooltip shows the command the click would copy.
    assert "START-CMD" in out["expired"]["title"]
    assert "STOP-CMD" in out["converged"]["title"]


def test_no_unsubstituted_placeholder(tmp_path, monkeypatch):
    monkeypatch.setattr(build_kanban.sys, "platform", "darwin")
    page = _page(tmp_path, healthy_until=1_700_000_000)
    leftovers = re.findall(r"__[A-Z_]+__", page)
    assert not leftovers, f"unsubstituted placeholders in the page: {leftovers}"


def test_html_js_parses(tmp_path):
    """HTML_JS is valid JavaScript after substitution.

    Found while building 1f: HTML_JS is a NON-raw triple-quoted Python string,
    so a backslash-n written into it is consumed by Python and reaches the page
    as a real line break inside a JS string literal. The whole script then fails
    to parse — filters, panel-scroll and pill all dead — and nothing on the
    board looks broken enough to notice. A syntax check is the only thing that
    catches it.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    js = tmp_path / "page.js"
    js.write_text(build_kanban.HTML_JS.replace(
        "__REFRESH_MS__", str(build_kanban.REFRESH_SECONDS * 1000)))
    r = subprocess.run([node, "--check", str(js)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"HTML_JS does not parse:\n{r.stderr}"
