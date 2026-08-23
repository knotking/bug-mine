# BugMine MVP — Multi-Tenant Catalog, Dual-Trigger Ingestion, and Scanning

**Status:** Draft, for approval
**Date:** 2026-08-22
**Requirements:** FR-1 – FR-16, FR-38 – FR-46, NFR-4, NFR-9 – NFR-24, NFR-38 – NFR-46
**Out of scope:** evals (FR-47 – FR-56), the advisor, own-code analysis, subscriptions, reports,
cross-tenant promotion.

---

## 1. What this MVP is

Three capabilities, in dependency order:

1. **A multi-tenant catalog** — global records everyone sees, plus tenant-private records only
   the owning tenant sees.
2. **Ingestion with two triggers** — the admin system fills the global catalog; each tenant can
   fill its own private catalog from its own sources.
3. **Scanning for both admin and tenant users**, reachable from IDEs (Cursor, Claude Code),
   GitHub bots, CI, and any language via generated SDKs.

The single most consequential design choice below is §5: **for IDE clients, the scan runs on the
client and only the catalog lookup crosses the network.** Source code never leaves the developer's
machine, which turns the hardest objection to this product into a non-issue for the surface where
adoption starts.

---

## 2. Tenancy model

### Catalog scope

Every catalog record has exactly one scope. This is the whole tenancy model in one table:

| Scope | Written by | Visible to | Example |
| --- | --- | --- | --- |
| **`global`** | Admin-triggered ingestion | **All tenants** | "langchain 0.3.1 changed retry semantics" |
| **`tenant`** | Tenant-triggered ingestion, and that tenant's scans | **Owning tenant only** | "our internal payments-sdk 2.4 breaks on retry" |

Retrieval for any tenant is **`global ∪ own-tenant`**, never another tenant's private records.
This is FR-11's "shared unless a private system", made concrete.

**Tenant-private records never become global in the MVP.** Promotion requires corroboration
across unaffiliated tenants (ADR-0004), and with ten design partners nothing will reach a
defensible threshold. Building the promotion pipeline now would mean building a privacy control
that cannot yet be validated — worse than not having it.

### Isolation, enforced twice

NFR-19 requires isolation at the gateway **and** the data layer, never one alone. Concretely:

| Layer | Mechanism | Catches |
| --- | --- | --- |
| **Gateway** | Auth resolves `tenant_id`; it is never read from a request body or path | Forged or manipulated tenant references |
| **Database** | Postgres **row-level security**, `tenant_id` set per connection from the session | Any application bug that forgets a `WHERE` clause |
| **Storage** | GCS object prefixes per tenant, IAM conditions on the prefix | Cross-tenant artifact reads |
| **Jobs** | `tenant_id` on every job row; workers assume tenant context, never choose it | A worker processing the wrong tenant's payload |

Row-level security is the part worth insisting on. Application-level filtering is one forgotten
predicate away from a cross-tenant read, and that class of bug does not announce itself — it
returns *more* data, not an error. RLS makes the database refuse.

### Roles

| Role | Can | Cannot |
| --- | --- | --- |
| **Tenant member** | Scan own repos, read `global ∪ own`, manage own crawl sources | See another tenant; write global records |
| **Tenant admin** | The above, plus manage members, API keys, quotas | Write global records |
| **BugMine admin** | Manage global sources, curate global records, scan anything for validation | *Read tenant-private records without an audited break-glass path* |

The last row matters and is easy to get wrong. Operators being able to read customer data casually
is the thing a security review will find. Admin access to tenant-private records must be an
explicit, logged, time-boxed action, not an ambient property of the role.

---

## 3. Ingestion — one pipeline, two triggers

The pipeline is identical in both cases. **Scope is a property of the source, not of the code.**
An admin source produces `global` records; a tenant source produces `tenant` records. There is no
second pipeline and no branch in the workers.

| | **Admin-triggered** | **Tenant-triggered** |
| --- | --- | --- |
| Configured by | BugMine admin | Tenant admin |
| Sources | GitHub Releases, OSV, ~10 vendor changelogs | Tenant's own changelogs, internal release feeds, private repos |
| Scope of output | `global` | `tenant` |
| Schedule | Cloud Scheduler, daily | Tenant-set, quota-bounded |
| Cost borne by | BugMine (cost of goods) | Tenant (metered, FR-17) |
| Credentials | None — public sources | Tenant-supplied, in Secret Manager |
| URL validation | Allowlist (ADR-0005) | **Allowlist plus SSRF defence — see below** |

