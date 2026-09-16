# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Version advisory + anonymous usage ping (ADT-170 3b/3c).

Two mechanisms, deliberately separate.

**Advisory** — a static JSON manifest published as a release asset, fetched over
plain HTTPS at a VERSION-PINNED url. No server, so the safety-critical half
cannot fail because a collector is down or was never built. Deliberately
unsigned: because the client fails open, an attacker who can intercept the fetch
can only *suppress* a revocation, never forge one; forging needs the release
asset itself, which is a supply-chain compromise signing would not survive.

**Ping** — a separate, optional POST. Its absence degrades nothing.

Both FAIL OPEN on every error class — network, timeout, non-2xx, malformed or
truncated JSON, unexpected schema. `adt_watch` had zero outbound HTTP before
this, and it runs unattended on a 60s launchd tick, so an unhandled exception
here is a regression before it is a security question. Both are silent under
--once (ADT-119: no per-tick output).

Stdlib only, in keeping with the rest of tools/.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid

# Set at release. Empty disables the ping entirely (and is what ships until a
# collector exists). The advisory does not use this.
TELEMETRY_ENDPOINT = "https://adt-telemetry.zurichrich.workers.dev"

_ADVISORY_URL = ("https://github.com/zurichrich/ai-orchestrator-adt/releases/"
                 "download/v{version}/advisory.json")
_TIMEOUT = 5


def _state_dir(project_root: str) -> str:
    return os.path.join(project_root, ".adt", "state")


def read_version(repo_root: str) -> str:
    try:
        with open(os.path.join(repo_root, "VERSION")) as fh:
            return fh.read().strip()
    except OSError:
        return ""


