# Python, Java and Go — version issues

**Status:** Plan, not started
**Date:** 2026-08-24
**Relates to:** [`ingestion-sources.md`](ingestion-sources.md),
[`../data-model/stack-profile.md`](../data-model/stack-profile.md) §2 (applicability),
[ADR-0005](../adr/0005-untrusted-content-in-model-pipelines.md)

---

## What is in the catalog today

Measured against the deployed API on 2026-08-24:

| Query | Records |
| --- | :-: |
| `cpython` | **0** |
| `openjdk` / `jdk` | **0** |
| Go runtime | **0** — the `go` hits are libraries with "go" in the name |

The `language_runtime` domain holds nine components: kotlin, elixir, scala, otp, rust, ruby,
php-src, julia, and one called `runtime` — which is a repository name, not a component. The
three most widely deployed runtimes in existence are absent. Seven of the nine also carry
`ecosystem = "pypi"`, which contradicts their domain and makes them unretrievable
([`ingestion-sources.md`](ingestion-sources.md)).

Node is in the catalog with 1,417 records — filed as `repo_library` under `npm`, so its
*runtime* breaking changes are recorded as library changes.

## Two different questions, currently conflated

"Package issues for their different versions" is two problems, and only one of them is a
catalog record.

**A — the runtime is the component.** *Python 3.12 removed `distutils`. JDK 21 removed the
Security Manager. Go 1.21 changed loop variable scoping.* The defect belongs to the runtime,
the affected versions are the runtime's own versions, and `VersionRange` already expresses it.
This is a straightforward gap in coverage: the sources exist and nothing has crawled them.

**B — the constraint is between two components.** *numpy 1.26.0 does not run on Python 3.13.*
Verified from PyPI just now: `numpy 1.26.0` declares `requires_python = "<3.13,>=3.9"`. The
defect is not in numpy and not in Python; it is in the pair. **The schema cannot express this
and the query cannot ask it**, which is the substance of this plan.

## Why B does not fit the current model

`applicability` is a tagged union over four variants, and every one of them describes a single
axis — the component's own versions, builds, dates or model id. A two-component constraint has
nowhere to go. Recording it anyway leaves two bad options: mint one record per (package
version × runtime version) pair, which explodes combinatorially and cannot be grouped because
`identity_key` already collides on a single CVE fixed on two branches; or write the runtime
bound into `description` as prose, where matching cannot see it.

The read path has the same hole. `Query` is:

```python
Query(subject_domain, ref, version=None, ecosystem=None)
```

There is no field for *which runtime the project runs on*, so even a record that could express
the bound could not be selected by it. And `inventory/` has only `python.py` and `npm.py` —
**no Go and no Java manifest parser at all**, and neither existing parser extracts a runtime
floor. Three layers have to change together, which is why this is a plan rather than a task.

### The proposal, in one shape

Keep the union for what it is good at, and add the second axis beside it rather than inside it:

```python
runtime_bound: {"runtime": "cpython", "min": "3.9", "max": "3.13", "max_exclusive": true} | None
```

Orthogonal to which applicability variant a record uses, so a record can carry a normal version
range *and* a runtime bound without multiplying the union from four variants to sixteen.
`Query` gains the project's runtime, read from the manifest.

Matching must return `UNCONFIRMED` when the project's runtime version is unknown — never `NO`.
That is the same failure direction ADR-0006 specifies for reachability, and it matters more
here, because most manifests do not state a runtime version at all.

### The part that should not be catalog records

The full package↔runtime constraint set is PyPI's `requires_python` for every release of every
project — several hundred thousand projects. Crawling that is re-hosting PyPI, and it goes
stale the moment it lands.

It should be a **resolver, not a crawl**: at scan time, look up the runtime constraint for
exactly the dependencies in the manifest. The data is authoritative at the registry, free, and
needed only for packages a customer actually has. The `package_pull` service account already
exists for fetching of this kind, and ADR-0005 governs where that fetch may run.

Only **A** — the runtime's own breaking changes — becomes catalog records.

## Sources, and what each costs

The three languages are not symmetric, and the asymmetry decides the order.

### Python — free and exact, both halves

