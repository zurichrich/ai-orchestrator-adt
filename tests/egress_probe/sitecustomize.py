# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Record and block any request to the telemetry collector during a test run.

Active only when `ADT_EGRESS_CAPTURE` is set. Python imports a `sitecustomize`
at interpreter start when its directory is on `PYTHONPATH`, so this also runs
in every python subprocess a shell test spawns.

It wraps `urllib.request.urlopen`, the call `send_ping` makes in
`tools/adt_phone_home.py`. A request to the collector host is appended to
`$ADT_EGRESS_CAPTURE` and then refused with `URLError`, which `send_ping`
already catches, so no request leaves the machine.

Putting this directory on `PYTHONPATH` hides the interpreter's own
`sitecustomize.py` (Homebrew python uses one to set up site-packages), so the
function at the bottom runs that file too.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CAPTURE = os.environ.get("ADT_EGRESS_CAPTURE")

if _CAPTURE:
    import urllib.error
    import urllib.request

    # Default matches TELEMETRY_ENDPOINT's host in tools/adt_phone_home.py.
    _HOST = os.environ.get("ADT_EGRESS_HOST",
                           "adt-telemetry.zurichrich.workers.dev")
    _original_urlopen = urllib.request.urlopen

    def _guarded_urlopen(url, *args, **kwargs):
        target = getattr(url, "full_url", None) or str(url)
        if _HOST in target:
            with open(_CAPTURE, "a", encoding="utf-8") as fh:
                fh.write("%s\t%s\n" % (os.getpid(), target))
            raise urllib.error.URLError("blocked by the ADT egress probe")
        return _original_urlopen(url, *args, **kwargs)

    urllib.request.urlopen = _guarded_urlopen


def _chain_to_the_shadowed_sitecustomize():
    """Run the `sitecustomize` this file displaced, if the interpreter had one."""
    import importlib.util
    for entry in sys.path:
        if not entry or os.path.abspath(entry) == _HERE:
            continue
        candidate = os.path.join(entry, "sitecustomize.py")
        if not os.path.isfile(candidate):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                "_adt_shadowed_sitecustomize", candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            # The other file is not ours; an error in it must not stop the
            # interpreter from starting.
            pass
        return


_chain_to_the_shadowed_sitecustomize()
