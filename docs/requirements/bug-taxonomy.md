# BugMine — Bug Taxonomy

**Status:** Draft, for discussion
**Requirements:** elaborates FR-1; adds FR-38 and FR-39.
**Date:** 2026-08-22

> **On the examples below.** They are *illustrative shapes* — the form a catalog record takes for
> each cell — not verified records. Well-known historical cases are marked ✱. Nothing here is the
> seed set; every record must be independently verified against a real source before it enters
> the catalog (ADR-0003).

---

## 1. Two axes

A catalog record is classified on **both** axes. Either alone is insufficient for retrieval: the
advisor asks "what is wrong with *Couchbase 7.2*" (subject) and the ranker asks "is this a
*breaking change* or a *deprecation*" (type), and a record missing either is unreachable by one
of them.

| ID | Requirement |
| --- | --- |
| **FR-38** | Every catalog record carries both a subject domain and a bug type. Neither is optional. |
| **FR-39** | Catalog coverage per subject is explicit. A subject with no records returns **"not covered"**, never an empty result that reads as a clean bill of health. |

FR-39 closes open question A3 in [`advisor.md`](advisor.md). It matters most early, when the
catalog is thin and "no findings" will usually mean "we don't know", not "you're fine".

## 2. Coverage matrix

Which cells are meaningful is itself information. `—` marks cells that are **structurally empty**,
not merely unpopulated — a SaaS platform cannot have a build failure because you never build it.

| Subject ↓ / Type → | Security | Functional | Performance | Compatibility | Breaking change | Deprecation | Build |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| **LLM models** | ● | ●● | ●● | ● | ●● | ● | — |
| **Operating systems** | ●● | ● | ● | ●● | ● | ● | ● |
| **Databases** | ● | ●● | ●● | ● | ● | ● | — |
| **Messaging queues** | ● | ●● | ●● | ●● | ● | ● | — |
| **SaaS platforms** | ● | ●● | ● | ●● | ●● | ●● | — |
| **Languages / runtimes** | ● | ● | ● | ●● | ●● | ●● | ●● |
| **Repos / libraries** | ●● | ●● | ● | ●● | ●● | ● | ●● |

●● dense · ● present · — structurally absent

The two columns that matter most for BugMine's positioning are **functional** and **breaking
change** — the ones CVE-shaped tooling does not report at all. Security is the crowded column,
and it is the one BugMine competes least on.

---

## 3. Examples by subject

### LLM models

The domain with no version numbers — behavior shifts underneath a stable identifier, which is
why applicability is a `ModelRevision` or `TimeWindow`, not a version range.

| Type | Example shape |
| --- | --- |
| Functional | Structured-output mode emits trailing prose when the schema nests beyond a certain depth, breaking strict parsers |
| Functional | A model stops honoring a system-prompt constraint it previously respected, with no version change |
| Performance | p95 latency doubles above a given context length after a provider-side change |
| Compatibility | Tokenizer revision changes token counts, silently breaking budget and cost assumptions |
| Breaking change | A floating alias (`-latest`) repoints to a new snapshot with different refusal behavior |
| Deprecation | Announced model retirement and endpoint sunset date |
| Security | Tool-use mode susceptible to instruction injection from retrieved content |

### Operating systems

| Type | Example shape |
| --- | --- |
| Security | Local privilege escalation in a kernel point release ✱ (Dirty COW class) |
| Functional | Filesystem operation regression introduced in a patch release |
| Performance | Scheduler change regresses throughput for a workload class |
| Compatibility | glibc bump breaks binaries built against an older base image ✱ |
| Breaking change | cgroups v1 removal breaks container runtimes pinned to it ✱ |
| Deprecation | Distribution EOL date ends security backports |
| Build | Toolchain change breaks native module compilation on a new base image |

### Databases

| Type | Example shape |
| --- | --- |
| Functional | Query planner regression returns wrong results for one query shape after a minor upgrade |
| Performance | An index stops being selected post-upgrade; unrelated query latency collapses |
| Performance | Compaction stalls under sustained write load at a particular document size |
| Compatibility | Driver and server version skew silently degrades a feature rather than erroring |
| Breaking change | A configuration default changes across a major version — same config file, different behavior |
| Deprecation | A removed configuration option becomes a startup failure |
| Security | Authentication bypass under a specific auth configuration |

