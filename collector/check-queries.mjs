// Copyright 2026 zurichrich
// SPDX-License-Identifier: Apache-2.0
//
// Run every dashboard query against the REAL Analytics Engine and report what
// it says about each one.
//
// WHY THIS EXISTS. ADT-284's DoD graded the dashboard queries as STRINGS — that
// one contains `argMax`, that another does not contain `SUM(_sample_interval *
// double`. Every condition was green and two of the twelve queries were invalid
// SQL. `COUNT(install)` is rejected by Analytics Engine, which requires
// `COUNT()` with zero arguments, and no string assertion can know that.
//
// The suite cannot do this: the account is the maintainer's and unreachable
// from a grader host, which is why collector/DEPLOY.md records the live smoke
// as a manual step. So this is the manual step, made runnable instead of
// described — the deploy checklist points at it.
//
//   CLOUDFLARE_ACCOUNT_ID=<id> CLOUDFLARE_API_TOKEN=<token> \
//     node collector/check-queries.mjs
//
// Exits non-zero if any query is rejected, so it can gate a deploy.
import { dashboardQueries } from "./collector.js";

const acct = process.env.CLOUDFLARE_ACCOUNT_ID;
const token = process.env.CLOUDFLARE_API_TOKEN;
if (!acct || !token) {
  console.error("set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN.\n"
    + "The token needs Account -> Account Analytics -> Read; it is the same one\n"
    + "deployed as the AE_TOKEN secret. See collector/DEPLOY.md.");
  process.exit(2);
}

const url = `https://api.cloudflare.com/client/v4/accounts/${acct}/analytics_engine/sql`;
const queries = dashboardQueries();
let rejected = 0;

for (const [name, sql] of Object.entries(queries)) {
  let res;
  try {
    res = await fetch(url, { method: "POST", body: sql,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "text/plain" } });
  } catch (err) {
    console.log(`  UNREACHABLE ${name}: ${err && err.message}`);
    rejected++;
    continue;
  }
  const body = await res.text();
  if (res.ok) {
    let n = "?";
    try { n = (JSON.parse(body).data || []).length; } catch { /* shape only */ }
    console.log(`  ok   ${name}  (${n} rows)`);
    continue;
  }
  rejected++;
  console.log(`\n  REJECTED ${name} -> ${res.status}`);
  console.log(`  ${body.trim().replace(/\s+/g, " ").slice(0, 300)}`);
  console.log(sql.split("\n").map((l) => "    " + l).join("\n"));
}

console.log(`\n${rejected} of ${Object.keys(queries).length} queries rejected`);
process.exit(rejected ? 1 : 0);
