# Deploying the ADT telemetry collector

Handoff instructions. Everything here is derived from `collector/wrangler.toml`,
`collector/collector.js` and `tools/adt_usage_report.py` as they stand on this
branch — check them if anything below disagrees with the code, and trust the
code.

**Who this is for:** whoever holds the Cloudflare account. Nothing in this file
can be done from a grader host or CI; the account is the maintainer's, which is
why `tools/tests/test_collector.py` records the live smoke as a manual carve-out
rather than pretending to automate it.

**What changes:** ADT-224 rewrote the collector. The existing deployment stores
every ping in KV, which exhausted the 1000 put/day free-tier cap on 2026-09-05
and again on 2026-09-06. The new one splits storage by mutability and adds two
bindings that **do not exist on the current deployment yet**. Until you deploy,
the merged code is inert.

---

## 0. Before you start

You need:

- `wrangler` (`npx wrangler --version` is enough; no global install required)
- to be logged in: `npx wrangler login`, or `CLOUDFLARE_API_TOKEN` set
- the account that already owns the `adt-telemetry` Worker and the `STATS` KV
  namespace `ba5f2326fbe343029362f2c25af925ac`

Confirm you are on the right account before anything else — deploying
`adt-telemetry` from the wrong account creates a second Worker rather than
updating the existing one:

```
cd collector
npx wrangler whoami
npx wrangler deployments list --name adt-telemetry
```

### The dashboard route needs two secrets and an Access policy

`GET /dashboard` reads the `adt_usage_v2` dataset back out and renders it. It needs
an account token and the account id, and it must not be reachable by the public.

**Do the Cloudflare Access policy first.** `adt-telemetry.zurichrich.workers.dev` is already
a public hostname and `/dashboard` is an easy path to guess, so the window
between deploying the route and applying the policy is a window in which anyone
can read your usage data. In Cloudflare Zero Trust, add a self-hosted Cloudflare Access
application for `adt-telemetry.zurichrich.workers.dev/dashboard` and a policy
allowing your own email. There is no auth code in this repo and no credential in
it: the route is protected by configuration, and this step is the protection.

Then the two secrets. Create an API token with **Account → Account Analytics →
Read** (the same scope §4 describes; nothing wider is needed), and find the
account id on any Cloudflare dashboard URL.

```
cd collector
npx wrangler secret put AE_TOKEN
npx wrangler secret put AE_ACCOUNT_ID
```

Both are secrets rather than `[vars]` entries in `wrangler.toml`, because that
file is committed and this repo is public. With either unset the route answers
503 and says which one is missing, so a half-configured deploy is legible rather
than broken.

---

## 1. What the config declares, and why each part matters

`collector/wrangler.toml` declares three bindings. They are not
interchangeable and the code fails differently if any is missing.

| Binding | Kind | Holds | If absent |
|---|---|---|---|
| `STATS` | KV | `install:<uuid>` → `{first_seen}` — identity only | every request throws on `env.STATS.get` |
| `USAGE` | Analytics Engine | version, os, per-command counts — one data point per ping | `env.USAGE.writeDataPoint` is undefined; every accepted ping throws |
| `IP_BUDGET` | Durable Object | the per-IP daily request + novel-install budgets | `env.IP_BUDGET.get` is undefined; every request throws before any write |

Two details are load-bearing:

**The Durable Object must be SQLite-backed.** `[[migrations]]` says
`new_sqlite_classes = ["IpBudget"]`, not `new_classes`. A classic
(KV-backed) Durable Object class is not on the free plan — if you change that
line to `new_classes`, the deploy may succeed and then bill, or fail with a plan
error. The free-tier claim in the config comment is only true with
`new_sqlite_classes`.

**The dataset was renamed from `adt_usage` to `adt_usage_v2` on 2026-09-08.**
Analytics Engine has no delete or truncate operation and no way to remove a row,
so the only way to discard data is to write to a different dataset — a new name
is created automatically on first write. The old dataset held eleven synthetic
probe rows and nothing else; it ages out on its own after three months. If you
ever need the old rows, they are still queryable under `adt_usage` until then.

**The Analytics Engine dataset name is `adt_usage_v2`, and the reader depends on
it.** `tools/adt_usage_report.py` queries `FROM adt_usage_v2`. Rename the dataset in
`wrangler.toml` and the read path silently returns nothing — the query succeeds
against a dataset with no rows.