# The daemon ticks every 60s. Everything below is gated to once per UTC day by
# a stamp file — without this, wiring it into the tick would call out 1440x the
# rate docs/security-posture.md publishes, and the egress test would still pass
# because it checks hosts, never frequency.
def due_today(project_root: str, name: str, today: str = None) -> bool:
    """True at most once per UTC day per `name`. Stamps on the way out."""
    import datetime
    today = today or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(_state_dir(project_root), f"{name}-last")
    try:
        with open(path) as fh:
            if fh.read().strip() == today:
                return False
    except OSError:
        pass
    try:
        os.makedirs(_state_dir(project_root), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(today + "\n")
    except OSError:
        return False          # cannot stamp -> do not fire, rather than fire forever
    return True


# Cap the read. The threat model explicitly contemplates an attacker who
# controls the release asset; without a cap they can defeat the fail-open
# contract with an oversized body on an unattended daemon, because MemoryError
# is not in the except tuple below and would not be caught anyway.
_MAX_BYTES = 64 * 1024

# Cloudflare's browser-integrity check 403s (error 1010) on urllib's default
# "Python-urllib/3.x" user agent, so an unset UA meant every real ping was
# rejected while every mocked test passed. Identifying honestly is also just
# better manners than looking like an unnamed script.
def _ua(version: str = "") -> str:
    return f"adt/{version or 'dev'} (+https://github.com/zurichrich/ai-orchestrator-adt)"


def _get_json(url: str):
    """GET + parse. Returns None on ANY failure — that is the fail-open contract,
    and it covers parse errors as well as transport ones."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _ua()})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            raw = resp.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                return None        # oversized -> treat as unreadable, fail open
            return json.loads(raw.decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError, TimeoutError):
        return None


def check_advisory(version: str, url_template: str = _ADVISORY_URL):
    """Return an advisory string if THIS version is revoked, else None.

    Version-pinned rather than a `latest` pointer, so a later release changing
    the manifest shape cannot confuse an older client.
    """
    if not version:
        return None
    data = _get_json(url_template.format(version=version))
    # Schema check is part of failing open: anything unexpected is ignored.
    if not isinstance(data, dict):
        return None
    revoked = data.get("revoked")
    if not isinstance(revoked, list):
        return None
    if version not in revoked:
        return None
    current = data.get("current")
    msg = f"ADT {version} has been revoked and should not be used."
    if isinstance(current, str) and current:
        msg += f" Current release is {current}."
    return msg


def _read_install_id(project_root: str) -> str:
    """The stored install id, or '' when there is none. Never creates one."""
    try:
        with open(os.path.join(_state_dir(project_root), "install-id")) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def install_id(project_root: str) -> str:
    """A random UUID minted once per install and reused.

    Explicitly NOT platform.node() or any machine fingerprint. The cost ledger
    uses a hostname for cross-machine accounting and that is the wrong precedent
    to copy here: a fingerprint is what makes telemetry read as tracking.
    """
    existing = _read_install_id(project_root)
    if existing:
        return existing
    path = os.path.join(_state_dir(project_root), "install-id")
    new = str(uuid.uuid4())
    try:
        os.makedirs(_state_dir(project_root), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(new + "\n")
    except OSError:
        pass
    return new


def telemetry_enabled(project_root: str) -> bool:
    if not TELEMETRY_ENDPOINT:
        return False
    if os.environ.get("DO_NOT_TRACK", "").strip() not in ("", "0"):
        return False
    if os.path.exists(os.path.join(_state_dir(project_root), "telemetry-off")):
        return False
    return True


def known_commands(repo_root: str) -> set:
    """The commands whose counts may cross the wire.

    ADT-224 criterion 12: what is sent is counts keyed by ADT's own command
    names and NOTHING else. This is an allowlist derived from the shipped
    playbooks, not a pattern — `usage.log` latches whatever `/adt-...` token
    appeared in a prompt, and 33 of this repo's own 236 rows are not commands at
    all (`adt-token-log`, `adt-wt-graphql-pool`, ...). A pattern would let those
    through; an allowlist cannot.
    """
    import glob
    return {os.path.basename(p)[:-3]
            for p in glob.glob(os.path.join(repo_root, "commands", "*.md"))}


def command_counts(project_root: str, repo_root: str, cache_dir=None) -> dict:
    """Tally the local usage log per command, plus ticket and token totals.

    Every value is a COUNT. No argument, ticket id, file path, repo or branch
    name, or prompt text is read out of either log — `usage.log` holds a
    timestamp and a command name and only the name is looked at; the ledger's
    ticket ids are counted, never carried.

    Silent on any failure: this feeds a best-effort daily ping on an unattended
    tick, so a missing or malformed log degrades to zero rather than raising.
    """
    state = _state_dir(project_root)
    allowed = known_commands(repo_root)
    commands, tickets, tokens = {}, set(), 0

    try:
        with open(os.path.join(state, "usage.log"), encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 2:
                    continue
                # adt-usage-log.sh writes the invocation verbatim (`adt-plan`); a
                # command's own name is its playbook basename (`plan`), which is
                # the key space the collector's cmdset is ordered by.
                name = parts[1][4:] if parts[1].startswith("adt-") else parts[1]
                if name in allowed:
                    commands[name] = commands.get(name, 0) + 1
    except OSError:
        pass

    try:
        with open(os.path.join(state, "cost-ledger.log"), encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 4:
                    continue
                if parts[1]:
                    tickets.add(parts[1])
                try:
                    tokens += int(parts[2]) + int(parts[3])
                except ValueError:
                    continue
    except OSError:
        pass

    quality = _quality(project_root, repo_root, cache_dir)
    return {"commands": commands, "tickets": len(tickets), "tokens": tokens,
            **_cost(project_root), **quality,
            **_autonomy(project_root, repo_root, cache_dir),
            **_qa(cache_dir),
            "surfaces": _surfaces(project_root)}


# ADT-284. The two dimension sets the collector validates as CLOSED enums. Held
# here as well so an unknown name is dropped BEFORE it reaches the wire: the
# collector answers 400 on one it does not know, `send_ping` swallows the 400,
# and the whole ping is lost. A gate added on this side before the Worker is
# redeployed would otherwise take telemetry down rather than degrade it.
GATE_NAMES = ("coverage", "plan-quality")
TRACK_NAMES = ("fast", "standard", "full", "unset")

_GATE_FIELDS = ("ran", "caused_edit", "under_recorded", "tickets")
_TRACK_FIELDS = ("closed", "stamped", "follow_ons", "defects",
                 "days_sum", "days_n")


def _days_to_close(cache_dir, rows):
    """{track: (days_sum, n)} from `created` to `closed_on`.

    CALENDAR time, not working time: a ticket that sat in the backlog for a
    month before anyone picked it up counts that month. Sent as a sum and a
    count rather than a mean so the dashboard can add tiers across installs —
    a mean of means weights a one-ticket install the same as a hundred-ticket
    one.
    """
    import datetime

    def parse(v):
        v = str(v).strip().rstrip("Z")
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(v[:19] if "T" in v else v, fmt)
            except ValueError:
                continue
        return None

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import adt_metrics
    out = {}
    for r in rows:
        if not r.get("closed_on"):
            continue
        try:
            text = open(r["path"], encoding="utf-8").read()
        except OSError:
            continue
        m = adt_metrics.CREATED_RE.search(text)
        if not m:
            continue
        a, b = parse(m.group(1)), parse(r["closed_on"])
        # A close stamped before the create is a clock or an edit, not a
        # duration. Dropped rather than counted as zero, which would drag a
        # tier's mean toward nothing.
        if not a or not b or b < a:
            continue
        acc = out.setdefault(r["track"], [0.0, 0])
        acc[0] += (b - a).total_seconds() / 86400.0
        acc[1] += 1
    return out


def _qa(cache_dir) -> dict:
    """How often QA sent a build back, counted from the tickets' QA reports.

    `/adt-qa-run` writes `**Result:** PASS` or `**Result:** FAIL` under a
    `## QA report` heading each time it grades a build, and a FAIL sends the
    ticket back to building. The ticket file is the only record of that outcome,
    so it is counted here. A `**Result:**` line under any other heading, such as
    a release check, is not a QA result. Counts only, no ticket ids.
    """
    out = {"qa_checks": 0, "qa_fails": 0, "qa_tickets": 0, "qa_tickets_failed": 0}
    if not cache_dir or not os.path.isdir(str(cache_dir)):
        return out
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import adt_metrics
        for path in adt_metrics.all_tickets(cache_dir):
            checks = fails = 0
            in_qa = False
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        if line.startswith("## "):
                            in_qa = line.startswith("## QA report")
                        elif in_qa and line.startswith("**Result:**"):
                            checks += 1
                            if line[len("**Result:**"):].strip().startswith("FAIL"):
                                fails += 1
            except OSError:
                continue
            if checks:
                out["qa_checks"] += checks
                out["qa_fails"] += fails
                out["qa_tickets"] += 1
                out["qa_tickets_failed"] += 1 if fails else 0
    except Exception:
        pass
    return out


def _surfaces(project_root: str) -> dict:
    """{name: count} over every subagent dispatched and skill invoked.

    `adt-token-log` already tallies these into `surface-log.tsv`. Five review
    agents ship and only the two that write a `gate_effects` record were
    visible in telemetry, so a security or design review left no trace at all.

    Counts only. The name is a shipped agent or skill name and is checked
    against the same bounded pattern command names use, so nothing else can
    ride the field.

    Commit cc5fb8a (2026-09-09) added an `adt-` prefix to ADT's six agents, and
    the log keeps every row written before that under the old name. Those rows
    are counted under the new name, so one agent is one entry. The list is the
    six names that rename touched, not every agent ADT ships, so a later
    `adt-foo` agent never absorbs a project's own `foo` history. A project can
    also have its own agent with one of the six plain names; when
    `.claude/agents/<name>.md` exists in the project, the name is left alone.
    """
    renamed = {old for old in _RENAMED_AGENTS
               if not os.path.exists(os.path.join(project_root, ".claude", "agents", old + ".md"))}
    out = {}
    try:
        path = os.path.join(_state_dir(project_root), "surface-log.tsv")
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 5:
                    continue
                name = parts[3]
                if name in renamed:
                    name = "adt-" + name
                if not _SURFACE_RE.match(name):
                    continue
                try:
                    out[name] = out.get(name, 0) + int(parts[4])
                except ValueError:
                    continue
    except OSError:
        return {}
    return dict(sorted(out.items(), key=lambda kv: -kv[1])[:_MAX_SURFACES])


#: The agents commit cc5fb8a renamed with the adt- prefix on 2026-09-09.
_RENAMED_AGENTS = ("critical-path-reviewer", "design-reviewer", "dod-coverage-reviewer",
                   "forensic-investigator", "plan-quality-reviewer", "security-reviewer")

#: Mirrors COMMAND_KEY in collector/collector.js. An agent or skill name cannot
#: be a ticket id, a path or a branch name and satisfy it.
_SURFACE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_MAX_SURFACES = 32


def _autonomy(project_root: str, repo_root: str, cache_dir) -> dict:
    """How often a run needed a person, and how much of the DoD is provable.

    All four are already computed by tools/adt_metrics.py and tools/adt_dod.py
    and none of them was sent. Counts only, no ticket ids.
    """
    out = {"handbacks": 0, "guard_denies": 0, "blocked_markers": 0,
           "dod_conditions": 0, "dod_pinned": 0}
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import adt_metrics, adt_dod

        ledger = os.path.join(_state_dir(project_root), "cost-ledger.log")
        out["handbacks"] = sum(adt_metrics.handbacks(ledger).values())
        out["guard_denies"] = int(adt_metrics.guard_denies(project_root) or 0)
        if cache_dir and os.path.isdir(str(cache_dir)):
            rows = [r for r in (adt_metrics.read_ticket(p)
                                for p in adt_metrics.closed_tickets(cache_dir)) if r]
            out["blocked_markers"] = sum(r["blocked_markers"] for r in rows)
            for path in adt_metrics.all_tickets(cache_dir):
                try:
                    conds = adt_dod.parse_done_evidence(path)
                except Exception:
                    continue
                out["dod_conditions"] += len(conds)
                # A condition pinned to a commit where it demonstrably failed.
                # The remainder has never been observed failing, which is the
                # always-green defect and the number worth watching.
                out["dod_pinned"] += sum(1 for c in conds if c.get("was_red_at"))
    except Exception:
        pass
    return out


def _cost(project_root: str) -> dict:
    """Lifetime cost in MICRO-DOLLARS, and the part of it that was measured.

    Priced here rather than in the collector: the price table carries per-model
    cache-tier multipliers, a dated-suffix retry and a measured/estimated/legacy
    ranking, and a second copy in the Worker would need active syncing. What
    crosses the wire is two integers — no model name, no per-row breakdown.

    The measured share is sent separately because `price_ledger` is deliberately
    pessimistic: one estimated row makes a whole ticket approximate. A single
    dollar figure would present a part-estimated total as if it were measured.

    Silent on any failure, like everything else feeding an unattended tick.
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import adt_cost
        prices = adt_cost.load_prices()
        if not prices:
            return {"cost_micros": 0, "cost_measured_micros": 0}
        ledger = os.path.join(_state_dir(project_root), "cost-ledger.log")
        estimator = adt_cost.load_estimator(project_root)
        with open(ledger, encoding="utf-8") as fh:
            priced = adt_cost.price_ledger(fh, prices, estimator)
        total = sum(int(v["micros"] or 0) for v in priced.values())
        measured = sum(int(v["micros"] or 0) for v in priced.values()
                       if v.get("tier") == "measured")

        # WHERE the tokens go, not just how many. The ledger records three cache
        # tiers and a model per turn; a cache read is priced at a fraction of
        # fresh input, so a total with no split cannot be acted on.
        inp = out = cread = cwrite = 0
        models = {}
        with open(ledger, encoding="utf-8") as fh:
            for line in fh:
                r = adt_cost.parse_row(line)
                if not r:
                    continue
                inp += r["input"]; out += r["output"]
                cread += r["cache_read"]
                cwrite += r["cache_write_5m"] + r["cache_write_1h"]
                name = r["model"] or ""
                if not _SURFACE_RE.match(name):
                    continue          # `<synthetic>` and blanks never cross
                micros, _ = adt_cost.price_row(r, prices, estimator)
                acc = models.setdefault(name, {"tokens": 0, "micros": 0})
                acc["tokens"] += r["input"] + r["output"]
                acc["micros"] += int(micros or 0)
        return {"cost_micros": total, "cost_measured_micros": measured,
                "input_tokens": inp, "output_tokens": out,
                "cache_read_tokens": cread, "cache_write_tokens": cwrite,
                "models": dict(sorted(models.items(),
                                      key=lambda kv: -kv[1]["tokens"])[:16])}
    except Exception:
        return {"cost_micros": 0, "cost_measured_micros": 0,
                "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_write_tokens": 0, "models": {}}


def _quality(project_root: str, repo_root: str, cache_dir) -> dict:
    """Gate effects, per-track delivery, and the DoD revision count.

    COUNTS ONLY, and only the ones a success criterion asks for. `handbacks`,
    `guard_denies` and the install-level `post_merge_defects` are deliberately
    not sent: every field is privacy surface, and one no reader asked for is cost
    without a purpose.

    Reads the CACHE, which is outside every git checkout and which this module
    could not previously reach — hence the argument. With no cache_dir the maps
    come back empty rather than raising, so an install whose config the watcher
    could not resolve still sends its command counts.
    """
    empty = {"dod_amendments": 0, "gates": {}, "tracks": {}}
    if not cache_dir or not os.path.isdir(str(cache_dir)):
        return empty
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import adt_metrics, adt_dod

        gates = {}
        for name, row in adt_metrics.gate_effect_rows(cache_dir).items():
            if name in GATE_NAMES:                       # drop before the wire
                gates[name] = {f: int(row.get(f) or 0) for f in _GATE_FIELDS}

        rows = [r for r in (adt_metrics.read_ticket(p)
                            for p in adt_metrics.closed_tickets(cache_dir)) if r]
        defects = adt_metrics.post_merge_defects(cache_dir, rows)
        days = _days_to_close(cache_dir, rows)
        tracks = {}
        for name, row in adt_metrics.by_track(rows, defects).items():
            if name in TRACK_NAMES:
                acc = {f: int(row.get(f) or 0) for f in _TRACK_FIELDS}
                d = days.get(name, [0.0, 0])
                # Whole days, so the wire carries integers like everything else.
                acc["days_sum"], acc["days_n"] = int(round(d[0])), int(d[1])
                tracks[name] = acc

        # dod_amendments is per TICKET, not a cache-level aggregate like the two
        # above — a different call shape, which is why it is summed here.
        amendments = 0
        for path in adt_metrics.all_tickets(cache_dir):
            try:
                amendments += int(adt_dod.dod_amendments(path) or 0)
            except Exception:
                continue
        return {"dod_amendments": amendments, "gates": gates, "tracks": tracks}
    except Exception:
        return empty


def build_payload(project_root: str, version: str, counts: dict) -> dict:
    """The complete field set. Anything not listed here is not sent — no prompts,
    no code, no file paths, no ticket titles, no repo names.

    ADT-224: `commands` is a name-keyed map of counts. It used to be an integer
    that `adt_watch` never populated, so every ping ever sent carried three
    constant zeros while `docs/security-posture.md` published counts as data ADT
    collects.

    ADT-284 takes the schema to 3 and appends cost, the DoD revision count and
    the two gate/track maps. Every value is still a COUNT — money is an integer
    number of micro-dollars, and the gate and track names come from closed sets
    filtered again here, so a name the collector would reject never reaches it.
    The collector still accepts schemas 1 and 2, because deployed clients send
    them and `send_ping` fails open on a 400.
    """
    import platform
    commands = counts.get("commands", {})
    if not isinstance(commands, dict):     # a pre-ADT-224 caller's bare total
        commands = {}
    def _grp(field, names, fields):
        out = {}
        for name, row in (counts.get(field) or {}).items():
            if name in names and isinstance(row, dict):
                out[name] = {f: int(row.get(f) or 0) for f in fields}
        return out

    return {
        "schema": 5,
        "install_id": install_id(project_root),
        "version": version,
        "os": platform.system(),
        "commands": {k: int(v) for k, v in commands.items()},
        "tickets": int(counts.get("tickets", 0)),
        "tokens": int(counts.get("tokens", 0)),
        # ADT-284. Money as an integer count of micro-dollars, never a currency
        # string and never a per-model breakdown.
        "cost_micros": int(counts.get("cost_micros", 0)),
        "cost_measured_micros": int(counts.get("cost_measured_micros", 0)),
        "dod_amendments": int(counts.get("dod_amendments", 0)),
        "gates": _grp("gates", GATE_NAMES, _GATE_FIELDS),
        "tracks": _grp("tracks", TRACK_NAMES, _TRACK_FIELDS),
        # Schema 4 (ADT-284 follow-up): how often a run needed a person, how
        # much of the Definition of Done has ever been observed failing, where
        # the tokens actually go, and which review agents ran at all.
        "handbacks": int(counts.get("handbacks", 0)),
        "guard_denies": int(counts.get("guard_denies", 0)),
        "blocked_markers": int(counts.get("blocked_markers", 0)),
        "dod_conditions": int(counts.get("dod_conditions", 0)),
        "dod_pinned": int(counts.get("dod_pinned", 0)),
        "input_tokens": int(counts.get("input_tokens", 0)),
        "output_tokens": int(counts.get("output_tokens", 0)),
        "cache_read_tokens": int(counts.get("cache_read_tokens", 0)),
        "cache_write_tokens": int(counts.get("cache_write_tokens", 0)),
        "surfaces": {k: int(v) for k, v in (counts.get("surfaces") or {}).items()},
        "models": {k: {"tokens": int(v.get("tokens") or 0),
                       "micros": int(v.get("micros") or 0)}
                   for k, v in (counts.get("models") or {}).items()
                   if isinstance(v, dict)},
        # Schema 5 (ADT-371): how often QA sent a build back.
        "qa_checks": int(counts.get("qa_checks", 0)),
        "qa_fails": int(counts.get("qa_fails", 0)),
        "qa_tickets": int(counts.get("qa_tickets", 0)),
        "qa_tickets_failed": int(counts.get("qa_tickets_failed", 0)),
    }


def send_ping(project_root: str, payload: dict, endpoint: str = None) -> bool:
    """POST the payload. Returns True only on a 2xx; never raises."""
    endpoint = endpoint if endpoint is not None else TELEMETRY_ENDPOINT
    if not endpoint:
        return False
    try:
        req = urllib.request.Request(
            endpoint, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "User-Agent": _ua(payload.get("version", ""))},
            method="POST")
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError, TimeoutError):
        return False


