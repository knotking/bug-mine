# What we can pull, and in what order

**Status:** Plan, not yet started
**Date:** 2026-08-24
**Relates to:** [`requirements/discovery.md`](../requirements/discovery.md) (three origins),
[ADR-0005](../adr/0005-untrusted-content-in-model-pipelines.md) (which worker may hold a model),
[`architecture/ingestion.md`](../architecture/ingestion.md)

---

## Where the catalog actually is

Measured from `/v1/public/stats` on 2026-08-24:

| | |
| --- | --- |
| Records | 24,540 |
| Components | 267 |
| Registered sources | 313 |
| Beyond security | 20,379 — **83%** |

By type: deprecation 9,945 · breaking_change 8,421 · security 4,161 · functional 1,913 ·
performance 100.

The 83% is the whole thesis holding up in production: a dependency scanner would hold the 4,161
and nothing else. Performance at 100 is the thin one, and it is thin because nothing we crawl
today is shaped like a performance regression report.

## The four paths, and why that is the right way to divide the work

What we can pull is not limited by what exists on the internet. It is limited by which of four
mechanisms can read it, and they differ by two orders of magnitude in cost.

| Path | Cost | Reads |
| --- | --- | --- |
| `worker/osv.py` | free, deterministic | OSV JSON — affected ranges declared, direction never inferred |
| `worker/structured.py` | free, deterministic | Feeds whose shape is known. Today: GitHub releases, recognised by content rather than by URL |
| `worker/extract.py` | tokens per artifact | Any prose. Schema-constrained output, no network egress (ADR-0005) |
| `evals/*` | inference bill, recurring | Model behaviour. Corroborated by failure rate with a Wilson interval, not by a boolean |

**Sequencing follows this table, not the subject domains.** Everything a deterministic path can
read should be read before anything the model path reads, because the deterministic paths are
free, exact, and already written.

## 1. OSV, in bulk — the largest single step available

OSV publishes per-ecosystem zips covering PyPI, npm, Maven, Go, crates, NuGet, RubyGems,
Packagist **and** the distribution trackers — Debian, Ubuntu, Alpine, Rocky. That last group is
how `operating_system` gets populated without writing a parser for it, and `apk`/`deb`/`rpm`
already map to that domain in `ECOSYSTEM_DOMAIN`.

The ingest code exists and handles the two things aggregators get wrong: withdrawal, and
direction stated rather than inferred. What is missing is the bulk fetch and a scheduled
refresh.

Expected effect: the security count moves from 4,161 to the order of the published advisory
corpus, at zero token cost. It also makes the beyond-security share *fall*, which is worth
predicting now so nobody reads it later as a regression.

## 2. Widen the GitHub releases list — the highest yield per unit of work

267 components against a parser that will read any repo's releases feed. The next ~1,500
components are a source-registration exercise and a rate-limit budget, not new code, and they
feed exactly the two types nobody else catalogs: deprecation and breaking_change.

The constraint is the sweep. It runs every 15 minutes over 313 sources; five times that many
needs the interval, the per-source `interval_minutes`, and the GitHub rate limit reconciled
deliberately rather than discovered.

## 3. Three more deterministic parsers, each small

Each is a function beside `looks_like_github_releases`, in the worker that holds no model:

- **GitHub issues and PRs** labelled `bug` / `regression`. Same API, different shape. Reaches
  defects that never appear in a release note because they are still open.
- **endoflife.date.** Structured JSON, dates rather than versions. Feeds `deprecation` directly
  and is the cleanest source of a `time_window` that is not a guess.
- **Distribution security trackers not carried by OSV**, where a kernel or vendor advisory has
  no OSV record.

## 4. The model path, for what genuinely needs prose

- **SaaS status pages and changelogs** — Stripe, Twilio, GitHub, AWS, Cloudflare. This is the
  `saas_platform` domain, currently one record. It is the domain where a behaviour change ships
  with no version and often no announcement, so it is both the hardest to crawl and the one
  competitors cannot cover at all.
- **Database and queue release notes written as prose** — Postgres, Kafka, Couchbase.
- **Performance regression reports.** The type sitting at 100 records. These are almost never
  structured, which is why the count is what it is.

## 5. LLM models — a publication layer, not a tracker

`discovery.md` marks the crawl origin for this domain ✗. No *vendor* publishes a defect tracker,
which is true and remains the important point. But nine third-party publishers do, and 20
crawlable records were transcribed from them by hand in one sitting — see
[`../data-model/llm-seed-slice.md`](../data-model/llm-seed-slice.md).

Crawlable, mostly through the model path: UK AISI, METR, CAISI, Apollo, Palisade, HiddenLayer,
the AI Incident Database. 0DIN's disclosure index is structured enough for a parser, though the
model name only appears on the detail page.

What this layer cannot be is timely or complete: tier-one evaluators publish after coordinated
disclosure, so it is weeks to months behind, and a technique is published against whichever
models the researcher happened to test. Evals remain the only origin that finds a model defect
first, and the only one that can populate `ModelRevision.effective_from` at all.

## 6. Scan-derived — gated on customers, not on engineering

Nothing to build ahead of a customer base large enough for *k* unaffiliated tenants (FR-44).
Planning as though this flywheel is available early would be a mistake.

---

## A defect this survey found, worth fixing before widening anything

**`ecosystem` is wrong on components registered from GitHub feeds.** In the live top twelve,
`kotlin`, `elixir`, `moby`, `grafana`, `containerd` and `aws-cdk` all carry `ecosystem = "pypi"`.

This is not cosmetic. `check` and `scan` derive the subject domain **from the ecosystem the
caller declares** (`ECOSYSTEM_DOMAIN`) and then retrieve on the pair, so:

- `kotlin` and `elixir` are stored as `language_runtime` under an ecosystem implying
  `repo_library`. Nothing can retrieve them. That is 986 records, extracted at cost, invisible.
- `moby`, `grafana` and `containerd` are Go modules stored as `pypi`. A Go project's manifest
  queries ecosystem `go` and matches nothing.

Both fail as an empty result, which reads as *nothing known about this component* rather than as
an error — so the volume of records hides the fact that they cannot be reached.

Two halves to the fix, and only one of them is code:

1. **Done in this change.** `add_source` now refuses a pair whose ecosystem contradicts the
   declared domain, and the deploy skill's `curl` example — the likely origin of the copied
   `"ecosystem":"pypi"` — now says what the field means. The guard cannot catch `moby`, because
   `go` and `pypi` both imply `repo_library`; it catches only self-contradiction.
2. **Not done.** Correcting the 313 registered sources and the components already written from
   them. That is a data fix against the deployed database, and it needs a decision on whether
   correcting a component's ecosystem re-keys its existing records or creates a second
   component beside the first.

Widening the source list before this is fixed multiplies the wrong ecosystem across five times
as many components.

## Order, with what actually gates each step

| | Step | Gated by |
| :-: | --- | --- |
| 1 | Fix the ecosystem data | A decision on re-keying versus re-creating components |
| 2 | OSV bulk ingest | Nothing — code exists, needs a bulk fetch and a schedule |
| 3 | Widen GitHub releases to ~1,500 | Sweep interval and GitHub rate limit, reconciled deliberately |
| 4 | Issues/PRs, endoflife.date parsers | Step 3's rate-limit budget |
| 5 | SaaS via the model path | Token budget — this is cost of goods, not R&D |
| 6 | LLM publishers and scheduled evals | Eval scheduler, which does not exist yet |

Steps 2 and 3 are the ones that change the numbers. Step 5 is the one that changes what the
product can claim, because no competitor holds it.
