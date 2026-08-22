# BugMine — Non-Functional Requirements

**Status:** Draft. **Every number here is `PROPOSED`** — derived from the three answers below, not
from measurement or a customer commitment. They exist to be argued with; correct them and they
become binding.
**Date:** 2026-08-22
**IDs:** `NFR-n`, a separate namespace from the functional `FR-n`.

## Settled inputs

| | Decision |
| --- | --- |
| **Deployment** | Multi-tenant SaaS **and** a self-hosted option |
| **Inference** | All of: third-party hosted models, self-hosted models, and per-tenant selection between them |
| **Year-one scale** | Early SaaS — hundreds of teams |

**Sizing assumption**, used throughout: ~300 teams, ~1,500 users, ~2,000 tracked components.
Every capacity number below is a consequence of that line; change it and they all move.

---

## 1. Performance

| ID | Requirement |
| --- | --- |
| **NFR-1** | Catalog search (FR-7): p95 < 500 ms, p99 < 1.5 s, at 50 rps sustained. |
| **NFR-2** | Job state (FR-6): p95 < 200 ms at 200 rps — it is polled, so it carries more load than its importance suggests. |
| **NFR-3** | Advice run (FR-35): p95 < 90 s from job accepted to result, for a Stack Profile of ≤ 30 components. |
| **NFR-4** | Scan run (FR-13): p95 < 10 min for a repo ≤ 250k LOC with ≤ 1,000 resolved dependencies. |
| **NFR-5** | Index lag: p95 < 5 min from Bug Service write to searchable (FR-7). |

NFR-3 and NFR-4 are queue-to-result targets, not request latencies — both are async by FR-13 and
FR-35. NFR-5 is the number that makes FR-8's "changes since last read" meaningful; without a
bound on it, a client cannot tell a quiet catalog from a stalled indexer.

## 2. Scale

| ID | Requirement |
| --- | --- |
| **NFR-6** | 300 teams / 1,500 users, with tenant count able to triple without re-architecture. |
| **NFR-7** | 2,000 tracked components; 150k bug records; 500k bug versions. |
| **NFR-8** | 20k crawl jobs/day sustained, absorbing 3× burst when a scheduled sweep coincides with an ad-hoc refresh. |
| **NFR-9** | 3,000 scans/day, absorbing 10× burst within any 15-minute window. |
| **NFR-10** | Single repo up to 250k LOC and 1,000 resolved dependencies. |

NFR-9's burst factor is the one that shapes the queue. Scans are CI-triggered, so demand is not
smooth — a customer merging a dependency bump across forty services produces forty scans in a
minute. Sizing to the daily average and discovering this in production is the predictable failure.

NFR-7 is a direct consequence of content-hash dedup (`../data-model/stack-profile.md` §3).
Without it, bug-version count tracks crawl frequency rather than reality and NFR-7 is unbounded.

## 3. Availability

| ID | Requirement |
| --- | --- |
| **NFR-11** | Query plane: 99.9% monthly (search, job state, results). |
| **NFR-12** | Ingestion plane: 99% monthly. Degradation is catalog staleness, not user-visible failure. |
| **NFR-13** | **The query plane must remain fully available while the ingestion plane is degraded.** |
| **NFR-14** | Advice and scan submission accept work during ingestion degradation; jobs queue rather than being rejected. |

NFR-13 is the one worth defending in review. BugMine reports on *other providers'* outages
(FR-2), so users reach for it precisely when their infrastructure is having a bad day — and a
correlated failure would make it useless exactly when it matters. This forces the plane split in
`../architecture/ingestion.md` §1 to be a real isolation boundary, not just a diagram convention.

## 4. Durability and recovery

| ID | Requirement |
| --- | --- |
| **NFR-15** | Catalog: RPO 15 min, RTO 4 hours. |
| **NFR-16** | **Bug version history may never be lost.** It is not reconstructible — a re-crawl recovers the current state of a source, never its past states. Tier-1 durability, independent backups, restore tested quarterly. |
| **NFR-17** | Raw crawl artifacts retained ≥ 90 days, so extraction can be re-run against improved prompts or models without re-crawling. |
| **NFR-18** | Findings and reports: RPO 1 hour, RTO 8 hours. Regenerable from the catalog, so lower tier than NFR-16. |

NFR-16 is the sharpest durability claim in the document, and it follows from FR-5 rather than
from preference: the version history *is* the differentiated asset. Losing the catalog's current
state costs a re-crawl; losing its history costs the thing that cannot be bought back.

## 5. Security and privacy

| ID | Requirement |
| --- | --- |
| **NFR-19** | No cross-tenant data access, enforced at both the gateway and the data layer — never at one alone. |
| **NFR-20** | Customer source code is retained only for the duration of a scan plus 24 hours, then deleted. Never used for model training. |
| **NFR-21** | **Per-tenant inference routing is a hard guarantee**: a tenant pinned to self-hosted inference has its code sent to no third-party model, ever, on any code path. |
| **NFR-22** | Encryption in transit (TLS 1.3) and at rest for all tenant data. |
| **NFR-23** | Audit log of every access to tenant data, retained 12 months, tamper-evident. |
| **NFR-24** | **Secrets present in scanned code must never reach findings, reports, logs, or telemetry.** |
| **NFR-25** | Catalog data derived from public sources is not tenant data and is not subject to NFR-20; private-system records (FR-11) are. |

