# BugMine

A holistic bug-intelligence system: a continuously-crawled, versioned catalog of known bugs and
live outages across an entire software stack — functional, performance, system-level, and
dependency — plus surfaces that tell a team which of them actually affect it.

## The problem

Existing tooling is the wrong shape for how software actually breaks. A peer-reviewed study of
2,414 repositories measured a **92.0% false positive rate** in vulnerability scanners, caused
primarily by flagging defects in code that is never reached. The larger problem is what they never
report at all: **67%** of Maven packages have violated semantic versioning, **41.58%** of
client-impacting breaking changes arrive in non-major upgrades, and **70%** of vulnerable
dependencies require an update that breaks source code — so the tool that files the ticket is
silent about what fixing it costs. Meanwhile hosted LLMs have stopped being versioned dependencies
at all: GPT-4's code-execution success rate fell from **52% to 10% in three months with no version
change**.

Full evidence and sources in [`docs/motivation/`](docs/motivation/).

## Three surfaces on one catalog

| Surface | Question it answers | Status |
| --- | --- | --- |
| **Search / subscribe** | What is known about this software? | Requirements + architecture |
| **Scan** | What is wrong with the code I have? | Requirements only |
| **Advise** | Given what I propose to build, what will I run into? | Requirements + architecture + data model |
| **Evals** | What is wrong with software nobody has reported on yet? | Requirements only |

The advisor is being built first: it is the sharpest differentiator, the thinnest build on top of
the catalog, and the cheapest way to find out whether the catalog produces advice anyone values.

Bugs enter the catalog from **three origins** with different capabilities — crawling (broad,
cheap, but structurally always behind what has been published), scanning customer repos (knows
what actually breaks real systems, and improves as the customer base grows), and evals (the only
origin that can find a defect nobody has reported). For LLM models, where no vendor publishes a
defect tracker and behavior shifts under a stable identifier, evals are the *only* viable origin.
See [`docs/requirements/discovery.md`](docs/requirements/discovery.md).

## Status

**Design phase — no implementation yet.** This repository currently contains design documents and
the skills used to produce them. Nothing is deployed and no language or framework is committed to.

## Documentation

All docs live in [`docs/`](docs/). See [`docs/README.md`](docs/README.md) for the layout and
conventions.

**Start here:** [`docs/motivation/blog.md`](docs/motivation/blog.md) — the whole thing in one
document: why this should exist (with cited research), what it is, how it is designed, and what is
still open. Everything else in `docs/` is the detailed version of a section in that file.

| Document | What it covers |
| --- | --- |
| [`docs/requirements/bugmine.md`](docs/requirements/bugmine.md) | FR-1 – FR-20, FR-36 – FR-37; API and worker proposals |
| [`docs/requirements/advisor.md`](docs/requirements/advisor.md) | FR-21 – FR-35 — the advisor surface |
| [`docs/requirements/non-functional.md`](docs/requirements/non-functional.md) | NFR-1 – NFR-40 — performance, scale, availability, security, operability |
| [`docs/requirements/bug-taxonomy.md`](docs/requirements/bug-taxonomy.md) | FR-38 – FR-39 — what bugs are covered, subject × type matrix, concrete examples |
| [`docs/requirements/discovery.md`](docs/requirements/discovery.md) | FR-40 – FR-56 — three discovery origins, the scan feedback loop, and evals |
| [`docs/requirements/feedback.md`](docs/requirements/feedback.md) | FR-57 – FR-63 — finding disposition, suppression, precision as a measured metric |
| [`docs/requirements/record-lifecycle.md`](docs/requirements/record-lifecycle.md) | FR-64 – FR-71 — record states, retraction, bug identity and merging |
| [`docs/architecture/ingestion.md`](docs/architecture/ingestion.md) | Crawl → extract → index, and the shared job substrate |
| [`docs/architecture/advisor.md`](docs/architecture/advisor.md) | Intake → profile → sufficiency → retrieve → reason → report |
| [`docs/architecture/auth.md`](docs/architecture/auth.md) | Firebase for people, API Gateway for programs, and how both resolve to one principal |
| [`docs/architecture/scanner.md`](docs/architecture/scanner.md) | Two engines, dependency graph, reachability, secret redaction at the sandbox boundary |
| [`docs/architecture/evals.md`](docs/architecture/evals.md) | Eval results as measurements with distributions; regression detection |
| [`docs/architecture/promotion.md`](docs/architecture/promotion.md) | Candidate → corroboration → sanitization → shared catalog |
| [`docs/architecture/metering.md`](docs/architecture/metering.md) | Inline cost ceilings, usage ledger, billing and product metrics |
| [`docs/data-model/stack-profile.md`](docs/data-model/stack-profile.md) | The advisor IR, catalog record shape, version matching |
| [`docs/motivation/`](docs/motivation/) | Researched problem evidence, trajectory, market sizing, and the solution mapped to both |
| [`docs/plan/mvp.md`](docs/plan/mvp.md) | MVP plan — multi-tenancy, dual-trigger ingestion, scanning, client integrations, GCP mapping and costs |
| [`docs/plan/token-accounting.md`](docs/plan/token-accounting.md) | LLM token attribution and ceiling enforcement across system, tenant, team and user |
| [`docs/plan/mvp-sequence.md`](docs/plan/mvp-sequence.md) | Implementation order — eleven milestones, dependencies, and the test that gates each |
| [`docs/plan/advisor.md`](docs/plan/advisor.md) | Advisor implementation — six stages, what is already built, and the decision that blocks stage 4 |
| [`docs/api/`](docs/api/) | API contract — OpenAPI 3.1 spec and the decisions behind it |
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
- [ADR-0004](docs/adr/0004-scan-derived-catalog-entries.md) — bugs found while scanning customer
  repos feed the shared catalog, but only as candidates until corroborated across unaffiliated
  tenants; own-code findings never do.