---

## 2. Validate the queries against the real dataset, THEN deploy

The dashboard runs seventeen SQL queries. The test suite grades them as strings and
cannot execute them — the account is yours and unreachable from a grader host.
ADT-284 shipped two invalid queries that way (`COUNT(install)`; Analytics Engine
requires `COUNT()` with zero arguments) with every DoD condition green. Run them
against the real dataset first:

```
cd collector
CLOUDFLARE_ACCOUNT_ID=<id> CLOUDFLARE_API_TOKEN=<token> node check-queries.mjs
```

It exits non-zero if any query is rejected and prints what Analytics Engine said
plus the offending SQL. The token is the same one deployed as `AE_TOKEN`.

Deploy the collector before releasing a client that sends the uninstall event.
A collector deployed before ADT-391 answers that event with a 400, and
`send_uninstall_event` fails open without saying so, so every uninstall reported
in between is lost. Step 3h checks the deployed collector accepts it.

```
npx wrangler deploy
```

Expect wrangler to report the KV, Analytics Engine and Durable Object bindings,
and to apply migration tag `v1` creating `IpBudget`. **Read that output.** A
deploy that silently skips a binding is the failure mode to catch here, because
every symptom afterwards is a 500 on a public endpoint you are not watching.

If the Analytics Engine binding is rejected, the account may not have Analytics
Engine enabled. It is enabled per-account in the dashboard under Workers &
Pages → Analytics Engine. Enable it and re-deploy; do not work around it by
removing the binding, because usage would then have nowhere to go and the
per-command counts criterion is unmet.

---

## 3. Verify the deployment, in this order

These are ordered so a failure tells you which layer broke.

**3a. The Worker answers at all.** A GET must be refused — the handler is
POST-only:

```
curl -si https://adt-telemetry.<your-subdomain>.workers.dev | head -1
# expect: HTTP/2 405
```

**3b. A malformed payload is rejected without storing anything.**

```
curl -si -X POST https://adt-telemetry.<your-subdomain>.workers.dev \
  -H 'Content-Type: application/json' -d '{"nope":1}' | head -1
# expect: HTTP/2 400
```

**3c. A valid schema-2 payload is accepted.** Use a UUID you can recognise
later, and note it — you will delete it in step 5.

```
curl -si -X POST https://adt-telemetry.<your-subdomain>.workers.dev \
  -H 'Content-Type: application/json' -d '{
    "schema": 2,
    "install_id": "00000000-0000-4000-8000-000000000001",
    "version": "0.1.0",
    "os": "Linux",
    "commands": {"plan": 1},
    "tickets": 0,
    "tokens": 0
  }' | head -1
# expect: HTTP/2 202
```

**3d. A schema-1 payload is STILL accepted.** This is the compatibility
guarantee — 0.1.0 clients are deployed in the wild and `send_ping` fails open,
so if schema 1 ever 400s, every existing install goes silent and nothing reports
it:

```
curl -si -X POST https://adt-telemetry.<your-subdomain>.workers.dev \
  -H 'Content-Type: application/json' -d '{
    "schema": 1, "install_id": "00000000-0000-4000-8000-000000000002",
    "version": "0.1.0", "os": "Linux", "commands": 3, "tickets": 2, "tokens": 100
  }' | head -1
# expect: HTTP/2 202
```

**3e. Exactly one KV key was written, and it is an identity record.** This is
the headline of the whole change: a ping is no longer a put.

```
npx wrangler kv key list --namespace-id ba5f2326fbe343029362f2c25af925ac \
  | grep -c '"name":"install:'
npx wrangler kv key get --namespace-id ba5f2326fbe343029362f2c25af925ac \
  'install:00000000-0000-4000-8000-000000000001'
# expect: {"first_seen":"YYYY-MM-DD"} — and NO version field.
```

`version` is deliberately absent. If identity carried it, every record would
change on release day and "put on change" would become one put per install
inside 24 hours — the exact cost shape being removed.

**3f. A REPEAT ping costs zero puts.** Re-send the exact 3c request and confirm
the key count is unchanged. If it grows, the read-before-write path is not
working and the fix has not landed.

**3g. Old `ping:` keys are now dead weight.** The previous design wrote
`ping:<day>:<uuid>` with a 400-day TTL. Nothing reads them any more:

