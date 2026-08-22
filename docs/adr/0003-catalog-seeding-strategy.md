# ADR-0003 — Hand-seed a narrow catalog slice before building crawlers

**Status:** Accepted
**Date:** 2026-08-22
**Decided by:** Parag Agarwal (product direction), drafted in session

## Context

The advisor is being built before the catalog it reads. Under ADR-0002, an advisor with an empty
catalog correctly produces nothing — which is honest but unprovable: it is indistinguishable from
a broken advisor, and it validates nothing about whether grounded advice is any good.

Meanwhile the crawler and extraction pipeline (FR-3, FR-4) needs a target schema, and the record
shape in `docs/data-model/stack-profile.md` — particularly the four applicability variants — was
derived by reasoning about the domains rather than from real records.

## Options

**A. Build crawlers first.** Real data, at volume, in the natural build order. But it front-loads
the largest, slowest component before anything has validated that the output is useful, and the
extraction schema gets designed against guessed record shapes.

**B. Synthetic/generated seed data.** Fast and arbitrarily large. Fatal flaw: LLM-generated bug
records would be used to test an LLM-based advisor, so both sides share the same failure mode and
the test proves nothing. Generated data also conforms to whatever schema was imagined, which is
precisely the assumption under test.

**C. Hand-seed a narrow slice.** 15–20 components across all seven subject domains, with real,
cited bug records written by hand. Slow per record, tiny in volume, real.

## Decision

**Option C.** Hand-seed 15–20 components spanning all seven subject domains, each record carrying
a real citation, before building any crawler.

Breadth across domains matters more than depth within one. The seed set's job is to stress the
*schema*, and the schema's riskiest claim is §2 of the data model: that applicability is a union
of four variants rather than a version range. Only real records from a SaaS platform and an LLM
model can confirm that a `TimeWindow` and a `ModelRevision` are genuinely needed — twenty
database records would not surface it at all.

## Consequences

**Easier.** The advisor loop becomes end-to-end testable now. Extraction schema gets designed
against real records. The advisor cannot hide behind model knowledge, since a hand-seeded catalog
is small enough that every grounded finding is manually checkable against its source.

**Harder.** Seeding is manual and does not scale; it is throwaway effort in volume terms. Early
demos will cover very few components and must be framed accordingly.

**Accepted downsides.** A 20-component catalog will make the advisor look narrow, and there is a
real temptation to relax ADR-0002 to fill the gaps — which would destroy the property being
built. Related: with a thin catalog, "no findings" almost always means "not covered", which is
open question A3 and becomes urgent rather than theoretical under this decision.

**New obligation.** Seed records need the same provenance as crawled ones (`raw_artifact_id` or
an equivalent citation), so they are not a privileged second class the pipeline cannot later
reproduce. When crawlers arrive, seeded records must be verifiable against crawled ones for the
same component — that comparison is the first real test of extraction quality.
