// Copyright 2026 zurichrich
// SPDX-License-Identifier: Apache-2.0
//
// Tests the REAL Worker logic (collector.js), not a reimplementation of it.
// ADT-224 A2c: the put-accounting cases drive `handle()` — the same function
// worker.js calls — through stub bindings that COUNT. The claim this ticket
// makes is about cost per request class, and a claim about cost can only be
// tested by counting the writes, not by reading the code that makes them.
import assert from "node:assert/strict";
import {
  validate, handle, IpBudget, BUDGETS, CMDSET_V1, ALLOWED, ALLOWED_V3, utcDay,
  dashboardQueries, dataPoints, GATE_NAMES, TRACK_NAMES, windowValue, dashboardSettings,
  EVENT_KEYS, EVENT_NAMES, validateEvent, lifecycle,
} from "./collector.js";

let pass = 0;
const t = (name, fn) => { fn(); pass++; console.log("  ok:", name); };
const at = async (name, fn) => { await fn(); pass++; console.log("  ok:", name); };

const UUID_A = "3f2504e0-4f89-11d3-9a0c-0305e82c3301";
const UUID_B = "6ba7b810-9dad-11d1-80b4-00c04fd430c8";
const UUID_C = "9f1c0a52-3d84-4c77-9b21-77c2a1d0e4aa";

const good = () => ({ schema: 1, install_id: UUID_A,
                      version: "0.1.0", os: "Darwin", commands: 3, tickets: 2, tokens: 100 });

const good2 = () => ({ schema: 2, install_id: UUID_A, version: "0.1.0", os: "Darwin",
                       commands: { plan: 4, build: 2 }, tickets: 2, tokens: 100 });

// ADT-284. The numbers are this repo's real ones as of 2026-09-08, so the
// fixture exercises the magnitudes that actually occur rather than toy values:
// tokens past the old 1e7 bound, cost in the billions of micro-dollars, and all
// four tracks including `unset`.
const good3 = () => ({
  schema: 3, install_id: UUID_A, version: "0.1.0", os: "Darwin",
  commands: { plan: 4, build: 2, close: 0 }, tickets: 51, tokens: 22_700_797,
  cost_micros: 4_382_024_945, cost_measured_micros: 4_382_024_945,
  dod_amendments: 12,
  gates: {
    coverage: { ran: 23, caused_edit: 4, under_recorded: 0, tickets: 8 },
    "plan-quality": { ran: 12, caused_edit: 2, under_recorded: 0, tickets: 8 },
  },
  tracks: {
    fast: { closed: 2, stamped: 2, follow_ons: 0, defects: 2 },
    standard: { closed: 44, stamped: 15, follow_ons: 0, defects: 19 },
    full: { closed: 21, stamped: 17, follow_ons: 2, defects: 11 },
    unset: { closed: 4, stamped: 1, follow_ons: 0, defects: 0 },
  },
});

// --- validation -----------------------------------------------------------

t("accepts the documented payload", () => assert.equal(validate(good()).ok, true));

t("allowlist matches the client's build_payload field set", () =>
  assert.deepEqual([...ALLOWED].sort(),
    ["commands","install_id","os","schema","tickets","tokens","version"]));

t("schema 1 integer commands still accepted", () => {
  // 0.1.0 clients are deployed and send_ping fails open, so dropping schema 1
  // would turn every existing install's ping into a silent 400.
  assert.equal(validate(good()).ok, true);
});

t("per-command counts survive validation", () =>
  assert.equal(validate(good2()).ok, true));

t("rejects a command key outside the bounded pattern", () => {
  for (const bad of ["../etc/passwd", "ADT-224", "plan build", "a".repeat(41),
                     "feature/branch", "-leading-dash", ""]) {
    const b = good2();
    b.commands = { [bad]: 1 };
    assert.equal(validate(b).ok, false, `key should have been rejected: ${bad}`);
  }
});

t("rejects a command map wider than the cap", () => {
  const b = good2();
  b.commands = {};
  for (let i = 0; i < 65; i++) b.commands[`cmd-${i}`] = 1;
  assert.equal(validate(b).ok, false);
});

for (const [name, mutate] of [
  ["extra key",          (b) => (b.repo_name = "secret", b)],
  ["missing key",        (b) => (delete b.tokens, b)],
  ["unsupported schema", (b) => (b.schema = 3, b)],
  ["schema 2 with an integer commands", (b) => (b.schema = 2, b)],
  ["schema 1 with a commands map",      (b) => (b.commands = { plan: 1 }, b)],
  ["non-uuid id",        (b) => (b.install_id = "not-a-uuid", b)],
  ["hostname as id",     (b) => (b.install_id = "someones-macbook.local", b)],
  ["non-semver",         (b) => (b.version = "latest", b)],
  ["oversized os",       (b) => (b.os = "x".repeat(500), b)],
  ["negative count",     (b) => (b.commands = -1, b)],
  ["float count",        (b) => (b.tickets = 1.5, b)],
  ["absurd count",       (b) => (b.tickets = 10 ** 12, b)],
  ["absurd cumulative",  (b) => (b.tokens = 10 ** 13, b)],
  ["string count",       (b) => (b.tokens = "100", b)],
  ["negative per-command count", (b) => (b.schema = 2, b.commands = { plan: -1 }, b)],
]) t(`rejects: ${name}`, () => assert.equal(validate(mutate(good())).ok, false));

for (const [name, v] of [["null", null], ["array", []], ["string", "hi"], ["number", 7]])
  t(`rejects non-object body: ${name}`, () => assert.equal(validate(v).ok, false));

// --- ADT-284: the cumulative ceiling, the closed sets, the write path -------

t("a lifetime token total above 10M is accepted", () => {
  // THE LIVE DEFECT. `MAX_COUNT` bounded tokens at 1e7 while this install's real
  // lifetime total is 22,700,797, so the collector answered 400 and `send_ping`
  // swallowed it — every ping from this install has been dropped without a
  // trace. Asserted on schema 2 as well as 3 because the clients sending it
  // today are schema 2, and they are the ones being dropped.
  assert.equal(validate({ ...good2(), tokens: 22_700_797 }).ok, true);
  assert.equal(validate(good3()).ok, true);
});

t("rejects a gate name outside the closed set", () => {
  const b = good3();
  b.gates = { ...b.gates, evil: { ran: 1, caused_edit: 0, under_recorded: 0, tickets: 1 } };
  assert.equal(validate(b).ok, false);
  assert.deepEqual(GATE_NAMES, ["coverage", "plan-quality"]);
});

t("rejects a track name outside the closed set", () => {
  const b = good3();
  b.tracks = { ...b.tracks, "../etc/passwd": { closed: 1, stamped: 0, follow_ons: 0, defects: 0 } };
  assert.equal(validate(b).ok, false);
  // `unset` is IN the set: adt_metrics.by_track returns it for tickets with no
  // `track:` field, and dropping it would lose those tickets rather than reject
  // a bad value.
  assert.ok(TRACK_NAMES.includes("unset"));
});

t("no data point exceeds 20 doubles or 20 blobs, and a ping stays under the 250 point cap", () => {
  // Cloudflare's documented per-call limits: twenty blobs, twenty doubles, one
  // index; 250 data points per Worker invocation. Asserted against the platform
  // numbers rather than against this payload's arithmetic, so the check cannot
  // drift when a command, gate or track is added.
  const widest = { ...good3(),
    commands: Object.fromEntries(CMDSET_V1.map((n) => [n, 1])) };
  const pts = dataPoints(widest);
  assert.ok(pts.length <= 250, `${pts.length} points`);
  for (const p of pts) {
    assert.ok(p.doubles.length <= 20, `${p.doubles.length} doubles`);
    assert.ok(p.blobs.length <= 20, `${p.blobs.length} blobs`);
  }
  // The widest real ping: 1 install + 14 commands + 2 gates + 4 tracks.
  assert.equal(pts.length, 21);
});

t("a ping writes every point dataPoints returns, commands gates and tracks included", () => {
  // Completeness, not the cap. A build that wrote only the install point would
  // satisfy every ceiling check above and silently drop 8 rows in 9.
  const pts = dataPoints(good3());
  const kinds = pts.map((p) => p.blobs[3]);
  assert.equal(kinds.filter((k) => k === "install").length, 1);
  assert.equal(kinds.filter((k) => k === "command").length, 2, "close:0 is omitted");
  assert.equal(kinds.filter((k) => k === "gate").length, 2);
  assert.equal(kinds.filter((k) => k === "track").length, 4);
  assert.equal(pts.length, 9);
  assert.ok(pts.every((p) => p.blobs[2] === "totals-v1"));
});

t("schema 1 and 2 still write one cmdset-v1 row", () => {
  // The other half of the same claim: the new layout must not disturb what
  // deployed clients write, or three months of retention overlap breaks.
  for (const b of [good(), good2()]) {
    const pts = dataPoints(b);
    assert.equal(pts.length, 1);
    assert.equal(pts[0].blobs[2], "cmdset-v1");
    assert.equal(pts[0].doubles.length, CMDSET_V1.length + 2);
  }
});

// --- the counting harness -------------------------------------------------
// Every binding counts what it was asked to do. `handle` is the real function;
// only the bindings are stubs, which is the boundary that makes the cost claim
// mean something (ADT-153 — stub at the boundary that proves the claim).

function stubEnv() {
  const kv = new Map();
  const doStates = new Map();
  const meta = new Map();
  const env = {
    puts: 0, gets: 0, lists: 0, dataPoints: [],
    STATS: {
      async get(k) { env.gets++; return kv.has(k) ? kv.get(k) : null; },
      async put(k, v, opts) { env.puts++; kv.set(k, v);
        if (opts && opts.metadata) meta.set(k, opts.metadata); },
      // ADT-263 A1f. Faithful at the boundary: Cloudflare returns { keys,
      // list_complete, cursor }, and the dashboard pages until list_complete.
      async list({ prefix = "", cursor } = {}) {
        env.lists++;
        // Faithful at the boundary: Cloudflare returns metadata inline on
        // list(), which is what lets the dashboard read `first_seen` for every
        // install without one subrequest each.
        const keys = [...kv.keys()].filter((k) => k.startsWith(prefix))
          .map((name) => ({ name, metadata: meta.get(name) }));
        return { keys, list_complete: true, cursor: undefined };
      },
    },
    USAGE: { writeDataPoint(dp) { env.dataPoints.push(dp); } },
    IP_BUDGET: {
      idFromName(name) { return name; },
      get(id) {
        if (!doStates.has(id)) {
          const store = new Map();
          doStates.set(id, new IpBudget({
            storage: {
              async get(k) { return store.has(k) ? store.get(k) : undefined; },
              async put(k, v) { store.set(k, v); },
            },
          }));
        }
        // A stub's job is to be faithful at the boundary: Cloudflare hands the
        // object a Request, whatever the caller passed to stub.fetch(). Taking
        // the string straight through would let the class get away with an
        // interface production never gives it.
        const instance = doStates.get(id);
        return { fetch: (url) => instance.fetch(new Request(url)) };
      },
    },
    _kv: kv,
  };
  return env;
}

const post = (body, ip = "203.0.113.7") =>
  new Request("https://collector.example/", {
    method: "POST",
    headers: { "Content-Type": "application/json", "CF-Connecting-IP": ip },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });

const DAY1 = Date.parse("2026-09-06T12:00:00Z");
const DAY2 = Date.parse("2026-09-07T12:00:00Z");

// --- put accounting, one case per request class ---------------------------

await at("rejected request costs zero puts", async () => {
  const env = stubEnv();
  const bad = good(); bad.install_id = "not-a-uuid";
  const res = await handle(post(bad), env, DAY1);
  assert.equal(res.status, 400);
  assert.equal(env.puts, 0);
  assert.equal(env.dataPoints.length, 0, "a rejected payload must not reach the dataset");

  const res2 = await handle(post("{not json"), env, DAY1);
  assert.equal(res2.status, 400);
  assert.equal(env.puts, 0);
});

await at("new install costs exactly one put", async () => {
  const env = stubEnv();
  const res = await handle(post(good()), env, DAY1);
  assert.equal(res.status, 202);
  assert.equal(env.puts, 1);
  assert.ok(env._kv.has(`install:${UUID_A}`), "the identity record is the one put");
});

await at("returning install costs zero puts", async () => {
  const env = stubEnv();
  await handle(post(good()), env, DAY1);
  const before = env.puts;
  for (let i = 0; i < 25; i++) await handle(post(good()), env, DAY1);
  assert.equal(env.puts - before, 0, "steady state must be free of puts at any user count");
});

await at("returning install writes one data point and zero puts", async () => {
  const env = stubEnv();
  await handle(post(good()), env, DAY1);
  const puts = env.puts, points = env.dataPoints.length;
  await handle(post(good()), env, DAY1);
  assert.equal(env.puts - puts, 0);
  assert.equal(env.dataPoints.length - points, 1, "usage still lands on a return");
});

// --- the uninstall event -------------------------------------------------

const uninstall = (over = {}) => ({ event: "uninstall", install_id: UUID_B,
                                    version: "0.1.0", os: "Darwin", ...over });

await at("an uninstall event is accepted and writes one event point", async () => {
  assert.deepEqual([...EVENT_KEYS].sort(), ["event", "install_id", "os", "version"],
    "the allowlist matches send_uninstall_event in tools/adt_phone_home.py");
  assert.equal(validateEvent(uninstall()).ok, true);
  const env = stubEnv();
  const res = await handle(post(uninstall()), env, DAY1);
  assert.equal(res.status, 202);
  assert.equal(env.dataPoints.length, 1);
  assert.deepEqual(env.dataPoints[0], {
    indexes: [UUID_B],
    blobs: ["0.1.0", "Darwin", "totals-v1", "event", "uninstall"],
    doubles: [1],
  });
});

