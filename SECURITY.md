# Security policy

## Reporting a vulnerability

Report privately through GitHub's **Report a vulnerability** button on the
Security tab of this repository, which opens a private advisory visible only to
the maintainer. Please do not open a public Issue for a security problem.

Include what you did, what happened, and what you expected. A proof of concept
helps but is not required.

Expect an acknowledgement within a week. There is one maintainer, so please
allow reasonable time before disclosing publicly.

## Scope

ADT is a configuration layer for Claude Code: markdown playbooks, bash hooks,
and a stdlib-only Python sync engine. It never makes an inference call and never
handles your model credentials.

In scope: anything in this repository — a hook that can be made to run
unintended commands, an installer that writes outside its manifest, a sync path
that leaks ticket content, a supply-chain issue in the install flow.

Out of scope: Claude Code itself (report to Anthropic), GitHub, and the security
of a consuming project's own code.

## What ADT sends over the network

See `docs/security-posture.md`. Every outbound destination is enumerated there.
