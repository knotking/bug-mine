# BugMine MVP on GCP — ingestion, jobs, scan, and the tools that consume them

**Status:** Approved 2026-08-22. Implementation in progress.

## Context

`docs/` now holds a complete design — 71 functional and 46 non-functional requirements, six
architecture documents, six ADRs, and researched motivation. **There is no code.**

This plan builds the first running system: the ingestion pipeline that fills the catalog, the job
substrate everything runs on, and the scanner — reachable from a **GitHub bot, Cursor, and Claude
CLI**, hosted on GCP. Evals are explicitly deferred to a later phase.

The consumer list changes one thing the design never addressed. Cursor and Claude Code both speak
**MCP**, and a GitHub bot speaks webhooks and the Checks API. So the delivery surface is an MCP
server and a GitHub App, not only the REST endpoints sketched in Appendix A of
`docs/requirements/bugmine.md`. MCP is how a developer's agent asks BugMine a question mid-task,
which is a materially better fit for the advisor-style value than a dashboard would be.

**Intended outcome:** a developer opens a PR and gets a Check Run listing which known defects
actually reach their code, and can ask the same questions from Cursor or Claude Code without
leaving the editor.

## Scope

**In:** ingestion (crawl → extract → catalog), job substrate, scanner with static reachability,
MCP server, GitHub App, Terraform for all of it, OTel, inline cost ceilings.

**Out, deliberately:** evals (later phase, as directed), own-code analysis (FR-36/37 — a different
engine, and its findings cannot be cited), the advisor surface, subscriptions and reports, the
promotion pipeline (no cross-tenant corroboration needed while single-tenant), a dedicated search
engine, self-hosted deployment.

## Platform decisions

**Cloud Run**, not GKE. Services and Jobs both fit, it scales to zero for a system idle most of
the time, and the fetch/analysis egress split the scanner needs is a per-service VPC egress
setting rather than a NetworkPolicy. Containerise everything cleanly so a later move to GKE is a
deployment change; nothing in the code should know it is on Cloud Run.

| Concern | Service | Why this one |
| --- | --- | --- |
| Job queue | **Cloud Tasks** | Per-task dispatch with retry and rate limiting matches lease semantics; Pub/Sub's fan-out is the wrong shape for work queues |
| Workers | **Cloud Run Jobs** | One image per job type, scale to zero |
| API / MCP / webhook | **Cloud Run Services** | Three services, one image each |
| Catalog | **Cloud SQL Postgres 16** | Relational model with `JSONB` for the applicability union; **Postgres FTS defers the search-engine decision (I-D1, I-D5) entirely** |
| Raw artifacts | **GCS** | Durable, cheap, enables re-extraction without re-crawling |
| Repo snapshots | **GCS + 24h lifecycle rule** | NFR-20 delete-after-scan enforced by bucket policy, not application code |
| Inference | **Vertex AI (Gemini)** | IAM auth instead of API keys; audit logging; stays in-project |
| Scheduling | **Cloud Scheduler** → Cloud Tasks | The crawl scheduler from `ingestion.md` |
| Secrets | **Secret Manager** | GitHub App private key, webhook secret |
| Telemetry | **OTel → Cloud Trace + Monitoring** | FR-20, NFR-32 |
| Build/deploy | **Cloud Build + Artifact Registry** | |
| Infra | **Terraform 1.14** | Already installed |

New GCP project `bugmine-dev` — current gcloud config points at `memdog-dev`, which is unrelated.

## Repository layout

`uv` workspace, Python 3.12 (both already installed).

```
infra/terraform/          project, Cloud SQL, GCS, Cloud Run, Tasks, Scheduler, IAM
packages/bugmine/
  models/                 SQLAlchemy: BugRecord, BugVersion, Applicability, Job, Finding
  catalog/                write path (dedup), read path (version-range matching)
  jobs/                   Cloud Tasks enqueue + lease, job state, OTel, cost ceiling
  llm/                    Vertex client behind an interface; schema-constrained output only
workers/
  crawl/                  fetch source → GCS artifact          (egress: yes, model: no)
  extract/                artifact → typed records via Gemini  (egress: no,  model: yes)
  scan_fetch/             clone repo + resolve packages → GCS  (egress: yes, model: no)
  scan_analyze/           inventory → retrieve → reach → findings (egress: no, model: yes)
services/
  api/                    REST (Appendix A subset)
  mcp/                    MCP server — Cursor and Claude CLI
  github_app/             webhook → enqueue scan → Check Run
migrations/               Alembic
tests/
```

The **egress column is the security boundary**, not a comment. `extract` and `scan_analyze` feed
untrusted content to a model, so per ADR-0005 and NFR-42 they get Cloud Run VPC egress set to deny
everything but the Vertex endpoint. Terraform enforces this; it must not be a convention.

## Phases

### Phase 1 — Foundation

Terraform for project, Cloud SQL, GCS buckets, Artifact Registry, service accounts with per-worker
least privilege. Alembic schema implementing `docs/data-model/stack-profile.md`: `BugRecord`
(subject domain, subject ref, bug type, applicability `JSONB`, origin, lifecycle state),
`BugVersion` (content hash, artifact ref), `ComponentRegistry` with aliases, `Job`, `Finding`.

The two schema details that are expensive to retrofit: **applicability as a tagged union** (not
`version_min`/`version_max` — SaaS and models have no versions), and the **component registry with
aliases**, since `postgres`/`postgresql`/`PostgreSQL` is the retrieval join key and a mismatch
returns an empty result that reads as good news.

### Phase 2 — Job substrate

Enqueue/lease over Cloud Tasks, job state table behind `GET /job/{id}` (FR-6), OTel spans on every
worker, and the **inline cost ceiling** — reserve before starting, check inside the loop, fail
loudly on overrun (NFR-40). Not downstream aggregation: by then the tokens are spent.

