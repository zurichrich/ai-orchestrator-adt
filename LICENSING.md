# ai-orchestrator-adt — licensing

**One line: the whole repository is Apache License 2.0. There is no split, no
reserved use, and nothing you need permission for.**

Use it, fork it, modify it, run it in production, run it for clients, build a
product on it, sell that product. The only obligations are Apache-2.0's own:
keep the licence and copyright notices, state what you changed, and don't use
the project's name to imply endorsement (see Trademark below).

## Why one licence, and why Apache rather than MIT

ADT was published under a split — MIT for most of the tree, BSL-1.1 for
`tools/` — reserving hosted, managed or embedded operation for third parties.
That split is withdrawn. Two reasons.

**The BSL was guarding the wrong asset.** `tools/` is a stdlib-only GitHub
Issues sync loop, a kanban HTML renderer and a cost meter — a fortnight's work
for anyone who wanted to reimplement it, and no moat at all. What makes ADT
worth using is `commands/` and `defaults/`: the
judgement about how to hold an agent to a gated path. That was already MIT, and
it is what a competitor would actually have to copy. Reserving the engine cost
adoption and protected nothing.

**The reservation wasn't buying the option people think it buys.** A source-
available licence today does not preserve the ability to charge tomorrow — sole
copyright does (see Contributing). And a hosted ADT service, if one is ever
built, is new code: a server, tenancy, auth, a database. None of that exists in
this repository, and none of it has to be licensed the way this repository is.
The option was never in the licence file.

**Apache-2.0 rather than MIT** because it adds two things MIT lacks and this
project wants: an express patent grant (§3), and a trademark clause (§6) that
keeps the name distinct from the code. It is also the licence the rest of the
category uses — Cline, Aider and OpenHands are all Apache-2.0 — so it raises no
question in a procurement review.

## What this means for you

| You want to | Allowed | Conditions |
|---|---|---|
| Run ADT on your own machines | Yes | None |
| Run it in production, commercially | Yes | None |
| Modify it, keep the changes private | Yes | None |
| Fork and redistribute it | Yes | Keep LICENSE + NOTICE; mark changed files |
| Offer it to others as a hosted service | Yes | Same as above |
| Bundle it inside a product you sell | Yes | Same as above |
| Call your fork "ADT" | Ask first | Apache-2.0 §6 — see Trademark |

No path here requires a commercial licence, because none is offered.

## Licence headers

Every source file carries an SPDX identifier:

```
# SPDX-License-Identifier: Apache-2.0
```

This is what licence scanners read — they do not read this file.
`tests/test_licence_header_coverage.sh` fails when a shipped source file is
missing one, so the tree cannot drift out of agreement with the `LICENSE` at the
root. It is a local test, not CI: this repository runs no CI (ADR-036), so the
test binds only when someone runs the suite.

**"Every source file" means every `.py` and `.sh` under version control, with no
stated exclusion.** Every one of them carries a header, and
`tests/test_licence_header_coverage.sh` reads that policy from this file, so the
policy and the check cannot drift. The nine frozen experiment trees that were
previously excluded went to the private archive when ADT was published.

**Files that are not source.** Everything else that ships — `.md`, `.yaml`,
`.js`, `.toml`, `.html`, and files with no extension such as `LICENSE`, `NOTICE`
and `VERSION` — carries no SPDX header and does not need one. Documentation,
configuration and data are covered by the `LICENSE` at the root by virtue of
being in the repository. The header convention exists for the benefit of
automated licence scanners, which read source files; a header in a Markdown
document serves no one and would have to be maintained forever. If you
redistribute any of it, the `LICENSE` and `NOTICE` requirements in the table
above apply just the same.

## Trademark

The Apache-2.0 grant covers the code, not the name. "ADT" and
"ai-orchestrator-adt" identify this project; please don't use them for a fork or a
derived product in a way that suggests it is this project or endorsed by it.
Saying your tool is *built on* or *compatible with* ADT is fine and always will
be — that is the "reasonable and customary use in describing the origin of the
Work" that §6 preserves.

## Contributing

Contributions require the CLA in `CLA.md`.

Apache-2.0 §5 would already put your contribution under Apache-2.0 without any
CLA, so the CLA is not there to license the code inbound. It is there so the
project keeps sole copyright, which is what makes *future versions*
relicensable — the option that the BSL was mistakenly carrying. The first
merged contribution without a CLA ends that permanently, because a relicense
would then need every past contributor's individual consent.

To be explicit about what that option is and is not: anything already published
under Apache-2.0 stays Apache-2.0 forever. The grant is perpetual and
irrevocable in terms (§2). Relicensing can only ever apply to versions released
after the change, and anyone may fork from the last Apache-2.0 commit. That is
the real cost of exercising the option, and it is the same cost HashiCorp and
Redis paid. Holding the option is not the same as it being free to use.
