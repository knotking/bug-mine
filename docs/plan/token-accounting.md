# LLM Token Accounting — System, Tenant, Team, and User

**Status:** Draft, for approval
**Date:** 2026-08-22
**Requirements:** FR-17 (billing), FR-19 (per-team/user metrics), NFR-32 (attribution),
NFR-39 (measurable per tenant), NFR-40 (overrun fails the job), NFR-21 (per-tenant inference tier)
**Architecture:** [`../architecture/metering.md`](../architecture/metering.md)

---

## 1. Why this is not usage analytics

The obvious design — emit usage events, aggregate nightly, bill monthly — cannot satisfy NFR-40:

> A single job cannot exceed its ceiling without failing loudly — cost overrun is a job failure,
> not a silent charge.

By the time an aggregation pipeline sees an event, the tokens are spent and the money is gone.
So the meter sits **on the critical path of every job that spends**, which is a materially
different component from a billing rollup.

The reason is not tidiness. Every worker that spends tokens is a model-driven loop over content
BugMine does not control — a crawled page, a customer's repository. A pathological input or an
injected instruction that induces a long generation is **one bad input away from an unrecoverable
bill**. A system that bills on a quantity it cannot cap is unbounded by construction.

There is a second constraint that shapes the mechanism: **you cannot stop a generation in
flight.** Once a model call starts, its output tokens will be produced. So the ceiling cannot be
a check *during* generation — it has to be expressed as `max_output_tokens` on each call, with a
running-total check *between* calls. That single fact determines most of §4.

---

## 2. Who bears the cost

Every job carries a **cost bearer**, determined by the job's origin — never by which worker runs
it. The same extraction worker serves both, and reading the bearer from the worker would
misattribute half the spend.

| Bearer | Spends on | Billed to | Scales with |
| --- | --- | --- | --- |
| **`system`** | Global crawl extraction, catalog backfill, evals, internal validation scans | Nobody — **cost of goods** | Catalog ambition |
| **`tenant`** | Tenant-triggered ingestion, tenant scans, tenant advice | The tenant (FR-17) | That tenant's usage |

Within a tenant bearer, attribution narrows further:

| Level | Answers | Used for |
| --- | --- | --- |
| **Tenant** | What do we invoice? | FR-17 billing, quota enforcement |
| **Team** | Which group inside the customer is spending? | FR-17, customer's own chargeback |
| **User** | Who triggered this run? | FR-19 metrics, abuse investigation |
| **Job** | What did this one unit of work cost? | NFR-40 enforcement, cost-per-finding |

**The system/tenant split is the number that matters commercially.** System spend does not fall
per tenant as customers are added — global crawl and extraction scale with how much of the world
the catalog covers, not with how many people read it. Mixing the two into one inference line
item hides the only cost that grows without revenue attached.

---

## 3. The ledger

One append-only table. Every model call writes exactly one row, at the time of the call.

| Field | Why |
| --- | --- |
| `job_id`, `job_type` | Ties spend to work; NFR-40 enforcement reads it |
| `cost_bearer` (`system` \| `tenant`) | §2 |
| `tenant_id`, `team_id`, `user_id` | Null when bearer is `system` |
| `purpose` | `extract`, `scan_judge`, `advise`, `eval` — cost per *capability*, not just per tenant |
| `model_id`, `inference_tier` | NFR-21: hosted vs self-hosted are different currencies |
| `input_tokens`, `output_tokens`, `cached_input_tokens` | Priced differently; a flat count over-bills |
| `attempt`, `outcome` | **Failed and retried calls still consumed tokens** |
| `rate_card_version` | §5 |
| `cost_micros` | Priced at write time, not recomputed |
| `occurred_at` | |

Three fields exist because of failure modes that are easy to miss:

**`cached_input_tokens`** — context caching is billed at a different rate. Counting total input
tokens over-bills a customer whose scans reuse a cached prompt prefix, which is exactly the
workload that benefits most.

**`attempt` / `outcome`** — a call that errored after generating tokens still costs money.
Counting only successes systematically under-accounts, and the gap widens precisely when the
system is unhealthy.

**`inference_tier`** — a tenant pinned to self-hosted inference (NFR-21) consumes GPU-seconds,
not API tokens. Its rows have real token counts and near-zero `cost_micros`, so tokens and cost
must be separate columns rather than one derived from the other.

---

## 4. Enforcement

Four points, in order. Only the first and third can actually stop anything.

| # | Point | Mechanism | Stops |
| --- | --- | --- | --- |
| 1 | **Before the job** | Check tenant budget; reserve an allowance on the job row | A tenant already over quota |
| 2 | **Before each call** | `max_output_tokens = min(model_max, remaining_reservation)` | A single runaway generation |
| 3 | **Between calls** | Compare running total to reservation; abort if exhausted | A runaway *loop* |
| 4 | **After the job** | Release unused reservation; write ledger rows | — |

Point 2 is the load-bearing one and it is a provider feature, not our code. Since a generation
cannot be interrupted, **`max_output_tokens` is the only hard cap that exists.** Every model call
in the system must set it from the remaining reservation rather than from a constant.

Point 3 catches the case point 2 cannot: a loop making a thousand individually-small calls. Both
are needed; neither substitutes for the other.

Exhaustion produces `budget_exceeded`, a **distinct terminal state** from `failed`. A user who
hit a ceiling needs to know it was a limit, not a crash — the remedies are completely different.

### Reservation sizing

Reserve on an estimate, since output length is unknowable in advance:

| Job type | Estimate basis |
| --- | --- |
| `extract` | Artifact size → input tokens; output bounded by the response schema |
| `scan_judge` | Candidate findings × per-finding budget |
| `advise` | Stack Profile component count × per-component budget |
| `eval` | Suite size × runs, known exactly in advance |