Verify with a no-op job type end to end before building real workers.

### Phase 3 — Ingestion

Sources for the MVP, all allowlisted per ADR-0005:

- **GitHub Releases API** for ~50 tracked repos — release notes are where breaking changes and
  deprecations live, which is exactly the non-security data nothing else catalogs
- **OSV** for a security baseline (structured, free)
- ~10 vendor changelogs (Postgres, Kafka, Couchbase, Twilio, and similar)

`crawl` writes the response body verbatim to GCS keyed by content hash and **stops if the hash is
unchanged** — without this, versioning mints a record per crawl forever and catalog size tracks
crawl frequency rather than reality. `extract` then calls Gemini with a **strict response schema**,
and every field is validated against the component registry before persistence, so an injected
instruction cannot produce an unparseable component or a field that does not exist.

### Phase 4 — Scanner

`scan_fetch` (has egress) clones the repo and resolves packages; `scan_analyze` (no egress) runs
inventory → catalog retrieval → reachability → findings. Two ecosystems only: **Python** (uv, pip,
poetry lockfiles) and **npm** (package-lock, pnpm-lock).

Reachability is **static narrowing only** for the MVP — resolve whether the affected symbol or
module is referenced at all, using per-language parsers (`ast`, `tree-sitter`). Per ADR-0006 this
is where most of the win is and it needs a parser rather than a compiler. The model-judgment stage
is deferred, so findings ship with lower confidence rather than being suppressed — the failure
direction ADR-0006 specifies.

Two gates, both structural rather than advisory:
- **Provenance** — the model sees only retrieved records, its schema requires a record ID per
  finding, and IDs outside the retrieved set fail a set-membership check
- **Secret redaction** at the sandbox exit, covering findings, logs, *and* OTel attributes —
  NFR-24, and a span attribute is the easiest place for a credential to escape

### Phase 5 — Delivery surfaces

**MCP server** (Cursor, Claude CLI) — the tools worth having:

| Tool | Does |
| --- | --- |
| `scan_repo` | Scan a path or repo; returns job handle |
| `get_scan` | Findings, each citing its catalog record |
| `search_bugs` | Query the catalog by component, version, bug type |
| `check_dependency` | "I'm about to add X@1.2 — what's known?" |

`check_dependency` is the one that matters. It answers the 70%-of-fixes-break-your-code problem at
the moment the decision is being made, inside the editor, which is the whole reason MCP is a
better fit here than a dashboard.

**GitHub App** — webhook on `pull_request` → enqueue scan → post a Check Run. Only findings that
survive reachability, each with its citation, plus an explicit **"not covered"** line for
components absent from the catalog (FR-39). Silence would read as a clean bill of health, and the
catalog will be thin early.

### Phase 6 — Hardening

Crawler staleness alerting on **absence of successful runs**, not error rate — a dead crawler
produces no errors while the catalog quietly ages (NFR-29). Plus Cloud Monitoring dashboards, and
a load test against NFR-9's 10× CI burst.

## Verification

**Per phase, before moving on:**

1. **Substrate** — enqueue a no-op job, observe state transitions via `GET /job/{id}`, confirm the
   trace lands in Cloud Trace and a forced overrun fails the job rather than charging silently.
2. **Ingestion** — crawl a known repo's releases; assert typed records appear with correct
   subject/type on both axes; **re-run and assert no new versions are created** (dedup);
   corrupt a source and assert extraction fails validation rather than persisting garbage.
3. **Scanner** — the four tests that actually matter:
   - Every finding names the catalog record grounding it. One without a citation is a bug.
   - **A repo whose dependencies are absent from the catalog returns "not covered" — never
     invented findings.** This is the test an LLM-backed system fails silently.
   - A version just outside a record's affected range does **not** match.
   - A repo that depends on an affected package but never imports the affected symbol is
     narrowed out by reachability.
4. **Egress isolation** — from inside `scan_analyze`, attempt an outbound connection to a
   non-Vertex host and assert it fails. This is a security control, so it needs a test, not a
   config review.
5. **Secret redaction** — plant a credential-shaped string in a fixture repo; assert it appears in
   no finding, log line, or span attribute.
6. **End to end** — open a PR against a fixture repo with a known-affected dependency, confirm the
   Check Run appears with a cited finding, then ask `check_dependency` about the same package from
   Claude CLI and confirm the answers agree.

Tests 3b, 4, and 5 are regression tests, not one-off checks — each is a failure that is invisible
in normal use.

## Cost

Rough monthly at MVP scale: Cloud SQL small instance ~$50, Cloud Run scale-to-zero ~$20, GCS ~$5,
Cloud Tasks and Scheduler negligible. **Gemini extraction dominates and scales with crawl breadth**
— roughly $100–300/month at 50 repos plus 10 changelogs on a daily cadence. Budget ~$200–400/month,
and treat extraction as cost of goods rather than infrastructure.

## Open, and deliberately not decided here

- **Auth and tenancy.** The MVP is effectively single-tenant. FR-11's private-system boundary and
  NFR-19's cross-tenant isolation need real answers before a second customer, not after.
- **Reachability's model stage** — ADR-0006 is still `Proposed`. Static narrowing alone will leave
  false positives that the 61.9% figure predicts.
- **Ecosystem coverage** beyond Python and npm — NFR-38 requires this stated rather than
  discovered by a user.
- **Evals**, per your direction — but note ADR-0003's hand-seeding was a workaround for an empty
  catalog, and Phase 3 crawling supersedes it for the domains that *can* be crawled. LLM models
  remain uncoverable until evals exist.
