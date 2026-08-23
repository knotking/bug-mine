# MVP Implementation Sequence

**Status:** Draft, for approval
**Date:** 2026-08-22
**Plans:** [`mvp.md`](mvp.md) (what), [`token-accounting.md`](token-accounting.md), [`../api/openapi.yaml`](../api/openapi.yaml) (the contract)

---

## The shape of the sequence

Two facts drive the ordering.

**Tenancy cannot be retrofitted.** Row-level security, the principal model, and privacy scope have
to be in the schema from the first migration. Adding them later means touching every query and
re-testing every isolation boundary, and the failure mode of getting it wrong is a data leak
rather than a bug.

**The first real value does not need the scanner.** M4 puts BugMine in an IDE — ask about a
dependency, get a cited answer — using only the catalog and `POST /check/dependencies`. No scan
workers, no GitHub App, no reachability. That is roughly half the system deferred behind the first
thing a user can actually feel.

---

## The matrix

| # | Milestone | Delivers | Depends on | The test that proves it | Size |
| :-: | --- | --- | :-: | --- | :-: |
| **M0** | **Foundations** | GCP project, Terraform, Cloud SQL, GCS, Artifact Registry, CI. Schema + Alembic. RLS, tenant/user/team/membership/invite model. OIDC, device flow, per-principal API keys. Admin CLI for provisioning. | — | **Tenant B cannot read tenant A's rows** — asserted by test, not code review | L |
| **M1** | **Job substrate** | Cloud Tasks queues → dispatcher service → Cloud Run Jobs. Job table behind `GET /v1/jobs/{id}`. OTel spans. Reservation + inline cost ceiling. | M0 | A no-op job runs end to end; a forced overrun ends `budget_exceeded`, not `failed` | M |
| **M2** | **Ingestion** | `crawl` worker (egress, no model) → GCS artifacts, content-hash dedup. `extract` worker (model, no egress), schema-constrained output. Component registry + aliases. Global sources as config-as-code. | M1 | **Re-running a crawl creates zero new versions.** Corrupt input fails validation rather than persisting | L |
| **M3** | **Catalog read** | `GET /bugs/*`, `/components`. `POST /check/dependencies`. The `/v1/public/*` route tree. | M2 | A lockfile's dependencies return cited bugs; uncovered ones return `not_covered`, never inventions | M |
| **M4** | **IDE surface** ⭐ | Python SDK generated from the spec. CLI with local inventory (Python, npm). MCP stdio server: `check_dependency`, `search_bugs`, `explain_finding`. | M3 | **In Claude Code: ask about a dependency, get a cited answer — and no source leaves the machine** | M |
| **M5** | **Reachability** | Per-language parsers, symbol/import narrowing, confidence model. | M4 | A repo that depends on an affected package but never imports the symbol is narrowed out | L |
| **M6** | **Server-side scan + GitHub App** | `scan_fetch` (egress), `scan_analyze` (no egress). Secret redaction at the sandbox exit. Webhook → scan → Check Run. | M5 | A PR gets a Check Run with cited findings **and** a "not covered" line. A planted credential reaches no finding, log, or span | L |
| **M7** | **Feedback** | Disposition endpoints, suppression scoped to state, precision by origin. | M6 | A dismissal suppresses recurrence — but the finding returns when the underlying record changes | S |
| **M8** | **Tenant ingestion** | Tenant source CRUD, credentials, quotas, **SSRF defence**. | M2, M6 | **The crawler refuses a metadata-endpoint URL** — at the application layer *and* at the NAT | M |
| **M9** | **Console** | TS SDK. Onboarding, invites, API keys, sources, usage. Server-rendered public bug pages. | M8 | An invited user completes onboarding without operator involvement | M |
| **M10** | **Hardening** | Staleness alerting, dashboards, burst load test, billing reconciliation. | M9 | **A stopped crawler alerts despite producing no errors** | M |

⭐ = first milestone a user can feel.

---

## Why each thing sits where it does

**M0 first, and it is the largest foundational lift.** Not because infrastructure is hard, but
because tenancy, principals, and privacy scope are all one-way doors. Note that the schema was
already written once and deleted — `git checkout 1a8a3a4 -- packages migrations` recovers it,
including the two migration defects that were found and fixed by round-tripping against a real
Postgres.

**M1 before any worker.** Every worker is a job type; building one before the substrate means
building the substrate badly, inside a worker, and then extracting it.

**M2 before M3 because an empty catalog proves nothing.** A read path over no data cannot
distinguish "working" from "broken", which is the same reason ADR-0003 argued for seeding.
Crawling supersedes hand-seeding for every domain that *can* be crawled.

**M4 before M5 and M6 — the important sequencing call.** The IDE path needs the catalog and one
endpoint. It does not need reachability, scan workers, sandboxes, or the GitHub App. Putting it
before them means design partners are using the product while the expensive half is still being
built, and their dispositions start informing M5 before M5 is written.

**M5 before M6 because scanning without reachability reproduces the problem.** A server-side scan
that reports every catalog match is the 92%-false-positive behaviour BugMine exists to fix.
Shipping M6 first would demo the thing we criticise.

**M8 last, deliberately.** It is the only milestone that lets untrusted input choose what our
infrastructure fetches. It should land when NAT, egress policy, quotas, and job isolation already
exist — not alongside them.

**M9 late because invite-only makes it optional.** With operator-provisioned tenants and an admin
CLI, design partners can be onboarded without a console. The console becomes necessary at the
point self-serve does.

---

## What runs in parallel

| Track | Can start | Runs alongside |
| --- | --- | --- |
| Terraform / CI | M0, day one | Everything |
| SDK generation | **Now** — the spec exists | M1 onward |
| Crawl source curation | **Now** — no code needed | M0–M1 |
| Parser work (M5) | After M2's schema settles | M3, M4 |
| Public bug page templates | After M3 | M4–M6 |

Crawl source curation is worth starting immediately and is easy to forget: choosing and vetting
~50 GitHub repos and ~10 changelogs is judgement work with no dependency on any code, and M2 is
blocked without it.

---

## Milestone gates

Do not pass a milestone until its test passes. Three are security controls and need adversarial
tests rather than happy-path ones:

| Gate | Milestone |
| --- | --- |
| Cross-tenant read is impossible | M0 |
| Egress from `scan_analyze` to a non-Vertex host fails | M6 |
| A planted secret reaches no finding, log, or span attribute | M6 |
| A metadata-endpoint URL is refused at both layers | M8 |

---

## What this sequence does not include

Evals, the advisor, own-code analysis, subscriptions, reports, cross-tenant promotion, and
self-hosted deployment — all out of MVP scope per [`mvp.md`](mvp.md).

One consequence to keep visible: with evals absent, the `llm_model` subject domain **stays empty
through every milestone above**, because it has no crawlable source. The catalog will not cover
the subject its own requirements list first until evals exist.