def send_uninstall_event(project_root: str, repo_root: str,
                         endpoint: str = None) -> bool:
    """ADT-391: tell the collector this install was uninstalled.

    Uninstall deletes `.adt/`, so without this an uninstalled install reads the
    same as one that turned telemetry off or whose watcher stopped. The body is
    four fields, three of which every daily report already carries.

    The id is READ, never minted: an install that never reported has nothing to
    say goodbye from, and minting one here would register an install on its way
    out. Returns True only on a 2xx; never raises.
    """
    try:
        if not telemetry_enabled(project_root):
            return False
        existing = _read_install_id(project_root)
        version = read_version(repo_root)
        if not existing or not version:
            return False
        import platform
        return send_ping(project_root, {"event": "uninstall", "install_id": existing,
                                        "version": version, "os": platform.system()},
                         endpoint=endpoint)
    except Exception:
        return False


def first_run_notice(project_root: str) -> str:
    """Shown once, the first time telemetry would fire. Returns '' afterwards."""
    marker = os.path.join(_state_dir(project_root), "telemetry-disclosed")
    if os.path.exists(marker):
        return ""
    try:
        os.makedirs(_state_dir(project_root), exist_ok=True)
        open(marker, "w").close()
    except OSError:
        return ""
    return ("[adt] Anonymous usage stats are on: a random install id, the ADT "
            "version, your OS, counts, and what your token ledger cost in "
            "dollars (priced locally), sent daily and as one last report when "
            "you uninstall. Never prompts, code, paths, ticket "
            "titles or repo names. Turn them off permanently with "
            "DO_NOT_TRACK=1 or by creating "
            ".adt/state/telemetry-off . "
            "Details: docs/security-posture.md")