- [ADR-0005](docs/adr/0005-untrusted-content-in-model-pipelines.md) — crawled pages and
  third-party code are untrusted input to models; the blast radius of a successful prompt
  injection is engineered rather than its probability.
- [ADR-0006](docs/adr/0006-reachability-analysis.md) — **Proposed, not accepted.** Narrow
  candidates with cheap static symbol analysis, then judge the residue with a model.
- [ADR-0007](docs/adr/0007-firebase-auth-and-api-gateway.md) — Firebase owns passwords, API
  Gateway owns API keys; the gateway also makes the deployment publicly reachable, which the
  org policy otherwise prevents.

## Open

- **Non-functional requirements are drafted but unconfirmed.** Every number in
  [`docs/requirements/non-functional.md`](docs/requirements/non-functional.md) is proposed, not
  measured or committed. They unblock most of `ingestion.md`'s open decisions once agreed.
- **Own-code analysis** (FR-36, FR-37) is unscoped — a different engine from catalog lookup.
- **The value of *k*** in the corroboration threshold (FR-44) is unset: too low leaks tenant
  information, too high starves the catalog when it is thinnest.
- **Probabilistic eval failures** have no corroboration model yet — LLM defects often reproduce at
  a *rate* rather than reliably, which a boolean threshold would reject.
- **Bug identity across origins** (FR-70) is unresolved — the same defect is described in
  different vocabularies by a changelog, a scan, and an eval, and cross-origin corroboration
  depends on matching them.
- **Reachability is undecided.** [ADR-0006](docs/adr/0006-reachability-analysis.md) is
  `Proposed`, not accepted — it is the largest cost fork in the system, and the scanner design
  is shaped around its outcome.
- **Bug identity across origins** (FR-70) is unsolved, and the promotion pipeline cannot corroborate
  anything without it.
- **No API contracts exist.** Appendix A of `bugmine.md` is a proposal; `api-design` has not run,
  so nothing is versioned or specified.
- **No implementation.** Every surface is designed; none is built. The MVP plan is in
  [`docs/plan/mvp.md`](docs/plan/mvp.md).
- **Reachability** — static call-graph analysis vs LLM-judged usage — is the largest cost fork in
  the scanner and is unanswered.

## Development

Design work uses the project skills in [`.claude/skills/`](.claude/skills/) —
`requirements`, `data-model`, `api-design`, `architecture`, `adr`, and `design-doc` (which
sequences the other five). Invoke them as `/requirements`, `/architecture`, and so on.

The SDLC phase commands (`/plan`, `/implement`, `/test`, `/release`) are user-level and live
outside this repository.
