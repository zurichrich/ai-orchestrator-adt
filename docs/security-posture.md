# Security posture

What ADT is, what it can reach, and what it never touches. Written for the
question a reader asks on day one: *if I install this, what leaves my machine?*

## Credentials

**ADT never sees your Anthropic key, never logs prompts, and bundles nothing beyond git, gh and python3.**

Claude Code holds the model credential — from your OS keychain or your
subscription — and ADT never reads it, never receives it, and has no code path
that could. ADT is not an agent: it makes no inference call at all. Claude Code
is the runtime; ADT ships it instructions as markdown.

Runtime dependencies are exactly `git`, `gh`, `python3`, and Claude Code itself.
The Python is stdlib-only — no third-party packages, no lockfile, nothing
vendored. The hooks are bash.

Prompts and transcripts are never logged by ADT. The token ledger records
*counts* per turn (input, output, cache-read, cache-write) grouped by model, and
never content.

## Network egress

**Every outbound destination ADT can contact is listed in this section.**

That claim is enforced, not asserted: `tools/tests/test_egress_disclosure.py`
derives the egress set from the source — literal `https://` URLs plus every
`gh`/`curl` subprocess invocation mapped to its host — and fails if any host is
missing from this list. Add a destination and forget to document it, and the
suite goes red.

| Destination | When | Carries | Yours or ours |
|---|---|---|---|
| `api.github.com` | Continuously, via the `gh` CLI. The sync engine reconciles your backlog against Issues, the board renderer reads them, the watch daemon ticks, and the traffic snapshot reads your own repo's clone/view counts. Once a day, each machine's watch also updates its own comment on a closed Issue labelled `adt:install` in your repo (ADT-384). | Your ticket titles, bodies, labels and stage. On the `adt:install` Issue, each machine's ADT version, the commit its ADT clone is at, and its OS name (`Darwin`, `Linux`), so the installer and the board can show which machines run an older ADT. Not the telemetry install id and not the hostname. Written whether telemetry is on or off, because it goes to your repo, not to us. | Your repo, your `gh` auth. |
| `api.resend.com` | Only if you configure `RESEND_API_KEY`. `/adt-block` emails you when a ticket is paused. | The blocked ticket's question, in the body. | Your key, your account. Leave it unset and this path never fires. |
| `adt-telemetry.zurichrich.workers.dev/dashboard` | Not from your install. The maintainer's own view of the collected telemetry, behind Cloudflare Access. | Nothing outbound: it is a page, not a destination your install posts to. | Ours. |
| `adt-telemetry.zurichrich.workers.dev` | Daily, and one last report when you uninstall, if telemetry is on. | On the daily report, exactly the twenty-seven JSON fields listed below. On uninstall, the four fields in the uninstall event table below. Nothing else. Every one is a count or a short fixed string. Never prompts, code, file paths, ticket titles or repo names. | Ours. Opt out with `DO_NOT_TRACK=1` or the off-switch; disclosed on first run. |
| A release asset on `github.com` | Daily. The version advisory — ADT checking whether the version you run has been revoked. | Nothing outbound beyond the request itself. | Public file, unauthenticated GET. |
| `api.cloudflare.com` | Not from your install, ever. Two things reach it, both ours: the maintainer running `tools/adt_usage_report.py`, and the collector Worker serving `adt-telemetry.zurichrich.workers.dev/dashboard`. The Worker itself calls this endpoint on every dashboard load. | An SQL query against the maintainer's own `adt_usage_v2` dataset. Nothing of yours leaves your machine on this path — it is a read of data already collected. | Ours. The CLI needs `CLOUDFLARE_API_TOKEN` and prints instructions without it; the Worker holds its own token as a secret, which this repo does not contain. |

There is no other endpoint. ADT has no backend your work passes through: your
tickets live in your GitHub repo and a cache on your disk.

### The exact ping payload

Field for field, as it goes on the wire. `tools/tests/test_ping_payload_capture.py`
decodes a payload the client actually sent and fails if this list and that
payload disagree — it does not read the builder, because reading the builder is
the mistake this list is correcting. Until 2026-09-06 the doc said ADT sent
"counts (commands run, tickets, tokens)" and the watcher never passed any, so
every ping in the feature's life carried all three as `0`.

| Field | Type | What it is |
|---|---|---|
| `schema` | integer | Payload version. `5`; the collector still accepts `1`, `2`, `3` and `4`, because deployed clients send them. |
| `install_id` | uuid | A random id generated on your machine, stored in `.adt/state/`. Not derived from your hostname, user, repo or hardware — delete the file and you are a new install. |
| `version` | semver | The ADT version you are running. |
| `os` | string | `platform.system()` — `Darwin`, `Linux`. Not your hostname, kernel build or architecture. |
| `commands` | map of name → integer | How many times each `/adt-*` command ran. Keys are ADT's own command names, checked against the shipped playbook list; a `/adt-...` token in your prompt that is not a real command is dropped rather than sent. Never arguments, ticket ids, paths or branch names. |
| `tickets` | integer | How many distinct tickets appear in your local cost ledger. The count only — never an id. |
| `tokens` | integer | Input + output tokens across that ledger. |
| `cost_micros` | integer | What that ledger costs, in millionths of a US dollar. Priced **on your machine** from a local table: the model names and per-row breakdown stay there, and only this one total leaves. |
| `cost_measured_micros` | integer | The part of `cost_micros` priced from a recorded model rate rather than estimated. Sent so the dashboard cannot present a part-estimated total as a measured one. |
| `dod_amendments` | integer | How many times a ticket's Definition of Done was revised, summed over your tickets. A count of revisions — never their content. |
| `gates` | map of name → four integers | Per review gate (`coverage`, `plan-quality` only): how many times it ran, how many tickets it changed, how many were under-recorded, and how many tickets it saw. Counts only, and the gate name is checked against that closed set before sending. |
| `tracks` | map of name → four integers | Per impact tier (`fast`, `standard`, `full`, `unset` only): tickets closed, tickets with a follow-on count stamped, follow-ons, and post-merge defects. Counts only, and the tier name is checked against that closed set before sending. |