Too coarse a reservation blocks capacity that is never used; too fine means renewal round-trips
on the hot path. **Reserve per job, renew per stage** is the starting position — recorded as T-D2
because it needs measurement, not argument.

---

## 5. Rate cards, and pricing at write time

`cost_micros` is computed **when the event is written**, using a versioned rate card, and never
recomputed.

Model pricing changes. Recomputing a March invoice with August rates produces a number that
matches neither what was charged nor what it cost, and the error is invisible because it still
looks like a plausible invoice. Storing `rate_card_version` alongside the price makes historical
figures reproducible and repricing a deliberate, auditable act.

The rate card is configuration, not code: model ID → input, output, and cached-input rates, with
an effective date. A model whose ID is absent from the card must **fail the call**, not default
to zero — a silently free model is a billing hole that grows quietly.

---

## 6. Quotas and fairness

Two separate mechanisms, often confused:

| Control | Prevents | Where |
| --- | --- | --- |
| **Token quota** per tenant/period | Unbounded spend | Enforcement point 1 |
| **Rate limit** per tenant | Starving other tenants | Cloud Tasks per-queue dispatch rate |

A tenant merging a dependency bump across forty services generates forty scans in a minute
(NFR-9's 10× burst). A token quota alone does not stop that from monopolising the queue — it is
within budget, just all at once. Cloud Tasks per-queue rate limiting is the noisy-neighbour
control, and it is the reason Cloud Tasks was chosen over Pub/Sub.

Quota exhaustion behaviour needs deciding (T-D1): hard stop, soft overage with alerting, or
degrade to a cheaper tier. Degrading is tempting and dangerous — it silently changes finding
quality, which NFR-21's tier distinction says must be visible.

---

## 7. Reconciliation

**Our counted tokens will not match the Vertex invoice.** Known sources of drift: cached-token
accounting, retries and partial generations, requests that failed before billing, and
provider-side rounding.

A monthly reconciliation job compares ledger totals against billing export, per model, and alerts
above a threshold. Without it the first discovery of drift is a customer disputing an invoice.

For GCP specifically: Cloud Billing export to BigQuery gives the authoritative figure, and the
ledger gives attribution. **Neither alone is sufficient** — billing export knows the true cost but
not which tenant caused it; the ledger knows the tenant but only our estimate of the cost.

---

## 8. Reporting

| Report | Requirement | Source |
| --- | --- | --- |
| Tenant invoice | FR-17 | Ledger, grouped by tenant and period |
| Team / user usage | FR-17, FR-19 | Ledger, grouped by team and user |
| Cost per capability | — | Ledger, grouped by `purpose` |
| **Cost per useful record** | — | Ledger joined to catalog records by origin |
| System cost of goods | §2 | Ledger where `cost_bearer = system` |

**Cost per useful record is the one worth building early even though nothing requires it.** It
answers whether an origin pays for itself — extraction spend divided by records that later
grounded a finding. If crawling one source costs more than its records ever contribute, that is a
decision to stop crawling it, and nothing else in the system would surface that.

---

## 9. GCP implementation

| Concern | Service |
| --- | --- |
| Ledger | Cloud SQL Postgres, append-only, partitioned monthly |
| Reservation / running total | Same transaction as the job row — no separate store to fall out of sync |
| Rollups | Scheduled aggregation into summary tables; BigQuery export if analytics outgrow Postgres |
| Reconciliation | Cloud Billing → BigQuery export, compared monthly |
| Alerting | Cloud Monitoring on quota approach, drift, and unattributed spend |
| Token counts | Vertex response `usage_metadata`, never estimated after the fact |

**Unattributed spend must be alerted on, not tolerated.** Any model call whose bearer cannot be
resolved is a bug in the calling path, and if it is allowed to accumulate silently the billing
figures quietly stop summing to the invoice.

### Worked example, at MVP scale

Against the [MVP plan](mvp.md) assumptions — 10 tenants, ~400 changed artifacts/day, 200
scans/day, static-only reachability:

| Bearer | Purpose | Monthly | Note |
| --- | --- | --- | --- |
| `system` | Global crawl extraction | ~$30 | Does not fall per tenant |
| `system` | Catalog backfill | ~$120 once | |
| `tenant` | Scans | **$0** | No LLM stage in the MVP |
| `tenant` | Tenant-triggered extraction | ~$2/tenant | Scales with their sources |

**The MVP bills tenants almost nothing, because the expensive stage is deferred.** That is worth
naming: it means FR-17's billing path will be exercised on near-zero amounts, and the first real
test of it arrives with the reachability LLM stage (+$200–400/mo) or with evals. Build the ledger
now; do not assume it is correct until something meaningful flows through it.

---

## 10. Open decisions

| # | Decision |
| --- | --- |
| **T-D1** | Quota exhaustion: hard stop, soft overage, or degrade to a cheaper tier. Degrading changes finding quality silently, which conflicts with NFR-21's premise |
| **T-D2** | Reservation granularity — per job, per stage, or per call |
| **T-D3** | Whether self-hosted-inference tenants are billed at all, and in what unit — GPU-seconds, scans, or a flat fee |
| **T-D4** | Whether system-bearer spend is ever apportioned to tenants, or stays entirely cost of goods |
| **T-D5** | Ledger retention and partition-drop policy at NFR-8/9 volumes |
| **T-D6** | Whether tenants see their own token detail, or only a derived unit like scans — exposing raw tokens couples pricing to model choice permanently |

T-D6 deserves an early answer. Billing customers in tokens means every model change is a pricing
change they can see, and it forecloses ever switching to a cheaper model without an argument.
