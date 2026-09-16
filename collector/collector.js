// Copyright 2026 zurichrich
// SPDX-License-Identifier: Apache-2.0
//
// ADT-170 3e — the collector's logic, deliberately free of Cloudflare bindings
// so it can be tested directly rather than through a deploy. ADT-224 A2a moved
// the request handler here too (`handle`), so the put-accounting cases in
// test-collector.mjs run against the code that actually ships rather than a
// reimplementation of it; `worker.js` is now binding glue and nothing else.
//
// This endpoint's URL ships inside open-source client code, so it is a PUBLIC
// UNAUTHENTICATED POST from the moment it merges. Without validation and a
// budget it is floodable into either a denial-of-wallet on the free tier or
// poisoned adoption stats — and poisoned stats defeat the only thing the
// feature exists to produce. (ADT-170 security review, MEDIUM.)
//
// ADT-224: STORAGE IS SPLIT BY MUTABILITY, and that is the whole design.
// Install identity changes rarely; usage changes daily. Keeping both in KV made
// the rare thing pay the frequent thing's cost — every accepted ping was a put
// (`ping:<day>:<id>`) plus two limiter puts, against a 1000/day free ceiling,
// which at 20k installs is 20x over. So:
//
//   KV   holds `install:<uuid>` -> {first_seen}. GET first, PUT only when novel.
//        Steady state at ANY user count is zero puts.
//   AE   holds what varies (version, os, per-command counts) as one data point
//        per ping. AE writes do not touch the KV quota.
//
// `version` is deliberately NOT in the KV record: if identity carried it, every
// record would change on release day and "put on change" would become 20,000
// puts in 24 hours. Version is a usage dimension.

// Mirrors build_payload() in tools/adt_phone_home.py. An allowlist, not a
// blocklist: an unexpected key is rejected rather than stored, so a future
// client that starts sending something new fails loudly here instead of
// quietly persisting whatever it sent.
export const ALLOWED = ["schema", "install_id", "version", "os",
                        "commands", "tickets", "tokens"];

// ADT-284: schema 3 APPENDS these. Kept in a separate list rather than added to
// ALLOWED because the check below requires every allowed key to be PRESENT — a
// deployed 0.1.0 client sends exactly ALLOWED and must keep passing, so folding
// the new fields into one list would 400 every existing install.
export const ALLOWED_V3 = ["cost_micros", "cost_measured_micros",
                           "dod_amendments", "gates", "tracks"];

// Schema 4 appends these. Same reason ALLOWED_V3 is separate: every allowed key
// must be PRESENT, so a schema 3 client sending exactly ALLOWED + ALLOWED_V3
// keeps passing.
export const ALLOWED_V4 = ["handbacks", "guard_denies", "blocked_markers",
                           "dod_conditions", "dod_pinned", "input_tokens",
                           "output_tokens", "cache_read_tokens",
                           "cache_write_tokens", "surfaces", "models"];

// Schema 5 appends the QA counts, for the same reason: every allowed key must be
// PRESENT, so a schema 4 client keeps passing.
export const ALLOWED_V5 = ["qa_checks", "qa_fails", "qa_tickets", "qa_tickets_failed"];

// ADT-391: the uninstall event is a separate body shape, not a report schema.
// Every allowed report key must be present, so an event sent as a report would
// have to carry the whole counts payload to say one thing. The body is told apart
// on the `event` key in `handle`, which no report carries, so no deployed
// client changes.
// There is no update event: an update shows as a newer `version` on the daily
// report, so nothing new is sent for it.
export const EVENT_KEYS = ["event", "install_id", "version", "os"];
export const EVENT_NAMES = ["uninstall"];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SEMVER = /^\d+\.\d+\.\d+$/;
const MAX_COUNT = 10_000_000;

// Money needs its own ceiling. A lifetime cost in micro-dollars runs three
// orders of magnitude past a count — this repo's own ledger prices to
// 4,382,024,945 micros ($4,382.02) as of 2026-09-08 — so sharing MAX_COUNT
// would reject every ping from a long-lived install. It would do it silently:
// `send_ping` returns False on any non-2xx and never raises.
//
// The same trap is already live for `tokens`, which MAX_COUNT still bounds at
// 1e7 while this install's real lifetime total is 22,700,797. Raised below.
const MAX_CUMULATIVE = 1_000_000_000_000;  // $1M in micros; ~228x the largest real total

// The two dimension sets that are CLOSED, and therefore checkable as enums
// rather than as a pattern. `blob5` is the only field carrying a per-row string,
// so it is the one place an unvalidated value would reach the dataset.
export const GATE_NAMES = ["coverage", "plan-quality"];
// `unset` is a real track, not a placeholder: `adt_metrics.by_track` returns it
// for tickets with no `track:` field, and 4 of this repo's 71 closed tickets are
// in that bucket. Leaving it out would drop their row or 400 the whole ping.
export const TRACK_NAMES = ["fast", "standard", "full", "unset"];

// ADT-224 criterion 12: what crosses the wire is counts keyed by ADT's own
// command names and nothing else. The key pattern is what stops an arbitrary
// string being smuggled in as a "command" — a ticket id, a file path or a
// branch name cannot satisfy it — and the cap bounds the map's width.
const COMMAND_KEY = /^[a-z0-9][a-z0-9-]{0,39}$/;
const MAX_COMMAND_KEYS = 64;

// Positional doubles on the AE data point, against the `cmdset-v1` blob. The
// blob is what lets a later reordering be a NEW set rather than a silent
// corruption of old rows: a reader that sees cmdset-v1 knows this order.
export const CMDSET_V1 = [
  "block", "brief", "build", "build-todone", "close", "decide",
  "plan", "plan-fasttrack", "qa-run", "release-check",
  "review-critical-path", "review-designer", "review-security", "unblock",
];

// Two budgets, per IP per UTC day, because they protect different ceilings.
// `requests` bounds AE writes (500 is 0.5% of the 100k/day free data points).
// `novelInstalls` bounds KV puts (50 is 5% of the 1000/day free puts). They are
// separate because the observed shape — 344 install_ids behind 4 IPs — means a
// shared egress IP legitimately sends far more requests than it registers new
// installs, so a single number either false-positives on a NATed org or leaves
// puts unbounded.
export const BUDGETS = { requests: 500, novelInstalls: 50 };

export function validate(body) {
  if (body === null || typeof body !== "object" || Array.isArray(body))
    return { ok: false, why: "body must be a JSON object" };

  const allowed = body.schema === 5 ? [...ALLOWED, ...ALLOWED_V3, ...ALLOWED_V4, ...ALLOWED_V5]
    : body.schema === 4 ? [...ALLOWED, ...ALLOWED_V3, ...ALLOWED_V4]
    : body.schema === 3 ? [...ALLOWED, ...ALLOWED_V3] : ALLOWED;
  const keyed = keyProblem(body, allowed);
  if (keyed) return { ok: false, why: keyed };

  // Schemas 1 and 2 are still accepted. 0.1.0 clients are deployed and
  // `send_ping` fails open, so an unversioned change would turn every existing
  // install's ping into a silent 400.
  if (![1, 2, 3, 4, 5].includes(body.schema))
    return { ok: false, why: "unsupported schema" };
  const who = identityProblem(body);
  if (who) return { ok: false, why: who };

  // `tickets` is a small count; `tokens` is a lifetime cumulative total and gets
  // the cumulative ceiling. Splitting them is the fix for the live defect: this
  // install sends tokens=22,700,797 against the old shared 1e7 bound, so its
  // pings have been rejected and dropped without a trace.
  if (!sane(body.tickets, MAX_COUNT))
    return { ok: false, why: "tickets must be a sane non-negative integer" };
  if (!sane(body.tokens, MAX_CUMULATIVE))
    return { ok: false, why: "tokens must be a sane non-negative integer" };

  if (body.schema === 1) {
    const v = body.commands;
    if (!sane(v, MAX_COUNT))
      return { ok: false, why: "commands must be a sane non-negative integer" };
    return { ok: true };
  }

  const cmds = body.commands;
  if (cmds === null || typeof cmds !== "object" || Array.isArray(cmds))
    return { ok: false, why: "commands must be a name-keyed object at schema 2" };
  const names = Object.keys(cmds);
  if (names.length > MAX_COMMAND_KEYS)
    return { ok: false, why: `too many command keys: ${names.length}` };
  for (const name of names) {
    if (!COMMAND_KEY.test(name))
      return { ok: false, why: `command key outside the allowed pattern: ${name}` };
    const v = cmds[name];
    if (!sane(v, MAX_COUNT))
      return { ok: false, why: `count for ${name} must be a sane non-negative integer` };
  }
  if (body.schema === 2) return { ok: true };

  // ---- schema 3 ---------------------------------------------------------
  for (const k of ["cost_micros", "cost_measured_micros"])
    if (!sane(body[k], MAX_CUMULATIVE))
      return { ok: false, why: `${k} must be a sane non-negative integer` };
  if (body.cost_measured_micros > body.cost_micros)
    return { ok: false, why: "cost_measured_micros cannot exceed cost_micros" };
  if (!sane(body.dod_amendments, MAX_COUNT))
    return { ok: false, why: "dod_amendments must be a sane non-negative integer" };

  // The closed sets. An enum, not a pattern: `blob5` is the one field that
  // carries a string chosen per row, so it is where an unvalidated value would
  // land in a dataset the dashboard queries.
  for (const [field, allowedNames, fields] of [
    ["gates", GATE_NAMES, ["ran", "caused_edit", "under_recorded", "tickets"]],
    ["tracks", TRACK_NAMES, ["closed", "stamped", "follow_ons", "defects"]],
  ]) {
    const group = body[field];
    if (group === null || typeof group !== "object" || Array.isArray(group))
      return { ok: false, why: `${field} must be a name-keyed object` };
    for (const name of Object.keys(group)) {
      if (!allowedNames.includes(name))
        return { ok: false, why: `${field} name outside the closed set: ${name}` };
      const row = group[name];
      if (row === null || typeof row !== "object" || Array.isArray(row))
        return { ok: false, why: `${field}.${name} must be an object` };
      for (const f of fields)
        if (!sane(row[f], MAX_COUNT))
          return { ok: false, why: `${field}.${name}.${f} must be a sane non-negative integer` };
    }
  }
  if (body.schema === 3) return { ok: true };

  // ---- schema 4 ---------------------------------------------------------
  for (const k of ["handbacks", "guard_denies", "blocked_markers",
                   "dod_conditions", "dod_pinned"])
    if (!sane(body[k], MAX_COUNT))
      return { ok: false, why: `${k} must be a sane non-negative integer` };
  for (const k of ["input_tokens", "output_tokens", "cache_read_tokens",
                   "cache_write_tokens"])
    if (!sane(body[k], MAX_CUMULATIVE))
      return { ok: false, why: `${k} must be a sane non-negative integer` };
  if (body.dod_pinned > body.dod_conditions)
    return { ok: false, why: "dod_pinned cannot exceed dod_conditions" };

  // `surfaces` and `models` are OPEN sets — an agent or skill name, a model id.
  // They cannot be enums, so they take the bounded key pattern instead, which a
  // ticket id, a file path or a branch name cannot satisfy.
  const surfaces = body.surfaces;
  if (surfaces === null || typeof surfaces !== "object" || Array.isArray(surfaces))
    return { ok: false, why: "surfaces must be a name-keyed object" };
  const sNames = Object.keys(surfaces);
  if (sNames.length > MAX_SURFACES)
    return { ok: false, why: `too many surface keys: ${sNames.length}` };
  for (const name of sNames) {
    if (!COMMAND_KEY.test(name))
      return { ok: false, why: `surface key outside the allowed pattern: ${name}` };
    if (!sane(surfaces[name], MAX_COUNT))
      return { ok: false, why: `count for ${name} must be a sane non-negative integer` };
  }

  const models = body.models;
  if (models === null || typeof models !== "object" || Array.isArray(models))
    return { ok: false, why: "models must be a name-keyed object" };
  const mNames = Object.keys(models);
  if (mNames.length > MAX_MODELS)
    return { ok: false, why: `too many model keys: ${mNames.length}` };
  for (const name of mNames) {
    if (!COMMAND_KEY.test(name))
      return { ok: false, why: `model key outside the allowed pattern: ${name}` };
    const row = models[name];
    if (row === null || typeof row !== "object" || Array.isArray(row))
      return { ok: false, why: `models.${name} must be an object` };
    if (!sane(row.tokens, MAX_CUMULATIVE) || !sane(row.micros, MAX_CUMULATIVE))
      return { ok: false, why: `models.${name} must carry sane counts` };
  }
  if (body.schema === 4) return { ok: true };

  // ---- schema 5 ---------------------------------------------------------
  for (const k of ALLOWED_V5)
    if (!sane(body[k], MAX_COUNT))
      return { ok: false, why: `${k} must be a sane non-negative integer` };
  if (body.qa_fails > body.qa_checks)
    return { ok: false, why: "qa_fails cannot exceed qa_checks" };
  if (body.qa_tickets_failed > body.qa_tickets)
    return { ok: false, why: "qa_tickets_failed cannot exceed qa_tickets" };
  return { ok: true };
}

