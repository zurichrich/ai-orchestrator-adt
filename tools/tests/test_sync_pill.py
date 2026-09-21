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

import json
import pathlib
import re
import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402
import adt_sync  # noqa: E402
import adt_watch  # noqa: E402


# ── 1a: the slug mirror must not drift from lib/watcher.sh ──────────────────
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


def _run_tick(tmp_path, monkeypatch, *, breached=False, moved=False, on_sync=None,
              existing_root=None):
    """One real tick of adt_watch.watch(once=True) with the network stubbed.

    Only the calls that reach GitHub or write HTML are replaced. The state
    plumbing under test — _stamp_health, _save_watch_state, _watch_state — runs
    for real against a temp sidecar.
    """
    root = existing_root or _project(tmp_path)

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
    assert after > seen["mid"], (
        "the window was not re-stamped after the pass completed. `>=` would "
        "hold trivially here when the two are equal, which is exactly how a "
        "deleted post-sync stamp passed QA")


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

def _page(tmp_path, *, healthy_until):
    (tmp_path / "enhancements" / "ideas").mkdir(parents=True, exist_ok=True)
    build_kanban.configure(str(tmp_path), healthy_until=healthy_until)
    return build_kanban.render_html(build_kanban.load_items())


def test_render_independent_of_window(tmp_path, monkeypatch):
    """Two windows straddling the decision boundary render the same bytes.

    The straddle is what gives this power: two windows on the same side would
    render identically even from a server-side computation, so the test would
    pass while the regression it exists to catch was present.
    """
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
    page = _page(tmp_path, healthy_until=1_700_000_000)
    assert re.search(r'data-healthy-until="\d{10}"', page)
    assert 'id="board-sync"' in page and "hidden" in page
    # No commands: the button acts, it does not hand the reader a string.
    assert "launchctl" not in page and "data-stop" not in page


def test_missing_window_hides_pill(tmp_path, monkeypatch):
    """No published window -> no button at all, never a window of zero.

    Zero would read as long expired and paint a board red that has simply never
    been stamped — a first-ever tick, or a state file predating this field.
    """
    page = _page(tmp_path, healthy_until=None)
    assert 'id="board-sync"' not in page
    assert "data-healthy-until" not in page
def test_pill_css_uses_tokens_and_both_themes():
    css = build_kanban.HTML_CSS
    for sel in (".sync-pill {", ".sync-pill.sync-on", ".sync-pill.sync-off",
                ".sync-pill::after", ".sync-dot", ".sync-pill:focus-visible",
                ".sync-pill[hidden]", ".sync-pill[disabled]"):
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

let ctxFetch = () => Promise.reject('no fetch');
let execResult = false;
let navigatorStub = {};
const pending = [];