| `handbacks` | integer | How many times a run stopped and returned control to you. A count of turn boundaries — never what was said. |
| `guard_denies` | integer | How many times the deferral guard refused an unauthorised new ticket. |
| `blocked_markers` | integer | How many times `/adt-block` was used across your closed tickets. |
| `dod_conditions` | integer | How many Definition-of-Done conditions your tickets carry, summed. Never the conditions themselves — they contain commands and file paths. |
| `dod_pinned` | integer | How many of those are pinned to a commit where the check demonstrably failed. A count; no SHAs. |
| `input_tokens` | integer | Fresh input tokens across the ledger. |
| `output_tokens` | integer | Output tokens across the ledger. |
| `cache_read_tokens` | integer | Tokens served from prompt cache. |
| `cache_write_tokens` | integer | Tokens written to prompt cache. |
| `surfaces` | map of name → integer | How many times each review agent or skill was dispatched. Keys are ADT's own agent and skill names, checked against the same bounded pattern as command names. Never a prompt, a verdict or a ticket id. |
| `models` | map of name → two integers | Per model id, tokens and cost in micro-dollars. The model id only — never a request, a response, or which ticket used it. A model id names which Anthropic model ran and says nothing about your work; it is the one piece of cost-ledger row content that crosses, and `tools/tests/test_collector.py` asserts it appears in this map and nowhere else in the payload. |
| `qa_checks` | integer | How many QA results are written in your tickets' QA reports, summed. A count — never the report. |
| `qa_fails` | integer | How many of those QA results were FAIL, meaning QA sent the build back. |
| `qa_tickets` | integer | How many tickets have at least one QA result. The count only — never an id. |
| `qa_tickets_failed` | integer | How many of those tickets failed QA at least once. |

**Why the two maps are name-keyed and still safe.** A map key is the one place a
string chosen at runtime could reach the collector. Both key sets are closed and
short, and both ends check them: the client drops a name it does not recognise
before sending, and the collector rejects one it does not recognise on arrival.
A ticket id, a file path or a branch name cannot satisfy either check.

**The read direction.** Usage lands in a Cloudflare Analytics Engine dataset
(`adt_usage_v2`), not in KV; install identity is the only thing stored in KV, as
`install:<uuid>` → `{first_seen}`. Reading the usage back is a separate,
authenticated request to the Analytics Engine SQL endpoint
(`/accounts/{id}/analytics_engine/sql`) made by `tools/adt_usage_report.py` with
a token only the maintainer holds. Nothing in that path is reachable from your
install, and no token for it ships in this repo.

### The uninstall event payload

`adt-install.sh --uninstall` sends one last report when you uninstall, right
after it stops the watcher and before it deletes `.adt/`. It is the only way the
maintainer can tell an uninstalled install from one that turned telemetry off or
whose machine is switched off, because after the uninstall nothing arrives from
either. It is sent only when telemetry is on and `.adt/state/install-id` already
exists: an install that never reported sends nothing, and no id is created for
it. It gives up after a five-second network timeout and never stops the uninstall.
`tools/tests/test_ping_payload_capture.py` decodes the event the client sends
and fails if it carries a field this table does not name.

| Field | Type | What it is |
|---|---|---|
| `event` | string | Always `uninstall`. The collector accepts no other value. |
| `install_id` | uuid | The same random id the daily report sends, read from `.adt/state/install-id`. |
| `version` | semver | The ADT version being uninstalled. |
| `os` | string | `platform.system()`, the same value the daily report sends. |

Nothing is sent when you update ADT. The collector sees an update when an
install's daily report starts carrying a newer `version`. The event is stored as
one Analytics Engine row and never written to KV, so an uninstall never adds an
install to the count.

## The version advisory is not a kill switch

ADT fetches a static manifest and **warns on stderr** if the version you are
running has been marked revoked. It does not refuse to run, block, or exit —
nothing here can stop you using the version you have. It is the only channel for
getting people off a release with a serious bug, and it is deliberately **not**
signed and deliberately **fails open**: any network,
parse or schema error is ignored rather than blocking you. Because it fails
open, an attacker who can intercept the fetch can only *suppress* a revocation,
never forge one.

There is no remote disable, and there will not be. The code is public and on
your disk, so a kill switch would be an `if` you could delete in one line, and
the licence grant is irrevocable in terms — it would be a control that reads
stronger than it is.

## What the installer writes

`adt-install.sh` copies rules, agents, hooks, skills and commands into your
project's `.claude/` as real files, and records `{path, sha256}` for each in
`.claude/.adt-manifest.json` along with the bundle version. Update and uninstall
act on exactly that manifest — nothing outside it is touched, and a file you
have edited is kept rather than overwritten.

Everything ADT reads is text you can read too. There is no compiled component
and no obfuscation: the playbooks must be plaintext because Claude Code reads
them as prompts.