### Tenant-supplied URLs are a new attack surface

ADR-0005 assumed crawl sources were chosen by us. A tenant-configurable source inverts that: the
tenant supplies a URL that our infrastructure will fetch, with our credentials, from inside our
network. That is a **server-side request forgery primitive**, and it did not exist in the
admin-only design.

The realistic attack is not exotic. A tenant points a source at
`http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token` and
the crawler fetches a service account token into an artifact the tenant can then read back.

Required controls, all in the fetch worker before any request:

1. **Resolve DNS first, then validate the resolved IP** — validating the hostname alone is
   defeated by DNS rebinding.
2. **Deny RFC1918, loopback, link-local, and `169.254.169.254`** explicitly.
3. **Egress through Cloud NAT with a deny-by-default firewall**, so a bypass at the application
   layer still fails at the network layer.
4. **No redirects to a different host** without re-validating.
5. **Fetch worker holds no credentials of its own** beyond the tenant's supplied ones.

This is a genuine blocker, not a hardening nice-to-have. Tenant-configurable sources must not ship
before these do.

---

## 4. Scanning

Two execution modes, because two very different consumers need it.

| | **Client-side scan** | **Server-side scan** |
| --- | --- | --- |
| Used by | IDE (Cursor, Claude Code), CLI, local dev | GitHub bot, CI, hosted API |
| Inventory + reachability run | On the developer's machine | In an egress-isolated Cloud Run job |
| What crosses the network | **Component names and versions only** | The repository |
| Source code leaves the machine | **No** | Yes, deleted after 24h (NFR-20) |
| Latency | Seconds | Job-queued |

**Client-side is the default for IDEs and it is the important one.** The scan needs two things:
the dependency graph (derivable locally) and the catalog (remote). Only the second requires the
network, and asking *"what is known about `langchain@0.3.1`?"* leaks nothing but a dependency
name a lockfile already publishes.

This inverts the usual objection. The hardest question a security-conscious buyer asks — *does our
code go to your servers?* — gets answered "not from your editor, no", and the server-side path
exists only where the consumer has no local machine to run on.

### Pipeline (both modes)

```
manifests/lockfiles ──▶ inventory ──▶ dependency graph
                                          │
                          catalog retrieval│ (global ∪ tenant)
                                          ▼
                              candidate findings
                                          │
                    static reachability ──┤  symbol/import referenced at all?
                                          ▼
                         findings + "not covered" list
```

Reachability is **static narrowing only** in the MVP — is the affected symbol or module
referenced anywhere? Per ADR-0006 this needs a per-language *parser*, not a compiler, and it is
where most of the measured 61.9% reduction comes from. The model-judgment stage is deferred, so
uncertain findings ship with lower confidence rather than being suppressed.

Ecosystems at launch: **Python** (uv, pip, poetry) and **npm** (npm, pnpm, yarn). NFR-38 requires
this stated rather than discovered; anything else returns *"not covered"*.

---

## 5. Client integrations

### One spec, generated clients

Hand-writing an SDK per language is the wrong shape of work. The API is specified once in
**OpenAPI 3.1**, and clients are generated from it in CI.

| Client | How | Effort | Priority |
| --- | --- | --- | --- |
| **Python SDK** | Generated | Low | P0 — the CLI and MCP server both build on it |
| **TypeScript SDK** | Generated | Low | P0 — GitHub Action, Node CI |
| **Go SDK** | Generated | Low | P1 |
| **Java / .NET** | Generated | Low | P2 — on request |
| **CLI** (`bugmine scan`) | Thin wrapper on the Python SDK | Medium | P0 |
| **MCP server** | Wraps the SDK; stdio and streamable-HTTP | Medium | **P0 — Cursor + Claude Code** |
| **GitHub App** | Webhooks + Checks API, own service | High | P0 |
| **GitHub Action** | Thin wrapper on the CLI | Low | P1 |

The generated SDKs are close to free once the spec exists. **The real work is the MCP server and
the GitHub App**, which are hand-written because neither is a REST client.

### MCP server

Runs in two transports from one codebase:

- **stdio, local** — the default for Cursor and Claude Code. Performs client-side scanning; only
  catalog lookups leave the machine.
- **streamable HTTP, hosted** — for clients that cannot run a local process. Server-side scanning.