// Exactly the allowed keys, no more and no fewer, for a report and an event.
function keyProblem(body, allowed) {
  const keys = Object.keys(body);
  const extra = keys.filter((k) => !allowed.includes(k));
  if (extra.length) return `unexpected keys: ${extra.join(",")}`;
  const missing = allowed.filter((k) => !keys.includes(k));
  if (missing.length) return `missing keys: ${missing.join(",")}`;
  return null;
}

// The three fields a report and an event share, checked the same way for both.
function identityProblem(body) {
  if (typeof body.install_id !== "string" || !UUID.test(body.install_id))
    return "install_id must be a uuid";
  if (typeof body.version !== "string" || !SEMVER.test(body.version))
    return "version must be semver";
  if (typeof body.os !== "string" || body.os.length > 32)
    return "os must be a short string";
  return null;
}

export function validateEvent(body) {
  const keyed = keyProblem(body, EVENT_KEYS);
  if (keyed) return { ok: false, why: keyed };
  // An enum: `event` lands in blob5, and a closed set keeps a runtime string out.
  if (!EVENT_NAMES.includes(body.event))
    return { ok: false, why: "event outside the closed set" };
  const who = identityProblem(body);
  return who ? { ok: false, why: who } : { ok: true };
}

const MAX_SURFACES = 32;
const MAX_MODELS = 16;

const sane = (v, max) => Number.isInteger(v) && v >= 0 && v <= max;

/**
 * The positional doubles for one ping: the per-command counts in CMDSET_V1
 * order, then `tickets` and `tokens`.
 *
 * ADT-263 A1b: tickets and tokens are APPENDED at 15 and 16 rather than
 * inserted, so positions 1-14 keep the meaning the `cmdset-v1` blob guard
 * versions. Rows written before this change carry 14 doubles and read back as 0
 * for the two new columns, which is the correct reading: the client sent them on
 * every ping, but the collector discarded them before the write.
 */
export function commandDoubles(body) {
  const commands = body.schema === 1
    ? CMDSET_V1.map(() => 0)
    : CMDSET_V1.map((name) => body.commands[name] || 0);
  return [...commands, body.tickets, body.tokens];
}

/**
 * ADT-284 — every data point one ping writes.
 *
 * The positional layout above spends a fixed double per command, which is why
 * `cmdset-v1` exists: it versions a position map that this file and
 * tools/adt_usage_report.py have to move in lockstep. That does not extend. One
 * data point holds twenty doubles and sixteen were already spent, while cost,
 * the gate metrics and the track metrics need roughly forty more.
 *
 * So the dimension moves OUT of the position and INTO a blob. One point per
 * (install, kind, dimension value), and a new command, gate or track is a new
 * blob value rather than a new column and a new schema version. Worst case is 21
 * points per ping (1 install + 14 commands + 2 gates + 4 tracks) against a
 * documented ceiling of 250 per Worker invocation.
 *
 * SCHEMA 1 AND 2 KEEP THEIR SINGLE cmdset-v1 ROW, unchanged. Deployed clients
 * still send them, the old queries still read them, and Analytics Engine retires
 * them on its own after three months of retention — so there is no migration and
 * no backfill, only two readers for one quarter.
 */
export function dataPoints(body) {
  if (body.schema < 3)
    return [{ blobs: [body.version, body.os, "cmdset-v1"],
              doubles: commandDoubles(body) }];

  const envelope = (kind, dim) => [body.version, body.os, "totals-v1", kind, dim];
  // The install point had 5 doubles of 20. Schema 4 spends 9 more of the
  // spare 15, so none of the additions below costs a data point.
  const points = [{
    blobs: envelope("install", ""),
    doubles: [body.tickets, body.tokens, body.cost_micros,
              body.cost_measured_micros, body.dod_amendments,
              ...(body.schema >= 4
                ? [body.handbacks, body.guard_denies, body.blocked_markers,
                   body.dod_conditions, body.dod_pinned,
                   body.input_tokens, body.output_tokens,
                   body.cache_read_tokens, body.cache_write_tokens] : []),
              // Schema 5: doubles 15-18. A schema 4 row reads back 0 for them.
              ...(body.schema >= 5
                ? [body.qa_checks, body.qa_fails, body.qa_tickets,
                   body.qa_tickets_failed] : [])],
  }];
  // ADT-378: double 19 is the schema the report was sent with. Every figure an
  // older schema did not send reads back as 0, so without it the page could not
  // tell "not reported" from zero. The gap is padded so the schema always sits
  // at 19, and the client already sends `schema`, so nothing new is collected.
  while (points[0].doubles.length < 18) points[0].doubles.push(0);
  points[0].doubles.push(body.schema);
  // A zero-count command is omitted rather than written as a zero row: the
  // reader sums totals per install, and an absent row and a zero row mean the
  // same thing there, so the row is pure cost.
  for (const [name, n] of Object.entries(body.commands))
    if (n > 0) points.push({ blobs: envelope("command", name), doubles: [n] });
  for (const [name, g] of Object.entries(body.gates))
    points.push({ blobs: envelope("gate", name),
                  doubles: [g.ran, g.caused_edit, g.under_recorded, g.tickets] });
  for (const [name, t] of Object.entries(body.tracks))
    points.push({ blobs: envelope("track", name),
                  doubles: [t.closed, t.stamped, t.follow_ons, t.defects] });
  if (body.schema >= 4) {
    for (const [name, n] of Object.entries(body.surfaces))
      if (n > 0) points.push({ blobs: envelope("surface", name), doubles: [n] });
    for (const [name, m] of Object.entries(body.models))
      points.push({ blobs: envelope("model", name),
                    doubles: [m.tokens, m.micros] });
  }
  return points;
}

export function utcDay(now) {
  return new Date(now).toISOString().slice(0, 10);
}

/**
 * The Durable Object holding one IP's two daily budgets.
 *
 * WHY A DURABLE OBJECT AND NOT KV. Bounding REQUESTS needs an increment per
 * request, and a KV counter increment is one put per request — 20k/day at 20k
 * installs, the exact defect this ticket exists to fix. A budget that
 * incremented only on novel ids would not bound at all: register B ids once,
 * then replay them forever for unlimited AE writes at zero KV cost. A DO gives
 * an atomic counter, is on the free plan (as a SQLite-backed class), and costs
 * no KV quota.
 *
 * ONE object per IP, not one per IP-per-day, with the day stamp held inside and
 * reset on rollover — object count is then bounded by distinct IPs rather than
 * growing with IPs x days, which is the same unbounded-namespace trap the
 * `install:` prefix carries.
 */
export class IpBudget {
  constructor(state) {
    this.state = state;
  }

  async fetch(request) {
    const params = new URL(request.url).searchParams;
    const kind = params.get("kind") === "novelInstalls" ? "novelInstalls" : "requests";
    const now = Number(params.get("now")) || Date.now();
    const today = utcDay(now);

    let rec = await this.state.storage.get("budget");
    if (!rec || rec.day !== today)
      rec = { day: today, requests: 0, novelInstalls: 0 };

    rec[kind] += 1;
    await this.state.storage.put("budget", rec);

    return new Response(
      JSON.stringify({ ok: rec[kind] <= BUDGETS[kind], count: rec[kind] }),
      { headers: { "Content-Type": "application/json" } });
  }
}

async function spend(env, ip, kind, now) {
  const stub = env.IP_BUDGET.get(env.IP_BUDGET.idFromName(ip));
  const res = await stub.fetch(`https://ip-budget/spend?kind=${kind}&now=${now}`);
  return res.json();
}

/**
 * The whole request path. Cost per request class, which is what the
 * put-accounting cases in test-collector.mjs assert:
 *
 *   rejected (bad method / json / payload) -> 0 KV puts, 0 AE writes
 *   over the request budget                -> 0 KV puts, 0 AE writes
 *   returning install                      -> 0 KV puts, 1 AE write
 *   new install                            -> 1 KV put,  1 AE write
 */
export async function handle(request, env, now = Date.now()) {
  // ADT-263 A1a: the read path branches before the POST-only guard. Everything
  // below this point is the write path and is unchanged.
  if (request.method === "GET" && new URL(request.url).pathname === "/dashboard")
    return dashboard(env, request, now);

  if (request.method !== "POST")
    return new Response("POST only", { status: 405 });

  let body;
  try { body = await request.json(); }
  catch { return new Response("invalid json", { status: 400 }); }

  // ADT-391: an uninstall event has its own shape and its own one-point write
  // below, and shares the request budget with reports.
  const isEvent = body !== null && typeof body === "object" && !Array.isArray(body)
    && "event" in body;
  const v = isEvent ? validateEvent(body) : validate(body);
  if (!v.ok) return new Response(v.why, { status: 400 });

  const ip = request.headers.get("CF-Connecting-IP") || "unknown";

  // Spend the request budget BEFORE any write, so a rejection costs nothing.
  // The limiter this replaces wrote its counter first and checked second, which
  // made saying "no" cost two-thirds of what saying "yes" cost.
  const reqBudget = await spend(env, ip, "requests", now);
  if (!reqBudget.ok) return new Response("rate limited", { status: 429 });

  // Usage: one data point, no KV quota. The budget above covers this write as
  // well as the put — the endpoint is public and unauthenticated, and poisoned
  // adoption stats are recorded as co-equal with denial-of-wallet, so gating
  // only the put would leave every usage dimension writable at request volume.
  // One write per data point. `dataPoints()` returns a single cmdset-v1 row for
  // schema 1 and 2, so this loop is the old behaviour unchanged for every
  // deployed client, and the totals-v1 set for schema 3.
  // ADT-391: an event is one point in the totals-v1 envelope under its own kind,
  // so every dashboard query (each filters on a kind) skips it. It never
  // registers an install: an uninstall is the last thing an install says, so a
  // novel id here is one that never reported, and counting it would add an
  // install on its way out.
  if (isEvent) {
    env.USAGE.writeDataPoint({ indexes: [body.install_id],
      blobs: [body.version, body.os, "totals-v1", "event", body.event], doubles: [1] });
    return new Response("ok", { status: 202 });
  }

  for (const point of dataPoints(body))
    env.USAGE.writeDataPoint({ indexes: [body.install_id], ...point });

  // Identity: read first. A returning install — the steady state at any user
  // count — costs no put at all.
  //
  // THE WHOLE BLOCK IS GUARDED, and the response deliberately does not depend
  // on it. By the time we reach here the AE data point is already written, so
  // the ping HAS been recorded and 202 is the truthful answer whatever KV does
  // next. Unguarded, a KV quota error escaped the handler and Cloudflare
  // returned 500 — a lie about what happened, and the worst possible lie on the
  // day it fires: the put quota is exhausted precisely when traffic is highest,
  // so a launch day would show a wall of 500s. The honest reading of that is
  // "the collector is broken", and it would send the operator debugging a
  // healthy system on its busiest day.
  //
  // Losing the identity write is the DESIGNED degradation and it self-heals: no
  // key was written, so the install is still novel tomorrow and is registered
  // then. Under-counting installs for a day is the conservative direction;
  // discarding usage already accepted is not.
  const key = `install:${body.install_id}`;
  try {
    const known = await env.STATS.get(key);
    if (!known) {
      const newBudget = await spend(env, ip, "novelInstalls", now);
      // Over the novel-install budget, same story: usage lands, identity defers.
      if (newBudget.ok)
        // `first_seen` is written BOTH as the value and as KV metadata. The
        // dashboard needs it for every install at once, and `list()` returns
        // metadata inline while a value needs a `get()` per key. A subrequest is
        // any call to KV, and the free plan allows 50 per request, so reading
        // the value per install capped the dashboard at ~37 installs.
        await env.STATS.put(key, JSON.stringify({ first_seen: utcDay(now) }),
                            { metadata: { first_seen: utcDay(now) } });
    }
  } catch (err) {
    // Logged, never swallowed. A deferred identity must stay distinguishable
    // from a registered one, or this fix trades a loud wrong signal for a
    // silent one and nobody can tell a quota-exhausted day from a quiet one.
    console.log(`identity deferred for one install: ${err && err.message}`);
  }

  return new Response("ok", { status: 202 });
}


