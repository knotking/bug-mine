# BugMine — Functional Requirements (high level)

**Status:** Draft, for discussion
**Source:** `~/Bug Mine.pdf` (2 pages), read 2026-08-22. Every requirement below traces to a line
in that PDF; nothing else was used.
**Altitude:** Capability level only — what the system does, not how well, not how. Non-functional
requirements, acceptance criteria, entities, and API contracts are deliberately out of this
document. IDs are stable; later documents will cite them.

---

## What BugMine does

Builds a versioned catalog of known bugs and live outages across the software ecosystem by
crawling, exposes it for search and subscription, and scans user code against it to identify
issues — with billing and metrics on that usage.

Users are `UNSPECIFIED IN SOURCE`. Inferred and unconfirmed: engineering teams who need to know
whether their dependencies have known defects or are currently broken. See Q1.

---

## 1. Ingestion — getting bugs and outages in

| ID | Requirement |
| --- | --- |
| **FR-1** | Record bugs across seven domains: LLM models, operating systems, databases, messaging/pub-sub queues, SaaS platforms, programming languages, and well-known GitHub repos. |
| **FR-2** | Record ongoing outages and their current status for providers (e.g. Twilio, AWS). |
| **FR-3** | Populate bug records via a web crawler that is configurable without a code change. |
| **FR-4** | Populate outage records via a configurable web crawler, on the same terms. |
| **FR-5** | Version all bug data over time — changes create new versions, prior versions stay retrievable. |
| **FR-6** | Expose the state of every data pull job via API. |

## 2. Read and distribution — getting data out

| ID | Requirement |
| --- | --- |
| **FR-7** | Make bug and outage data searchable, with each result carrying its version and timestamp. |
| **FR-8** | Let users poll for data. |
| **FR-9** | Let users request a subscription and be notified on change instead of polling. |
| **FR-10** | Publish a report of bugs from user-supplied search criteria. |
| **FR-11** | Share all bug information across users, except records belonging to a private system. |

## 3. Scanning — using the data against user code

| ID | Requirement |
| --- | --- |
| **FR-12** | Scan code and repositories against the bug catalog and report the issues found. |
| **FR-13** | Run scans asynchronously in a worker system rather than inline with the request. |
| **FR-14** | Pull missing package data just in time so a scan is grounded in complete package information. |
| **FR-15** | Identify issues using both the stored bug data and an LLM. |
| **FR-16** | Use LLMs pre-configured by the system for scans, not chosen per request. |

## 4. Billing

| ID | Requirement |
| --- | --- |
| **FR-17** | Track token usage and scan charges per team and per user. |

## 5. Metrics

Two distinct audiences here — keep them separate. FR-18/FR-19 are product metrics answering a
customer's questions about their own usage; FR-20 is operational telemetry answering the team's
questions about whether BugMine itself is working.

### Product metrics

| ID | Requirement |
| --- | --- |
| **FR-18** | Report top-down breakage per bug. |
| **FR-19** | Report scan runs and bugs identified, per team and per user. |

### Operational telemetry

| ID | Requirement |
| --- | --- |
| **FR-20** | Instrument every worker run — crawler runs (FR-3, FR-4) and scan runs (FR-13) alike — with OpenTelemetry, covering at minimum run outcome, duration, and volume processed. |

Note: naming OpenTelemetry is a technology decision rather than a capability, recorded here
because it was specified directly. The capability is that worker execution is observable in
production; OTel is how. Worth an `adr` when the design firms up, so the choice has a record and
the collector/backend question gets settled explicitly rather than by default.

---

## 6. Own-code analysis

Added 2026-08-22 from the design conversation, not from the PDF. BugMine identifies bugs
holistically — functional, performance, system-level, and dependency — and that includes defects
in the user's **own** code, not only known third-party bugs that reach it.

| ID | Requirement |
| --- | --- |
| **FR-36** | Identify functional and performance defects in the user's own code, independent of whether any catalog record matches. |
| **FR-37** | Distinguish own-code findings from catalog-grounded findings in every output, since the two carry different kinds of evidence. |

FR-37 exists because of a real asymmetry. A catalog-grounded finding cites a bug record: *this
component, this version, this documented defect*. An own-code finding has nothing to cite — it is
the system's own judgment about code it just read. Presenting both in one undifferentiated list
would let the weaker evidence borrow the authority of the stronger. This is the same discipline
FR-32 imposes on the advisor.

**Note on numbering:** FR-36 and FR-37 follow FR-21 – FR-35 in [`advisor.md`](advisor.md). IDs are
unique and stable across all requirements documents; they are not sequential within a file.

Own-code analysis is a **different engine** from catalog lookup — one reads a corpus of known
defects, the other reasons about code it has never seen. They share a surface, not a mechanism,
and scoping it is deliberately left to its own design pass.

---

## Non-goals

- **Evals on LLMs and repos to identify bugs** — the source marks this "mostly future work".
  Worth noting it would become a second origin for catalog entries alongside crawling (FR-3),
  so FR-1 should not assume a crawler is the only way a bug gets in.

---

## Open questions

These shape the product, not just the spec — worth settling before the next document.