| Tool | Purpose |
| --- | --- |
| `scan_workspace` | Scan the open project; returns findings with citations |
| `check_dependency` | *"I'm about to add X@1.2 — what's known?"* |
| `search_bugs` | Query the catalog by component, version, or bug type |
| `explain_finding` | The catalog record behind a finding, with its evidence URL |

`check_dependency` is the one that earns the integration. It answers the
70%-of-security-fixes-break-your-code problem *at the moment the decision is being made*, inside
the editor, before the dependency is added — which is a thing a dashboard structurally cannot do.

### GitHub App

`pull_request` webhook → enqueue server-side scan → Check Run. Reports only findings surviving
reachability, each with its citation, **plus an explicit "not covered" line** (FR-39). The catalog
will be thin early, and silence would read as a clean bill of health.

### Authentication

| Consumer | Mechanism |
| --- | --- |
| Web / IDE user | Google OIDC → short-lived session |
| CLI / MCP local | Device-code flow → refresh token in the OS keychain |
| CI / bots | Tenant-scoped API key, prefix-identifiable, revocable |
| GitHub App | Installation token, mapped to a tenant at install |

Every path resolves to a `tenant_id` server-side. **No request may name its own tenant.**

---

## 6. GCP technology

| Concern | Service | Why this rather than the alternative |
| --- | --- | --- |
| API, MCP-HTTP, GitHub webhook | **Cloud Run services** | Scale to zero; three services, one image each. GKE costs a cluster baseline before any traffic |
| Workers | **Cloud Run jobs** | Per-job-type images, no idle cost |
| Work queue | **Cloud Tasks** | Per-task dispatch, retries, and **per-queue rate limits — the noisy-neighbour control**. Pub/Sub's fan-out is the wrong shape |
| Catalog | **Cloud SQL Postgres 16** | Relational, `JSONB` for the applicability union, **RLS for tenant isolation**, and FTS defers the search-engine choice entirely |
| Artifacts | **GCS**, per-tenant prefixes | Re-extraction without re-crawling |
| Repo snapshots | **GCS + 24h lifecycle rule** | NFR-20 enforced by bucket policy, not application code |
| Inference | **Vertex AI (Gemini Flash)** | IAM auth rather than API keys; audit logs; stays in-project |
| Scheduling | **Cloud Scheduler** | Admin cadence and per-tenant schedules |
| Egress control | **Cloud NAT + VPC firewall** | The network half of the SSRF defence in §3 |
| Secrets | **Secret Manager** | Tenant crawl credentials, GitHub App key |
| Telemetry | **OTel → Cloud Trace + Monitoring** | FR-20, NFR-32 |
| Identity | **Identity Platform** | OIDC without building session handling |
| Build | **Cloud Build + Artifact Registry** | |
| Infra | **Terraform** | |

### Egress topology — the security-relevant part

| Component | Egress | Model access | Rationale |
| --- | --- | --- | --- |
| `crawl` / `scan_fetch` | **Yes**, via NAT with deny-by-default | No | Fetches untrusted content; must not hold a model |
| `extract` / `scan_analyze` | **No** (Vertex endpoint only) | Yes | Feeds untrusted content to a model; a successful injection reaches nothing |
| API / MCP-HTTP | Yes | No | |
| GitHub webhook | Yes | No | |

This split is ADR-0005's containment made physical. Terraform enforces it — it must never be a
convention.

---

## 7. Cost estimate

**Assumptions:** 10 tenants, ~50 users, 200 tracked global components, ~20k catalog records,
200 scans/day, daily crawl cadence.

### Fixed infrastructure

| Line item | Configuration | Monthly |
| --- | --- | --- |
| Cloud SQL Postgres | 2 vCPU / 8 GB, 100 GB SSD, no HA | **$120** |
| Cloud SQL HA (optional) | Regional failover | +$120 |
| Cloud Run services | 3 services, scale-to-zero, low traffic | **$25** |
| Cloud Run jobs | Crawl, extract, scan workers | **$30** |
| Cloud NAT | 1 gateway + data processing | **$45** |
| GCS | ~200 GB artifacts + operations | **$8** |
| Cloud Tasks / Scheduler | Well within free tier | **~$1** |
| Artifact Registry | ~10 GB images | **$1** |
| Secret Manager | ~30 secrets | **$2** |
| Cloud Trace / Monitoring | Above free tier | **$15** |
| Identity Platform | 50 MAU (50k free) | **$0** |
| **Fixed subtotal** | | **~$247/mo** |