await at("an event with an extra key or an unknown name is rejected", async () => {
  assert.deepEqual(EVENT_NAMES, ["uninstall"], "there is no update event: updates come from the daily report");
  const cases = [
    [uninstall({ schema: 5 }), /unexpected keys: schema/],
    [uninstall({ commands: {} }), /unexpected keys: commands/],
    [uninstall({ event: "update" }), /closed set/],
    [uninstall({ event: "install" }), /closed set/],
    [uninstall({ install_id: "not-a-uuid" }), /uuid/],
    [uninstall({ version: "latest" }), /semver/],
  ];
  const { os, ...noOs } = uninstall();
  cases.push([noOs, /missing keys: os/]);
  for (const [body, why] of cases) {
    const v = validateEvent(body);
    assert.equal(v.ok, false, JSON.stringify(body));
    assert.match(v.why, why);
    const env = stubEnv();
    assert.equal((await handle(post(body), env, DAY1)).status, 400);
    assert.equal(env.dataPoints.length, 0, "a rejected event must not reach the dataset");
  }
});

await at("an uninstall event never registers an install", async () => {
  const env = stubEnv();
  await handle(post(uninstall()), env, DAY1);
  assert.equal(env.puts, 0, "an event writes no KV");
  assert.equal(env.gets, 0, "and does not even read identity");
  assert.equal(env._kv.has(`install:${UUID_B}`), false);
});

await at("version is not stored in the install record", async () => {
  // If identity carried version, every record would change on release day and
  // "put on change" would become one put per install in 24 hours.
  const env = stubEnv();
  await handle(post(good()), env, DAY1);
  const rec = env._kv.get(`install:${UUID_A}`);
  assert.ok(!rec.includes("0.1.0"), `version leaked into the identity record: ${rec}`);
  assert.deepEqual(Object.keys(JSON.parse(rec)), ["first_seen"]);

  const bumped = good(); bumped.version = "0.2.0";
  const puts = env.puts;
  await handle(post(bumped), env, DAY1);
  assert.equal(env.puts - puts, 0, "a version bump must not cost a put");
});

await at("puts do not scale with a fresh-uuid burst", async () => {
  // The defect this ticket exists to fix: 344 fresh uuids from 4 IPs, each one
  // a new key. The novel-install budget is what bounds it now.
  const env = stubEnv();
  for (let i = 0; i < 300; i++) {
    const b = good();
    b.install_id = UUID_A.slice(0, 24) + String(i).padStart(12, "0");
    await handle(post(b), env, DAY1);
  }
  assert.ok(env.puts <= BUDGETS.novelInstalls,
    `${env.puts} puts for 300 fresh uuids — the novel-install budget did not bound them`);
});

// --- the per-IP budgets ---------------------------------------------------

await at("one source cannot exceed its daily request budget", async () => {
  const env = stubEnv();
  let accepted = 0, refused = 0;
  for (let i = 0; i < BUDGETS.requests + 50; i++) {
    const res = await handle(post(good()), env, DAY1);
    if (res.status === 202) accepted++; else if (res.status === 429) refused++;
  }
  assert.equal(accepted, BUDGETS.requests);
  assert.equal(refused, 50);
});

await at("over budget writes no data point", async () => {
  const env = stubEnv();
  for (let i = 0; i < BUDGETS.requests; i++) await handle(post(good()), env, DAY1);
  const points = env.dataPoints.length, puts = env.puts;
  const res = await handle(post(good()), env, DAY1);
  assert.equal(res.status, 429);
  assert.equal(env.dataPoints.length - points, 0, "a refused request must cost no AE write");
  assert.equal(env.puts - puts, 0);
});

await at("replayed known id still counts against the budget", async () => {
  // A budget that only counted NOVEL ids would not bound anything: register a
  // handful of ids once, then replay them forever for unlimited AE writes.
  const env = stubEnv();
  await handle(post(good()), env, DAY1);
  let refused = 0;
  for (let i = 0; i < BUDGETS.requests + 10; i++)
    if ((await handle(post(good()), env, DAY1)).status === 429) refused++;
  assert.ok(refused > 0, "replaying a known id was never refused");
});

await at("budget resets on the day boundary", async () => {
  const env = stubEnv();
  for (let i = 0; i < BUDGETS.requests + 5; i++) await handle(post(good()), env, DAY1);
  assert.equal((await handle(post(good()), env, DAY1)).status, 429);
  assert.equal((await handle(post(good()), env, DAY2)).status, 202,
    "the next UTC day must start from zero");
});

await at("budgets are per IP, not global", async () => {
  const env = stubEnv();
  for (let i = 0; i < BUDGETS.requests + 5; i++)
    await handle(post(good(), "198.51.100.1"), env, DAY1);
  assert.equal((await handle(post(good(), "198.51.100.1"), env, DAY1)).status, 429);
  assert.equal((await handle(post(good(), "203.0.113.9"), env, DAY1)).status, 202,
    "one noisy source must not black out everyone else");
});

// --- the AE data point ----------------------------------------------------

await at("per-command counts reach the dataset in cmdset-v1 order", async () => {
  const env = stubEnv();
  await handle(post(good2()), env, DAY1);
  const dp = env.dataPoints[0];
  assert.deepEqual(dp.blobs, ["0.1.0", "Darwin", "cmdset-v1"]);
  assert.equal(dp.doubles.length, CMDSET_V1.length + 2);
  assert.equal(dp.doubles[CMDSET_V1.indexOf("plan")], 4);
  assert.equal(dp.doubles[CMDSET_V1.indexOf("build")], 2);
  assert.equal(dp.doubles[CMDSET_V1.indexOf("close")], 0);
});

await at("tickets and tokens are written as doubles 15 and 16", async () => {
  const env = stubEnv();
  await handle(post({ ...good2(), tickets: 7, tokens: 4242 }), env, DAY1);
  const dp = env.dataPoints[0];
  // Positional, and the position is the claim: a reader indexes double15 and
  // double16 by number, so an off-by-one here silently reports ticket counts as
  // a command's usage.
  assert.equal(dp.doubles.length, 16);
  assert.equal(dp.doubles[14], 7);
  assert.equal(dp.doubles[15], 4242);
  // 1-14 keep their meaning. That is what the cmdset-v1 blob guard versions,
  // and appending must not disturb it.
  assert.equal(dp.doubles[CMDSET_V1.indexOf("plan")], 4);
  assert.deepEqual(dp.doubles.slice(0, CMDSET_V1.length),
                   CMDSET_V1.map((n) => ({ plan: 4, build: 2 })[n] || 0));
});

await at("a schema 1 ping writes zeroes, not a missing point", async () => {
  const env = stubEnv();
  await handle(post(good()), env, DAY1);
  assert.equal(env.dataPoints.length, 1);
  assert.deepEqual(env.dataPoints[0].doubles,
                   [...CMDSET_V1.map(() => 0), 2, 100]);
});

t("the install record stamps the day it was first seen", () =>
  assert.equal(utcDay(DAY1), "2026-09-06"));

// --- the guard must be able to FAIL, not only pass ------------------------

await at("negative control: an unbounded budget would let the burst through", async () => {
  // Runs the same burst as the budget cases against a DO stub whose counter
  // never trips. If this does NOT flood, the assertions above are passing for
  // some reason other than the budget.
  const env = stubEnv();
  env.IP_BUDGET = {
    idFromName: (n) => n,
    get: () => ({ async fetch() { return new Response(JSON.stringify({ ok: true, count: 1 })); } }),
  };
  let accepted = 0;
  for (let i = 0; i < BUDGETS.requests + 50; i++)
    if ((await handle(post(good()), env, DAY1)).status === 202) accepted++;
  assert.equal(accepted, BUDGETS.requests + 50,
    "the budget cases above are not measuring the budget");
});

// --- ADT-254 Phase A: a KV quota failure is not a crash ---------------------
// The AE write happens BEFORE any KV touch, so on an exhausted day the usage
// lands and only the identity record fails. That is the designed degradation.
// Unguarded it escaped the handler as a 500 — a lie, and one that fires exactly
// when traffic is highest.

function throwingKvEnv({ onGet = false, onPut = false } = {}) {
  const env = stubEnv();
  const realGet = env.STATS.get, realPut = env.STATS.put;
  env.STATS = {
    async get(k) {
      if (onGet) throw new Error("KV GET failed: daily limit exceeded");
      return realGet.call(env.STATS, k);
    },
    async put(k, v) {
      if (onPut) throw new Error("KV PUT failed: daily limit exceeded");
      return realPut.call(env.STATS, k, v);
    },
  };
  return env;
}

await at("kv quota failure still returns 202", async () => {
  const env = throwingKvEnv({ onPut: true });
  const res = await handle(post(good()), env, DAY1);
  assert.equal(res.status, 202,
    "a KV quota failure must not be reported as a server error — the ping was accepted");
});

await at("kv quota failure still writes the data point", async () => {
  const env = throwingKvEnv({ onPut: true });
  await handle(post(good()), env, DAY1);
  assert.equal(env.dataPoints.length, 1,
    "usage must land even when the identity record cannot be written");
});

await at("a failing kv read also returns 202", async () => {
  const env = throwingKvEnv({ onGet: true });
  const res = await handle(post(good()), env, DAY1);
  assert.equal(res.status, 202);
  assert.equal(env.dataPoints.length, 1);
});

await at("a deferred identity is logged distinguishably", async () => {
  const env = throwingKvEnv({ onPut: true });
  const lines = [];
  const realLog = console.log;
  console.log = (m) => lines.push(String(m));
  try { await handle(post(good()), env, DAY1); } finally { console.log = realLog; }
  assert.ok(lines.some((l) => l.includes("identity deferred")),
    `a deferred install must be distinguishable from a registered one; logged: ${JSON.stringify(lines)}`);
});

await at("negative control: a healthy request logs no deferral and still puts", async () => {
  // Without this, the four cases above would all pass against a handler that
  // deferred EVERY identity write and logged every time.
  const env = stubEnv();
  const lines = [];
  const realLog = console.log;
  console.log = (m) => lines.push(String(m));
  try { await handle(post(good()), env, DAY1); } finally { console.log = realLog; }
  assert.equal(env.puts, 1, "a healthy new install must still cost exactly one put");
  assert.ok(!lines.some((l) => l.includes("identity deferred")),
    "a healthy request must not report a deferral");
});


// --- ADT-263: the dashboard read path --------------------------------------
//
// These drive the REAL handle() the same way the write-path cases do. `fetch` is
// stubbed at the boundary the Worker actually crosses — the Analytics Engine SQL
// endpoint — so what is under test is the route, the queries and the render,
// not a reimplementation of them.

const AE_ROWS = {
  // Legacy cmdset-v1 rows. Read with argMax now, so the fixture carries a
  // standing total and a window figure per column, not a per-ping sum.
  totals: [{
    pings: 120, installs_window: 3,
    tickets: 41, tickets_win: 6, tokens: 998877, tokens_win: 40000,
    build: 30, build_win: 4, plan: 12, plan_win: 2, close: 5, close_win: 1,
  }],
  versions: [{ name: "0.9.2", pings: 90 }, { name: "0.9.1", pings: 30 }],
  systems: [{ name: "Darwin", pings: 100 }, { name: "Linux", pings: 20 }],
  installs: [{ install: UUID_A, pings: 100, tickets: 40, tickets_win: 5,
               tokens: 900000, tokens_win: 30000 }],

  // ADT-284 totals-v1 rows, with this repo's real magnitudes.
  v1Totals: [{
    pings: 30, installs_window: 2,
    tickets: 51, tickets_win: 9, tokens: 22700797, tokens_win: 1200000,
    cost: 4382024945, cost_win: 190000000,
    cost_measured: 3111000000, cost_measured_win: 140000000,
    dod_amendments: 12, dod_amendments_win: 3,
    // Schema 5. This repo's own QA counts on 2026-09-14.
    qa_checks: 61, qa_fails: 22, qa_tickets: 38, qa_tickets_failed: 18,
  }],
  v1Installs: [{ install: UUID_C, pings: 30, tickets: 51, tickets_win: 9,
                 tokens: 22700797, tokens_win: 1200000,
                 cost: 4382024945, cost_win: 190000000,
                 cost_measured: 3111000000, cost_measured_win: 140000000,
                 dod_amendments: 12, dod_amendments_win: 3,
                 qa_checks: 61, qa_fails: 22, schema_seen: 5 }],
  v1Versions: [{ name: "0.9.3", pings: 30 }],
  v1Systems: [{ name: "Darwin", pings: 30 }],
  v1Commands: [{ name: "plan", total: 40, total_win: 7 },
               { name: "build", total: 22, total_win: 3 }],
  // ADT-378: one row per install. UUID_C ran plan 40 and build 22.
  v1ByInstall: [{ install: UUID_C, runs: 62, used: 2 }],
  v1GatesByInstall: [{ install: UUID_C, caused_edit: 6, tickets: 16 }],
  v1TracksByInstall: [{ install: UUID_C, closed: 71, defects: 32 }],
  v1ReviewersByInstall: [{ install: UUID_C, reviews: 30 }],
  v1Gates: [{ name: "coverage", ran: 23, ran_win: 5, caused_edit: 4, caused_edit_win: 1,
              under_recorded: 0, under_recorded_win: 0, tickets: 8, tickets_win: 2 },
            { name: "plan-quality", ran: 12, ran_win: 2, caused_edit: 2, caused_edit_win: 1,
              under_recorded: 0, under_recorded_win: 0, tickets: 8, tickets_win: 2 }],
  v1Tracks: [{ name: "fast", closed: 2, closed_win: 0, stamped: 2, stamped_win: 0,
               follow_ons: 0, follow_ons_win: 0, defects: 2, defects_win: 0,
               days_sum: 4, days_sum_win: 0, days_n: 2, days_n_win: 0 },
             { name: "full", closed: 21, closed_win: 3, stamped: 17, stamped_win: 2,
               follow_ons: 2, follow_ons_win: 0, defects: 11, defects_win: 1,
               days_sum: 105, days_sum_win: 0, days_n: 21, days_n_win: 0 },
             { name: "standard", closed: 44, closed_win: 4, stamped: 15, stamped_win: 1,
               follow_ons: 0, follow_ons_win: 0, defects: 19, defects_win: 2,
               days_sum: 396, days_sum_win: 0, days_n: 44, days_n_win: 0 },
             { name: "unset", closed: 4, closed_win: 0, stamped: 1, stamped_win: 0,
               follow_ons: 0, follow_ons_win: 0, defects: 0, defects_win: 0,
               days_sum: 4, days_sum_win: 0, days_n: 4, days_n_win: 0 }],
};