// --- ADT-263: the dashboard read path --------------------------------------
//
// The write path above stores one data point per ping. This reads them back.
// Nothing here writes: no KV put, no AE write, no cache. The page is current
// because the queries run when it loads.

const WINDOW_DAYS = 30;
// The DATASET NAME, not the binding name. wrangler.toml binds USAGE ->
// adt_usage_v2; writeDataPoint goes through the binding, SQL goes through the name.
// Must match `dataset` in collector/wrangler.toml and DATASET in
// tools/adt_usage_report.py.
const DATASET = "adt_usage_v2";
const AE_SQL = (account) =>
  `https://api.cloudflare.com/client/v4/accounts/${account}/analytics_engine/sql`;

// tickets and tokens sit after the command doubles (see commandDoubles), so at
// 15 and 16 today. DERIVED, not written as 15 and 16: adt_usage_report.py
// derives its pair from len(CMDSET_V1), and a hardcoded pair here would keep
// pointing at the old columns the day a command is added while the Python side
// moved — the reader/writer drift the cmdset-v1 blob guard does NOT cover,
// because that guard versions positions 1-14 only.
const D_TICKETS = CMDSET_V1.length + 1;
const D_TOKENS = CMDSET_V1.length + 2;

const WHERE = `FROM ${DATASET}
WHERE timestamp > NOW() - INTERVAL '${WINDOW_DAYS}' DAY
  AND blob3 = 'cmdset-v1'`;

// ADT-284: the totals-v1 rows. One point per (install, kind, dimension), so a
// row set is selected by kind rather than by column position.
const V1 = (kind) => `FROM ${DATASET}
WHERE timestamp > NOW() - INTERVAL '${WINDOW_DAYS}' DAY
  AND blob3 = 'totals-v1' AND blob4 = '${kind}'`;

/**
 * TWO READINGS OF ONE NUMBER, AND THEY AGGREGATE DIFFERENTLY.
 *
 * Every value on a totals-v1 row is a LIFETIME CUMULATIVE TOTAL, re-sent whole
 * on every ping. That is what the old page got wrong: it applied
 * `SUM(_sample_interval * doubleN)` to those snapshots, so an install that had
 * run `plan` three times contributed 3 on Monday, 3 on Tuesday and 3 on
 * Wednesday and the panel read 9.
 *
 *   where things stand  ->  argMax(value, timestamp) per install, then summed.
 *   what happened here  ->  MAX(value) - MIN(value) per install, then summed.
 *
 * Neither is a SUM over rows, and `_sample_interval` weighting is deliberately
 * absent from both. That weighting corrects a SUM over per-row events, where a
 * surviving row stands for N dropped siblings. These are point reads of a
 * cumulative series: multiplying a lifetime total by a sample weight would
 * invent tokens, not recover them. The legacy cmdset-v1 queries below keep the
 * weighting, because they really are summing per-ping counts.
 *
 * `MAX - MIN` undercounts an install whose earliest surviving row in the window
 * is its first-ever ping, because the baseline is that row rather than zero.
 * `listInstalls` reads `first_seen` from KV so the page can say so.
 */
export function dashboardQueries(install) {
  // ADT-378: narrow every query to one install. The id is checked here as well
  // as where it is read from the address, because it is written into the SQL.
  if (install != null && !UUID.test(install))
    throw new Error("dashboardQueries: install must be a uuid");
  const only = install ? `\n  AND index1 = '${install.toLowerCase()}'` : "";
  const WHERE_ = WHERE + only;
  const V1_ = (kind) => V1(kind) + only;
  const perInstall = (cols) => cols
    .map(([alias, d]) => `    argMax(double${d}, timestamp) AS ${alias},\n`
                       + `    MAX(double${d}) - MIN(double${d}) AS ${alias}_win`)
    .join(",\n");
  const rollup = (cols) => cols
    .map(([a]) => `SUM(${a}) AS ${a}, SUM(${a}_win) AS ${a}_win`).join(",\n  ");

  // Doubles 6-14 are schema 4 and 15-18 are schema 5. A schema 3 row carries
  // five doubles and reads back as 0 for the rest, which is the correct reading:
  // it never sent them. Doubles 9-14 (dod_conditions, dod_pinned and the token
  // split) are still stored, but no panel reads them, so the query leaves them out.
  const INSTALL_COLS = [["tickets", 1], ["tokens", 2], ["cost", 3],
                        ["cost_measured", 4], ["dod_amendments", 5],
                        ["handbacks", 6], ["guard_denies", 7],
                        ["blocked_markers", 8], ["qa_checks", 15],
                        ["qa_fails", 16], ["qa_tickets", 17],
                        ["qa_tickets_failed", 18]];

  return {
    // --- legacy cmdset-v1 rows ---------------------------------------------
    // Read with the SAME rule as totals-v1, because they are the same KIND of
    // value: `command_counts()` has always recounted the whole log, so every
    // cmdset-v1 double is a lifetime cumulative total too. Summing them is the
    // bug this ticket exists to fix, and it is not confined to the new layout —
    // correcting only the new rows would leave the page wrong for the three
    // months of old rows retention still holds, and wrong forever for an install
    // that never upgrades.
    //
    // `pings` and `installs_window` stay weighted: those count rows, and a row
    // really is one ping under cmdset-v1.
    totals: `SELECT
  SUM(pings) AS pings,
  COUNT() AS installs_window,
  SUM(tickets) AS tickets, SUM(tickets_win) AS tickets_win,
  SUM(tokens) AS tokens, SUM(tokens_win) AS tokens_win,
  ${CMDSET_V1.map((n) => { const a = n.replace(/-/g, "_");
      return `SUM(${a}) AS ${a}, SUM(${a}_win) AS ${a}_win`; }).join(",\n  ")}
FROM (
  SELECT index1 AS install,
    SUM(_sample_interval) AS pings,
${perInstall([["tickets", D_TICKETS], ["tokens", D_TOKENS],
              ...CMDSET_V1.map((n, i) => [n.replace(/-/g, "_"), i + 1])])}
  ${WHERE_}
  GROUP BY index1
)`,
    versions: `SELECT blob1 AS name, SUM(_sample_interval) AS pings
${WHERE_}
GROUP BY blob1 ORDER BY pings DESC`,
    systems: `SELECT blob2 AS name, SUM(_sample_interval) AS pings
${WHERE_}
GROUP BY blob2 ORDER BY pings DESC`,
    installs: `SELECT index1 AS install,
  SUM(_sample_interval) AS pings,
${perInstall([["tickets", D_TICKETS], ["tokens", D_TOKENS]])}
${WHERE_}
GROUP BY index1`,

    // --- totals-v1 -----------------------------------------------------------
    // One `install` row per ping, so this is also where the ping count and the
    // version/os dimensions come from for schema 3 clients. Counting every row
    // would multiply a ping by up to 21.
    v1Totals: `SELECT
  ${rollup(INSTALL_COLS)},
  SUM(pings) AS pings,
  COUNT() AS installs_window
FROM (
  SELECT index1 AS install,
    SUM(_sample_interval) AS pings,
${perInstall(INSTALL_COLS)}
  ${V1_("install")}
  GROUP BY index1
)`,
    v1Installs: `SELECT index1 AS install,
  SUM(_sample_interval) AS pings,
${perInstall(INSTALL_COLS)},
    argMax(double19, timestamp) AS schema_seen
${V1_("install")}
GROUP BY index1`,
    v1Versions: `SELECT blob1 AS name, SUM(_sample_interval) AS pings
${V1_("install")}
GROUP BY blob1 ORDER BY pings DESC`,
    v1Systems: `SELECT blob2 AS name, SUM(_sample_interval) AS pings
${V1_("install")}
GROUP BY blob2 ORDER BY pings DESC`,
    v1Commands: `SELECT name, SUM(total) AS total, SUM(total_win) AS total_win
FROM (
  SELECT blob5 AS name, index1,
${perInstall([["total", 1]])}
  ${V1_("command")}
  GROUP BY blob5, index1
)
GROUP BY name ORDER BY total DESC`,
    // ADT-378: the four per-install queries return ONE row per install, so the
    // result stays at the install count however many commands, gates, tracks or
    // surfaces there are. The Analytics Engine SQL reference states no row limit.
    v1CommandsByInstall: `SELECT install, SUM(total) AS runs, COUNT() AS used
FROM (
  SELECT index1 AS install, blob5,
    argMax(double1, timestamp) AS total
  ${V1_("command")}
  GROUP BY index1, blob5
)
GROUP BY install`,
    v1GatesByInstall: `SELECT install, SUM(caused_edit) AS caused_edit, SUM(tickets) AS tickets
FROM (
  SELECT index1 AS install, blob5,
    argMax(double2, timestamp) AS caused_edit,
    argMax(double4, timestamp) AS tickets
  ${V1_("gate")}
  GROUP BY index1, blob5
)
GROUP BY install`,
    v1TracksByInstall: `SELECT install, SUM(closed) AS closed, SUM(defects) AS defects
FROM (
  SELECT index1 AS install, blob5,
    argMax(double1, timestamp) AS closed,
    argMax(double4, timestamp) AS defects
  ${V1_("track")}
  GROUP BY index1, blob5
)
GROUP BY install`,
    v1ReviewersByInstall: `SELECT install, SUM(total) AS reviews
FROM (
  SELECT index1 AS install, blob5,
    argMax(double1, timestamp) AS total
  ${V1_("surface")}
  AND blob5 LIKE 'adt-%-reviewer'
  GROUP BY index1, blob5
)
GROUP BY install`,
    v1Gates: `SELECT name,
  ${rollup([["ran", 1], ["caused_edit", 2], ["under_recorded", 3], ["tickets", 4]])}
FROM (
  SELECT blob5 AS name, index1,
${perInstall([["ran", 1], ["caused_edit", 2], ["under_recorded", 3], ["tickets", 4]])}
  ${V1_("gate")}
  GROUP BY blob5, index1
)
GROUP BY name ORDER BY name`,
    v1Surfaces: `SELECT name, SUM(total) AS total, SUM(total_win) AS total_win
FROM (
  SELECT blob5 AS name, index1,
${perInstall([["total", 1]])}
  ${V1_("surface")}
  GROUP BY blob5, index1
)
GROUP BY name ORDER BY total DESC`,
    v1Tracks: `SELECT name,
  ${rollup([["closed", 1], ["stamped", 2], ["follow_ons", 3], ["defects", 4],
            ["days_sum", 5], ["days_n", 6]])}
FROM (
  SELECT blob5 AS name, index1,
${perInstall([["closed", 1], ["stamped", 2], ["follow_ons", 3], ["defects", 4],
              ["days_sum", 5], ["days_n", 6]])}
  ${V1_("track")}
  GROUP BY blob5, index1
)
GROUP BY name ORDER BY name`,

    // ADT-391: one row per (install, version, row kind) with the first and last
    // time that set arrived. A report is a cmdset-v1 row or a totals-v1
    // `install` row; the other kind is the uninstall event. `lifecycle()` below
    // turns these into updates per version and a status per install.
    lifecycle: `SELECT index1 AS install, blob1 AS version, blob4 AS kind,
  toUnixTimestamp(MIN(timestamp)) AS first_at, toUnixTimestamp(MAX(timestamp)) AS last_at
FROM ${DATASET}
WHERE timestamp > NOW() - INTERVAL '${WINDOW_DAYS}' DAY
  AND (blob3 = 'cmdset-v1' OR blob4 = 'install' OR (blob4 = 'event' AND blob5 = 'uninstall'))${only}
GROUP BY index1, blob1, blob4`,
  };
}