| # | Question |
| --- | --- |
| ~~Q1~~ | **Closed 2026-08-22.** Engineering teams whose stack breaks under them, poorly served by CVE-only tooling that reports mostly non-exploitable findings while missing the functional, performance, and system-level breakage that actually costs them. |
| ~~Q2~~ | **Closed 2026-08-22.** Neither alone. The catalog is the hub; three surfaces sit on it — search/subscribe (§2), scan (§3), and advise ([`advisor.md`](advisor.md)). The advisor is being built first. |
| **Q3** | What is versioned in FR-5 — each bug record, each crawl run, or the catalog as a whole? |
| **Q4** | What is a "private system" in FR-11 — a tenant, a self-hosted deployment, or a per-record flag? |
| ~~Q5~~ | **Closed 2026-08-22.** Confirmed: issues in the scanned code. |
| **Q6** | What does "top-down breakage on bug" (FR-18) actually measure? Left as stated; no measure invented. |
| **Q7** | Does BugMine ever suggest or apply fixes, or only identify? No remediation appears in the source. |

---

## Appendix A — API proposal

**This is a proposal, not a requirement.** The FRs above say *what* must be possible; this
sketches the surface that would make it so, to give the requirements something concrete to be
argued against. It is superseded by `api-design` when that runs, and nothing here is binding.

Endpoint names follow the sketch in `~/searchengine.png` where it already named one.

### Catalog — read

| Endpoint | Purpose | FR |
| --- | --- | --- |
| `GET /bug/search` | Search the catalog; results carry version and timestamp | FR-7 |
| `GET /bug/{id}` | Fetch one bug record at its current version | FR-1 |
| `GET /bug/{id}/versions` | Version history for a record | FR-5 |
| `GET /bug/changes?since=` | Everything changed since a watermark — the poll path | FR-8 |
| `GET /outage` | Current outage status by provider | FR-2 |

### Catalog — subscriptions and reports

| Endpoint | Purpose | FR |
| --- | --- | --- |
| `POST /subscription` · `GET` · `DELETE /subscription/{id}` | Manage subscriptions | FR-9 |
| `POST /report` | Generate a report from search criteria; returns a job handle | FR-10 |
| `GET /report/{id}` | Retrieve a generated report | FR-10 |

### Control plane

| Endpoint | Purpose | FR |
| --- | --- | --- |
| `GET`/`POST`/`PUT`/`DELETE /bug/config/source` | Manage crawl sources | FR-3, FR-4 |
| `POST /bug/config/schedule` | Set a source's crawl schedule *(name from the sketch)* | FR-3 |
| `GET /job/{id}` | State of any job, of any type | FR-6 |
| `GET /job?type=&status=` | List jobs — the operational view | FR-6 |

### Work surfaces

| Endpoint | Purpose | FR |
| --- | --- | --- |
| `POST /scan` | Request a scan of code or a repo; returns a job handle | FR-12, FR-13 |
| `GET /scan/{id}` | Scan findings once complete | FR-12 |
| `POST /advise` | Submit any of the four intake types; returns a job handle | FR-21 – FR-25 |
| `GET /advise/{id}` | Findings, **or** the specific questions the advisor needs answered | FR-27, FR-34 |
| `POST /advise/{id}/answer` | Supply missing facts and continue — only if A1 resolves interactive | FR-27 |

### Billing and metrics

| Endpoint | Purpose | FR |
| --- | --- | --- |
| `GET /billing/usage?team=&user=` | Token usage and scan charges | FR-17 |
| `GET /metrics/bug/{id}/breakage` | Blast radius for a bug | FR-18 |
| `GET /metrics/usage?team=&user=` | Scan runs and bugs identified | FR-19 |

**Three things to argue about before this hardens.** Every long-running action (`/scan`,
`/advise`, `/report`) returns a job handle and is read back through `/job/{id}` — one async
pattern, not three. `GET /advise/{id}` deliberately returns *either* findings or questions,
which is unusual and is the API-shaped consequence of FR-26. And no endpoint here is versioned
yet; `api-design` must settle that before anything is exposed.

## Appendix B — Worker proposal

**Also a proposal.** Every entry is a job type on the one shared substrate: it leases from the
Job Queue, reports state through FR-6, and emits telemetry through FR-20. Adding a capability
should mean adding a job type, never a parallel pipeline.

| Worker | Job type | Does | Writes | Characteristic failure |
| --- | --- | --- | --- | --- |
| **data crawler worker** | `crawl` | Fetches a configured source | Raw artifact store | Source dies quietly — shows as staleness, not errors |
| **extraction worker** | `extract` | Raw artifact → structured records via LLM | Bug Service | Fails *softly* — plausible wrong records, needs provenance to catch |
| **scan worker** | `scan` | Repo → findings against the catalog | Findings store | Incomplete package grounding (FR-14) |
| **package fetch worker** | `package_pull` | Just-in-time package data a scan is missing | Package store | Upstream registry unavailable mid-scan |
| **advisor worker** | `advise` | Stack Profile → predicted problems | Advice store | Ungrounded output — the FR-32 risk |
| **report worker** | `report` | Search criteria → published report | Report store | Long-running over large result sets |
| **notification worker** | `notify` | Delivers subscription events | — | Duplicate or dropped delivery |

Two components that look like workers but are **not** jobs, and should not be modelled as such:

- **bug job scheduler** — a control-plane producer. It decides when work is due and enqueues it;
  it is not itself leased work.
- **Indexer** — a continuous stream consumer of Bug DB change events, not a discrete job. Its
  health is measured as *lag*, where a worker's is measured as job outcome, so it needs different
  instrumentation under FR-20.

The `package_pull` split is the one genuinely open call: FR-14 describes the pull as happening
inside a scan, but as a separate job type it becomes cacheable and shareable across concurrent
scans of the same dependency. Worth an ADR.

## Next

Non-functional requirements now live in [`non-functional.md`](non-functional.md) (NFR-1 – NFR-40)
— drafted 2026-08-22, every number proposed rather than measured. `api-design` is the natural
next step for Appendix A.