### Messaging queues

| Type | Example shape |
| --- | --- |
| Functional | Message loss under a specific broker failover sequence |
| Functional | Duplicate delivery when a consumer rebalances mid-commit |
| Performance | Rebalance storms as consumer group size crosses a threshold |
| Compatibility | Broker/client version skew disables exactly-once semantics without an error |
| Breaking change | Protocol change requiring a coordinated broker-then-client upgrade order |
| Deprecation | ZooKeeper-mode removal forcing a migration ✱ |

### SaaS platforms

No versions at all. Behavior changes on a **date**, which is why `TimeWindow` applicability
exists. This domain is also the only one carrying live outage records (FR-2).

| Type | Example shape |
| --- | --- |
| Functional | Webhook retry semantics change; previously-idempotent handlers start double-processing |
| Functional | An error code is repurposed, so client branching on it takes the wrong path |
| Compatibility | A response field is removed without a version bump |
| Breaking change | API version sunset on an announced date |
| Deprecation | Announced end-of-life for an endpoint or auth scheme |
| Performance | Rate limit reduced without notice |
| Security | Token scope semantics narrowed, breaking previously-working calls |
| *Outage* | Ongoing incident with current status (FR-2) — a distinct record class, time-bounded |

### Languages and runtimes

| Type | Example shape |
| --- | --- |
| Breaking change | Python 2 → 3 string/bytes semantics ✱ |
| Breaking change | Java 17 removes `SecurityManager`, breaking sandboxed frameworks ✱ |
| Compatibility | ABI break invalidates compiled extensions |
| Performance | GC or JIT regression in a specific release for a specific allocation pattern |
| Functional | Standard-library behavior change in an edge case (locale, timezone, float formatting) |
| Deprecation | API marked for removal with a stated release |
| Build | Toolchain version incompatible with a widely-used build plugin |

### Repos and libraries

| Type | Example shape |
| --- | --- |
| Security | Supply-chain compromise — a malicious version published under a trusted name ✱ |
| Deprecation | Package unpublished or repository archived, breaking installs ✱ (left-pad class) |
| Breaking change | A semver-violating minor release removes a public API |
| Functional | Regression introduced in a patch release and fixed three releases later |
| Performance | Memory leak introduced in a specific version under long-running processes |
| Compatibility | Peer-dependency conflict between two widely-used libraries |
| Build | Native module fails to compile against a new runtime version |

---

## 4. What is *not* covered

Naming the boundary is as useful as naming the contents.

| Not covered | Why |
| --- | --- |
| **Whether your code does what your business wants** | Requirements conformance is not a bug class. BugMine has no access to intent. |
| **Style, formatting, idiom** | Linters own this and do it better. |
| **Bugs in software absent from the catalog** | Per FR-39 this is reported as *not covered*, which is a coverage gap — not a finding. |
| **Zero-days not yet public** | The crawler can only know what has been published. Evals ([`discovery.md`](discovery.md)) partially address this — the only origin that can find something first. |
| **Bugs whose reproduction depends on private customer data** | Unreproducible outside the tenant, therefore uncorroborable and never promoted to the shared catalog. |

Own-code analysis (FR-36) reports **functional and performance defects** in the user's own code —
concurrency errors, resource leaks, error-handling gaps. It does not judge design quality, and
its findings are a separate evidence class from catalog matches (FR-37).

---

## 5. Open

| # | Question |
| --- | --- |
| **T1** | Is *outage* an eighth bug type, or a separate record class? It behaves differently — inherently time-bounded, resolves rather than gets fixed, and has no version. Modelled as distinct in §3; not yet settled. |
| **T2** | Do the seven types need severity independent of the ranker's per-stack scoring (FR-33)? A generic severity is what causes the alert fatigue BugMine exists to fix, so the bar for adding one is high. |
| **T3** | Are subject domains extensible? Adding "cloud services" or "CI systems" is plausible and the schema allows it, but each new domain may need its own applicability variant. |
