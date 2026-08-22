# BugMine

A holistic bug-intelligence system: a continuously-crawled, versioned catalog of known bugs and
live outages across an entire software stack — functional, performance, system-level, and
dependency — plus surfaces that tell a team which of them actually affect it.

## The problem

Existing tooling is the wrong shape for how software actually breaks. Security scanners find
CVEs, but CVEs are a minority of what breaks a build, and most flagged vulnerabilities aren't
exploitable — so teams get heavy alert noise while the real causes go unreported: breaking
changes, deprecations, functional regressions, build failures, compatibility and performance
problems. AI coding assistants are increasing the volume.

## Three surfaces on one catalog

| Surface | Question it answers | Status |
| --- | --- | --- |
| **Search / subscribe** | What is known about this software? | Requirements + architecture |
| **Scan** | What is wrong with the code I have? | Requirements only |
| **Advise** | Given what I propose to build, what will I run into? | Requirements + architecture + data model |

The advisor is being built first: it is the sharpest differentiator, the thinnest build on top of
the catalog, and the cheapest way to find out whether the catalog produces advice anyone values.

## Status

**Design phase — no implementation yet.** This repository currently contains design documents and
the skills used to produce them. Nothing is deployed and no language or framework is committed to.

## Documentation

All docs live in [`docs/`](docs/). See [`docs/README.md`](docs/README.md) for the layout and
conventions.

| Document | What it covers |
| --- | --- |
| [`docs/requirements/bugmine.md`](docs/requirements/bugmine.md) | FR-1 – FR-20, FR-36 – FR-37; API and worker proposals |
| [`docs/requirements/advisor.md`](docs/requirements/advisor.md) | FR-21 – FR-35 — the advisor surface |
| [`docs/requirements/non-functional.md`](docs/requirements/non-functional.md) | NFR-1 – NFR-40 — performance, scale, availability, security, operability |
| [`docs/architecture/ingestion.md`](docs/architecture/ingestion.md) | Crawl → extract → index, and the shared job substrate |
| [`docs/architecture/advisor.md`](docs/architecture/advisor.md) | Intake → profile → sufficiency → retrieve → reason → report |
| [`docs/data-model/stack-profile.md`](docs/data-model/stack-profile.md) | The advisor IR, catalog record shape, version matching |
| [`docs/adr/`](docs/adr/) | Decision records — append-only |

Requirement IDs are unique and stable across every document. They are **not** sequential within a
file: FR-36 and FR-37 were added to `bugmine.md` after FR-21 – FR-35 were assigned in
`advisor.md`.

## Decisions made so far

- [ADR-0001](docs/adr/0001-advisor-input-normalization.md) — all four advisor input types
  normalize into one Stack Profile before anything downstream runs.
- [ADR-0002](docs/adr/0002-grounding-and-provenance.md) — findings must cite catalog records, and
  this is enforced by schema and set membership rather than by prompting.
- [ADR-0003](docs/adr/0003-catalog-seeding-strategy.md) — hand-seed a narrow catalog slice across
  all seven subject domains before building crawlers.

## Open

- **Non-functional requirements are drafted but unconfirmed.** Every number in
  [`docs/requirements/non-functional.md`](docs/requirements/non-functional.md) is proposed, not
  measured or committed. They unblock most of `ingestion.md`'s open decisions once agreed.
- **Own-code analysis** (FR-36, FR-37) is unscoped — a different engine from catalog lookup.
- **Reachability** — static call-graph analysis vs LLM-judged usage — is the largest cost fork in
  the scanner and is unanswered.

## Development

Design work uses the project skills in [`.claude/skills/`](.claude/skills/) —
`requirements`, `data-model`, `api-design`, `architecture`, `adr`, and `design-doc` (which
sequences the other five). Invoke them as `/requirements`, `/architecture`, and so on.

The SDLC phase commands (`/plan`, `/implement`, `/test`, `/release`) are user-level and live
outside this repository.