def run_once(project_root: str, repo_root: str, counts=None,
             quiet: bool = False) -> None:
    """The single entry point `adt_watch` calls each tick.

    Everything here is best-effort and silent on failure: it runs on an
    unattended 60s tick, so an exception escaping this function is a daemon
    regression before it is anything else. The bare except is deliberate and is
    the outermost guard — the inner functions already fail open individually.
    """
    try:
        version = read_version(repo_root)
        if version and due_today(project_root, "advisory"):
            msg = check_advisory(version)
            if msg:
                # Loud on purpose, even under --once: a revoked version is the
                # one thing worth breaking the quiet-tick contract for.
                print(f"[adt] {msg}", file=sys.stderr)
        if telemetry_enabled(project_root) and due_today(project_root, "telemetry"):
            # ADT-203: first_run_notice() STAMPS the marker as a side effect of
            # being called, so calling it when we cannot print consumes the one
            # disclosure and discards it. The watcher runs under launchd/systemd
            # with --once, i.e. quiet=True and stderr redirected to a log file,
            # so on every real install the notice was eaten by the first
            # background tick and could never be shown again. Ask for it only
            # when there is somewhere for it to go. The install-time disclosure
            # in adt-install.sh is the surface a human actually reads; this is
            # the fallback for a watcher run in the foreground.
            if not quiet:
                notice = first_run_notice(project_root)
                if notice:
                    print(notice, file=sys.stderr)
            # `counts` may be a provider rather than a dict, and the watcher
            # passes one: this branch runs once a UTC day while the tick is 60s,
            # so tallying the logs eagerly would do the work ~1439 times for
            # nothing.
            resolved = counts(project_root, repo_root) if callable(counts) else (counts or {})
            send_ping(project_root, build_payload(project_root, version, resolved))
    except Exception:
        return


if __name__ == "__main__":
    # ADT-391: lib/uninstall.sh runs `adt_phone_home.py --uninstall-event
    # <project_root>`. Always exits 0, because uninstall must not stop over a
    # report it cannot send.
    if len(sys.argv) == 3 and sys.argv[1] == "--uninstall-event":
        send_uninstall_event(sys.argv[2],
                             os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(0)