// ADT-391: an install whose last daily report is older than this, with no
// uninstall after it, has gone quiet. Seven missed daily reports is well past a
// weekend or a machine left off.
export const QUIET_DAYS = 7;

// The query returns `toUnixTimestamp` seconds, so no date string is parsed and
// the machine's time zone cannot move a day. Number() also takes a JSON string.
const toMs = (v) => Number(v) * 1000;

const semverLess = (a, b) => {
  const x = String(a).split(".").map(Number), y = String(b).split(".").map(Number);
  for (let i = 0; i < 3; i++) if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) < (y[i] || 0);
  return false;
};

/**
 * ADT-391: updates per version and a status per install, from `lifecycle` rows.
 *
 * An install UPDATED to version X when it reported a lower version before its
 * first report on X, inside the window. The day it moved is that first report.
 * An install with no report in the window is skipped, which also drops any
 * uninstall event from an id that never reported: the endpoint is public, and a
 * random uuid must not add an uninstall. Each remaining install is uninstalled
 * (its latest uninstall is at or after its latest report: the event is sent once
 * the watcher has stopped, so a same-second pair is an uninstall), reporting (a report in
 * the last QUIET_DAYS), or gone quiet.
 */
export function lifecycle(rows, now) {
  const byInstall = new Map();
  for (const r of rows || []) {
    const i = byInstall.get(r.install) || { versions: new Map(), lastReport: -Infinity, lastUninstall: -Infinity };
    const first = toMs(r.first_at), last = toMs(r.last_at);
    if (r.kind === "event") {
      i.lastUninstall = Math.max(i.lastUninstall, last);
    } else {
      // A version can arrive as both a cmdset-v1 and a totals-v1 row set.
      const v = i.versions.get(r.version) || { first: Infinity };
      v.first = Math.min(v.first, first);
      i.versions.set(r.version, v);
      i.lastReport = Math.max(i.lastReport, last);
    }
    byInstall.set(r.install, i);
  }

  const updates = new Map();
  const status = { uninstalled: 0, quiet: 0, reporting: 0 };
  for (const i of byInstall.values()) {
    if (!i.versions.size) continue;
    if (i.lastUninstall >= i.lastReport) status.uninstalled++;
    else if (now - i.lastReport <= QUIET_DAYS * 86_400_000) status.reporting++;
    else status.quiet++;
    const seen = [...i.versions.entries()];
    for (const [version, { first }] of seen) {
      if (!seen.some(([other, o]) => semverLess(other, version) && o.first < first)) continue;
      const u = updates.get(version) || { version, installs: 0, first: Infinity, latest: -Infinity };
      u.installs++;
      u.first = Math.min(u.first, first);
      u.latest = Math.max(u.latest, first);
      updates.set(version, u);
    }
  }
  return {
    updates: [...updates.values()].sort((a, b) => semverLess(a.version, b.version) ? 1 : -1)
      .map((u) => ({ ...u, first: utcDay(u.first), latest: utcDay(u.latest) })),
    status,
  };
}

// A window figure is MAX - MIN of a total that is meant to be non-decreasing.
// Nothing in this repo truncates `usage.log` or the cost ledger, but a truncated
// one would make the difference negative and a negative bar is worse than a
// missing one. Clamped at the boundary where the numbers enter the page.
export const windowValue = (v) => Math.max(0, Number(v) || 0);

/**
 * One query, and it NEVER throws.
 *
 * The page runs a dozen queries. Throwing meant any single rejected query took
 * the whole page to a 502 — twelve panels blanked because one of them asked for
 * something Analytics Engine did not like.
 *
 * And the message was discarded. `analytics engine returned 422` says a query
 * was rejected without saying WHICH of the twelve or WHY, while AE's response
 * body says both. That turned a one-line fix into a round trip through whoever
 * holds the token.
 *
 * Returns `{rows}` or `{error}`. The caller decides what an absent panel means.
 */