NFR-21 is the load-bearing one given the "all of the above" inference answer. It is not a
configuration preference but a contractual guarantee, and it must hold on every path — including
retries, fallbacks when a self-hosted model is unavailable, and error reporting. **A fallback
that silently reaches a hosted model on failure would breach it**, so the correct behavior for a
pinned tenant whose local inference is down is to fail the job.

NFR-24 is easy to miss and expensive to get wrong: crawled content is public, but scanned code is
not, and it routinely contains credentials. Any component that echoes code back — a finding
excerpt, a stack trace, an OTel span attribute — is a potential exfiltration path.

## 6. Compliance

| ID | Requirement |
| --- | --- |
| **NFR-26** | SOC 2 Type II readiness within 12 months of first paying customer — the expected bar for selling a code-processing tool to teams. |
| **NFR-27** | GDPR: deletion and export for user-identifying data (accounts, billing, audit records). |
| **NFR-28** | No HIPAA or PCI scope. Treated as out of scope unless a customer requires it, at which point it is a deliberate decision, not a discovery. |

## 7. Operability

| ID | Requirement |
| --- | --- |
| **NFR-29** | **Alert when a crawl source has had no successful run in 3× its schedule interval.** |
| **NFR-30** | Alert when index lag exceeds NFR-5's target for 15 consecutive minutes. |
| **NFR-31** | Every finding is traceable to the bug version and raw artifact that produced it (`../architecture/advisor.md` §3). |
| **NFR-32** | Worker telemetry (FR-20) retained 30 days; per-tenant token cost attributable per job, since FR-17 bills on it. |
| **NFR-33** | Indexer health is measured as **lag**, not job outcome — it is a stream consumer, not a job, and outcome-based alerting would report it healthy while it falls behind. |

NFR-29 addresses the failure mode that no other alert catches: a dead crawler produces **no
errors**. The catalog keeps serving, dashboards stay green, and the data quietly ages. Error-rate
alerting is structurally blind to it; only absence-of-success detects it.

## 8. Deployment and portability

| ID | Requirement |
| --- | --- |
| **NFR-34** | A self-hosted deployment must deliver scan and advise at functional parity with SaaS. |
| **NFR-35** | **Self-hosted deployments must be able to receive catalog updates**, with a defined freshness bound and an offline mode that reports its own staleness rather than presenting stale data as current. |
| **NFR-36** | Self-hosted supports at least one fully local inference path, so a deployment can run with no outbound network. |
| **NFR-37** | API versioning: breaking changes require 6 months' notice and one release of overlap. |
| **NFR-38** | Package-ecosystem coverage at launch is stated explicitly, and an unsupported ecosystem produces "not covered", never silence (FR-14). |

NFR-35 is the largest consequence of choosing dual deployment, and it deserves to be argued about
before it is built. **The catalog is the product, so self-hosting means shipping the product
continuously rather than shipping software once.** That is a distribution problem — packaging,
delta updates, licensing, and revocation — and it does not exist at all in the SaaS-only world.
It is an ADR, not a line item.

## 9. Cost

| ID | Requirement |
| --- | --- |
| **NFR-39** | Inference cost per scan and per advice run is measurable per tenant and bounded by a configurable ceiling. |
| **NFR-40** | A single job cannot exceed its ceiling without failing loudly — cost overrun is a job failure, not a silent charge. |

Both follow from FR-17 billing on token usage: a system that bills on a quantity it cannot cap is
one prompt-loop away from an unrecoverable bill.

---

## Consequences worth confronting

**Per-tenant inference means finding quality varies by tenant.** A tenant on a hosted frontier
model and one on a local 8B model will not receive the same findings for the same code. This
touches more than infrastructure: accuracy claims must be qualified by inference tier, FR-19's
metrics are not comparable across tenants, and FR-17's billing must reflect that the cheaper tier
is also the weaker one. Currently unaddressed anywhere in the requirements.

**Dual deployment forks nearly everything.** Upgrades, telemetry (a self-hosted tenant emits
nothing you can see), support, and catalog distribution all split in two. NFR-34 through NFR-36
state the target; the engineering cost lands across every component.

## Open

| # | Question |
| --- | --- |
| **N1** | Is the self-hosted option available at launch, or a later tier? NFR-35's distribution problem is large enough that the answer changes the year-one roadmap. |
| **N2** | Which inference tier is the default, and is the weaker tier disclosed to the tenant in its findings? |
| **N3** | What is the compliance obligation for private-system catalog records (FR-11) — are they tenant data under NFR-20, or a third category? |
| **N4** | Does NFR-16's "never lost" extend to self-hosted deployments, where you do not control the storage? |
