# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""ADT-386: an open ticket panel keeps its scroll position across the board's
automatic reload, and a newly opened ticket starts at its top.

Runs the board's real HTML_JS under node, in a vm context with a small stub
DOM. A "reload" is a second run of the script in a fresh context that shares
the first one's sessionStorage, which is what a real reload keeps. The stub
makes `.ticket-detail:target` match only once `load` fires, the Chrome
behaviour measured when the bug was filed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import build_kanban as bk  # noqa: E402

HARNESS = r"""
const vm = require('vm');
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const store = new Map();
const sessionStore = {
  getItem: k => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: k => store.delete(k),
};

function page(hash, opts) {
  opts = opts || {};
  const docListeners = {}, winListeners = {};
  const on = (m) => (type, fn) => { (m[type] = m[type] || []).push(fn); };
  // A real Element subclass, so the page may test the target with either
  // `instanceof Element` + `matches()` or `classList`.
  let targetMatches = false;
  class Element {}
  const panel = Object.assign(new Element(), {
    classList: { contains: c => c === 'ticket-detail' },
    matches: sel => sel === '.ticket-detail' || (sel === '.ticket-detail:target' && targetMatches),
    scrollTop: 0,
  });
  const empty = [];
  const ctx = {
    document: {
      addEventListener: on(docListeners),
      querySelectorAll: () => empty,
      querySelector: sel => (sel === '.ticket-detail:target' && targetMatches && hash ? panel : null),
      getElementById: () => null,
      body: { classList: { toggle() {} } },
    },
    location: { hash: hash, reload() {} },
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout() {},
    JSON: JSON,
    Element: Element,
  };
  if (opts.storageThrows) {
    Object.defineProperty(ctx, 'sessionStorage', { get() { throw new Error('SecurityError'); } });
  } else {
    ctx.sessionStorage = sessionStore;
  }
  ctx.window = ctx;
  ctx.addEventListener = on(winListeners);
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  const fire = (m, type, ev) => (m[type] || []).forEach(fn => fn(ev || {}));
  return {
    panel,
    scroll(top) { panel.scrollTop = top; fire(docListeners, 'scroll', { target: panel }); },
    domContentLoaded() { fire(docListeners, 'DOMContentLoaded'); fire(winListeners, 'DOMContentLoaded'); },
    load() { targetMatches = true; fire(winListeners, 'load'); },
    hashchange(h) { ctx.location.hash = h; fire(winListeners, 'hashchange'); },
  };
}

const result = {};
__SCENARIO__
process.stdout.write(JSON.stringify(result));
"""


def _run(tmp_path, scenario: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    js = tmp_path / "page.js"
    js.write_text(bk.HTML_JS.replace("__REFRESH_MS__", str(bk.REFRESH_SECONDS * 1000)))
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS.replace("__SCENARIO__", scenario))
    r = subprocess.run([node, str(harness), str(js)], capture_output=True, text=True)
    assert r.returncode == 0, f"harness failed:\n{r.stdout}\n{r.stderr}"
    return json.loads(r.stdout)


def test_reload_keeps_the_open_panels_position(tmp_path):
    out = _run(tmp_path, """
      const before = page('#t-a'); before.load(); before.scroll(1800);
      const after = page('#t-a'); after.load();
      result.top = after.panel.scrollTop;
    """)
    assert out["top"] == 1800


def test_position_is_applied_at_load_not_at_domcontentloaded(tmp_path):
    out = _run(tmp_path, """
      const before = page('#t-a'); before.load(); before.scroll(1800);
      const after = page('#t-a'); after.domContentLoaded();
      result.atDcl = after.panel.scrollTop;
      after.load();
      result.atLoad = after.panel.scrollTop;
    """)
    assert out == {"atDcl": 0, "atLoad": 1800}


def test_hashchange_clears_the_saved_position(tmp_path):
    out = _run(tmp_path, """
      const before = page('#t-a'); before.load(); before.scroll(1800);
      before.hashchange('#t-b'); before.hashchange('#t-a');
      const after = page('#t-a'); after.load();
      result.top = after.panel.scrollTop;
    """)
    assert out["top"] == 0


def test_a_different_ticket_after_reload_starts_at_the_top(tmp_path):
    out = _run(tmp_path, """
      const before = page('#t-a'); before.load(); before.scroll(1800);
      const after = page('#t-b'); after.load();
      result.top = after.panel.scrollTop;
    """)
    assert out["top"] == 0


def test_page_still_runs_when_session_storage_throws(tmp_path):
    out = _run(tmp_path, """
      const p = page('#t-a', { storageThrows: true });
      p.load(); p.scroll(1800); p.hashchange('#t-b');
      const again = page('#t-a', { storageThrows: true }); again.load();
      result.top = again.panel.scrollTop;
    """)
    assert out["top"] == 0