async function withFetch(stub, fn) {
  const real = globalThis.fetch;
  globalThis.fetch = stub;
  try { return await fn(); } finally { globalThis.fetch = real; }
}

/** Register an install exactly as `handle()` does: the value AND the metadata.
 *  The dashboard reads `first_seen` from metadata, so a fixture that writes only
 *  the value is not a record the collector would ever produce. */
const putInstall = (env, key, firstSeen) =>
  env.STATS.put(key, JSON.stringify({ first_seen: firstSeen }),
                { metadata: { first_seen: firstSeen } });

const dashEnv = (extra = {}) => {
  const env = stubEnv();
  env.AE_TOKEN = "t0ken";
  env.AE_ACCOUNT_ID = "acct";
  return Object.assign(env, extra);
};

/** The rendered page for the standard fixture, and its <style> body. Layout
 *  assertions need the CSS, which no other helper exposes. */
let _sample = null;
const render_sample = () => _sample;
const CSS_OF = (html) => (html.match(/<style>([\s\S]*?)<\/style>/) || ["", ""])[1];

/** One panel's markup, by heading. Asserting a bare figure against the whole
 *  page is weak — `100` also appears in a bar's `width:100%` — so every figure
 *  assertion below is scoped to the panel that is supposed to carry it. */
/** The value cells of one row, found by its label. Row-scoped rather than
 *  panel-scoped: a panel can carry the same rendered string in two rows, and an
 *  `includes` over the panel cannot tell which one is wrong. */