Cloud NAT at $45 is the line people forget. It exists specifically to make the egress deny-list in
§6 enforceable at the network layer, and cutting it removes the second half of the SSRF defence.

### Variable inference

Gemini Flash-class pricing, ~$0.15/M input and ~$0.60/M output tokens. **Verify current rates
before committing to these numbers — model pricing moves.**

| Workload | Volume | Tokens | Monthly |
| --- | --- | --- | --- |
| Extraction — changed artifacts only | ~400/day | 5k in / 1.5k out | **$30** |
| Extraction — initial catalog backfill | 20k records, one-off | 5k in / 1.5k out | **$120 once** |
| Scan LLM stage | *Deferred — static reachability only* | — | **$0** |
| **Variable subtotal** | | | **~$30/mo** |

**Dedup is what makes this cheap.** Without the content-hash gate, extraction would run on every
crawl of every source rather than on the ~5% that changed — roughly **20× this bill**, growing
with crawl frequency rather than with reality.

### Total

| Scenario | Monthly |
| --- | --- |
| **MVP, no HA** | **~$280** |
| MVP with Cloud SQL HA | ~$400 |
| Plus one-off catalog backfill | +$120 once |
| If the scan LLM stage is added (~200 scans/day × 50 candidates) | +$200–400 |

Sensitivity, in order: **inference volume**, then Cloud SQL tier, then NAT. Everything else is
noise. The two things that would change the picture materially are enabling the LLM reachability
stage and increasing crawl breadth — both scale with catalog ambition rather than with tenant
count, which means **cost does not fall per-tenant as customers are added**. That is unusual and
worth pricing around.

---

## 8. Delivery phases

| Phase | Delivers | Proves |
| --- | --- | --- |
| **1. Foundation** | Terraform, Cloud SQL + RLS, schema, auth, tenant/user model | A second tenant cannot read the first's rows — tested, not reviewed |
| **2. Job substrate** | Cloud Tasks enqueue/lease, job state API, OTel, per-tenant quotas, inline cost ceiling | A no-op job runs end to end; a forced overrun fails loudly |
| **3. Admin ingestion** | Crawl + extract workers, global sources, dedup | Re-running a crawl creates no new versions |
| **4. Scanning core** | Inventory, retrieval, static reachability, provenance gate, secret redaction | An uncovered repo returns "not covered", never inventions |
| **5. Client surfaces** | OpenAPI + generated SDKs, CLI, MCP (stdio + HTTP), GitHub App | A PR gets a Check Run; the IDE gives the same answer |
| **6. Tenant ingestion** | Tenant sources, credentials, **SSRF defence**, quota enforcement | The crawler refuses a metadata-endpoint URL |
| **7. Hardening** | Staleness alerting, dashboards, burst load test | A stopped crawler alerts despite producing no errors |

Phase 6 is deliberately last. It is the only phase that lets untrusted input choose what our
infrastructure fetches, and it should land when the surrounding controls are already in place.

---

## 9. Risks and open decisions

| # | Item | Note |
| --- | --- | --- |
| **R1** | **SSRF via tenant-supplied URLs** | The highest-severity item here. Blocks Phase 6, not negotiable |
| **R2** | Admin access to tenant-private records | Needs a break-glass path with audit, not ambient permission |
| **R3** | Static-only reachability leaves false positives | The 61.9% figure predicts the remainder; feedback data will show how much |
| **R4** | Cost does not amortise per tenant | Crawl and extraction scale with catalog ambition, not customer count |
| **R5** | Client-side scanning means version skew | An old CLI runs old reachability logic; needs a minimum-version handshake |
| **R6** | RLS performance under load | Untested at NFR-9's burst; measure in Phase 7 |
| **D1** | Whether tenant-private records ever promote to global | Deferred; ADR-0004 applies when there are enough tenants to corroborate |
| **D2** | Whether the MCP server defaults to local or hosted | Recommend local — it is the stronger privacy story and the better latency |
| **D3** | Ecosystems beyond Python and npm | NFR-38 requires stating coverage, not discovering it |

---

## 10. What this deliberately does not build

Evals (FR-47 – FR-56) — the origin the research argues is the fastest-growing market and the only
viable one for LLM models. Deferred at your direction, with one consequence worth recording: the
`llm_model` subject domain **cannot be populated by crawling**, so it stays empty until evals
exist. The catalog will not cover the subject its own requirements list first.

Also out: the advisor, own-code analysis, subscriptions, reports, cross-tenant promotion, and
self-hosted deployment.
