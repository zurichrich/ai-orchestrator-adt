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
    state: () => ({ label: label.textContent, aria: attrs['aria-label'],
                    textareas: textareas.map(
                      ta => ({ attached: ta.attached, selected: ta.selected,
                               value: ta.value })) }),
    flush: () => { const q = pending.splice(0); q.forEach(fn => fn()); },
  };
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


def test_pause_flag_round_trip(tmp_path, monkeypatch):
    """_set_paused flips the flag AND clears the backoff.

    The ladder reset is the half that is easy to miss: after QUIET_GRACE
    all-noop ticks `next_due` is up to BACKOFF_CAP away, so a resume would sit
    idle for minutes and look broken. Proven by driving a real tick first, so
    the state under test is one a real watcher produced.
    """
    root = _run_tick(tmp_path, monkeypatch, moved=False)
    cfg = adt_sync.load_config(root)
    cache = adt_sync.cache_dir(cfg)

    assert adt_watch._is_paused(cache) is False
    # Put the ladder somewhere a resume would have to climb down from.
    adt_watch._save_watch_state(cfg, 9, 1e12, "fp")
    assert adt_watch._watch_state(cfg)["next_due"] == 1e12

    assert adt_watch._set_paused(cfg, cache, True) is True
    assert adt_watch._is_paused(cache) is True
    st = adt_watch._watch_state(cfg)
    assert st["quiet"] == 0 and st["next_due"] == 0.0, (
        "the backoff was not cleared; a resume would idle for up to "
        "BACKOFF_CAP seconds and look broken")

    assert adt_watch._set_paused(cfg, cache, False) is False
    assert adt_watch._is_paused(cache) is False


def test_paused_tick_does_not_sync_and_goes_red(tmp_path, monkeypatch):
    """Paused means the loop keeps running and skips the pass.

    The window is not re-stamped, so the pill goes red — truthful, because the
    board is genuinely not being synced. This is the whole reason pause beats
    unloading the agent: the process stays alive to be started again.
    """
    root = _run_tick(tmp_path, monkeypatch, moved=False)
    cfg = adt_sync.load_config(root)
    cache = adt_sync.cache_dir(cfg)
    stamped_while_running = adt_watch._health_stamp(cfg)
    assert stamped_while_running is not None

    adt_watch._set_paused(cfg, cache, True)
    synced = []
    _run_tick(tmp_path, monkeypatch, on_sync=lambda r: synced.append(1),
              existing_root=root)
    assert synced == [], "a paused tick still called _sync"
    assert adt_watch._health_stamp(cfg) == stamped_while_running, (
        "a paused tick re-stamped the window; the pill would stay green on a "
        "board that is not being synced")