| What | Where | Path |
| --- | --- | --- |
| Release cycles, EOL dates | `endoflife.date/api/python.json` — verified: `cycle`, `releaseDate`, `eol`, `latest`, `support` | deterministic |
| Removals and deprecations | "What's New in Python 3.x", *Deprecated* / *Removed* sections | model path — prose |
| Per-release changelog | `Misc/NEWS.d` in cpython, one structured entry per change | deterministic, parser needed |
| **Package↔runtime** | PyPI JSON API, `requires_python` per release — verified against numpy | resolver |

### Go — free and exact, and the best-designed of the three

| What | Where | Path |
| --- | --- | --- |
| Runtime behaviour changes | `doc/godebug.md` in golang/go — **every GODEBUG setting is a documented backwards-incompatible change, with the version that introduced it and its default per toolchain** | deterministic, parser needed |
| Release notes | `go.dev/doc/go1.N` | model path — prose |
| **Package↔runtime** | `proxy.golang.org/<module>/@v/<version>.mod`, the `go` directive — verified: `gin v1.10.0` → `go 1.20` | resolver, trivial to parse |

Go is the cheapest of the three by a wide margin. GODEBUG exists precisely to enumerate
compatibility breaks, which is the dataset this feature wants, already curated by the Go team.

### Java — the expensive one, and the reason is structural

| What | Where | Path |
| --- | --- | --- |
| Removed APIs | `docs.oracle.com/en/java/javase/<N>/migrate/removed-apis.html` — a per-release table | deterministic, parser needed |
| Deprecated for removal | `jdeprscan --release <N> -l --for-removal` — authoritative, from `ct.sym` inside the JDK | **toolchain-derived, not a feed** |
| Release cycles | `endoflife.date` (the `java.json` path 404s; needs the correct product slug) | deterministic |
| **Package↔runtime** | **Nothing declares it.** Verified: `spring-core-6.2.0.pom` states no JDK level, and Maven Central's search API returns only `id, g, a, latestVersion, p, ec, repositoryId, text, timestamp, versionCount` | must read the class-file major version out of the jar |

Two consequences worth stating before anyone estimates this. The deprecated-for-removal list
comes from *running a tool against each JDK release*, which is closer to the eval origin than
the crawl origin and needs somewhere to run JDKs. And the runtime floor of a Maven artifact is
only knowable by downloading the artifact and reading bytes 6–7 of a class file — cheap per
artifact, but a fetch-and-unzip rather than a metadata lookup, and it is the only one of the
three languages where that is true.

## Order

| | Step | Why here |
| :-: | --- | --- |
| 1 | Register cpython, go and openjdk as `language_runtime` components with **no ecosystem** | Closes a total coverage gap; the ecosystem-contradiction guard already prevents repeating the kotlin mistake |
| 2 | Parse `doc/godebug.md` | The single highest-quality source of the three. Deterministic, curated, one parser |
| 3 | Oracle removed-APIs tables + Python *What's New* | Table is deterministic; Python's is prose and costs tokens |
| 4 | `runtime_bound` on records, `runtime` on `Query`, `UNCONFIRMED` when unknown | Schema and read path change together or not at all |
| 5 | Go and Java manifest parsers in `inventory/`, plus runtime floor extraction in the Python one | Nothing can supply the query field until a manifest is read |
| 6 | Registry resolver for package↔runtime constraints | Needs 4 and 5 in place to be usable |
| 7 | `jdeprscan` per JDK release | Needs a JDK execution environment; lowest yield per unit of setup |

Steps 1–3 are catalog coverage and need no schema change. Step 4 is the one-way door.

## Open decisions

| # | Decision |
| --- | --- |
| **R1** | Whether `runtime_bound` sits beside `applicability` or becomes a fifth variant. Beside it is proposed; a fifth variant forces a record to choose between expressing its own range and expressing the pair |
| **R2** | Whether the package↔runtime resolver caches into the catalog at all, or stays a live lookup. Caching makes it stale; not caching puts a registry call on the scan path |
| **R3** | What a runtime-bounded finding says when the manifest declares no runtime version — which is the common case for Python and Java, and never for Go |
| **R4** | Whether Node moves from `repo_library` to `language_runtime`, and what happens to the 1,417 records already filed under the current classification |
| **R5** | Whether reading a JDK floor out of a jar belongs in the scanner or in a `package_pull` worker, given ADR-0005's split |

R3 is the one that decides whether this feature is useful or noisy. A bound that degrades to
"cannot tell" on most real projects produces findings nobody can act on.