function page(healthyUntil, proto) {
  const label = { textContent: '' };
  const attrs = {};
  const classes = new Set();
  const el = {
    dataset: { healthyUntil: healthyUntil === null ? '' : String(healthyUntil) },
    disabled: false,
    classList: {
      toggle: (c, on) => { on ? classes.add(c) : classes.delete(c); },
      contains: c => classes.has(c),
    },
    querySelector: sel => (sel === '.sync-label' ? label : null),
    setAttribute: (k, v) => { attrs[k] = v; },
    getAttribute: k => (k in attrs ? attrs[k] : null),
    title: '',
    hidden: true,
  };
  const listeners = {};
  const textareas = [];
  const ctx = {
    document: {
      addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
      execCommand: () => execResult,
      querySelectorAll: () => [],
      querySelector: () => null,
      getElementById: id => (id === 'board-sync' ? el : null),
      body: {
        classList: { toggle() {} },
        appendChild: n => { n.attached = true; },
        removeChild: n => { n.attached = false; n.parentNode = null; },
      },
      createElement: () => {
        const ta = { style: {}, setAttribute() {}, select() { ta.selected = true; },
                     selected: false, attached: false, value: '' };
        ta.parentNode = { removeChild: n => { n.attached = false; } };
        textareas.push(ta);
        return ta;
      },
    },
    location: { hash: '', protocol: proto || 'http:', reload() {} },
    localStorage: { getItem: () => null, setItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    navigator: navigatorStub,
    fetch: function (...a) { return ctxFetch(...a); },
    setTimeout: fn => { pending.push(fn); },
    JSON: JSON,
    Date: Date,
    Math: Math,
    parseInt: parseInt,
  };
  ctx.window = ctx;
  ctx.addEventListener = () => {};
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  const fire = () => (listeners.click || []).forEach(
    fn => fn({ target: { closest: sel => (sel === '#board-sync' ? el : null) } }));
  return {
    on: classes.has('sync-on'), off: classes.has('sync-off'),
    label: label.textContent, hidden: el.hidden, attrs: attrs, title: el.title,
    disabled: el.disabled,
    click: fire,
    state: () => ({
      on: classes.has('sync-on'), off: classes.has('sync-off'),
      label: label.textContent, hidden: el.hidden, attrs: attrs,
      title: el.title, disabled: el.disabled, aria: attrs['aria-label'],
    }),
    flush: () => { const q = pending.splice(0); q.forEach(fn => fn()); },
  };
}

const result = {};
__SCENARIO__
setImmediate(function () {
  for (const k of Object.keys(result)) {
    if (result[k] && typeof result[k].state === 'function') result[k] = result[k].state();
  }
  process.stdout.write(JSON.stringify(result));
});
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
    # State is in words as well as colour, and reaches assistive tech through
    # the label rather than a toggle role the button does not have.
    on_label = out["converged"]["attrs"]["aria-label"]
    off_label = out["expired"]["attrs"]["aria-label"]
    assert on_label and off_label and on_label != off_label
    assert "aria-pressed" not in out["converged"]["attrs"]
    # The tooltip says what the click DOES, which is what was asked for.
    assert out["converged"]["title"] == "click to stop"
    assert out["expired"]["title"] == "click to start"
    # Served over http, so the button is live.
    assert out["converged"]["disabled"] is False


def test_no_unsubstituted_placeholder(tmp_path, monkeypatch):
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


# ── 1g: the wiring, through the live path ───────────────────────────────────

def test_watch_render_wires_the_window(tmp_path, monkeypatch):
    """adt_watch._render reaches build_kanban.run with BOTH new kwargs.

    Through the real _render, not a grep and not build_kanban.run directly: a
    grep on the call site cannot see that run() rejects the argument, and a
    direct call cannot see that _render never passes it. ADT-224's zeros were
    exactly this shape — the wiring was wrong while both ends were right.
    """
    import inspect

    # run()'s REAL signature, captured before it is replaced below.
    accepted = inspect.signature(build_kanban.run).parameters
    assert "healthy_until" in accepted, (
        "build_kanban.run does not accept the kwarg _render passes; the "
        "watcher's render would raise TypeError every tick")

    root = _project(tmp_path)
    cfg = adt_sync.load_config(root)
    adt_watch._stamp_health(cfg, 1_700_000_000.0)

    seen = {}
    monkeypatch.setattr(build_kanban, "run", lambda **kw: seen.update(kw))
    adt_watch._render(adt_sync.cache_dir(cfg), cfg, root, quiet=True)

    assert seen.get("healthy_until") == 1_700_000_000.0 + 360.0, seen


# ── 1h: markup and accessibility, in the rendered page ──────────────────────

def test_pill_markup_is_accessible(tmp_path, monkeypatch):
    """A real button, reachable by Tab, with state in words as well as colour.

    Asserted on the rendered page rather than left to the UI walkthrough: it
    needs no browser, so waiving it would waive something checkable.
    """
    page = _page(tmp_path, healthy_until=1_700_000_000)
    tag = re.search(r'<button[^>]*id="board-sync"[^>]*>', page).group(0)

    assert tag.startswith("<button"), "not a real button; Tab would skip it"
    assert 'type="button"' in tag, "a bare button in a form would submit it"
    # The dot and the word live inside it: colour is never the only channel.
    assert '<span class="sync-dot">' in page
    assert '<span class="sync-label">' in page

    js = build_kanban.HTML_JS
    # aria-label is set at decision time, not render time — the same reason the
    # class is. NOT aria-pressed: that tells assistive tech "activating this
    # toggles this button's own state", and clicking only copies a command the
    # human runs elsewhere. The label states the state AND the real action.
    assert "aria-label" in js
    assert "aria-pressed" not in js, (
        "the label carries the state; aria-pressed would claim a toggle role "
        "this button does not have")
    assert "el.hidden = false" in js, "the pill is never revealed"
def test_hidden_pill_is_not_displayed():
    """`hidden` alone did NOT hide the pill.

    The UA stylesheet's `[hidden] { display: none }` is author-beatable, and
    `.sync-pill { display: inline-flex }` beat it — so a page whose JS never ran
    showed an empty grey capsule. Found in QA.

    Asserted here by specificity, which is decidable from the stylesheet alone:
    `.sync-pill[hidden]` is (0,2,0) and `.sync-pill` is (0,1,0), so the former
    wins regardless of source order. The computed-style proof is the Rule-10
    walkthrough's, recorded in the build log — a string check cannot make it.
    """
    css = build_kanban.HTML_CSS
    assert ".sync-pill[hidden]" in css, "nothing restores display:none when hidden"
    rule = css.split(".sync-pill[hidden]")[1].split("}")[0]
    assert "display: none" in rule, f"[hidden] rule does not set display: {rule!r}"
    # And no LATER, equally-or-more specific selector re-sets display on it.
    after = css.split(".sync-pill[hidden]")[1]
    for bad in (".sync-pill[hidden]", ".sync-pill.sync-on[hidden]"):
        assert f"{bad} {{" not in after.replace(rule, "", 1), f"{bad} redefined later"


def test_late_figure_is_rendered_and_correct(tmp_path):
    """The "Nm late" arithmetic, with a NON-ZERO figure.

    The earlier suite only drove a window 10s past, where the figure rounds to
    0 and the label takes the bare 'sync off' branch — so changing the /60
    divisor to /600 passed everything. Found in QA.
    """
    out = _run_pill(tmp_path, """
      const now = Math.floor(Date.now() / 1000);
      result.twelve = page(now - 745);     // 12.4 min -> 12
      result.one    = page(now - 90);      // 1.5 min  -> 2 (rounds up)
      result.zero   = page(now - 10);      // rounds to 0 -> bare label
    """)
    assert out["twelve"]["label"] == "sync off · 12m late", out["twelve"]["label"]
    assert out["one"]["label"] == "sync off · 2m late", out["one"]["label"]
    assert out["zero"]["label"] == "sync off", out["zero"]["label"]


# ── the toggle: the button does the thing ───────────────────────────────────

def test_file_url_disables_the_button(tmp_path):
    """Opened from disk there is no watcher to POST to, so the button says so.

    It still SHOWS the state — that half needs nothing but the rendered window.
    """
    out = _run_pill(tmp_path, """
      const now = Math.floor(Date.now() / 1000);
      result.served = page(now + 300, 'http:');
      result.onDisk = page(now + 300, 'file:');
    """)
    assert out["served"]["disabled"] is False
    assert out["served"]["title"] == "click to stop"
    assert out["onDisk"]["disabled"] is True
    assert "watcher" in out["onDisk"]["title"]
    # ...and the state is still readable in both.
    assert out["onDisk"]["on"] is True and out["onDisk"]["label"] == "sync on"


def test_stop_retracts_the_window(tmp_path, monkeypatch):
    """Pressing stop must expire the published window, not just set a flag.

    QA round 3: it did not. The window a completed pass published is up to
    HEALTH_WINDOW in the future when the stop lands, and syncPill() decides
    colour from that number alone — so the board read `sync on` for six more
    minutes on a watcher the user had just stopped, and the toggle's own reload
    took them straight to it.
    """
    root = _run_tick(tmp_path, monkeypatch, moved=False)
    cfg = adt_sync.load_config(root)
    cache = adt_sync.cache_dir(cfg)

    running = adt_watch._health_stamp(cfg)
    assert running > time.time(), "the fixture did not leave a live window"

    adt_watch._set_paused(cfg, cache, True)
    stopped = adt_watch._health_stamp(cfg)
    assert stopped < time.time(), (
        f"stop left the window {round(stopped - time.time())}s in the future; "
        f"the pill would read `sync on` for that long on a stopped watcher")


def test_toggle_does_not_write_the_watch_blob(tmp_path, monkeypatch):
    """The toggle runs on the HTTP thread and must not touch `watch`.

    `_update_state_doc` is an unlocked read-modify-write of the whole sidecar;
    its no-clobber guarantee assumes one writer under the pass lock. Writing the
    watch blob from the toggle both lost the ladder reset to the tick's own
    write and could clobber `file_hashes`, which re-pushes every ticket.
    """
    root = _run_tick(tmp_path, monkeypatch, moved=False)
    cfg = adt_sync.load_config(root)
    cache = adt_sync.cache_dir(cfg)

    adt_watch._save_watch_state(cfg, 7, 1e12, "fp-sentinel")
    before = dict(adt_watch._watch_state(cfg))

    adt_watch._set_paused(cfg, cache, True)
    adt_watch._set_paused(cfg, cache, False)

    assert dict(adt_watch._watch_state(cfg)) == before, (
        "the toggle wrote the watch blob from the HTTP thread")


def test_paused_tick_is_eager_and_makes_no_gh_call(tmp_path, monkeypatch):
    """A paused tick skips everything, including the two `gh` snapshots.

    The pause gate sits before the pass lock and before _rate_limit_snapshot /
    _branch_protection_snapshot, both of which shell out. A pause that only
    skipped the sync still spent a REST point and a GraphQL point per tick on a
    board the user believes is stopped. It also holds the ladder at eager, so
    the tick after the flag clears runs immediately.
    """
    root = _project(tmp_path)
    cfg = adt_sync.load_config(root)
    cache = adt_sync.cache_dir(cfg)
    adt_watch._set_paused(cfg, cache, True)
    adt_watch._save_watch_state(cfg, 9, 1e12, "fp")     # a deep backoff

    called = []
    for name in ("_sync", "_rate_limit_snapshot", "_branch_protection_snapshot",
                 "_render"):
        monkeypatch.setattr(adt_watch, name,
                            (lambda n: lambda *a, **k: called.append(n))(name))
    monkeypatch.setattr(adt_watch.adt_machines, "report", lambda *a, **k: None)
    monkeypatch.setattr(adt_watch.adt_phone_home, "run_once", lambda *a, **k: None)
    adt_watch.watch(root, once=True)

    assert called == [], f"a paused tick called {called}"
    st = adt_watch._watch_state(cfg)
    assert st["quiet"] == 0 and st["next_due"] == 0.0, (
        "the ladder was not held at eager; a resume would idle for up to "
        "BACKOFF_CAP seconds and look like a button that did nothing")


# ── the control server (QA round 3: M4, M5 — no test made an HTTP request) ──

import urllib.error  # noqa: E402
import urllib.request  # noqa: E402


def _board_server(tmp_path, body=b"<html>BOARD</html>"):
    root = _project(tmp_path)
    cfg = adt_sync.load_config(root)
    cache = adt_sync.cache_dir(cfg)
    if body is not None:
        pathlib.Path(cache, "kanban.html").write_bytes(body)
    srv = adt_watch._serve_board(cfg, cache, 0)       # 0 = ephemeral port
    return cfg, cache, srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _req(url, *, method="GET", headers=None, host=None):
    r = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    if host:
        r.add_header("Host", host)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_server_serves_only_the_board(tmp_path):
    """Two routes. Anything else 404s — M4 survived because nothing asked."""
    _cfg, _cache, srv, base = _board_server(tmp_path)
    try:
        assert _req(base + "/")[0] == 200
        assert _req(base + "/?x=1")[0] == 200
        for path in ("/kanban.html", "/../../etc/passwd", "/anything",
                     "/enhancements/ideas/p.md"):
            code, body = _req(base + path)
            assert code == 404, f"{path} returned {code}"
            assert b"BOARD" not in body
    finally:
        srv.shutdown()


def test_server_toggles_only_on_its_own_route(tmp_path):
    """M5: a POST to any path used to flip sync."""
    cfg, cache, srv, base = _board_server(tmp_path)
    hdr = {"X-ADT-Board": "1"}
    try:
        assert _req(base + "/nope", method="POST", headers=hdr)[0] == 404
        assert adt_watch._is_paused(cache) is False, "a stray POST flipped sync"
        code, body = _req(base + "/sync/toggle", method="POST", headers=hdr)
        assert code == 200 and json.loads(body)["paused"] is True
        assert adt_watch._is_paused(cache) is True
    finally:
        srv.shutdown()


def test_server_rejects_cross_origin_and_foreign_host(tmp_path):
    """Any page in any tab could stop the user's sync, and a rebound DNS name
    could read the whole backlog. Both measured before the guards existed."""
    cfg, cache, srv, base = _board_server(tmp_path)
    try:
        # No custom header -> a CORS *simple* request, which is not preflighted.
        assert _req(base + "/sync/toggle", method="POST")[0] == 403
        assert adt_watch._is_paused(cache) is False
        # Header present but a foreign Origin.
        assert _req(base + "/sync/toggle", method="POST",
                    headers={"X-ADT-Board": "1",
                             "Origin": "https://evil.example"})[0] == 403
        assert adt_watch._is_paused(cache) is False
        # DNS rebinding: the name resolves here, so only Host can tell.
        code, body = _req(base + "/", host="evil.example")
        assert code == 403 and b"BOARD" not in body
    finally:
        srv.shutdown()


def test_server_503s_before_the_first_render(tmp_path):
    _cfg, _cache, srv, base = _board_server(tmp_path, body=None)
    try:
        assert _req(base + "/")[0] == 503
    finally:
        srv.shutdown()


def test_toggle_reports_what_the_server_said(tmp_path):
    """M8: the toggle always said 'stopped', even on a start, and nothing saw
    it — the shipped harness provides no fetch, so the click path was ungraded.
    """
    out = _run_pill(tmp_path, """
      const now = Math.floor(Date.now() / 1000);
      const run = (paused, ok) => {
        const p = page(now + 300);
        ctxFetch = () => ok
          ? Promise.resolve({ok: true, json: () => Promise.resolve({paused: paused})})
          : Promise.resolve({ok: false, status: 500});
        p.click();
        return p;        // serialised as p.state() once microtasks have run
      };
      result.stopped = run(true, true);
      result.started = run(false, true);
      result.failed  = run(true, false);
    """)
    assert out["stopped"]["label"] == "stopped"
    assert out["started"]["label"] == "started", (
        "a start reported 'stopped'; the label must echo the server, not a guess")
    assert out["failed"]["label"] == "failed"


def test_origin_must_be_loopback_by_hostname(tmp_path):
    """A prefix match accepted http://127.0.0.1.evil.com.

    Not reachable through a browser while the custom header forces a preflight
    this server never answers — but it is a live bypass the moment anyone
    relaxes that requirement, and the suite only ever tried an obviously
    foreign origin. Security review, MEDIUM.
    """
    cfg, cache, srv, base = _board_server(tmp_path)
    try:
        for origin in ("http://127.0.0.1.evil.com", "http://localhost.evil.com",
                       "null", "http://evil.com"):
            code, _ = _req(base + "/sync/toggle", method="POST",
                           headers={"X-ADT-Board": "1", "Origin": origin})
            assert code == 403, f"{origin} was accepted"
            assert adt_watch._is_paused(cache) is False
        # ...and a real same-origin request still works.
        code, _ = _req(base + "/sync/toggle", method="POST",
                       headers={"X-ADT-Board": "1", "Origin": base})
        assert code == 200 and adt_watch._is_paused(cache) is True
    finally:
        srv.shutdown()


def test_state_writes_are_serialised(tmp_path):
    """The control server's thread and the loop both write the sidecar.

    `_update_state_doc` is a read-whole-doc / patch / write-whole-doc, so any
    interleaving drops one side's write regardless of which keys each touched.

    Each thread writes a strictly INCREASING value to its own key and reads both
    back. A lost update makes the other key go backwards, which identical values
    could never reveal — the first version of this test wrote the same numbers
    from both threads and passed with the lock removed. Security review, HIGH.
    """
    import threading as _th
    root = _project(tmp_path)
    cfg = adt_sync.load_config(root)
    adt_watch._save_watch_state(cfg, 0, 0.0, "fp")
    adt_watch._stamp_health(cfg, 0.0)

    stop = _th.Event()
    regressions = []

    def writer(write, read_other):
        seen = 0.0
        n = 0
        while not stop.is_set() and len(regressions) < 5:
            n += 1
            write(n)
            other = read_other()
            if other < seen:
                regressions.append(f"{other} < {seen}")
            seen = max(seen, other)

    a = _th.Thread(target=writer, args=(
        lambda n: adt_watch._save_watch_state(cfg, n, float(n), "fp"),
        lambda: adt_watch._health_stamp(cfg) or 0.0))
    b = _th.Thread(target=writer, args=(
        lambda n: adt_watch._stamp_health(cfg, float(n)),
        lambda: float(adt_watch._watch_state(cfg).get("next_due") or 0.0)))
    a.start(); b.start()
    time.sleep(2.0)
    stop.set(); a.join(); b.join()

    assert not regressions, (
        f"{len(regressions)} lost update(s) — a value went backwards, so one "
        f"thread's write was discarded: {regressions[:3]}")