```
npx wrangler kv key list --namespace-id ba5f2326fbe343029362f2c25af925ac \
  | grep -c '"name":"ping:'
```

Leave them to expire, or delete them once you have confirmed 3e–3f. They are not
harmful, only stale — but they will make the `install:` count harder to read.

**3h. An uninstall event is accepted and writes no KV key.** Count the
`install:` keys, send the event, and count again:

```
curl -si -X POST https://adt-telemetry.<your-subdomain>.workers.dev \
  -H 'Content-Type: application/json' -d '{
    "event": "uninstall", "install_id": "00000000-0000-4000-8000-000000000391",
    "version": "0.1.0", "os": "Darwin"
  }' | head -1
# expect: HTTP/2 202, and the same install: key count as before
```

A 400 here means the deployed collector predates the event, and clients that
send it are losing every uninstall. The ticket's done-lane check sends this same
request.

---

## 4. Wire up the read path

Usage no longer lives in KV, so `wrangler kv key list` — which is how this
ticket's own evidence was gathered — now shows identity keys and nothing else.
Reading usage is a separate, authenticated call.

Create an API token with **Account → Account Analytics → Read**. Nothing wider
is needed and nothing wider should be granted; this token only reads aggregates.

```
export CLOUDFLARE_ACCOUNT_ID=<your account id>
export CLOUDFLARE_API_TOKEN=<the token>
python3 tools/adt_usage_report.py --days 30
```

The token is read from the environment, is never written to disk by the tool,
and is not in this repo. Without it the tool prints instructions and exits 0 —
an unconfigured read is a normal state, not a crash. `--print-sql` shows the
query without contacting Cloudflare, which is the safe way to inspect it.

Expect the test pings from step 3 to appear. The report labels its install
figure **`installs (estimate)`** and means it: Analytics Engine samples on its
index, so a distinct-count over it reads low and drifts further low as adoption
grows. That is exactly why install identity was kept in KV rather than derived
from AE — for the **exact** adoption count, ask KV:

```
npx wrangler kv key list --namespace-id ba5f2326fbe343029362f2c25af925ac --remote \
  | grep -c '"name": *"install:'
```

Every aggregate in that tool is `SUM(_sample_interval * ...)`. If you ever
hand-write a query against `adt_usage_v2`, carry the same weighting: Analytics
Engine samples under load, and an unweighted sum under-reports by a factor that
grows with adoption and never looks obviously wrong.

---

## 5. Clean up the probe data

Delete the two synthetic installs so they do not sit in the adoption count:

```
npx wrangler kv key delete --namespace-id ba5f2326fbe343029362f2c25af925ac \
  'install:00000000-0000-4000-8000-000000000001'
npx wrangler kv key delete --namespace-id ba5f2326fbe343029362f2c25af925ac \
  'install:00000000-0000-4000-8000-000000000002'
```

The Analytics Engine data points cannot be deleted individually; they age out
with the dataset's retention. Two rows against real traffic is noise, but note
it rather than forgetting it.

The 3h uninstall probe (`00000000-0000-4000-8000-000000000391`) wrote no KV
key, so there is nothing to delete. Its one Analytics Engine row is an uninstall
from an install that sent no daily report, which the uninstalls panel ignores.

---

## 6. If the endpoint URL changed

Only if the `workers.dev` hostname is different from
`https://adt-telemetry.zurichrich.workers.dev`:

1. Set it as `TELEMETRY_ENDPOINT` in `tools/adt_phone_home.py`.
2. Add the host to the egress table in `docs/security-posture.md`.

Both halves, or `tools/tests/test_egress_disclosure.py` fails — it derives the
egress set from the source and requires every host to be documented. That test
is not an obstacle to route around; an undisclosed outbound host in a tool
people install is the thing it exists to prevent.

---

## What "working" looks like when you are done

- a new install costs exactly **1** KV put; a returning install costs **0**
- a rejected request costs **0** puts and writes **0** data points
- one source is held to 500 requests and 50 novel installs per UTC day
- `python3 tools/adt_usage_report.py` prints per-command counts, not zeros
- `npx wrangler kv key list` shows `install:` keys and no new `ping:` keys

The hermetic equivalents of the first three already run in CI over the real
handler with counting stubs (`node collector/test-collector.mjs`, 39 checks).
This deploy smoke confirms the deployed build matches the merged source; it is
not the thing that proves the accounting, and no DoD condition depends on it.