const valuesOf = (panel, label) => {
  const m = panel.match(new RegExp(
    `<div class="row[^"]*"><div class="k[^"]*">${label}</div>[\\s\\S]*?(?=<div class="row|</section>)`));
  assert.ok(m, `no row labelled ${label}`);
  return [...m[0].matchAll(/<div class="v[^"]*">([^<]*)<\/div>/g)]
    .map((x) => x[1].replace(/&ge; /g, "\u2265 ").replace(/&mdash;/g, "—"));
};

const panelOf = (html, title) => {
  const m = html.match(
    new RegExp(`<section class="panel[^"]*"[^>]*>(?:(?!</section>)[\\s\\S])*?<h2>${title}</h2>[\\s\\S]*?</section>`));
  assert.ok(m, `no panel titled ${title}`);
  return m[0];
};

/** The cells of one install's compare row, as text, by install id. */
const compareRow = (html, id) => {
  const m = html.match(new RegExp(`<tr[^>]*><th scope="row" class="first">(?:(?!</tr>)[\\s\\S])*?install=${id}[\\s\\S]*?</tr>`));
  return m && [...m[0].matchAll(/<td>([\s\S]*?)<\/td>/g)]
    .map((x) => x[1].replace(/<[^>]+>/g, "").replace(/&mdash;/g, "—").replace(/\s+/g, " ").trim());
};
/** Every compare row's cells, in page order. */
const compareRows = (html) => [...html.matchAll(/<tr[^>]*><th scope="row" class="first">[\s\S]*?<\/tr>/g)]
  .map((m) => [...m[0].matchAll(/<td>([\s\S]*?)<\/td>/g)]
    .map((x) => x[1].replace(/<[^>]+>/g, "").replace(/&mdash;/g, "—").replace(/\s+/g, " ").trim()));
const COL = Object.fromEntries(["tickets", "tokens", "cost", "commands", "reviews", "plans", "qa", "bugs",
  "handbacks", "blocked"].map((k, i) => [k, i]));
const helpedOf = (html) => html.match(/<section class="helped"[\s\S]*?<\/section>/)[0];

/** Enough installs to page and rank, with every column varied and some rates
 *  below the minimum. */
function manyInstalls(count) {
  const ids = Array.from({ length: count }, (_, k) =>
    `${(k + 1).toString(16).padStart(8, "0")}-0000-4000-8000-${String(k).padStart(12, "0")}`);
  const rows = { ...AE_ROWS, installs: [],
    v1Installs: ids.map((install, k) => ({ install, pings: 1, tickets: k + 1, tokens: 1000 * (k + 1),
      cost: 1e6 * (k + 1), schema_seen: k % 3 === 0 ? 4 : 5, handbacks: k, guard_denies: k % 5,
      qa_checks: 10 + (k % 7), qa_fails: k % 10 })),
    v1ByInstall: ids.map((install, k) => ({ install, runs: 3 * k, used: 1 + (k % 14) })),
    v1GatesByInstall: ids.map((install, k) => ({ install, caused_edit: k % 9, tickets: 5 + (k % 20) })),
    v1TracksByInstall: ids.map((install, k) => ({ install, closed: 12 + k, defects: k % 6 })),
    v1ReviewersByInstall: ids.map((install, k) => ({ install, reviews: 2 * k })),
  };
  return { ids, rows };
}


/** Dispatch on the SQL text, because the route sends thirteen different queries
 *  and a stub that ignored which one it was asked would let a swapped query
 *  pass. The dispatch mirrors what actually distinguishes them — the layout blob
 *  and the kind — so a query that changed shape lands in the wrong branch and
 *  fails loudly rather than silently reading someone else's rows. */
function whichQuery(sql) {
  if (sql.includes("AS first_at")) return "lifecycle";
  const v1 = sql.includes("'totals-v1'");
  if (sql.includes("GROUP BY install")) {
    if (sql.includes("blob4 = 'gate'")) return "v1GatesByInstall";
    if (sql.includes("blob4 = 'track'")) return "v1TracksByInstall";
    if (sql.includes("blob4 = 'surface'")) return "v1ReviewersByInstall";
    if (sql.includes("blob4 = 'command'")) return "v1ByInstall";
  }
  if (sql.includes("GROUP BY blob1")) return v1 ? "v1Versions" : "versions";
  if (sql.includes("GROUP BY blob2")) return v1 ? "v1Systems" : "systems";
  if (!v1) return sql.includes("FROM (") ? "totals" : "installs";
  if (sql.includes("blob4 = 'surface'")) return "v1Surfaces";
  if (sql.includes("blob4 = 'gate'")) return "v1Gates";
  if (sql.includes("blob4 = 'track'")) return "v1Tracks";
  if (sql.includes("blob4 = 'command'")) return "v1Commands";
  return sql.includes("FROM (") ? "v1Totals" : "v1Installs";
}

const load = (env, stub = aeStub(), path = "/dashboard") =>
  withFetch(stub, () => handle(new Request(`https://collector.example${path}`), env, DAY1));

function aeStub(rows = AE_ROWS, { fail = false } = {}) {
  return async (_url, init) => {
    if (fail) throw new Error("network is down");
    return { ok: true, status: 200,
             async json() { return { data: rows[whichQuery(init.body)] || [] }; } };
  };
}


await at("GET /dashboard renders the usage rows", async () => {
  const env = dashEnv();
  await putInstall(env, `install:${UUID_A}`, "2026-01-15");
  const res = await load(env);
  assert.equal(res.status, 200);
  assert.match(res.headers.get("Content-Type"), /text\/html/);
  const html = await res.text();
  assert.match(html, /<!doctype html>/i);
  // The VALUES from the stubbed rows, not just a page shape. Pings are the SUM
  // of both row sets now — 120 cmdset-v1 plus 30 totals-v1 — because an install
  // that has upgraded keeps old rows until retention ages them out, so neither
  // store is authoritative alone.
  assert.ok(html.includes("150"), "pings from both row sets");
  assert.ok(html.includes("0.9.2"), "a version from the legacy versions rows");
  assert.ok(html.includes("0.9.3"), "a version from the totals-v1 rows");
});

await at("an unset AE_TOKEN is a clean 503", async () => {
  const env = dashEnv();
  delete env.AE_TOKEN;
  const res = await load(env);
  assert.equal(res.status, 503);
  const body = await res.text();
  assert.ok(body.includes("AE_TOKEN"), "says which secret is missing");
  assert.ok(!/\bat\s+\w+\s+\(/.test(body), "no stack frames on a public path");
});

await at("an unset account id is a clean 503", async () => {
  const env = dashEnv();
  delete env.AE_ACCOUNT_ID;
  const res = await load(env);
  assert.equal(res.status, 503);
  const body = await res.text();
  assert.ok(body.includes("AE_ACCOUNT_ID"), "says which secret is missing");
  assert.ok(!/\bat\s+\w+\s+\(/.test(body), "no stack frames on a public path");
});

await at("an upstream query failure is a clean 502", async () => {
  // 502, not 503: the secrets are present and Cloudflare did not answer. The
  // operator fixes those two cases in different places.
  const env = dashEnv();
  const res = await load(env, aeStub(AE_ROWS, { fail: true }));
  assert.equal(res.status, 502);
  const body = await res.text();
  assert.ok(!/\bat\s+\w+\s+\(/.test(body), "no stack frames on a public path");
});

t("sampling weight is applied where rows are counted, and nowhere else", () => {
  // ONE RULE, STATED WHERE IT ACTUALLY BITES.
  //
  // `_sample_interval` corrects a count of EVENTS: Analytics Engine samples
  // under load and a surviving row stands for N dropped siblings, so counting
  // rows without the weight under-reports by an amount that grows with adoption.
  //
  // It corrects nothing else. Every double on both layouts is a lifetime
  // cumulative total — `command_counts()` has always recounted the whole log —
  // so the value reads are argMax/MAX/MIN, and multiplying a lifetime total by a
  // sample weight would invent activity rather than recover it.
  //
  // An earlier version of this test asserted every SUM carried the weight. That
  // was the rule for a page that summed per-ping counts, and it is wrong now in
  // both directions: it demands the weight on an outer rollup whose inner value
  // is already weighted (double-counting), and it would accept a weighted
  // cumulative read.
  const q = dashboardQueries();
  for (const [key, sql] of Object.entries(q)) {
    // 1. Nothing sums a raw double any more. That expression IS the defect.
    assert.ok(!/SUM\(_sample_interval \* double/.test(sql),
      `${key} still sums raw doubles`);
    assert.ok(!/SUM\(double\d/.test(sql), `${key} sums a raw double unweighted`);
    // 2. No sample weight inside a cumulative point read.
    for (const fn of ["argMax", "MAX", "MIN"])
      for (const m of sql.matchAll(new RegExp(`${fn}\\(([^)]*)\\)`, "g")))
        assert.ok(!m[1].includes("_sample_interval"),
          `sample weight applied to a cumulative value in ${key}: ${fn}(${m[1]})`);
    // 3. Where the query counts rows, it counts them weighted.
    if (/AS pings/.test(sql))
      assert.ok(/SUM\(_sample_interval\) AS pings|SUM\(pings\) AS pings/.test(sql),
        `${key} counts pings without the weight`);
  }
  // And the weight is genuinely still in use — a rule that removed it entirely
  // would satisfy every assertion above.
  assert.ok(Object.values(q).some((sql) => sql.includes("SUM(_sample_interval)")),
    "the weighting is still applied somewhere");
});

t("a standing total is argMax per install, then summed", () => {
  // Where things stand. The bug this replaces applied SUM to a lifetime total
  // re-sent daily, so three runs of `plan` read as nine after three pings.
  const q = dashboardQueries();
  assert.ok(q.v1Commands.includes("argMax(double1, timestamp) AS total"));
  assert.ok(/GROUP BY blob5, index1/.test(q.v1Commands), "per install first");
  assert.ok(/SELECT name, SUM\(total\) AS total/.test(q.v1Commands), "then across installs");
  assert.ok(!/SUM\(_sample_interval \* double/.test(q.v1Commands),
    "never a SUM over the raw doubles");
  assert.ok(q.v1Totals.includes("argMax(double2, timestamp) AS tokens"));
});

t("a window figure is MAX minus MIN per install, never a SUM of totals", () => {
  // What happened recently, derived from the same totals with no client cursor.
  const q = dashboardQueries();
  assert.ok(q.v1Commands.includes("MAX(double1) - MIN(double1) AS total_win"));
  assert.ok(q.v1Totals.includes("MAX(double2) - MIN(double2) AS tokens_win"));
  for (const key of ["v1Commands", "v1Totals", "v1Gates", "v1Tracks"])
    assert.ok(!/SUM\(_sample_interval \* double/.test(q[key]),
      `${key} still sums raw doubles`);
});

t("a negative window figure clamps to zero", () => {
  // MAX - MIN cannot go negative while the source totals are non-decreasing, and
  // nothing in this repo truncates usage.log or the cost ledger. A truncated one
  // would, and a negative bar is worse than a missing one.
  assert.equal(windowValue(-5), 0);
  assert.equal(windowValue("-12"), 0);
  assert.equal(windowValue(7), 7);
  assert.equal(windowValue(undefined), 0);
});

await at("the dashboard shows tickets and tokens", async () => {
  const env = dashEnv();
  const html = await (await load(env)).text();
  // Both row sets, added: 41 + 51 tickets, 998,877 + 22,700,797 tokens. Scoped
  // to the panel, because a bare figure matches a bar's width percentage too.
  const p = panelOf(html, "tickets and tokens");
  assert.ok(p.includes("tickets"), "the label");
  assert.ok(p.includes("92"), "tickets from both row sets");
  assert.ok(p.includes("23,699,674"), "tokens from both row sets");
});

await at("the dashboard shows per-command, version and os breakdowns", async () => {
  const env = dashEnv();
  const html = await (await load(env)).text();

  const cmds = panelOf(html, "per command");
  assert.ok(cmds.includes("build") && cmds.includes("30"), "a per-command count");

  // The NAMES were asserted here before and the COUNTS were not, so zeroing
  // every version and os figure left this case green. Criterion 3 is about what
  // the page shows, and a correct name against a wrong number is not it.
  const vers = panelOf(html, "versions");
  assert.ok(vers.includes("0.9.2") && vers.includes("90"), "a version and its count");
  assert.ok(vers.includes("0.9.1") && vers.includes("30"), "the other version and its count");

  const os = panelOf(html, "operating systems");
  assert.ok(os.includes("Darwin") && os.includes("100"), "an os and its count");
  assert.ok(os.includes("Linux") && os.includes("20"), "the other os and its count");
});

await at("two loads with different data render differently", async () => {
  // What rules out a cache or a build step: the page is a function of what the
  // query returned on THIS request.
  const env = dashEnv();
  const a = await (await load(env)).text();
  const other = JSON.parse(JSON.stringify(AE_ROWS));
  other.totals[0].pings = 7777;
  const b = await (await load(env, aeStub(other))).text();
  assert.notEqual(a, b);
  // 7777 legacy pings + 30 totals-v1 pings. Asserting the MERGED figure also
  // proves the page adds both row sets rather than reading one and ignoring
  // the other.
  assert.ok(b.includes("7,807") && !a.includes("7,807"));
});

await at("every panel states what it covers", async () => {
  const env = dashEnv();
  const html = await (await load(env)).text();
  const panels = [...html.matchAll(/<section class="panel[^"]*"([^>]*)>/g)];
  assert.ok(panels.length >= 5, `expected the panels, found ${panels.length}`);
  for (const p of panels)
    assert.match(p[1], /data-coverage="[^"]+"/, "a panel with no coverage");
});

await at("the install count is exact and the window figure is an estimate", async () => {
  // Two different measurements. KV holds every install ever, exactly; the AE
  // figure is a 30-day window and is weighted, so only that one is an estimate.
  // Labelling both would be as wrong as labelling neither.
  const env = dashEnv();
  for (const id of [UUID_A, UUID_B])
    await putInstall(env, `install:${id}`, "2026-01-15");
  const html = await (await load(env)).text();
  const sub = html.match(/<p class="sub">([\s\S]*?)<\/p>/)[1];
  // The union of what either store knows: UUID_A and UUID_B from KV, UUID_C
  // from the totals-v1 rows. An install AE has rows for and KV does not is real
  // — DEPLOY.md prunes probe keys — so dropping it would render an empty table
  // under a header saying three were active.
  assert.match(sub, /3 installs/, "the union count, not either store alone");
  assert.match(sub, /<span class="exact">3 installs<\/span>/,
    "the exact count carries the emphasis, so a skim does not land on the estimate");
  // "about" is what marks the sampled figure now. "(estimate)" trailing a number
  // read as a label on the wrong one, and "active" said nothing a reader could
  // check — an install is not active, it reported or it did not.
  assert.match(sub, /about 5 reported in the last 30 days/,
    "the windowed AE figure is the hedged one, summed across both layouts");
  assert.ok(!/\babout 3 installs\b/.test(sub), "the exact count is not hedged");
});

await at("the page is self-contained: no script and no external assets", async () => {
  const env = dashEnv();
  const html = await (await load(env)).text();
  assert.ok(!/<script/i.test(html), "no script tag");
  assert.ok(!/\ssrc=/i.test(html), "nothing loaded by src");
  assert.ok(!/<link/i.test(html), "no external stylesheet");
  assert.ok(!/https?:\/\//i.test(html.replace(/<html lang="en">/, "")),
    "no absolute URL to anything off the response");
});

await at("the page carries the viewport meta and the 900px collapse", async () => {
  const env = dashEnv();
  // The install table only renders when KV holds something, and this case
  // asserts on that table's scroll wrapper.
  await putInstall(env, `install:${UUID_A}`, "2026-01-15");
  const html = await (await load(env)).text();
  assert.match(html, /<meta name="viewport" content="width=device-width, initial-scale=1">/);
  assert.match(html, /@media\(max-width:900px\)\{\.grid\{grid-template-columns:1fr\}/);
  // The install row has three fixed tracks and cannot shrink to a phone width,
  // so it scrolls inside its own card. Without this it overflows the panel and
  // forces horizontal scroll on the whole page.
  assert.match(html, /\.cmp-wrap\{overflow-x:auto/, "the wide table scrolls in its card");
  assert.match(html, /<div class="cmp-wrap"><table class="cmp">/, "and the table is actually wrapped in it");
});

await at("the dashboard lists each install by uuid", async () => {
  const env = dashEnv();
  for (const id of [UUID_A, UUID_B])
    await putInstall(env, `install:${id}`, "2026-01-15");
  const html = await (await load(env, aeStub(), "/dashboard?dormant=show")).text();
  assert.ok(html.includes(UUID_A), "the active install");
  assert.ok(html.includes(UUID_B), "the other install");

  // Criterion 6 is "each install by its UUID, WITH WHAT THAT INSTALL HAS DONE".
  // Presence of the id was asserted and the activity was not, so rendering every
  // per-install figure as 0 left this case green.
  const row = compareRow(html, UUID_A);
  assert.ok(row, "the active install has a row");
  assert.equal(row[0], "40", "its tickets");
  assert.equal(row[1], "900K", "its tokens, in thousands");
});

await at("per-install activity is labelled an estimate", async () => {
  const env = dashEnv();
  await putInstall(env, `install:${UUID_A}`, "2026-01-15");
  const html = await (await load(env)).text();
  // ADT-284 moved this from a footnote under the table into the page explainer,
  // where the rest of the page's provenance now lives. The CLAIM is what is
  // asserted, not the sentence's old address: sampling is corrected on the
  // counts and not on the values, and the reader is told so.
  assert.match(html, /Analytics Engine samples under load/);
  assert.match(html, /Daily report and install counts are\s+weighted to undo that/);
  assert.match(html, /point reads of a running total/);
  // The note used to say the install count "is exact and comes from KV". Once
  // the count became the union of both stores that went false, and on a pruned
  // namespace it contradicted the panel right above it, which read
  // "3 total: 0 with a KV record".
  assert.ok(!/comes from KV\./.test(html),
    "the note must not claim the count comes from KV alone");
  assert.match(html, /every install either store knows about/);
});

await at("an install with no kv record still appears", async () => {
  // KV keys can be deleted (DEPLOY.md section 5 prunes probe data) while AE keeps
  // the rows for three months, so an install can exist in AE and not in KV.
  // Built from the KV list alone, the table would drop it.
  const env = dashEnv();                       // deliberately: nothing in KV
  const html = await (await load(env)).text();
  assert.equal(env._kv.size, 0, "fixture check: KV really is empty");
  assert.ok(AE_ROWS.installs.some((r) => r.install === UUID_A),
    "fixture check: UUID_A really does have an AE row");
  const row = compareRow(html, UUID_A);
  assert.ok(row, "the AE-only install is on the page");
  assert.equal(row[0], "40", "with its activity, not zeroes");
});

await at("an install with no rows in the window still appears", async () => {
  // UUID_B is in KV and has no AE row, so a table built from GROUP BY index1
  // alone would omit it, and an omitted row reads as "this install does not
  // exist" rather than "this install has stopped". It is hidden by default and
  // shown on request.
  const env = dashEnv();
  for (const id of [UUID_A, UUID_B])
    await putInstall(env, `install:${id}`, "2026-04-01");
  assert.ok(AE_ROWS.installs.every((r) => r.install !== UUID_B),
    "fixture check: UUID_B really has no AE row");
  const hidden = await (await load(env)).text();
  assert.ok(!compareRow(hidden, UUID_B), "a dormant install is hidden by default");
  const html = await (await load(env, aeStub(), "/dashboard?dormant=show")).text();
  assert.ok(html.includes(`install=${UUID_B}`), "and on the page when dormant installs are shown");
  assert.match(html, new RegExp(`<tr class="dormant"><th scope="row" class="first">(?:(?!</tr>)[\\s\\S])*${UUID_B}`),
    "marked dormant");
});

// --- ADT-284: the panels ---------------------------------------------------

await at("the page shows cost in dollars with the measured share", async () => {
  const env = dashEnv();
  const html = await (await load(env)).text();
  const p = panelOf(html, "tickets and tokens");
  // 4,382,024,945 micro-dollars is $4,382. Asserted PER ROW, not against the
  // panel: the two rows carried equal fixture values, so a panel-wide
  // `includes` passed while either one alone was wrong.
  assert.deepEqual(valuesOf(p, "cost"), ["$4,382", "$190"]);
  assert.deepEqual(valuesOf(p, "measured"), ["$3,111", "—"]);
  assert.ok(!/\b4382024945\b/.test(p), "micro-dollars never reach the page");
});

await at("each install has its own per-command counts", async () => {
  const env = dashEnv();
  const html = await (await load(env)).text();
  // Its OWN runs, from v1CommandsByInstall, not the all-installs rollup: plan 40
  // and build 22 make 62 runs of 2 commands.
  const row = compareRow(html, UUID_C);
  assert.ok(row, "the totals-v1 install has a row");
  assert.equal(row[3], "62 2/14");
});

await at("the gate and track panels render, each figure with its n", async () => {
  const env = dashEnv();
  const html = await (await load(env)).text();
  const g = panelOf(html, "gates");
  // The page labels each gate by the QUESTION it asks. "coverage" and
  // "plan-quality" are internal keys and appear on the wire, not on the screen.
  assert.ok(g.includes("does the DoD cover the spec?"), "the coverage gate, named by what it asks");
  assert.ok(g.includes("is this the right thing to build?"), "and the plan-quality gate");
  assert.ok(!/>coverage</.test(g), "the internal key is not shown as a label");
  assert.ok(g.includes("23"), "coverage rounds");
  assert.ok(g.includes("tickets (n)"), "the gate panel states its n too");
  const t = panelOf(html, "tracks");
  for (const name of ["fast", "standard", "full", "unset"])
    assert.ok(t.includes(name), `track ${name} is on the page`);
  // The n is the closed-ticket count, and it is rendered beside the defects it
  // qualifies. A defect rate without its n is the thing this panel must not be.
  assert.ok(/unfinished-work counts on \d+ of \d+ closed tickets/.test(t),
    "how complete the follow-on data is sits in the coverage line, not a column");
  assert.ok(t.includes("21") && t.includes("11"), "full: 21 closed, 11 defects");
});

await at("the gate panel shows plan-review rounds and DoD rounds", async () => {
  const env = dashEnv();
  const g = panelOf(await (await load(env)).text(), "gates");
  assert.ok(g.includes("times run"), "review rounds are labelled");
  assert.ok(/12 Definition-of-Done rewrites/.test(g),
    "rewrites are context in the coverage line, not a row pretending to be a gate");

  assert.ok(g.includes("changed"), "and how often a gate changed the plan");
});

await at("every panel carries an explainer naming its source and period", async () => {
  const env = dashEnv();
  const html = await (await load(env)).text();
  // One <details> per panel, plus the page-level one. No script anywhere: the
  // disclosure is native HTML, which is what lets the page stay self-contained.
  const panels = html.match(/<section class="panel/g) || [];
  // STRUCTURAL, not textual: every panel is documented twice — how the figure
  // is derived, and what decision it informs. Deliberately does not match the
  // summary wording. A check that greps for a sentence proves a string exists
  // and makes the copy immovable; whether the prose is any good is settled by
  // reading the rendered page, not by a regex.
  const inPanels = (html.match(/<details class="ex(?: tells)?"/g) || []).length;
  const pageLevel = (html.match(/<details class="ex page"/g) || []).length;
  assert.equal(inPanels, panels.length * 2,
    "every panel carries both disclosure blocks");
  assert.equal(pageLevel, 1, "and the page carries one of its own");
  assert.ok(html.includes('<details class="ex page"'), "and one for the page");
  assert.ok(!/<script/i.test(html), "still no javascript");
  for (const title of ["per command", "versions", "operating systems", "compare installs",
                       "gates", "tracks", "tickets and tokens"]) {
    const p = panelOf(html, title);
    assert.equal((p.match(/<details/g) || []).length, 2,
      `${title} carries both disclosure blocks`);
  }
  // The page-level block owes the reader the three things a figure cannot say
  // about itself.
  for (const term of ["What a daily report is", "Sampling", "Retention", "Lifetime versus"])
    assert.ok(html.includes(term), `the page explainer covers ${term}`);
});

await at("each figure is labelled lifetime or last 30 days", async () => {
  const env = dashEnv();
  const html = await (await load(env)).text();
  // Success criterion 2: a reader tells a standing total from a window figure
  // without knowing how either is computed, so the label sits in the column
  // header next to the number, not in a footnote.
  for (const title of ["per command", "tickets and tokens"]) {
    const p = panelOf(html, title);
    assert.ok(p.includes("lifetime"), `${title} labels the standing total`);
    assert.ok(p.includes("last 30 days"), `${title} labels the window figure`);
  }
});

// --- ADT-284 design review: the two layout bugs it found, now graded --------

await at("render a sample page for the layout assertions", async () => {
  const env = dashEnv();
  await putInstall(env, `install:${UUID_C}`, "2026-01-15");
  _sample = await (await load(env)).text();
  assert.ok(_sample.includes("<style>"), "the page carries its own styles");
});

/** Column tracks declared for each table variant, e.g. { g4: 6, inst: 7 }. Split
 *  on spaces outside parentheses, so `minmax(6em,1fr)` is one track. */
const tableTracks = (css) => Object.fromEntries(
  [...css.matchAll(/\.tbl\.([a-z0-9]+)\{grid-template-columns:([^;}]+)/g)].map(([, cls, list]) => {
    let depth = 0, n = 1;
    for (const ch of list.trim()) {
      if (ch === "(") depth++; else if (ch === ")") depth--;
      else if (ch === " " && depth === 0) n++;
    }
    return [cls, n];
  }));

await at("every table declares one track per cell it renders", () => {
  // The design review found eight tracks against seven cells: the per-install
  // command list landed in a 90px column while the 220px one sized for it sat
  // empty. A cell/track mismatch is invisible in every content assertion above,
  // which is why it took a human read to find and why it is asserted here now.
  // Rows take their columns from the table through `subgrid`, so a row with a
  // cell too many would wrap onto a line of its own.
  const html = render_sample();
  const tracks = tableTracks(CSS_OF(html));
  const tables = [...html.matchAll(/<div class="tbl ([a-z0-9]+)">/g)].map((m) => m[1]);
  for (const cls of ["cmd", "one", "nb", "p2", "p3", "g4", "g5"])
    assert.ok(tables.includes(cls), `the sample renders a ${cls} table`);
  for (const [, cls, body] of html.matchAll(
      /<div class="tbl ([a-z0-9]+)">([\s\S]*?)(?=<details|<\/section>|<p class="empty">)/g)) {
    assert.ok(tracks[cls], `.tbl.${cls} declares its tracks`);
    for (const [row] of body.matchAll(/<div class="row[^"]*">(?:(?!<div class="row)[\s\S])*/g)) {
      const cells = (row.match(/<div class="(?:k|v|bar)[ "]/g) || []).length;
      assert.equal(cells, tracks[cls],
        `.tbl.${cls} declares ${tracks[cls]} tracks for a row of ${cells} cells: ${row.slice(0, 80)}`);
    }
  }
});

await at("every grid collapses at the 900px breakpoint", () => {
  // `.grid.two` is two classes; the collapse rule targets one. Specificity beats
  // media scoping, so without its own override the gates/tracks pair stayed
  // side by side at 375px — and those rows cannot shrink below ~334px, so the
  // page itself scrolled horizontally.
  const css = CSS_OF(render_sample());
  const collapses = [...css.matchAll(/@media\(max-width:900px\)\{([^}]*(?:\}[^@]*?)?)\}/g)]
    .map((m) => m[0]).join("");
  // Every grid variant, discovered from the CSS rather than listed here — a new
  // one added without a collapse rule is exactly the bug this catches, and a
  // hardcoded list would not see it.
  const variants = [...new Set([...css.matchAll(/(\.grid(?:\.[a-z]+)?)\{grid-template-columns/g)]
    .map((m) => m[1]))];
  assert.ok(variants.length >= 2, `only found ${variants.join(", ")}`);
  for (const sel of variants)
    assert.ok(collapses.includes(sel + "{") || collapses.includes(sel + ","),
      `${sel} has no collapse rule inside the 900px breakpoint`);
});

// --- ADT-284 QA finding 2: every rendered figure is asserted ----------------
//
// The mutation sweep caught 19 of 102 figures. The suite asserted structure and
// labelling thoroughly and values only spottily, which is inverted for a page
// whose deliverable IS its figures. This pins every value the page renders, row
// by row, so corrupting any of them fails here.
//
// Written as one table per panel rather than forty separate asserts: the table
// is the page, and a figure added to the page without a row here shows up as a
// length mismatch rather than passing unnoticed.

await at("every figure the page renders is pinned to its row", async () => {
  const env = dashEnv();
  await putInstall(env, `install:${UUID_C}`, "2026-01-15");
  await putInstall(env, `install:${UUID_A}`, "2026-01-15");
  const html = await (await load(env)).text();

  const EXPECTED = {
    "per command": [
      ["build", ["52", "7"]],          // 30 legacy + 22 totals-v1, 4 + 3 window
      ["plan", ["52", "9"]],           // 12 + 40, 2 + 7
      ["close", ["5", "1"]],           // legacy only
    ],
    "gates": [
      // tickets (n), times run, changed the plan, % changed. Scope first, then
      // activity, then effect. n=8 is below MIN_N so the rate is withheld.
      ["does the DoD cover the spec\\?", ["8", "23", "4", "—", "0"]],
      ["is this the right thing to build\\?", ["8", "12", "2", "—", "0"]],
    ],
    "tracks": [
      // Two triples: closed, defects, % of closed — then recorded, follow-ons,
      // % of recorded. Each rate sits beside the number it divides by.
      // fast and unset are below MIN_N on both, so both rates are withheld.
      ["fast", ["2", "2", "—", "0", "—", "2.0"]],
      ["full", ["21", "11", "52.4%", "2", "11.8%", "5.0"]],
      ["standard", ["44", "19", "43.2%", "0", "0.0%", "9.0"]],
      ["unset", ["4", "0", "—", "0", "—", "1.0"]],
    ],
    "tickets and tokens": [
      ["tickets", ["92", "15"]],                    // 41 + 51, 6 + 9
      ["tokens", ["23,699,674", "1,240,000"]],      // 998,877 + 22,700,797
      ["cost", ["$4,382", "$190"]],
      ["measured", ["$3,111", "—"]],
    ],
  };

  for (const [title, rows] of Object.entries(EXPECTED)) {
    const p = panelOf(html, title);
    for (const [label, values] of rows)
      assert.deepEqual(valuesOf(p, label), values, `${title} / ${label}`);
    // The panel carries no row this table does not name. Without this a figure
    // could be added to the page and never asserted, which is the hole being
    // closed.
    const labelled = (p.match(/<div class="row(?! head)[^"]*"><div class="k[^"]*">([^<]*)</g) || []).length;
    assert.equal(labelled, rows.length, `${title}: unpinned rows on the page`);
  }

  // The compare table, one row per install, its figures from different row sets.
  assert.deepEqual(compareRow(html, UUID_C),
    ["51", "22,701K", "$4,382", "62 2/14", "0.42", "37.5%", "36.1%", "45.1", "0.00", "0.0"]);
  const a = compareRow(html, UUID_A);
  assert.deepEqual(a.slice(0, 3), ["40", "900K", "not reported"]);

  // An install with both legacy and totals-v1 rows adds them, and its supervision
  // figures come from its own row.
  const both = { ...AE_ROWS,
    installs: [...AE_ROWS.installs, { install: UUID_C, pings: 5, tickets: 4, tickets_win: 0, tokens: 300000, tokens_win: 0 }],
    v1Installs: [{ ...AE_ROWS.v1Installs[0], handbacks: 17, guard_denies: 3, blocked_markers: 5 }] };
  assert.deepEqual(compareRow(await (await load(env, aeStub(both))).text(), UUID_C),
    // 51 + 4 tickets, 22,700,797 + 300,000 tokens; 17 handbacks and 3 blocked filings over 71 closed tickets.
    ["55", "23,001K", "$4,382", "62 2/14", "0.42", "37.5%", "36.1%", "45.1", "0.24", "4.2"]);
});

// --- one bad query must not blank the page ---------------------------------

/** A stub that rejects ONE named query with a real AE-shaped 422 body and
 *  serves the rest normally. */
const aeStubReject = (target, status = 422, body = "line 1:8 no viable alternative at input 'argMax'") =>
  async (_url, init) => {
    if (whichQuery(init.body) === target)
      return { ok: false, status, async text() { return body; },
               async json() { return {}; } };
    return { ok: true, status: 200,
             async json() { return { data: AE_ROWS[whichQuery(init.body)] || [] }; } };
  };

await at("one rejected query costs its panel, not the page", async () => {
  // Twelve queries run per load. Throwing on the first rejection took all
  // twelve panels to a 502 — the whole dashboard blank because one query asked
  // for something Analytics Engine did not like.
  const env = dashEnv();
  await putInstall(env, `install:${UUID_C}`, "2026-01-15");
  const res = await load(env, aeStubReject("v1Gates"));
  assert.equal(res.status, 200, "the page still renders");
  const html = await res.text();
  // The panels fed by the queries that DID succeed are intact.
  assert.ok(panelOf(html, "tickets and tokens").includes("$4,382"), "cost survived");
  assert.ok(panelOf(html, "per command").includes("plan"), "per-command survived");
});

await at("a rejected query names itself and quotes what AE said", async () => {
  // `analytics engine returned 422` says a query was rejected without saying
  // which of twelve or why, while AE's body says both. Discarding it turned a
  // one-line fix into a round trip through whoever holds the token.
  const env = dashEnv();
  const html = await (await load(env, aeStubReject("v1Tracks"))).text();
  const warn = html.match(/<section class="panel warn">[\s\S]*?<\/section>/);
  assert.ok(warn, "the page says something is missing");
  assert.ok(warn[0].includes("v1Tracks"), "and names the query");
  assert.ok(warn[0].includes("422"), "with the status");
  assert.ok(warn[0].includes("no viable alternative"), "and AE's own message");
  assert.ok(/1 of \d+ queries failed/.test(warn[0]), "and how much is missing");
});

await at("every query failing is still a 502", async () => {
  // A page with nothing on it is an upstream outage, not a degraded render.
  const env = dashEnv();
  const res = await load(env, async () => ({
    ok: false, status: 503, async text() { return "upstream down"; },
    async json() { return {}; } }));
  assert.equal(res.status, 502);
  const body = await res.text();
  assert.ok(body.includes("503"), "carries the upstream status");
  assert.ok(!/\bat\s+\w+\s+\(/.test(body), "no stack frames on a public path");
});

await at("a kv failure degrades the compare table, not the page", async () => {
  const env = dashEnv();
  env.STATS.list = async () => { throw new Error("kv unavailable"); };
  const res = await load(env);
  assert.equal(res.status, 200);
  const html = await res.text();
  assert.ok(html.includes("kv: kv unavailable"), "says KV is what failed");
  assert.ok(panelOf(html, "tickets and tokens").includes("$4,382"), "the rest is current");
});

t("no query calls COUNT with an argument", () => {
  // Analytics Engine rejects it: "COUNT() function must have 0 arguments".
  // Two of the twelve dashboard queries shipped with `COUNT(install)` and every
  // DoD condition was green, because the conditions graded the query STRINGS
  // and never executed one. This is the cheap half of that lesson; the
  // executable half is collector/check-queries.mjs, which the deploy runs.
  for (const [name, sql] of Object.entries(dashboardQueries()))
    for (const m of sql.matchAll(/\bCOUNT\s*\(([^)]*)\)/gi))
      assert.equal(m[1].trim(), "",
        `${name} calls COUNT(${m[1]}); Analytics Engine requires COUNT()`);
});

// --- ADT-284: four defects visible on the deployed page --------------------

await at("no coverage line ships an HTML entity through the escaper", async () => {
  // `coverage` goes through esc(), which turns `&` into `&amp;` — so `&middot;`
  // reached the page as literal text in the gates and tracks panels. The page
  // subtitle is NOT escaped and may use entities; a panel's coverage may not.
  const env = dashEnv();
  const html = await (await load(env)).text();
  for (const m of html.matchAll(/<div class="cov">([^<]*)<\/div>/g))
    assert.ok(!/&(amp|middot|mdash|ge|nbsp);/.test(m[1]),
      `escaped entity visible in a coverage line: ${m[1]}`);
  // And the separator still renders as a character.
  assert.ok(html.includes("\u00b7"), "the middot is a real character");
});

await at("a lifetime panel does not blame the last 30 days when it is empty", async () => {
  // Gates, tracks and cost are LIFETIME figures. "nothing in the last 30 days"
  // says activity stopped; the truth is nothing was ever reported.
  const env = dashEnv();
  const empty = { ...AE_ROWS, v1Gates: [], v1Tracks: [], v1Totals: [] };
  const html = await (await load(env, aeStub(empty))).text();
  for (const title of ["gates", "tracks"]) {
    const p = panelOf(html, title);
    assert.ok(!p.includes("nothing in the last 30 days"),
      `${title} is a lifetime panel and must not cite the window`);
    assert.ok(/no (reviews|closed tickets) reported yet/.test(p),
      `${title} says what is actually absent`);
  }
});

t("no table column is a fixed pixel width", () => {
  // Every column used to be a pixel width, sized to the text at the font size
  // on the page. Under a browser minimum font size the same text is wider, and
  // the gate and track headings ran into each other ("TICKETSTIMES",
  // "CLOSEDAFTER"). A column is sized from its widest heading or value instead,
  // so it fits at whatever size the text renders.
  for (const [, cls, list] of CSS_OF(render_sample())
      .matchAll(/\.tbl\.([a-z0-9]+)\{grid-template-columns:([^;}]+)/g))
    assert.ok(!/\d+px/.test(list), `.tbl.${cls} has a pixel track: ${list}`);
});

await at("the roster costs one subrequest per 1000 installs, not one per install", async () => {
  // A KV call is a SUBREQUEST, and the free plan allows 50 per request. Reading
  // `first_seen` from each record's value cost one `get()` per install, which
  // capped the dashboard at about 37 installs — measured, not guessed:
  // 12 AE queries + 1 list + N gets against a limit of 50.
  const env = dashEnv();
  for (let i = 0; i < 120; i++)
    await putInstall(env, `install:${String(i).padStart(8, "0")}-0000-4000-8000-000000000000`,
                     "2026-01-15");
  const before = { gets: env.gets, lists: env.lists };
  await (await load(env)).text();
  const gets = env.gets - before.gets, lists = env.lists - before.lists;
  assert.equal(gets, 0, `the roster read ${gets} values; metadata comes back on list()`);
  assert.ok(lists <= 2, `${lists} list calls for 120 installs`);
});

await at("an install registered before metadata existed still renders", async () => {
  // Its `first_seen` is unknown. The row must not vanish.
  const env = dashEnv();
  await env.STATS.put(`install:${UUID_B}`, JSON.stringify({ first_seen: "2026-01-15" }));
  const html = await (await load(env, aeStub(), "/dashboard?dormant=show")).text();
  assert.ok(compareRow(html, UUID_B), "the legacy record still has a row");
});

// --- ADT-371: plain text, the QA panel, the reviewers filter, schema 5 -------

/** The "what this tells you" text of every panel, as approved in ADT-371's plan.
 *  Word for word, so the page cannot drift back to text nobody approved. */
const APPROVED_TELLS = {
  "per command": "Shows which ADT commands are used and which are not. A command with a high lifetime count but a low count for the last 30 days has stopped being used. A command that never appears here is not used at all, but it still has to be documented, installed and tested.",
  "versions": "Shows whether installs are updating to new releases. After a release, most daily reports should come from the new version within a few days. If an older version is still sending reports weeks later, some installs have not updated.",
  "operating systems": "Shows which operating systems ADT is running on. ADT's shell tests only run on macOS, so reports from Linux come from installs whose shell scripts have not been tested on that system.",
  "tickets and tokens": "Shows how much work goes through ADT and what it costs. Tickets is the number of pieces of work, tokens is how much model use they took, and cost is what that use cost in US dollars. Tokens cost different amounts on different models, so compare spending by cost, not by tokens.",
  "gates": "Shows whether the two plan reviews are worth running. Every review takes time and tokens. Changed the plan counts the tickets where a review led to a change in the plan before any code was written. If a review runs many times but rarely changes a plan, it may not be worth running on every ticket.",
  "tracks": "Compares the three tracks: fast, standard and full. Full-track tickets go through the most checks, so they should have the fewest bugs after closing. Compare bugs after with tickets closed on each row. Also check work left unfinished, because a track can show few bugs when unfinished work was moved into new tickets. Unset counts tickets that closed without a track, and they cannot be compared.",
  "autonomy": "Shows how often the agent had to stop and wait for a person. Handed back to a human counts each time the agent stopped and waited for you. If the number per ticket falls over time, ADT is doing more of the work without needing you. Spawns refused by the guard counts the times ADT stopped the agent from opening a new ticket you had not approved.",
  "reviewers": "Shows which agents and skills ran, and how often. Select ADT reviewers only to see just the five review agents ADT ships. A review agent that never appears is not being used. The security and design reviewers do not show in the gates panel, so this is the only place their use is recorded.",
  "build problems QA found": "Shows how often QA found a problem in a finished build. QA runs each ticket's Definition of Done checks against what the build delivered, and sends the ticket back to be fixed if a check fails or work is missing. Each ticket sent back is a problem the build had handed over as ready, which ADT caught before it merged. If the share sent back falls over time, more builds are passing QA the first time.",
  "updates": "Shows how many installs take each release, and how fast. A release that few installs move to within a week or two is one people are not updating to.",
  "uninstalls": "Shows how many installs are removed rather than just going quiet. A rise in uninstalled after a release points at that release. Gone quiet cannot say why an install stopped reporting.",
  "compare installs": "Shows which installs ADT helps most and which it helps least. Rank any column to see the installs at the top or the bottom. The caught columns count problems ADT stopped before merge, and the escaped column counts bugs that got through after a ticket closed. Rates are hidden for installs with too few tickets, because a handful of tickets can swing a rate a long way.",
};

const blockText = (panel, summary) => {
  const m = panel.match(new RegExp(
    `<details class="ex[^"]*"><summary>${summary}</summary><p>([\\s\\S]*?)</p></details>`));
  return m && m[1];
};

await at("every what-this-tells-you text is the approved wording", () => {
  const html = render_sample();
  const titles = [...html.matchAll(/<h2>([^<]+)<\/h2>/g)].map((m) => m[1])
    .filter((t) => t !== "some data is missing");
  assert.deepEqual([...titles].sort(), Object.keys(APPROVED_TELLS).sort(),
    "every panel on the page has approved wording, and no approved panel is missing");
  for (const [title, text] of Object.entries(APPROVED_TELLS))
    assert.equal(blockText(panelOf(html, title), "what this tells you"), text, title);
});

await at("the page says daily reports and never pings", () => {
  const visible = render_sample().replace(/<style>[\s\S]*?<\/style>/, "")
    .replace(/<[^>]+>/g, " ");
  assert.ok(!/\bpings?\b/i.test(visible), "no visible ping");
  assert.ok(/daily reports/.test(visible), "and it says daily reports");
});

await at("the definition of done panel is not on the page", () => {
  const html = render_sample();
  assert.ok(!/<h2>definition of done<\/h2>/i.test(html), "no panel heading");
  assert.ok(!html.includes("proven red then green"), "no row from it");
});

await at("the build problems QA found panel shows QA checks that sent the build back", async () => {
  const p = panelOf(render_sample(), "build problems QA found");
  assert.deepEqual(valuesOf(p, "QA checks that sent the build back"), ["22", "61", "36.1%"]);
  assert.deepEqual(valuesOf(p, "tickets sent back at least once"), ["18", "38", "47.4%"]);
  assert.equal(blockText(p, "what this counts"),
    "QA checks is the number of QA results written into tickets' QA reports, and sent back is how many of them were FAIL. "
    + "Tickets is the number of tickets QA has checked, and sent back at least once is how many of those failed QA one or "
    + "more times. A QA run that was not written into the ticket is not counted. Percentages are hidden below ten.");

  // Below ten, the share is withheld and the counts still show.
  const few = { ...AE_ROWS, v1Totals: [{ ...AE_ROWS.v1Totals[0],
    qa_checks: 9, qa_fails: 4, qa_tickets: 5, qa_tickets_failed: 3 }] };
  const small = panelOf(await (await load(dashEnv(), aeStub(few))).text(), "build problems QA found");
  assert.deepEqual(valuesOf(small, "QA checks that sent the build back"), ["4", "9", "—"]);
});

await at("the reviewers panel has an ADT reviewers only filter that needs no script", async () => {
  const rows = { ...AE_ROWS, v1Surfaces: [
    { name: "adt-dod-coverage-reviewer", total: 20, total_win: 1 },
    { name: "dod-coverage-reviewer", total: 168, total_win: 0 },
    { name: "adt-brief", total: 20, total_win: 2 },
    { name: "general-purpose", total: 61, total_win: 4 },
  ] };
  const html = await (await load(dashEnv(), aeStub(rows))).text();
  const p = panelOf(html, "reviewers");
  assert.match(p, /<input type="checkbox" class="flt" id="adt-reviewers-only"><label class="flt-btn" for="adt-reviewers-only">ADT reviewers only<\/label><div class="tbl p2">/,
    "the checkbox comes straight before the table, so the CSS sibling selector reaches it");
  const kept = [...p.matchAll(/<div class="row p2 adt-rev"><div class="k">([^<]+)<\/div>/g)].map((m) => m[1]);
  assert.deepEqual(kept, ["adt-dod-coverage-reviewer"], "only ADT review agents are kept by the filter");
  assert.match(CSS_OF(html), /\.flt:checked~\.tbl \.row:not\(\.head\):not\(\.adt-rev\)\{display:none\}/);
  assert.equal(blockText(p, "what this counts"),
    "Every agent and skill that ran, and how many times. ADT ships five review agents. Only two of them write the record "
    + "the gates panel reads, so the other three appear only here. A name without the adt- prefix is either another "
    + "project's own agent or a skill.");
  assert.ok(!/<script/i.test(html), "still no script on the page");
});

await at("a schema 5 ping with QA counts is accepted and stored", () => {
  const v4 = { ...good3(), schema: 4, handbacks: 3, guard_denies: 1, blocked_markers: 0,
    dod_conditions: 10, dod_pinned: 7, input_tokens: 5, output_tokens: 6,
    cache_read_tokens: 7, cache_write_tokens: 8, surfaces: {}, models: {} };
  assert.equal(validate(v4).ok, true, "schema 4 is still accepted");
  assert.equal(dataPoints(v4)[0].doubles[13], 8, "and still writes its 14 figures first");

  const v5 = { ...v4, schema: 5, qa_checks: 61, qa_fails: 22, qa_tickets: 38, qa_tickets_failed: 18 };
  assert.deepEqual(validate(v5), { ok: true });
  const install = dataPoints(v5)[0];
  assert.equal(install.blobs[3], "install");
  assert.deepEqual(install.doubles.slice(14, 18), [61, 22, 38, 18], "doubles 15-18 carry the QA counts");

  // The dashboard reads each field back from the double it was stored in. The
  // values are distinct, so a field's position in the stored doubles names the
  // column the totals query has to read it from.
  const sql = dashboardQueries().v1Totals;
  for (const k of ["qa_checks", "qa_fails", "qa_tickets", "qa_tickets_failed"])
    assert.ok(sql.includes(`argMax(double${install.doubles.indexOf(v5[k]) + 1}, timestamp) AS ${k},`),
      `the totals query reads ${k} from the double it is stored in`);

  const { qa_tickets_failed, ...missing } = v5;
  assert.equal(validate(missing).ok, false, "a schema 5 ping must carry every QA field");
  assert.equal(validate({ ...v5, qa_fails: 62 }).ok, false, "more fails than checks is refused");
});

// --- ADT-378: how ADT helped, install-level figures, compare installs ------

t("one install narrows every query and the per-install queries return one row per install", () => {
  const narrowed = dashboardQueries(UUID_A);
  for (const [name, sql] of Object.entries(narrowed))
    assert.ok(sql.includes(`AND index1 = '${UUID_A}'`), `${name} is narrowed to the install`);
  for (const [name, sql] of Object.entries(dashboardQueries()))
    assert.ok(!sql.includes("index1 = '"), `${name} is not narrowed without an install`);
  for (const name of ["v1CommandsByInstall", "v1GatesByInstall", "v1TracksByInstall", "v1ReviewersByInstall"]) {
    const sql = dashboardQueries()[name];
    assert.match(sql, /^SELECT install,/, `${name}: the outer query selects the install`);
    assert.match(sql, /\)\s*GROUP BY install$/, `${name}: and groups by nothing but the install`);
  }
  assert.match(dashboardQueries().v1ReviewersByInstall, /blob5 LIKE 'adt-%-reviewer'/, "reviews count ADT reviewers only");
  // Nothing hermetic runs the SQL, so each per-install column is held to the double
  // the all-installs query of the same kind reads for it.
  const reads = (sql) => Object.fromEntries([...sql.matchAll(/argMax\(double(\d+), timestamp\) AS (\w+)/g)].map((m) => [m[2], m[1]]));
  const q = dashboardQueries();
  for (const [byInstall, total] of [["v1CommandsByInstall", "v1Commands"], ["v1GatesByInstall", "v1Gates"],
                                    ["v1TracksByInstall", "v1Tracks"], ["v1ReviewersByInstall", "v1Surfaces"]]) {
    const own = reads(q[byInstall]);
    assert.ok(Object.keys(own).length > 0, `${byInstall} reads at least one double`);
    for (const [alias, d] of Object.entries(own))
      assert.equal(d, reads(q[total])[alias], `${byInstall} reads ${alias} from the same double as ${total}`);
  }
  assert.match(q.v1CommandsByInstall, /SUM\(total\) AS runs, COUNT\(\) AS used/, "runs adds the commands' totals and used counts the commands");
  assert.throws(() => dashboardQueries("x' OR '1'='1"), /uuid/, "a non-uuid never reaches the SQL");
});

t("each daily report's schema is stored so the page can tell not reported from zero", () => {
  const v4 = { ...good3(), schema: 4, handbacks: 3, guard_denies: 1, blocked_markers: 0,
    dod_conditions: 10, dod_pinned: 7, input_tokens: 5, output_tokens: 6,
    cache_read_tokens: 7, cache_write_tokens: 8, surfaces: {}, models: {} };
  const v5 = { ...v4, schema: 5, qa_checks: 61, qa_fails: 22, qa_tickets: 38, qa_tickets_failed: 18 };
  for (const [body, schema] of [[good3(), 3], [v4, 4], [v5, 5]]) {
    const d = dataPoints(body)[0].doubles;
    assert.equal(d.length, 19, `schema ${schema}: the install point carries 19 doubles`);
    assert.equal(d[18], schema, `schema ${schema}: double 19 is the schema`);
  }
  assert.deepEqual(dataPoints(v5)[0].doubles.slice(14, 18), [61, 22, 38, 18], "the QA counts keep doubles 15-18");
  assert.ok(dashboardQueries().v1Installs.includes("argMax(double19, timestamp) AS schema_seen"),
    "the per-install query reads it back");
});

await at("an install id, sort key or page number from the address is checked before it is used", async () => {
  const s = (q) => dashboardSettings(`https://collector.example/dashboard${q}`);
  assert.deepEqual(s(""), { install: null, sort: "tickets", dir: "desc", page: 1, q: "", dormant: false, min: 10 });
  assert.equal(s(`?install=${UUID_A.toUpperCase()}`).install, UUID_A, "a uuid is kept, lower-cased");
  assert.equal(s("?install=%27%20OR%201%3D1%20--").install, null, "anything else is dropped");
  assert.equal(s("?sort=bogus").sort, "tickets");
  assert.equal(s("?sort=qa").sort, "qa");
  assert.equal(s("?dir=sideways").dir, "desc");
  assert.equal(s("?dir=asc").dir, "asc");
  for (const bad of ["0", "-1", "abc", "999999", "2.5"]) assert.equal(s(`?page=${bad}`).page, 1, `page=${bad}`);
  assert.equal(s("?page=3").page, 3);
  assert.equal(s("?q=3F25").q, "3f25");
  assert.equal(s("?q=%3Cscript%3E").q, "", "a search that is not an id fragment is dropped");
  assert.equal(s("?min=7").min, 10);
  assert.equal(s("?min=25").min, 25);
  assert.equal(s("?dormant=show").dormant, true);

  // End to end: a crafted install id never reaches a query.
  const sent = [];
  const env = dashEnv();
  const stub = async (_url, init) => { sent.push(init.body);
    return { ok: true, status: 200, async json() { return { data: AE_ROWS[whichQuery(init.body)] || [] }; } }; };
  const res = await load(env, stub, "/dashboard?install=%27%20OR%201%3D1%20--");
  assert.equal(res.status, 200);
  assert.ok(sent.length > 0 && sent.every((sql) => !sql.includes("OR 1=1") && !sql.includes("index1 = '")),
    "no query carries the crafted value, and none is narrowed");
});

await at("a one-install page stays under the 50-subrequest limit", async () => {
  const count = async (path) => {
    let calls = 0;
    const env = dashEnv();
    await putInstall(env, `install:${UUID_C}`, "2026-01-15");
    const stub = async (_url, init) => { calls++;
      return { ok: true, status: 200, async json() { return { data: AE_ROWS[whichQuery(init.body)] || [] }; } }; };
    const before = env.lists;
    await (await load(env, stub, path)).text();
    return { calls, lists: env.lists - before };
  };
  const all = await count("/dashboard");
  const one = await count(`/dashboard?install=${UUID_C}`);
  assert.equal(all.calls, 17, "an all-installs page runs 17 queries");
  assert.equal(one.calls, 21, "a one-install page adds the 10 narrowed ones and drops 5 it reads only narrowed");
  assert.ok(one.calls + one.lists <= 49, `${one.calls} queries + ${one.lists} KV list calls`);
});

await at("the failed-query line counts the queries actually run, on both page kinds", async () => {
  const env = dashEnv();
  const warn = (html) => html.match(/<section class="panel warn">[\s\S]*?<\/section>/)[0];
  const allHtml = await (await load(env, aeStubReject("v1Tracks"))).text();
  assert.match(warn(allHtml), /1 of 17 queries failed/);
  const oneHtml = await (await load(env, aeStubReject("v1Tracks"), `/dashboard?install=${UUID_C}`)).text();
  assert.match(warn(oneHtml), /2 of 21 queries failed/, "the narrowed copy of the query fails too, and both count");
  assert.ok(warn(oneHtml).includes("one:v1Tracks"), "and the narrowed one is named as such");
});

await at("how ADT helped shows checks run, caught at plan, caught at QA and escaped, in that order", async () => {
  const helped = helpedOf(render_sample());
  assert.deepEqual([...helped.matchAll(/<span class="kind">([^<]*)<\/span>/g)].map((m) => m[1]),
    ["checks run", "caught at plan", "caught at QA", "escaped"]);
  const page = render_sample();
  assert.ok(page.indexOf('class="helped"') < page.indexOf("<h2>per command</h2>"), "it sits above the totals");
});

await at("the plan and QA figures show a percentage and the top figures carry no colour", async () => {
  const helped = helpedOf(render_sample());
  // Fixture: gates saw 8 + 8 tickets and changed 4 + 2; QA ran 61 checks and 22 failed.
  assert.ok(helped.includes("38% of 16 plans reviewed"), "caught at plan shows its percentage");
  assert.ok(helped.includes("36% of 61 QA checks"), "caught at QA shows its percentage");
  assert.ok(!/caught|escaped/.test(helped.replace(/<span class="kind">[^<]*<\/span>/g, "")),
    "no caught or escaped class on the figures");
  const tileCss = CSS_OF(render_sample()).split("\n").filter((l) => l.startsWith(".tile")).join("\n");
  assert.ok(tileCss && !/--caught|--escaped|#15803d|#b91c1c/.test(tileCss), "and no caught or escaped colour in their styles");
});

await at("the escaped figure is labelled as missed by ADT and Claude", async () => {
  const tiles = [...helpedOf(render_sample()).matchAll(/<div class="tile">[\s\S]*?<\/div>/g)].map((m) => m[0]);
  const escaped = tiles.find((x) => x.includes('<span class="kind">escaped</span>'));
  assert.ok(escaped, "there is an escaped figure");
  assert.ok(escaped.includes("missed by ADT and Claude"), "and it says what missed it");
  assert.ok(escaped.includes("per 100 closed tickets"), "with its rate per 100 closed tickets");
});

await at("the totals follow the approved order, with cost inside tickets and tokens", async () => {
  const html = render_sample();
  const titles = [...html.matchAll(/<h2>([^<]+)<\/h2>/g)].map((m) => m[1]).filter((x) => x !== "some data is missing");
  assert.deepEqual(titles, ["per command", "versions", "operating systems", "updates", "uninstalls",
    "tickets and tokens", "gates",
    "tracks", "autonomy", "reviewers", "build problems QA found", "compare installs"]);
  const p = panelOf(html, "tickets and tokens");
  const labels = [...p.matchAll(/<div class="row(?! head)[^"]*"><div class="k[^"]*">([^<]*)</g)].map((m) => m[1]);
  assert.deepEqual(labels, ["tickets", "tokens", "cost", "measured"]);
  assert.deepEqual(valuesOf(p, "cost"), ["$4,382", "$190"]);
});

await at("the installs, cost and where the tokens go panels are gone", async () => {
  const html = render_sample();
  for (const title of ["installs", "cost", "where the tokens go"])
    assert.ok(!html.includes(`<h2>${title}</h2>`), `no ${title} panel`);
});

await at("selecting an install shows its figures with all installs alongside", async () => {
  // The narrowed queries return this install's own rows and the rest return
  // different all-installs rows, so a figure read from the wrong set shows.
  const ALL = { ...AE_ROWS,
    v1Totals: [{ ...AE_ROWS.v1Totals[0], handbacks: 40, guard_denies: 6, blocked_markers: 9 }],
    v1Gates: AE_ROWS.v1Gates.map((g) => ({ ...g, tickets: 20 })),
    v1Surfaces: [{ name: "adt-dod-coverage-reviewer", total: 50 }, { name: "adt-plan-quality-reviewer", total: 20 },
                 { name: "general-purpose", total: 30 }] };
  const ONE = { totals: [], versions: [], systems: [],
    v1Totals: [{ ...AE_ROWS.v1Totals[0], tickets: 7, tickets_win: 2, tokens: 5000, tokens_win: 100,
                 cost: 1e9, cost_win: 0, cost_measured: 2e9, qa_checks: 12, qa_fails: 5, qa_tickets: 11,
                 qa_tickets_failed: 4, handbacks: 6, guard_denies: 1, blocked_markers: 2 }],
    v1Commands: [{ name: "plan", total: 5, total_win: 1 }],
    v1Gates: [{ name: "coverage", ran: 9, caused_edit: 3, under_recorded: 0, tickets: 12 }],
    v1Tracks: [{ name: "full", closed: 11, stamped: 8, follow_ons: 1, defects: 2, days_sum: 22, days_n: 11 }],
    v1Surfaces: [{ name: "adt-dod-coverage-reviewer", total: 9 }, { name: "general-purpose", total: 4 }] };
  const stub = async (_url, init) => {
    const key = whichQuery(init.body);
    const data = init.body.includes(`index1 = '${UUID_C}'`) && key in ONE ? ONE[key] : ALL[key];
    return { ok: true, status: 200, async json() { return { data: data || [] }; } };
  };
  const env = dashEnv();
  await putInstall(env, `install:${UUID_C}`, "2026-01-15");
  const html = await (await load(env, stub, `/dashboard?install=${UUID_C}`)).text();
  const banner = html.match(/<section class="scoped"[\s\S]*?<\/section>/);
  assert.ok(banner && banner[0].includes(UUID_C), "a banner names the install");
  assert.match(banner[0], /<a href="\?">Back to all installs<\/a>/, "and links back to all installs");
  const tt = panelOf(html, "tickets and tokens");
  assert.deepEqual(valuesOf(tt, "tickets"), ["7", "2", "92"], "this install's lifetime and window, then all installs");
  assert.deepEqual(valuesOf(tt, "cost"), ["$1,000", "$0", "$4,382"]);
  assert.deepEqual(valuesOf(tt, "tokens"), ["5,000", "100", "23,699,674"]);
  assert.deepEqual(valuesOf(tt, "measured"), ["$2,000", "—", "$3,111"]);
  assert.equal((helpedOf(html).match(/all installs:/g) || []).length, 4, "every top figure has its all-installs line");
  // This install: 9 ADT reviews (general-purpose is not one), 11 closed, 3 of 12 plans changed,
  // 5 of 12 QA checks failed, 2 bugs. All installs: 70 reviews, 71 closed, 6 of 40, 22 of 61, 32 bugs.
  const tiles = [...helpedOf(html).matchAll(/<div class="tile">([\s\S]*?)<\/div>/g)]
    .map((m) => [...m[1].matchAll(/<span class="(?:n|of|of all)">([^<]*)<\/span>/g)].map((x) => x[1]));
  assert.deepEqual(tiles, [
    ["9", "0.82 per closed ticket", "all installs: 0.99 per closed ticket"],
    ["3", "25% of 12 plans reviewed", "all installs: 15% of 40 plans reviewed"],
    ["5", "42% of 12 QA checks", "all installs: 36% of 61 QA checks"],
    ["2", "18.2 per 100 closed tickets", "all installs: 45.1 per 100 closed tickets"],
  ]);
  const EXPECTED = {
    "gates": [["does the DoD cover the spec\\?", ["12", "9", "3", "25.0%", "0", "20.0%"]]],
    "tracks": [["full", ["11", "2", "18.2%", "1", "—", "2.0", "52.4%"]]],
    "autonomy": [["handed back to a human", ["6", "0.55", "0.56"]],
                 ["paused with /adt-block", ["2", "0.18", "0.13"]],
                 ["spawns refused by the guard", ["1", "0.09", "0.08"]]],
    "reviewers": [["adt-dod-coverage-reviewer", ["9", "69%", "50"]], ["general-purpose", ["4", "31%", "30"]]],
    "build problems QA found": [["QA checks that sent the build back", ["5", "12", "41.7%", "36.1%"]],
                                ["tickets sent back at least once", ["4", "11", "36.4%", "47.4%"]]],
  };
  for (const [title, rows] of Object.entries(EXPECTED))
    for (const [label, values] of rows)
      assert.deepEqual(valuesOf(panelOf(html, title), label), values, `${title} / ${label}`);
  assert.ok(panelOf(html, "tracks").includes("unfinished-work counts on 8 of 11 closed tickets"), "the tracks line counts this install");
  for (const title of ["gates", "tracks", "autonomy", "build problems QA found"])
    assert.ok(panelOf(html, title).includes('<div class="v all">all installs'), `${title} carries an all-installs column`);
  // The one-install tables declare one track per cell too.
  const tracks = tableTracks(CSS_OF(html));
  for (const [, cls, body] of html.matchAll(/<div class="tbl ([a-z0-9]+)">([\s\S]*?)(?=<details|<\/section>|<p class="empty">)/g)) {
    assert.ok(tracks[cls], `.tbl.${cls} declares its tracks`);
    for (const [r] of body.matchAll(/<div class="row[^"]*">(?:(?!<div class="row)[\s\S])*/g))
      assert.equal((r.match(/<div class="(?:k|v|bar)[ "]/g) || []).length, tracks[cls], `.tbl.${cls}: ${r.slice(0, 60)}`);
  }
});

await at("compare installs lists its columns in the approved order", async () => {
  const p = panelOf(render_sample(), "compare installs");
  const heads = [...p.matchAll(/<a class="sort[^"]*" href="[^"]*">([\s\S]*?)<span class="arrow">/g)]
    .map((m) => m[1].replace(/<br>/g, " "));
  assert.deepEqual(heads, ["tickets", "tokens", "cost", "command runs", "ADT reviews per ticket",
    "plans changed by a review", "QA sent the build back", "bugs after closing per 100 tickets",
    "handbacks per ticket", "ticket filings blocked per 100 tickets"]);
  const groups = [...p.matchAll(/<th scope="colgroup" colspan="(\d)"[^>]*>([^<]*)/g)].map((m) => [m[2], Number(m[1])]);
  assert.deepEqual(groups, [["usage", 4], ["checks run", 1], ["caught before merge", 2], ["escaped", 1], ["supervision", 2]]);
});

await at("caught columns are marked caught, bugs after closing escaped, and ticket filings blocked says it includes false blocks", async () => {
  const p = panelOf(render_sample(), "compare installs");
  assert.match(p, /<th scope="colgroup" colspan="2" class="caught">caught before merge<span class="gnote">problems ADT stopped<\/span>/);
  assert.match(p, /<th scope="colgroup" colspan="1" class="escaped">escaped<span class="gnote">missed by ADT and Claude<\/span>/);
  assert.match(p, /ticket filings blocked<br>per 100 tickets<span class="arrow">[^<]*<\/span><\/a><span class="cnote">includes false blocks<\/span>/);
  const css = CSS_OF(render_sample());
  assert.match(css, /tr\.groups th\.caught[^{]*\{color:var\(--caught\)\}/);
  assert.match(css, /tr\.groups th\.escaped[^{]*\{color:var\(--escaped\)\}/);
});

await at("an install whose daily report does not carry a figure shows not reported", async () => {
  const env = dashEnv();
  await putInstall(env, `install:${UUID_A}`, "2026-01-15");
  const html = await (await load(env)).text();
  // UUID_A has only legacy rows: usage from them, nothing newer.
  const a = compareRow(html, UUID_A);
  assert.deepEqual([a[COL.tickets], a[COL.tokens]], ["40", "900K"]);
  for (const k of ["cost", "commands", "reviews", "plans", "qa", "bugs", "handbacks", "blocked"])
    assert.equal(a[COL[k]], "not reported", `legacy-only install: ${k}`);

  // Schema 4 sends no QA counts; a row with no stored schema (written before
  // ADT-378) cannot vouch for anything schema 4 or 5 sends.
  const { ids, rows } = manyInstalls(3);
  rows.v1Installs[1].schema_seen = 4;
  rows.v1Installs[2].schema_seen = 0;
  const many = await (await load(dashEnv(), aeStub(rows))).text();
  const four = compareRow(many, ids[1]), zero = compareRow(many, ids[2]);
  assert.equal(four[COL.qa], "not reported", "schema 4: QA");
  assert.notEqual(four[COL.reviews], "not reported", "schema 4: reviews are reported");
  for (const k of ["reviews", "handbacks", "blocked", "qa"])
    assert.equal(zero[COL[k]], "not reported", `no stored schema: ${k}`);
  assert.notEqual(zero[COL.plans], "not reported", "gates arrive from schema 3, so they still show");
});

await at("the compare installs panel carries its approved text", async () => {
  const p = panelOf(render_sample(), "compare installs");
  assert.equal(blockText(p, "what this counts"),
    "One row per install that has sent a daily report. Tickets, tokens, cost and command runs are lifetime totals. Plans changed is a percentage of plans reviewed, and QA sent back is a percentage of QA checks. ADT reviews and handbacks are per closed ticket, and bugs after closing and ticket filings blocked are per 100 closed tickets. Ticket filings blocked includes commands that only mention opening a ticket. An install whose daily report does not carry a figure shows not reported.");
  assert.equal(blockText(p, "what this tells you"),
    "Shows which installs ADT helps most and which it helps least. Rank any column to see the installs at the top or the bottom. The caught columns count problems ADT stopped before merge, and the escaped column counts bugs that got through after a ticket closed. Rates are hidden for installs with too few tickets, because a handful of tickets can swing a rate a long way.");
});

await at("every compare installs column ranks high to low and low to high, with hidden rates last", async () => {
  const { rows } = manyInstalls(20);
  const parse = (cell) => cell.startsWith("—") || cell === "not reported" ? null : parseFloat(cell.replace(/[$,K%]/g, ""));
  for (const key of ["plans", "reviews", "bugs", "tickets"]) {
    for (const dir of ["desc", "asc"]) {
      const html = await (await load(dashEnv(), aeStub(rows), `/dashboard?sort=${key}&dir=${dir}`)).text();
      const cells = compareRows(html).map((r) => parse(r[COL[key]]));
      const firstHidden = cells.indexOf(null);
      const shown = firstHidden === -1 ? cells : cells.slice(0, firstHidden);
      assert.ok(firstHidden === -1 || cells.slice(firstHidden).every((v) => v === null), `${key} ${dir}: hidden rates are last`);
      const sorted = [...shown].sort((x, y) => dir === "desc" ? y - x : x - y);
      assert.deepEqual(shown, sorted, `${key} ${dir}: ordered`);
      assert.ok(shown.length > 1, `${key} ${dir}: the check saw values`);
    }
  }
  const desc = await (await load(dashEnv(), aeStub(rows), "/dashboard?sort=plans")).text();
  assert.match(desc, /<a class="sort on" href="\?sort=plans&amp;dir=asc">plans changed/, "the active heading links to the other direction");
  assert.match(desc, /<th scope="col" aria-sort="descending"><a class="sort on"/);
});

await at("a rate is hidden when its denominator is below the minimum", async () => {
  const { ids, rows } = manyInstalls(20);
  // Plans reviewed is 5 + (k % 20): install 3 saw 8, install 15 saw 20.
  const at10 = await (await load(dashEnv(), aeStub(rows))).text();
  assert.equal(compareRow(at10, ids[3])[COL.plans], "— (8)", "8 plans reviewed is below 10");
  assert.match(compareRow(at10, ids[15])[COL.plans], /^\d+\.\d%$/, "20 is enough at 10");
  const at25 = await (await load(dashEnv(), aeStub(rows), "/dashboard?min=25")).text();
  assert.equal(compareRow(at25, ids[15])[COL.plans], "— (20)", "and not at 25");
  assert.match(at25, /<option value="25" selected>25<\/option>/, "the form shows the minimum in use");
});

await at("compare installs shows 25 rows a page, searches by install id and hides dormant installs", async () => {
  const { ids, rows } = manyInstalls(60);
  const env = dashEnv();
  const dormantId = "ffffffff-0000-4000-8000-000000000000";
  await putInstall(env, `install:${dormantId}`, "2026-01-15");
  const page1 = await (await load(env, aeStub(rows))).text();
  assert.equal(compareRows(page1).length, 25);
  assert.ok(page1.includes("Showing 1&ndash;25 of 60 installs"), "the dormant install is hidden by default");
  assert.ok(page1.includes('<a href="?page=2">Next</a>'), "and there is a next page");
  const page3 = await (await load(env, aeStub(rows), "/dashboard?page=3")).text();
  assert.equal(compareRows(page3).length, 10);
  assert.ok(page3.includes("Showing 51&ndash;60 of 60 installs"));
  assert.ok(page3.includes('<span class="rank">#51</span>'), "ranks continue from the earlier pages");
  const found = await (await load(env, aeStub(rows), `/dashboard?q=${ids[5].slice(0, 8)}`)).text();
  assert.equal(compareRows(found).length, 1, "a search by id fragment finds the one install");
  const shown = await (await load(env, aeStub(rows), "/dashboard?dormant=show")).text();
  assert.ok(shown.includes("of 61 installs"), "show dormant installs brings it back");
  const dormantRow = await (await load(env, aeStub(rows), "/dashboard?dormant=show&q=ffffffff")).text();
  assert.match(dormantRow, /<tr class="dormant">/, "and marks it dormant");
  const form = panelOf(page1, "compare installs").match(/<form class="cmp-tools" method="get"[\s\S]*?<\/form>/);
  assert.ok(form, "the controls are a GET form");
  for (const name of ["q", "dormant", "min", "sort", "dir"])
    assert.ok(form[0].includes(`name="${name}"`), `the form carries ${name}`);
  assert.ok(!/<script/i.test(page1), "and the page still has no script");
});

// --- updates and uninstalls ----------------------------------------------
// Rows as Analytics Engine returns them for the lifecycle query, against DAY1
// (2026-09-06 12:00 UTC) as now. kind "" is a cmdset-v1 report, "install" a
// totals-v1 report, "event" an uninstall. Times are unix seconds, as
// toUnixTimestamp returns them; the two update days sit half an hour either side
// of midnight UTC, so a seconds/milliseconds or zone slip moves one of them.
const UUID_D = "0b7c5e8a-2f4d-4e1a-9c3b-5d6e7f8a9b0c";
const UUID_E = "c4d5e6f7-a8b9-4c0d-8e1f-2a3b4c5d6e7f";
const sec = (utc) => Date.parse(`${utc}:00Z`) / 1000;
const LIFECYCLE_ROWS = [
  // A: updated on 08-26, then uninstalled after its last report.
  { install: UUID_A, version: "0.1.0", kind: "", first_at: sec("2026-08-20T09:00"), last_at: sec("2026-08-25T09:00") },
  { install: UUID_A, version: "0.2.0", kind: "install", first_at: sec("2026-08-26T23:30"), last_at: sec("2026-09-05T09:00") },
  { install: UUID_A, version: "0.2.0", kind: "event", first_at: sec("2026-09-05T18:00"), last_at: sec("2026-09-05T18:00") },
  // B: a new install on 0.2.0, reporting. Not an update.
  { install: UUID_B, version: "0.2.0", kind: "install", first_at: sec("2026-08-28T09:00"), last_at: sec("2026-09-06T09:00") },
  // C: last report 22 days ago, no uninstall: gone quiet.
  { install: UUID_C, version: "0.1.0", kind: "install", first_at: sec("2026-08-10T09:00"), last_at: sec("2026-08-15T09:00") },
  // D: uninstalled on 08-18, kept its id, reported again: updated on 08-21 and reporting.
  { install: UUID_D, version: "0.1.0", kind: "", first_at: sec("2026-08-12T09:00"), last_at: sec("2026-08-17T09:00") },
  { install: UUID_D, version: "0.1.0", kind: "event", first_at: sec("2026-08-18T09:00"), last_at: sec("2026-08-18T09:00") },
  { install: UUID_D, version: "0.2.0", kind: "", first_at: sec("2026-08-21T00:30"), last_at: sec("2026-08-22T09:00") },
  { install: UUID_D, version: "0.2.0", kind: "install", first_at: sec("2026-08-23T09:00"), last_at: sec("2026-09-06T08:00") },
  // E: an uninstall from an id that never reported.
  { install: UUID_E, version: "0.2.0", kind: "event", first_at: sec("2026-09-01T09:00"), last_at: sec("2026-09-01T09:00") },
];

t("updates count installs that moved to a higher version, with the day they moved", () => {
  assert.deepEqual(lifecycle(LIFECYCLE_ROWS, DAY1).updates,
    [{ version: "0.2.0", installs: 2, first: "2026-08-21", latest: "2026-08-26" }]);
  // Moving DOWN is not an update, in either direction of the pair.
  const down = [
    { install: UUID_A, version: "0.2.0", kind: "install", first_at: sec("2026-08-20T09:00"), last_at: sec("2026-08-25T09:00") },
    { install: UUID_A, version: "0.1.0", kind: "install", first_at: sec("2026-08-26T09:00"), last_at: sec("2026-09-05T09:00") },
  ];
  assert.deepEqual(lifecycle(down, DAY1).updates, []);
  // Newest version first.
  const two = [
    { install: UUID_A, version: "0.1.0", kind: "install", first_at: sec("2026-08-20T09:00"), last_at: sec("2026-08-21T09:00") },
    { install: UUID_A, version: "0.1.1", kind: "install", first_at: sec("2026-08-22T09:00"), last_at: sec("2026-08-23T09:00") },
    { install: UUID_B, version: "0.1.1", kind: "install", first_at: sec("2026-08-20T09:00"), last_at: sec("2026-08-23T09:00") },
    { install: UUID_B, version: "0.2.0", kind: "install", first_at: sec("2026-08-24T09:00"), last_at: sec("2026-09-05T09:00") },
  ];
  assert.deepEqual(lifecycle(two, DAY1).updates.map((u) => u.version), ["0.2.0", "0.1.1"]);
});

t("uninstalled, gone quiet and reporting are counted apart", () => {
  assert.deepEqual(lifecycle(LIFECYCLE_ROWS, DAY1).status, { uninstalled: 1, quiet: 1, reporting: 2 });
  // The quiet boundary is the planned 7 days: a last report 7 days old is still
  // reporting, 8 days old is gone quiet. Literal on purpose, so the constant is pinned.
  const edge = (days) => lifecycle([{ install: UUID_B, version: "0.2.0", kind: "install",
    first_at: (DAY1 - 30 * 86_400_000) / 1000, last_at: (DAY1 - days * 86_400_000) / 1000 }], DAY1).status;
  assert.deepEqual(edge(7), { uninstalled: 0, quiet: 0, reporting: 1 });
  assert.deepEqual(edge(8), { uninstalled: 0, quiet: 1, reporting: 0 });
  // An uninstall in the same second as the last report is an uninstall: the event
  // is sent after the watcher stops.
  const tie = sec("2026-09-05T09:00");
  assert.deepEqual(lifecycle([
    { install: UUID_B, version: "0.2.0", kind: "install", first_at: tie - 86_400, last_at: tie },
    { install: UUID_B, version: "0.2.0", kind: "event", first_at: tie, last_at: tie },
  ], DAY1).status, { uninstalled: 1, quiet: 0, reporting: 0 });
});

t("an uninstall from an install with no reports is ignored", () => {
  const onlyE = LIFECYCLE_ROWS.filter((r) => r.install === UUID_E);
  assert.deepEqual(lifecycle(onlyE, DAY1), { updates: [], status: { uninstalled: 0, quiet: 0, reporting: 0 } });
  const withoutE = LIFECYCLE_ROWS.filter((r) => r.install !== UUID_E);
  assert.deepEqual(lifecycle(LIFECYCLE_ROWS, DAY1), lifecycle(withoutE, DAY1));
});

await at("the dashboard shows the updates and uninstalls panels", async () => {
  const html = await (await load(dashEnv(), aeStub({ ...AE_ROWS, lifecycle: LIFECYCLE_ROWS }))).text();
  assert.deepEqual(valuesOf(panelOf(html, "updates"), "0.2.0"), ["2", "2026-08-21", "2026-08-26"]);
  // Two panels take the two-column grid, so a desktop row has no empty third slot.
  assert.match(html, /<div class="grid two">\s*<section class="panel[^"]*"[^>]*><h2>updates<\/h2>/);
  const statusPanel = panelOf(html, "uninstalls");
  assert.deepEqual(valuesOf(statusPanel, "uninstalled"), ["1", "25%"]);
  assert.deepEqual(valuesOf(statusPanel, "gone quiet"), ["1", "25%"]);
  assert.deepEqual(valuesOf(statusPanel, "reporting"), ["2", "50%"]);
  // With no lifecycle rows each panel says why it is empty.
  const empty = await (await load(dashEnv())).text();
  assert.ok(panelOf(empty, "updates").includes("no install moved to a newer version in the last 30 days"));
  assert.ok(panelOf(empty, "uninstalls").includes("nothing reported in the last 30 days"));
});

await at("a one-install page runs no lifecycle query and shows neither panel", async () => {
  const sql = [];
  const stub = async (url, init) => { sql.push(init.body); return aeStub()(url, init); };
  const env = dashEnv();
  await putInstall(env, `install:${UUID_C}`, "2026-01-15");
  const html = await (await load(env, stub, `/dashboard?install=${UUID_C}`)).text();
  assert.ok(sql.length > 0, "the page ran its queries");
  assert.ok(!sql.some((q) => q.includes("blob4 = 'event'")), "no lifecycle query on a one-install page");
  assert.ok(!html.includes("<h2>updates</h2>"), "no updates panel");
  assert.ok(!html.includes("<h2>uninstalls</h2>"), "no uninstalls panel");
});

console.log(`\n${pass} checks passed`);