async function runSql(env, sql) {
  let res;
  try {
    res = await fetch(AE_SQL(env.AE_ACCOUNT_ID), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.AE_TOKEN}`,
        "Content-Type": "text/plain",
      },
      body: sql,
    });
  } catch (err) {
    return { rows: [], error: `unreachable: ${err && err.message}` };
  }
  if (!res.ok) {
    // AE explains what it rejected. Carry that explanation to the page, capped
    // so a long upstream body cannot dominate the render, and with the query's
    // first line so the panel names itself.
    let why = "";
    try { why = (await res.text()).trim().replace(/\s+/g, " ").slice(0, 300); }
    catch { /* a body we cannot read is still a failure worth naming */ }
    return { rows: [], error: `${res.status}${why ? ": " + why : ""}` };
  }
  try {
    const body = await res.json();
    return { rows: body.data || [] };
  } catch (err) {
    return { rows: [], error: `unparseable response: ${err && err.message}` };
  }
}

/**
 * Three ways this route fails, and none of them may return a stack trace: the
 * path is public and guessable, and the Worker hostname is already disclosed.
 *
 *   AE_TOKEN unset       -> 503, the route is not configured
 *   AE_ACCOUNT_ID unset  -> 503, same class
 *   the AE call fails    -> 502, the upstream is the problem, not us
 *
 * The 502/503 split is not cosmetic: 503 means "this deployment is missing a
 * secret", which the operator fixes with wrangler; 502 means the secrets are
 * fine and Cloudflare did not answer.
 */
/**
 * The page's settings, read from its address and checked before use.
 *
 * Only `install` ever reaches a query, and only when it matches the UUID pattern
 * the collector already validates install ids with. Every other value is checked
 * against a fixed set or pattern and falls back to its default, so a crafted
 * address changes nothing but the view.
 */
const DEFAULT_SETTINGS = { install: null, sort: "tickets", dir: "desc", page: 1, q: "", dormant: false, min: 10 };

export function dashboardSettings(url) {
  const p = new URL(url).searchParams;
  const raw = (k) => p.get(k) || "";
  const install = UUID.test(raw("install")) ? raw("install").toLowerCase() : null;
  const sort = COMPARE_KEYS.includes(raw("sort")) ? raw("sort") : DEFAULT_SETTINGS.sort;
  const dir = raw("dir") === "asc" ? "asc" : "desc";
  const page = /^[1-9][0-9]{0,4}$/.test(raw("page")) ? Number(raw("page")) : 1;
  const qRaw = raw("q").toLowerCase();
  const q = /^[0-9a-f-]{1,36}$/.test(qRaw) ? qRaw : "";
  const dormant = raw("dormant") === "show";
  const min = [10, 25, 50].includes(Number(raw("min"))) ? Number(raw("min")) : DEFAULT_SETTINGS.min;
  return { install, sort, dir, page, q, dormant, min };
}

// The unnarrowed set feeds the all-installs figures and the compare table. The
// narrowed set feeds the top of a one-install page. The per command, versions and
// operating systems panels have no all-installs column, so a one-install page
// reads them from the narrowed set only.
const ALL_QUERIES = ["totals", "versions", "systems", "installs", "v1Totals",
                     "v1Installs", "v1Commands", "v1CommandsByInstall", "v1Gates",
                     "v1Tracks", "v1Versions", "v1Systems", "v1Surfaces",
                     "v1GatesByInstall", "v1TracksByInstall", "v1ReviewersByInstall",
                     "lifecycle"];
const NARROWED_ONLY = ["versions", "systems", "v1Commands", "v1Versions", "v1Systems"];
// ADT-391: queries that feed an all-installs panel and nothing on a one-install
// page. NARROWED_ONLY swaps a query for its narrowed copy; this drops it, so a
// one-install page does not spend a subrequest on a panel it does not show.
const ALL_INSTALLS_ONLY = ["lifecycle"];
const ONE_INSTALL_QUERIES = ["totals", "v1Totals", "v1Gates", "v1Tracks", "v1Surfaces",
                             ...NARROWED_ONLY];

async function dashboard(env, request, now) {
  if (!env.AE_TOKEN)
    return text("dashboard is not configured: AE_TOKEN is unset", 503);
  if (!env.AE_ACCOUNT_ID)
    return text("dashboard is not configured: AE_ACCOUNT_ID is unset", 503);

  const settings = dashboardSettings(request.url);
  const all = dashboardQueries();
  const jobs = ALL_QUERIES
    .filter((n) => !settings.install || (!NARROWED_ONLY.includes(n) && !ALL_INSTALLS_ONLY.includes(n)))
    .map((n) => [n, all[n]]);
  if (settings.install) {
    const one = dashboardQueries(settings.install);
    for (const n of ONE_INSTALL_QUERIES) jobs.push([`one:${n}`, one[n]]);
  }
  // Every query runs independently and none of them throws. A rejected query
  // costs its own panel, not the page: before this, one 422 among twelve
  // blanked the whole dashboard and reported a bare status code.
  const results = await Promise.all(jobs.map(([, sql]) => runSql(env, sql)));
  const rows = {}, errors = {};
  jobs.forEach(([n], i) => {
    rows[n] = results[i].rows;
    if (results[i].error) errors[n] = results[i].error;
  });

  let known;
  try {
    known = await listInstalls(env);
  } catch (err) {
    // KV is the install roster. Losing it degrades the compare table to what
    // Analytics Engine knows, which is the same shape as a pruned namespace.
    known = [];
    errors.installs = `${errors.installs ? errors.installs + "; " : ""}kv: ${err && err.message}`;
  }

  // Only a TOTAL failure is an upstream outage. Anything less is a page with a
  // named gap in it, which is more useful than a status code.
  if (jobs.every(([n]) => errors[n]))
    return text(`upstream query failed: ${errors[jobs[0][0]]}`, 502);

  const pick = (prefix) => Object.fromEntries(Object.entries(rows)
    .filter(([n]) => prefix ? n.startsWith(prefix) : !n.includes(":"))
    .map(([n, v]) => [prefix ? n.slice(prefix.length) : n, v]));
  return new Response(render({
    settings, all: pick(""), one: settings.install ? pick("one:") : null,
    known, errors, queryCount: jobs.length, now,
  }), {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}

const text = (body, status) =>
  new Response(body + "\n", { status, headers: { "Content-Type": "text/plain" } });

/**
 * Every install that has ever pinged, from KV.
 *
 * ADT-263 A1f. This is the EXACT set and the exact count: `install:<uuid>` is
 * written once when an install is first seen and never deleted, so it does not
 * depend on the 30-day window the AE queries use. That matters twice over. The
 * headline install count is this length rather than the AE distinct-count, so
 * the page does not show an estimate while holding the exact number; and an
 * install that stopped pinging before the window still gets a row, so a dormant
 * install is distinguishable from one that never existed.
 */
async function listInstalls(env) {
  const out = [];
  let cursor;
  do {
    const page = await env.STATS.list({ prefix: "install:", cursor });
    for (const k of page.keys) {
      const id = k.name.slice("install:".length);
      // ADT-284: `first_seen` comes from KV METADATA, which `list()` returns
      // inline. Reading it from the value instead cost one `get()` per install,
      // and a KV call is a subrequest: 50 per request on the free plan, so the
      // dashboard broke at about 37 installs. Metadata makes the whole roster
      // one subrequest per 1000 keys however many installs there are.
      //
      // A record written before this change has no metadata. Its `first_seen`
      // reads as undefined, the row carries no lower-bound mark, and nothing
      // else about it changes — the same as an install Analytics Engine knows
      // and KV does not.
      const first_seen = k.metadata && k.metadata.first_seen;
      out.push({ id, first_seen });
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return out;
}


// --- ADT-263 A1e: the render ------------------------------------------------
//
// One self-contained HTML string. No script, no external asset, no build step:
// a Worker returns one response, and the data is already in the HTML by the
// time the browser sees it, which is what makes the page current on load.
//
// The tokens and panel markup are ported from tools/build_dashboard.py, ADT's
// other rendered dashboard, so the two look like the same system. Ported rather
// than shared because the renderers are in different languages against
// different data.

const CSS = `
:root{--fg:#111827;--muted:#6b7280;--line:#e5e7eb;--bar:#1e40af;--bg:#fafafa;--caught:#15803d;--escaped:#b91c1c}
*{box-sizing:border-box}
body{margin:0;padding:24px;font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:var(--fg);background:var(--bg)}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 20px}
/* Rows are spaced by each panel's own bottom margin, so a grid that wraps to a second row keeps the same 16px as the space between grids. */
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:0 16px}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
.panel{background:#fff;border:1px solid var(--line);border-radius:8px;padding:14px;margin-bottom:16px}
.panel h2{font-size:13px;margin:0;text-transform:uppercase;letter-spacing:.04em}
.cov{color:var(--muted);font-size:12px;margin:2px 0 10px}
.tbl{display:grid;column-gap:8px;overflow-x:auto}
.row{display:grid;grid-column:1/-1;grid-template-columns:subgrid;row-gap:0;align-items:center;margin:3px 0}
.k{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar{background:var(--line);height:9px;border-radius:5px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--bar)}
.v{font-size:12px;text-align:right;font-variant-numeric:tabular-nums}
.grid .panel{min-width:0}
.grid.two{grid-template-columns:repeat(2,1fr)}
.grid .panel.wide{grid-column:1/-1}
/* ADT-378: on a one-install page gates gains an all-installs column and spans two columns beside tickets and tokens; tracks takes the whole row. */
.grid .panel.span2{grid-column:span 2}
@media(max-width:900px){.grid .panel.span2{grid-column:auto}}
.row.head .v,.row.head .k{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.row.head{margin-bottom:4px;align-items:end}
.row.head .bar{background:none}
details.ex{margin-top:10px;border-top:1px solid var(--line);padding-top:8px}
details.ex summary{color:var(--muted);font-size:12px;cursor:pointer;padding:4px 0}
details.ex p{color:var(--muted);font-size:12px;margin:8px 0 0}
details.ex.page{background:#fff;border:1px solid var(--line);border-radius:8px;padding:14px;margin-top:16px}
details.ex.tells{border-top:0;padding-top:2px}
details.ex.tells summary{color:var(--bar)}
.tbl.cmd{grid-template-columns:minmax(6em,max-content) minmax(3em,1fr) auto auto}
.tbl.one{grid-template-columns:minmax(6em,max-content) minmax(3em,1fr) auto}
.tbl.nb{grid-template-columns:minmax(6em,1fr) auto auto}
.tbl.p2{grid-template-columns:minmax(6em,1fr) auto auto}
.tbl.p3{grid-template-columns:minmax(6em,1fr) auto auto auto}
.tbl.g4{grid-template-columns:minmax(8em,1fr) auto auto auto auto auto}
.tbl.g5{grid-template-columns:minmax(6em,1fr) auto auto auto auto auto auto}
.tbl.nba{grid-template-columns:minmax(6em,1fr) auto auto auto}
.tbl.p2a{grid-template-columns:minmax(6em,1fr) auto auto auto}
.tbl.p3a{grid-template-columns:minmax(6em,1fr) auto auto auto auto}
.tbl.g4a{grid-template-columns:minmax(8em,1fr) auto auto auto auto auto auto}
.tbl.g5a{grid-template-columns:minmax(6em,1fr) auto auto auto auto auto auto auto}
.tbl.nb .k,.tbl.p2 .k,.tbl.p3 .k,.tbl.g4 .k,.tbl.g5 .k,.tbl.nba .k,.tbl.p2a .k,.tbl.p3a .k,.tbl.g4a .k,.tbl.g5a .k{white-space:normal}
.row .v.all{color:var(--muted)}
.row .v.esc{color:var(--escaped)}
.row .v.rate{color:var(--muted)}
.panel.warn{border-color:#b45309}
.panel.warn h2{color:#b45309}
.row .v.err{text-align:left;font-size:11px;color:#b45309;word-break:break-word}
.panel.warn .tbl.one{grid-template-columns:minmax(6em,max-content) 0 minmax(0,1fr)}
.row.soft .k,.row.soft .v{color:var(--muted)}
@media(max-width:900px){.grid.two{grid-template-columns:1fr}}
.exact{color:var(--fg);font-weight:600}
.empty{color:var(--muted);font-size:12px;margin:8px 0 0}
.flt{position:absolute;width:1px;height:1px;opacity:0}
.flt-btn{position:relative;display:inline-block;margin:0 0 8px;padding:2px 10px;border:1px solid var(--line);border-radius:999px;font-size:12px;color:var(--muted);cursor:pointer;user-select:none}
.flt-btn::before{content:"";position:absolute;inset:-12px -6px}
.flt:checked+.flt-btn{background:var(--bar);border-color:var(--bar);color:var(--bg)}
.flt:focus-visible+.flt-btn{outline:2px solid var(--bar);outline-offset:2px}
.flt:checked~.tbl .row:not(.head):not(.adt-rev){display:none}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.scoped{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center;background:#fff;border:1px solid var(--bar);border-radius:8px;padding:10px 14px;margin-bottom:16px;font-size:13px}
.scoped a,.cmp a,.pager a{color:var(--bar)}
.scoped a:focus-visible,.cmp a:focus-visible,.pager a:focus-visible,.cmp-tools :focus-visible{outline:2px solid var(--bar);outline-offset:2px}
.helped{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
@media(max-width:1000px){.helped{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:520px){.helped{grid-template-columns:1fr}}
.tile{display:grid;gap:2px;background:#fff;border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.tile .kind{font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.tile .n{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums;line-height:1.15}
.tile .what{font-size:13px}
.tile .of{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.cmp-tools{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;font-size:12px;margin:0 0 10px}
.cmp-tools label{display:inline-flex;gap:6px;align-items:center;color:var(--muted)}
.cmp-tools input[type=search]{font:inherit;font-size:12px;border:1px solid var(--line);border-radius:6px;padding:4px 8px;width:14em;max-width:100%}
.cmp-tools select,.cmp-tools button{font:inherit;font-size:12px;border:1px solid var(--line);border-radius:6px;padding:3px 8px;background:#fff}
.cmp-tools .count{margin-left:auto;color:var(--muted);font-variant-numeric:tabular-nums}
.cmp-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:6px}
table.cmp{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px;min-width:1200px}
table.cmp th,table.cmp td{text-align:right;padding:6px 10px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums;vertical-align:top;background:#fff}
table.cmp thead th{background:#f9fafb;font-size:11px;font-weight:400;color:var(--muted);vertical-align:bottom;line-height:1.3}
table.cmp tr.groups th{text-transform:uppercase;letter-spacing:.05em;font-size:10.5px;text-align:center;border-bottom:0}
table.cmp tr.groups th.caught,table.cmp tr.groups th.caught .gnote{color:var(--caught)}
table.cmp tr.groups th.escaped,table.cmp tr.groups th.escaped .gnote{color:var(--escaped)}
table.cmp .gnote,table.cmp .cnote{display:block;text-transform:none;letter-spacing:0;font-size:10.5px}
table.cmp .first{text-align:left;position:sticky;left:0;z-index:1;white-space:nowrap}
table.cmp .sort{text-decoration:none;display:inline-grid;justify-items:end;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
table.cmp .sort.on{color:var(--fg);font-weight:600}
table.cmp .arrow{font-size:10.5px;letter-spacing:0;color:var(--muted)}
table.cmp .id{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
table.cmp .rank,table.cmp .small,table.cmp .nr{color:var(--muted);font-size:11px}
table.cmp tr.dormant th{font-style:italic}
.pager{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;font-size:12px;margin-top:10px}
.pager .current{font-weight:600}
.pager .off,.pager .gap{color:var(--muted)}
`;

const esc = (s) => String(s).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const num = (v) => Number(v || 0).toLocaleString("en-GB");

/** One panel. `coverage` is rendered AND machine-readable: every panel says what
 *  it covers, the convention tools/tests/test_dashboard.py already enforces on
 *  the local dashboard. It matters more here, because a panel can be empty
 *  either because nothing happened or because nothing was collected.
 *
 *  ADT-284 adds `explain`, a native <details> block. Criterion 6 asks that every
 *  figure say what it counts, where it comes from and over what period. It is
 *  <details> rather than a second route because the Cloudflare Access
 *  application is scoped to the path `dashboard`, so an /explain path could fall
 *  outside it and be publicly readable — and rather than a toggle script because
 *  the page carries no JavaScript at all. */
function panel(title, coverage, inner, cls = "", explain = "", empty = "", tells = "") {
  return `<section class="panel ${cls}" data-coverage="${esc(coverage)}">`
    + `<h2>${esc(title)}</h2><div class="cov">${esc(coverage)}</div>`
    // An empty LIFETIME panel is not "nothing in the last 30 days" — that says
    // activity stopped, when the truth is that nothing was ever reported. The
    // caller says which, because only the caller knows what its figures mean.
    + (inner || (empty === null ? ""
        : `<p class="empty">${esc(empty || `nothing in the last ${WINDOW_DAYS} days`)}</p>`))
    + (explain ? `<details class="ex"><summary>what this counts</summary>`
                 + `<p>${explain}</p></details>` : "")
    // A figure explained is still a figure nobody acts on. The second block says
    // what the panel is FOR — the decision it informs — because a reader who
    // knows what a number counts still has to be told why it is on the page.
    + (tells ? `<details class="ex tells"><summary>what this tells you</summary>`
               + `<p>${tells}</p></details>` : "")
    + `</section>`;
}

/** Rows of one table. The wrapper owns the column tracks and every row takes
 *  them through `subgrid`, so each column is one width for the whole panel and
 *  that width comes from the widest heading or value in it. Fixed pixel tracks
 *  fitted the text only at the font size they were measured at: under a browser
 *  minimum font size the headings ran into each other. */
const tbl = (cls, inner) => inner && `<div class="tbl ${cls}">${inner}</div>`;

function bars(rows) {
  const max = Math.max(1, ...rows.map((r) => Number(r.value) || 0));
  return tbl("one", rows.map((r) =>
    `<div class="row one"><div class="k">${esc(r.name)}</div>`
    + `<div class="bar"><i style="width:${Math.round((Number(r.value) || 0) / max * 100)}%"></i></div>`
    + `<div class="v">${num(r.value)}</div></div>`).join(""));
}

/** A metric row carries BOTH readings side by side under a labelled header.
 *  Success criterion 2: a reader has to tell a standing total from a window
 *  figure without knowing how either is computed, so the label travels with the
 *  number rather than sitting in a footnote. */
const HEAD = `<div class="row head"><div class="k"></div><div class="bar"></div>`
  + `<div class="v">lifetime</div><div class="v">last ${WINDOW_DAYS} days</div></div>`;

function bars2(rows) {
  const max = Math.max(1, ...rows.map((r) => Number(r.total) || 0));
  return tbl("cmd", HEAD + rows.map((r) =>
    `<div class="row"><div class="k">${esc(r.name)}</div>`
    + `<div class="bar"><i style="width:${Math.round((Number(r.total) || 0) / max * 100)}%"></i></div>`
    + `<div class="v">${num(r.total)}</div>`
    + `<div class="v">${num(windowValue(r.win))}</div></div>`).join(""));
}

/** A rate, or "—" when the n behind it is too small to carry one.
 *  `adt_metrics.by_track`'s own docstring says a tier with one closed ticket
 *  supports no claim. Printing 50% over n=2 invites exactly the reading the
 *  panel warns against, so below the floor the page shows the counts and no
 *  rate at all. */
const MIN_N = 10;
const pct = (num, den) => (Number(den) || 0) < MIN_N ? "&mdash;"
  : (100 * (Number(num) || 0) / Number(den)).toFixed(1) + "%";

/** Tokens in thousands, rounded, for the compare installs table. */
const kilo = (v) => (Number(v) || 0) === 0 ? "0" : num(Math.round(Number(v) / 1e3)) + "K";

/** Micro-dollars in, whole dollars out. The ledger stores an integer number of
 *  micro-dollars precisely so no float rounding happens before this point. */
const money = (m) => "$" + Math.round(Number(m || 0) / 1e6).toLocaleString("en-GB");

// ---------------------------------------------------------------- ADT-378
// The figures a page section needs, from one set of query rows. The same
// function reads the all-installs rows and the one-install rows, so the two
// cannot be computed differently.
const ADT_REVIEWER = /^adt-[a-z0-9-]+-reviewer$/;

function summarize(r) {
  const n = (x) => Number(x) || 0;
  const totals = (r.totals && r.totals[0]) || {};
  const v1 = (r.v1Totals && r.v1Totals[0]) || {};
  const add = (a, b) => n(a) + n(b);
  // Legacy and totals-v1 rows describe the same installs in the same units, so
  // the page adds them. Neither store is authoritative on its own: an install
  // that has upgraded writes totals-v1 today and cmdset-v1 for as long as
  // retention keeps its old rows.
  const byName = new Map();
  for (const name of CMDSET_V1) {
    const a = name.replace(/-/g, "_");
    byName.set(name, { name, total: n(totals[a]), win: n(totals[a + "_win"]) });
  }
  for (const c of r.v1Commands || []) {
    const cur = byName.get(c.name) || { name: c.name, total: 0, win: 0 };
    byName.set(c.name, { name: c.name, total: add(cur.total, c.total), win: add(cur.win, c.total_win) });
  }
  const namedPings = (rows) => {
    const m = new Map();
    for (const x of rows) m.set(x.name || "unknown", n(m.get(x.name || "unknown")) + n(x.pings));
    return [...m.entries()].map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value);
  };
  const gates = r.v1Gates || [], tracks = r.v1Tracks || [], surfaces = r.v1Surfaces || [];
  const sum = (rows, k) => rows.reduce((a, x) => a + n(x[k]), 0);
  return {
    totals, v1, gates, tracks, surfaces,
    cmdRows: [...byName.values()].filter((c) => c.total > 0 || c.win > 0).sort((a, b) => b.total - a.total),
    versions: namedPings([...(r.versions || []), ...(r.v1Versions || [])]),
    systems: namedPings([...(r.systems || []), ...(r.v1Systems || [])]),
    tickets: add(totals.tickets, v1.tickets), ticketsWin: add(totals.tickets_win, v1.tickets_win),
    tokens: add(totals.tokens, v1.tokens), tokensWin: add(totals.tokens_win, v1.tokens_win),
    cost: n(v1.cost), costWin: windowValue(v1.cost_win), costMeasured: n(v1.cost_measured),
    closed: sum(tracks, "closed"), defects: sum(tracks, "defects"), stamped: sum(tracks, "stamped"),
    changed: sum(gates, "caused_edit"), reviewed: sum(gates, "tickets"),
    reviews: surfaces.filter((x) => ADT_REVIEWER.test(x.name || "")).reduce((a, x) => a + n(x.total), 0),
    surfaceTotal: sum(surfaces, "total"),
    qaChecks: n(v1.qa_checks), qaFails: n(v1.qa_fails), qaTickets: n(v1.qa_tickets), qaFailed: n(v1.qa_tickets_failed),
    handbacks: n(v1.handbacks), blocked: n(v1.blocked_markers), denies: n(v1.guard_denies),
    pings: add(totals.pings, v1.pings), installsWindow: add(totals.installs_window, v1.installs_window),
    dodAmendments: n(v1.dod_amendments),
  };
}

const per = (x, d) => (Number(d) || 0) === 0 ? "&mdash;" : ((Number(x) || 0) / Number(d)).toFixed(2);
const per100 = (x, d) => (Number(d) || 0) === 0 ? "&mdash;" : (100 * (Number(x) || 0) / Number(d)).toFixed(1);
const share = (x, d) => (Number(d) || 0) === 0 ? "&mdash;" : Math.round(100 * (Number(x) || 0) / Number(d)) + "%";

/** A page address for these settings with some changed. Defaults are left out,
 *  so the plain dashboard is just `?`. */
function hrefFor(settings, change = {}) {
  const s = { ...settings, ...change };
  const p = new URLSearchParams();
  if (s.install) p.set("install", s.install);
  if (s.sort !== DEFAULT_SETTINGS.sort) p.set("sort", s.sort);
  if (s.dir !== DEFAULT_SETTINGS.dir) p.set("dir", s.dir);
  if (s.page > 1) p.set("page", String(s.page));
  if (s.q) p.set("q", s.q);
  if (s.dormant) p.set("dormant", "show");
  if (s.min !== DEFAULT_SETTINGS.min) p.set("min", String(s.min));
  return esc("?" + p.toString());
}

/** How ADT helped: four figures in the order a ticket meets them. No colour: the
 *  labels say which is caught and which escaped. */
function helpedTiles(s, a) {
  const tile = (kind, big, what, of, allOf) =>
    `<div class="tile"><span class="kind">${kind}</span><span class="n">${big}</span>`
    + `<span class="what">${what}</span><span class="of">${of}</span>`
    + (allOf ? `<span class="of all">all installs: ${allOf}</span>` : "") + `</div>`;
  return `<section class="helped" aria-label="How ADT helped">`
    + tile("checks run", num(s.reviews), "ADT review agents run on plans and builds",
        `${per(s.reviews, s.closed)} per closed ticket`, a && `${per(a.reviews, a.closed)} per closed ticket`)
    + tile("caught at plan", num(s.changed), "plans a review changed before any code was written",
        `${share(s.changed, s.reviewed)} of ${num(s.reviewed)} plans reviewed`,
        a && `${share(a.changed, a.reviewed)} of ${num(a.reviewed)} plans reviewed`)
    + tile("caught at QA", num(s.qaFails), "builds QA sent back before merge",
        `${share(s.qaFails, s.qaChecks)} of ${num(s.qaChecks)} QA checks`,
        a && `${share(a.qaFails, a.qaChecks)} of ${num(a.qaChecks)} QA checks`)
    + tile("escaped", num(s.defects), "bugs filed after a ticket closed, missed by ADT and Claude",
        `${per100(s.defects, s.closed)} per 100 closed tickets`, a && `${per100(a.defects, a.closed)} per 100 closed tickets`)
    + `</section>`;
}

/** One table of label rows, with an all-installs column when `allHead` is given. */
const labelledTable = (cls, head, rows, allHead) => {
  const scoped = !!allHead;
  const c = scoped ? cls + "a" : cls;
  return tbl(c, `<div class="row head ${c}"><div class="k"></div>`
    + head.map((h) => `<div class="v">${h}</div>`).join("")
    + (scoped ? `<div class="v all">${allHead}</div>` : "") + `</div>`
    + rows.map(([label, vals, allVal, rowCls]) =>
        `<div class="row ${c}${rowCls ? " " + rowCls : ""}"><div class="k">${esc(label)}</div>`
        + vals.map((v) => Array.isArray(v) ? `<div class="v ${v[1]}">${v[0]}</div>` : `<div class="v">${v}</div>`).join("")
        + (scoped ? `<div class="v all">${allVal}</div>` : "") + `</div>`).join(""));
};

// ---------------------------------------------------------------- compare installs
// Each column: which schema must have sent it, and for a rate its numerator,
// denominator and format. A rate whose denominator is below the chosen minimum is
// hidden. The minimum is never below MIN_N, so `pct` never withholds one itself.
const COMPARE_COLUMNS = [
  { key: "tickets", group: "usage", head: "tickets", needs: (i) => i.reported, value: (i) => i.tickets, show: (v) => num(v) },
  { key: "tokens", group: "usage", head: "tokens", needs: (i) => i.reported, value: (i) => i.tokens, show: (v) => kilo(v) },
  { key: "cost", group: "usage", head: "cost", needs: (i) => i.v1, value: (i) => i.cost, show: (v) => money(v) },
  { key: "commands", group: "usage", head: "command<br>runs", needs: (i) => i.v1, value: (i) => i.runs,
    show: (v, i) => `${num(v)} <span class="small">${num(i.used)}/${CMDSET_V1.length}</span>` },
  { key: "reviews", group: "checks run", head: "ADT reviews<br>per ticket", needs: (i) => i.v1 && i.schema >= 4,
    top: (i) => i.reviews, bottom: (i) => i.closed, fmt: per },
  { key: "plans", group: "caught", head: "plans changed<br>by a review", needs: (i) => i.v1,
    top: (i) => i.changed, bottom: (i) => i.reviewed, fmt: pct },
  { key: "qa", group: "caught", head: "QA sent the<br>build back", needs: (i) => i.v1 && i.schema >= 5,
    top: (i) => i.qaFails, bottom: (i) => i.qaChecks, fmt: pct },
  { key: "bugs", group: "escaped", head: "bugs after closing<br>per 100 tickets", needs: (i) => i.v1,
    top: (i) => i.defects, bottom: (i) => i.closed, fmt: per100 },
  { key: "handbacks", group: "supervision", head: "handbacks<br>per ticket", needs: (i) => i.v1 && i.schema >= 4,
    top: (i) => i.handbacks, bottom: (i) => i.closed, fmt: per },
  { key: "blocked", group: "supervision", head: "ticket filings blocked<br>per 100 tickets", note: "includes false blocks",
    needs: (i) => i.v1 && i.schema >= 4, top: (i) => i.denies, bottom: (i) => i.closed, fmt: per100 },
];
// The compare installs columns a page address may sort by.
export const COMPARE_KEYS = COMPARE_COLUMNS.map((c) => c.key);
const COMPARE_GROUPS = [
  { group: "usage", name: "usage" },
  { group: "checks run", name: "checks run" },
  { group: "caught", name: "caught before merge", note: "problems ADT stopped", cls: "caught" },
  { group: "escaped", name: "escaped", note: "missed by ADT and Claude", cls: "escaped" },
  { group: "supervision", name: "supervision" },
].map((g) => ({ ...g, span: COMPARE_COLUMNS.filter((c) => c.group === g.group).length }));
const PAGE_ROWS = 25;

/** One cell's state: not reported, a hidden rate, or a value to sort by. */
function compareCell(col, i, min) {
  if (!col.needs(i)) return { state: "not reported" };
  if (!col.bottom) return { value: Number(col.value(i)) || 0 };
  const bottom = Number(col.bottom(i)) || 0;
  if (bottom < min) return { state: "hidden", bottom };
  return { value: (Number(col.top(i)) || 0) / bottom };
}

function installRows(all, known) {
  const n = (x) => Number(x) || 0;
  const byId = new Map();
  const row = (id) => {
    if (!byId.has(id)) byId.set(id, { id, legacy: false, v1: false, schema: 0, tickets: 0, tokens: 0, cost: 0,
      runs: 0, used: 0, changed: 0, reviewed: 0, closed: 0, defects: 0, reviews: 0, handbacks: 0, denies: 0,
      qaChecks: 0, qaFails: 0 });
    return byId.get(id);
  };
  // Legacy rows are read first, so they set tickets and tokens and totals-v1 adds to them.
  for (const x of all.installs || [])
    Object.assign(row(x.install), { legacy: true, tickets: n(x.tickets), tokens: n(x.tokens) });
  for (const x of all.v1Installs || []) {
    const i = row(x.install);
    Object.assign(i, { v1: true, schema: n(x.schema_seen), tickets: i.tickets + n(x.tickets),
      tokens: i.tokens + n(x.tokens), cost: n(x.cost), handbacks: n(x.handbacks), denies: n(x.guard_denies),
      qaChecks: n(x.qa_checks), qaFails: n(x.qa_fails) });
  }
  for (const x of all.v1CommandsByInstall || []) Object.assign(row(x.install), { runs: n(x.runs), used: n(x.used) });
  for (const x of all.v1GatesByInstall || []) Object.assign(row(x.install), { changed: n(x.caused_edit), reviewed: n(x.tickets) });
  for (const x of all.v1TracksByInstall || []) Object.assign(row(x.install), { closed: n(x.closed), defects: n(x.defects) });
  for (const x of all.v1ReviewersByInstall || []) Object.assign(row(x.install), { reviews: n(x.reviews) });
  for (const k of known) row(k.id);
  return [...byId.values()].map((i) => Object.assign(i, { reported: i.legacy || i.v1 }));
}

function compareInstalls(settings, rows) {
  const col = COMPARE_COLUMNS.find((c) => c.key === settings.sort);
  const visible = rows.filter((i) => (settings.dormant || i.reported) && (!settings.q || i.id.startsWith(settings.q)));
  // Rows with no value to sort by (not reported, or a hidden rate) always go
  // last, whichever way the column is sorted. Ties keep the install id order.
  const keyed = visible.map((i) => ({ i, c: compareCell(col, i, settings.min) }))
    .sort((x, y) => (("value" in x.c) ? 0 : 1) - (("value" in y.c) ? 0 : 1)
      || (("value" in x.c) ? (settings.dir === "desc" ? y.c.value - x.c.value : x.c.value - y.c.value) : 0)
      || x.i.id.localeCompare(y.i.id));
  const pages = Math.max(1, Math.ceil(keyed.length / PAGE_ROWS));
  const page = Math.min(settings.page, pages);
  const start = (page - 1) * PAGE_ROWS;
  const shown = keyed.slice(start, start + PAGE_ROWS).map((x) => x.i);

  const groups = `<tr class="groups"><th scope="col"></th>` + COMPARE_GROUPS.map((g) =>
    `<th scope="colgroup" colspan="${g.span}"${g.cls ? ` class="${g.cls}"` : ""}>${g.name}`
    + (g.note ? `<span class="gnote">${g.note}</span>` : "") + `</th>`).join("") + `</tr>`;
  const heads = `<tr><th scope="col" class="first">install</th>` + COMPARE_COLUMNS.map((c) => {
    const on = c.key === settings.sort;
    const nextDir = on && settings.dir === "desc" ? "asc" : "desc";
    return `<th scope="col" aria-sort="${on ? (settings.dir === "desc" ? "descending" : "ascending") : "none"}">`
      + `<a class="sort${on ? " on" : ""}" href="${hrefFor(settings, { sort: c.key, dir: nextDir, page: 1 })}">${c.head}`
      + `<span class="arrow">${on ? (settings.dir === "desc" ? "&#9660; high to low" : "&#9650; low to high") : "&#9660;&#9650;"}</span></a>`
      + (c.note ? `<span class="cnote">${c.note}</span>` : "") + `</th>`;
  }).join("") + `</tr>`;

  const body = shown.map((i, k) => {
    const cells = COMPARE_COLUMNS.map((c) => {
      const cell = compareCell(c, i, settings.min);
      const shownValue = cell.state === "not reported" ? `<span class="nr">not reported</span>`
        : cell.state === "hidden" ? `<span class="nr">&mdash; <span class="small">(${num(cell.bottom)})</span></span>`
        : c.bottom ? c.fmt(c.top(i), c.bottom(i)) : c.show(cell.value, i);
      return `<td>${shownValue}</td>`;
    }).join("");
    return `<tr${i.reported ? "" : ` class="dormant"`}><th scope="row" class="first">`
      + `<span class="rank">#${start + k + 1}</span> <a class="id" href="${hrefFor(settings, { install: i.id })}" title="${esc(i.id)}">${esc(i.id.slice(0, 8))}</a>`
      + (i.reported ? "" : ` <span class="small">no report in ${WINDOW_DAYS} days</span>`) + `</th>${cells}</tr>`;
  }).join("");

  const opt = (v) => `<option value="${v}"${settings.min === v ? " selected" : ""}>${v}</option>`;
  const form = `<form class="cmp-tools" method="get" action="">`
    + (settings.install ? `<input type="hidden" name="install" value="${esc(settings.install)}">` : "")
    + `<input type="hidden" name="sort" value="${esc(settings.sort)}"><input type="hidden" name="dir" value="${esc(settings.dir)}">`
    + `<label for="cmp-q">install id <input type="search" id="cmp-q" name="q" value="${esc(settings.q)}" maxlength="36"></label>`
    + `<label for="cmp-dormant"><input type="checkbox" id="cmp-dormant" name="dormant" value="show"${settings.dormant ? " checked" : ""}> show dormant installs</label>`
    + `<label for="cmp-min">rates need at least <select id="cmp-min" name="min">${[10, 25, 50].map(opt).join("")}</select> tickets</label>`
    + `<button type="submit">Apply</button>`
    + `<span class="count">${keyed.length ? `Showing ${num(start + 1)}&ndash;${num(start + shown.length)} of ${num(keyed.length)} installs` : "No installs match"}</span></form>`;

  const link = (label, p, extra = "") => `<a href="${hrefFor(settings, { page: p })}"${extra}>${label}</a>`;
  const nav = [page > 1 ? link("Previous", page - 1) : `<span class="off">Previous</span>`];
  let last = 0;
  for (const p of [...new Set([1, page - 1, page, page + 1, pages])].filter((p) => p >= 1 && p <= pages).sort((x, y) => x - y)) {
    if (p - last > 1) nav.push(`<span class="gap">&hellip;</span>`);
    nav.push(p === page ? `<span class="current" aria-current="page">${p}</span>` : link(String(p), p));
    last = p;
  }
  nav.push(page < pages ? link("Next", page + 1) : `<span class="off">Next</span>`);

  return form + `<div class="cmp-wrap"><table class="cmp"><thead>${groups}${heads}</thead><tbody>${body}</tbody></table></div>`
    + `<nav class="pager" aria-label="compare installs pages">${nav.join("")}</nav>`;
}

// ADT-391: the updates and uninstalls panels, on the all-installs page only.
function lifecycleGrid(life) {
  const updateRows = life.updates.length && labelledTable("p3",
    ["installs<br>updated", "first<br>moved", "latest<br>moved"],
    life.updates.map((u) => [u.version, [num(u.installs), u.first, u.latest]]));
  const installs = life.status.uninstalled + life.status.quiet + life.status.reporting;
  const statusRows = installs && labelledTable("p2", ["installs", "share"],
    [["uninstalled", "uninstalled"], ["gone quiet", "quiet"], ["reporting", "reporting"]]
      .map(([label, key]) => [label, [num(life.status[key]), share(life.status[key], installs)]]));
  return `<div class="grid two">
${panel("updates", `installs that reported an older version first, last ${WINDOW_DAYS} days`, updateRows || "", "",
  `An install counts as updated to a version when its daily reports show an older version first and then this one, both inside the last ${WINDOW_DAYS} days. <b>first moved</b> and <b>latest moved</b> are the days the first and the most recent of those installs sent their first report on the new version. An install that updated after its older reports aged out of the window is not counted, and a new install on this version is not an update.`,
  `no install moved to a newer version in the last ${WINDOW_DAYS} days`,
  `Shows how many installs take each release, and how fast. A release that few installs move to within a week or two is one people are not updating to.`)}
${panel("uninstalls", `installs that reported in the last ${WINDOW_DAYS} days, by status`, statusRows || "", "",
  `Every install that sent a daily report in the last ${WINDOW_DAYS} days, counted once. <b>uninstalled</b> sent an uninstall report after its last daily report. <b>gone quiet</b> has sent nothing for more than ${QUIET_DAYS} days and no uninstall report: telemetry turned off, the machine off, or the watcher stopped. <b>reporting</b> sent a daily report in the last ${QUIET_DAYS} days. An uninstall report from an install that sent no daily report in the window is not counted.`,
  `nothing reported in the last ${WINDOW_DAYS} days`,
  `Shows how many installs are removed rather than just going quiet. A rise in uninstalled after a release points at that release. Gone quiet cannot say why an install stopped reporting.`)}
</div>`;
}

function render({ settings, all, one, known, errors, queryCount, now = Date.now() }) {
  settings = settings || DEFAULT_SETTINGS;
  errors = errors || {};
  known = known || [];
  const a = summarize(all || {});
  const s = one ? summarize(one) : a;
  const scoped = !!one;

  const rows = installRows(all || {}, known);
  const sub = `<span class="exact">${num(rows.length)} installs</span>`
    + ` &middot; about ${num(a.installsWindow)} reported in the last ${WINDOW_DAYS} days`
    + ` &middot; ${num(a.pings)} daily ${a.pings === 1 ? "report" : "reports"}`;

  // The wire carries the gate's internal key. The page shows what the gate
  // asks, because "coverage" and "plan-quality" are meaningful only to someone
  // who has read tools/adt_dod.py.
  const GATE_LABEL = {
    "coverage": "does the DoD cover the spec?",
    "plan-quality": "is this the right thing to build?",
  };
  const cov = (text) => !scoped ? text
    : text.includes("all installs") ? text.replace("all installs", `install ${settings.install.slice(0, 8)}`)
    : `${text} \u00b7 install ${settings.install.slice(0, 8)}`;
  const allOf = (rows, name) => rows.find((x) => x.name === name) || {};

  const gateRows = s.gates.length && labelledTable("g4",
    ["tickets<br>seen", "times<br>run", "changed<br>the plan", "%<br>changed", "left no<br>record"],
    s.gates.map((g) => [GATE_LABEL[g.name] || g.name,
      [num(g.tickets), num(g.ran), num(g.caused_edit), [pct(g.caused_edit, g.tickets), "rate"], num(g.under_recorded)],
      pct(allOf(a.gates, g.name).caused_edit, allOf(a.gates, g.name).tickets)]),
    scoped && "all installs<br>% changed");

  // TWO DIFFERENT DENOMINATORS, and they are not interchangeable.
  // `adt_metrics.by_track` sums `follow_ons` only over tickets that stamped a
  // figure, so its rate is per STAMPED ticket; `defects` is counted over every
  // closed ticket. Dividing both by `closed` would understate the follow-on
  // rate by however many tickets never stamped one.
  const trackRows = s.tracks.length && labelledTable("g5",
    ["tickets<br>closed", "bugs<br>after", "%", "work left<br>unfinished", "%", "days to<br>close"],
    s.tracks.map((t) => [t.name,
      [num(t.closed), [num(t.defects), "esc"], [pct(t.defects, t.closed), "rate esc"],
       num(t.follow_ons), [pct(t.follow_ons, t.stamped), "rate"],
       (Number(t.days_n) || 0) === 0 ? "&mdash;" : ((Number(t.days_sum) || 0) / Number(t.days_n)).toFixed(1)],
      pct(allOf(a.tracks, t.name).defects, allOf(a.tracks, t.name).closed)]),
    scoped && "all installs<br>% bugs");

  const autonomyRows = labelledTable("p2", ["count", "per<br>ticket"], [
    ["handed back to a human", [num(s.handbacks), per(s.handbacks, s.closed)], per(a.handbacks, a.closed)],
    ["paused with /adt-block", [num(s.blocked), per(s.blocked, s.closed)], per(a.blocked, a.closed)],
    ["spawns refused by the guard", [num(s.denies), per(s.denies, s.closed)], per(a.denies, a.closed)],
  ], scoped && "all installs<br>per ticket");

  // The filter keeps only ADT's own review agents. A skill such as `adt-brief`
  // also starts with adt-, so the name has to end in -reviewer as well.
  const surfaceRows = s.surfaces.length
    ? `<input type="checkbox" class="flt" id="adt-reviewers-only">`
      + `<label class="flt-btn" for="adt-reviewers-only">ADT reviewers only</label>`
      + labelledTable("p2", ["dispatched", "share"],
          s.surfaces.map((x) => [x.name, [num(x.total), share(x.total, s.surfaceTotal)],
                                 num(allOf(a.surfaces, x.name).total), ADT_REVIEWER.test(x.name) ? "adt-rev" : ""]),
          scoped && "all installs<br>dispatched")
    : "";

  // Schema 5. A QA result is a `**Result:**` line in a ticket's QA report; a FAIL
  // is QA sending the build back. Counted on the install, from its cache.
  const qaRows = labelledTable("p3", ["found", "out of", "%"], [
    ["QA checks that sent the build back", [num(s.qaFails), num(s.qaChecks), pct(s.qaFails, s.qaChecks)], pct(a.qaFails, a.qaChecks)],
    ["tickets sent back at least once", [num(s.qaFailed), num(s.qaTickets), pct(s.qaFailed, s.qaTickets)], pct(a.qaFailed, a.qaTickets)],
  ], scoped && "all installs<br>%");

  const ticketRows = labelledTable("nb", ["lifetime", `last ${WINDOW_DAYS} days`], [
    ["tickets", [num(s.tickets), num(windowValue(s.ticketsWin))], num(a.tickets)],
    ["tokens", [num(s.tokens), num(windowValue(s.tokensWin))], num(a.tokens)],
    ["cost", [[money(s.cost), "exact"], [money(s.costWin), "exact"]], money(a.cost)],
    ["measured", [money(s.costMeasured), "&mdash;"], money(a.costMeasured), "soft"],
  ], scoped && "all installs<br>lifetime");

  const lifecyclePanels = scoped ? "" : lifecycleGrid(lifecycle((all || {}).lifecycle, now));

  const banner = scoped
    ? `<section class="scoped" aria-label="One install"><span>Showing install <b class="mono">${esc(settings.install)}</b></span>`
      + `<a href="${hrefFor(settings, { install: null })}">Back to all installs</a></section>`
    : "";

  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ADT telemetry</title><style>${CSS}</style></head><body>
<h1>ADT telemetry</h1>
<p class="sub">${sub}</p>
${Object.keys(errors).length ? `<section class="panel warn"><h2>some data is missing</h2>`
  + `<div class="cov">${Object.keys(errors).length} of ${queryCount || Object.keys(errors).length} queries failed; `
  + `the panels they feed are blank or short. Everything else on this page is current.</div>`
  + tbl("one", Object.entries(errors).map(([name, why]) =>
      `<div class="row one"><div class="k">${esc(name)}</div><div class="bar"></div>`
      + `<div class="v err">${esc(why)}</div></div>`).join(""))
  + `</section>` : ""}
${banner}
${helpedTiles(s, scoped ? a : null)}
<div class="grid">
${panel("per command", cov(`${s.cmdRows.length} of ${CMDSET_V1.length} commands used`), bars2(s.cmdRows), "",
  `How many times each <code>/adt-*</code> command has run. Each install reports a running lifetime total; `
  + `<b>lifetime</b> is the latest total per install added across installs, <b>last ${WINDOW_DAYS} days</b> is `
  + `each install's newest total minus its oldest one in the window. Neither adds the daily reports together, `
  + `which is what made this panel read three times too high.`, "",
  `Shows which ADT commands are used and which are not. A command with a high lifetime count but a low count for the last 30 days has stopped being used. A command that never appears here is not used at all, but it still has to be documented, installed and tested.`)}
${panel("versions", cov(`${s.versions.length} seen`), bars(s.versions), "",
  `One count per daily report, so this is how many daily reports arrived from each ADT version in the last ${WINDOW_DAYS} days &mdash; not how many installs run it.`, "",
  `Shows whether installs are updating to new releases. After a release, most daily reports should come from the new version within a few days. If an older version is still sending reports weeks later, some installs have not updated.`)}
${panel("operating systems", cov(`${s.systems.length} seen`), bars(s.systems), "",
  `<code>platform.system()</code> on the reporting machine: <code>Darwin</code> is macOS, <code>Linux</code> is Linux. Never a hostname, kernel build or architecture. One count per daily report over the last ${WINDOW_DAYS} days.`, "",
  `Shows which operating systems ADT is running on. ADT's shell tests only run on macOS, so reports from Linux come from installs whose shell scripts have not been tested on that system.`)}
</div>
${lifecyclePanels}
<div class="grid">
${panel("tickets and tokens", cov("lifetime, all installs"), ticketRows, "",
  `<b>tickets</b> is distinct ticket ids seen in an install's cost ledger; <b>tokens</b> is input plus output. `
  + `<b>cost</b> is in US dollars, rounded to the dollar, priced on each install from its own ledger; <b>measured</b> `
  + `is the part priced from a recorded model rate, and the rest is estimated. All are lifetime running totals `
  + `reported by each install, read the same way as the per-command panel.`, "",
  `Shows how much work goes through ADT and what it costs. Tickets is the number of pieces of work, tokens is how much model use they took, and cost is what that use cost in US dollars. Tokens cost different amounts on different models, so compare spending by cost, not by tokens.`)}
${panel("gates", cov(s.gates.length
  ? `${s.gates.length} reviews, before any ticket is built · `
    + `${num(s.dodAmendments)} Definition-of-Done rewrites`
  : "no reviews reported yet"),
  gateRows || "", scoped ? "span2" : "",
  `Lifetime, per plan-review gate. Before a ticket is built, two separate reviews read the plan. One asks `
  + `whether the <b>Definition of Done covers the spec</b> &mdash; whether the checks a ticket will be graded `
  + `against actually test everything it promised. The other asks whether it is the <b>right thing to `
  + `build</b>, and the simplest thing that solves the problem.<br><br>`
  + `Read a row left to right: <b>tickets (n)</b> is how many tickets the gate saw, <b>times run</b> is how `
  + `many review rounds that took &mdash; a gate can run several times on one ticket, rejecting it each `
  + `time &mdash; and <b>changed the plan</b> is how many of those tickets came out different because of it. `
  + `That last one is the number that says whether the gate is earning its cost. <b>% changed</b> is tickets `
  + `changed over tickets seen, withheld below ten tickets because a percentage over a handful reads as a `
  + `finding and is not one.<br><br>`
  + `The line under the heading counts <b>Definition-of-Done rewrites</b>: how often a ticket's own success `
  + `criteria were changed after they were first written. One is a plan responding to a review. Five is a `
  + `plan that was wrong at the start.`,
  null,
  `Shows whether the two plan reviews are worth running. Every review takes time and tokens. Changed the plan counts the tickets where a review led to a change in the plan before any code was written. If a review runs many times but rarely changes a plan, it may not be worth running on every ticket.`)}
${panel("tracks", cov(s.tracks.length
  ? `${s.tracks.length} tiers · unfinished-work counts on `
    + `${num(s.stamped)} of `
    + `${num(s.closed)} closed tickets`
  : "no closed tickets reported yet"),
  trackRows || "", scoped ? "wide" : "",
  `Lifetime, per impact tier. A tier decides how many gates a ticket passes through: <code>fast</code> goes `
  + `brief to build to done, <code>standard</code> adds a plan and a QA pass, <code>full</code> skips `
  + `nothing. <code>unset</code> is tickets filed before tiers existed.<br><br>`
  + `<b>tickets closed</b> is how many the tier delivered.<br><br>`
  + `<b>bugs after</b> counts bugs filed later that name one of those tickets by id. It only finds bugs `
  + `that carry the reference, so it undercounts &mdash; read it as a floor.<br><br>`
  + `<b>work left unfinished</b> counts new tickets spawned out of closing one. Finding something mid-build `
  + `and fixing it there costs nothing here; opening a ticket for it instead is what this counts, and it `
  + `should trend toward zero.<br><br>`
  + `The two are shown together because either one alone can be gamed. Push unfinished work to zero by `
  + `shipping half-done tickets and it comes back as bugs; close every loose end into a new ticket and the `
  + `bug count falls while the work never ends.<br><br>`
  + `<b>days to close</b> is calendar time from the day a ticket was filed to the day it closed, averaged `
  + `over the tickets carrying both dates. It counts time a ticket spent waiting in the backlog, so read it `
  + `as elapsed time rather than effort.<br><br>`
  + `Percentages are withheld below ten tickets: over a handful they read as a finding and are not one. The `
  + `unfinished-work rate is over the tickets that recorded a count &mdash; not every ticket does, and the `
  + `line under the heading says how many did.`,
  null,
  `Compares the three tracks: fast, standard and full. Full-track tickets go through the most checks, so they should have the fewest bugs after closing. Compare bugs after with tickets closed on each row. Also check work left unfinished, because a track can show few bugs when unfinished work was moved into new tickets. Unset counts tickets that closed without a track, and they cannot be compared.`)}
</div>
<div class="grid">
${panel("autonomy", cov(s.handbacks ? "lifetime, all installs" : "no handbacks reported yet"),
  autonomyRows, "",
  `How often a run stopped and needed a person. <b>handed back</b> counts turn boundaries where the agent `
  + `returned control; a subagent finishing is not one. <b>paused</b> counts explicit `
  + `<code>/adt-block</code> uses, which leave an artifact, so a conversational "what should I do?" is not `
  + `counted. <b>spawns refused</b> counts times the deferral guard denied an unauthorised new ticket.`,
  null,
  `Shows how often the agent had to stop and wait for a person. Handed back to a human counts each time the agent stopped and waited for you. If the number per ticket falls over time, ADT is doing more of the work without needing you. Spawns refused by the guard counts the times ADT stopped the agent from opening a new ticket you had not approved.`)}
${panel("reviewers", cov(s.surfaces.length
  ? `${s.surfaces.length} agents and skills dispatched`
  : "no dispatches reported yet"),
  surfaceRows, "",
  `Every agent and skill that ran, and how many times. ADT ships five review agents. Only two of them write the record `
  + `the gates panel reads, so the other three appear only here. A name without the adt- prefix is either another `
  + `project's own agent or a skill.`,
  null,
  `Shows which agents and skills ran, and how often. Select ADT reviewers only to see just the five review agents ADT ships. A review agent that never appears is not being used. The security and design reviewers do not show in the gates panel, so this is the only place their use is recorded.`)}
${panel("build problems QA found", cov(s.qaChecks ? "lifetime, all installs" : "no QA results reported yet"),
  qaRows, "",
  `QA checks is the number of QA results written into tickets' QA reports, and sent back is how many of them were FAIL. `
  + `Tickets is the number of tickets QA has checked, and sent back at least once is how many of those failed QA one or `
  + `more times. A QA run that was not written into the ticket is not counted. Percentages are hidden below ten.`,
  null,
  `Shows how often QA found a problem in a finished build. QA runs each ticket's Definition of Done checks against what the build delivered, and sends the ticket back to be fixed if a check fails or work is missing. Each ticket sent back is a problem the build had handed over as ready, which ADT caught before it merged. If the share sent back falls over time, more builds are passing QA the first time.`)}
</div>
<div class="grid">
${panel("compare installs", `lifetime, one row per install · select a column heading to rank by it, and select it again to reverse`,
  compareInstalls(settings, rows), "wide",
  `One row per install that has sent a daily report. Tickets, tokens, cost and command runs are lifetime totals. Plans changed is a percentage of plans reviewed, and QA sent back is a percentage of QA checks. ADT reviews and handbacks are per closed ticket, and bugs after closing and ticket filings blocked are per 100 closed tickets. Ticket filings blocked includes commands that only mention opening a ticket. An install whose daily report does not carry a figure shows not reported.`,
  null,
  `Shows which installs ADT helps most and which it helps least. Rank any column to see the installs at the top or the bottom. The caught columns count problems ADT stopped before merge, and the escaped column counts bugs that got through after a ticket closed. Rates are hidden for installs with too few tickets, because a handful of tickets can swing a rate a long way.`)}
</div>
<details class="ex page"><summary>How to read this page</summary>
<p><b>What a daily report is.</b> Each install posts one report a day, if telemetry is on. It carries a random install id,
the ADT version, the OS name, and counts &mdash; never prompts, code, file paths, ticket titles or repo names.
<code>DO_NOT_TRACK=1</code> or <code>development-team/.adt-state/telemetry-off</code> turns it off.</p>
<p><b>Lifetime versus last ${WINDOW_DAYS} days.</b> Every number an install sends is a running total for its whole
life, re-sent whole each day. <b>Lifetime</b> takes each install's most recent total and adds those across installs.
<b>Last ${WINDOW_DAYS} days</b> takes each install's newest total minus its oldest one inside the window, then adds
those. Adding the daily reports together instead is what inflated every count on this page before ADT-284.</p>
<p><b>Two install counts, two measurements.</b> The bold count is every install either store knows about: KV keeps
one record per install and never expires it. The muted count is Analytics Engine's estimate over ${WINDOW_DAYS} days.
A dormant install is one KV knows and AE has not heard from in the window.</p>
<p><b>Sampling.</b> Analytics Engine samples under load and reports a weight per row. Daily report and install counts are
weighted to undo that. The value columns are not: they are point reads of a running total, and multiplying a
lifetime total by a sample weight would invent activity rather than recover it.</p>
<p><b>Retention.</b> Analytics Engine keeps three months, and this page shows ${WINDOW_DAYS} days. A figure called
lifetime is the install's own lifetime total as most recently reported &mdash; not a sum over all time on this side.</p>
</details>
</body></html>`;
}
