# An empty report and an unexamined dependency look exactly the same

There is a specific moment that made me want to build BugMine.

You run a dependency scanner on a service you own. It returns clean. You ship. Three weeks
later something breaks in a way that turns out to have been documented — in a changelog entry,
in a release note, in a GitHub issue someone closed as "working as intended." The information
existed. Nothing you ran was looking for it.

The scanner was not wrong, exactly. It was answering a much narrower question than the one you
thought you had asked. You asked *is this safe to ship*. It answered *does any package here
appear in a CVE database*. Those are not the same question, and the gap between them is where
most production incidents actually live.

## The two failures, and they pull in opposite directions

Dependency scanning fails in two directions at once, which is unusual and is why the problem
has been hard to shift.

**It reports too much.** Measured across 2,414 repositories, the false-positive rate for
dependency scanners is **92%**. Not 9%. Ninety-two. The reason is structural rather than
sloppy: a scanner reports that a package containing a known defect is present in your
lockfile. It does not check whether your code ever calls the affected function. Most of the
time it does not — reachability analysis alone removes **61.9%** of those alarms.

A 92% error rate does not produce cautious engineers. It produces engineers who close the tab.
Every alert after the first few dozen is training data for ignoring alerts, and the one that
matters arrives into a habit of dismissal.

**It reports too little.** Everything in that category is built around the CVE, and security is
a minority of what breaks. A deprecation that silently changes behaviour has no advisory ID. A
performance regression in a patch release has no advisory ID. A breaking change buried in a
changelog has no advisory ID. None of them are security problems and all of them will ruin your
week.

Our own catalog is the clearest evidence I have for this. Of **24,363 records**, **83% have no
CVE at all**. That is not a gap the incumbents have not gotten to yet. It is a gap their data
model cannot represent.

## And the ground is moving

Every dependency tool rests on one assumption: dependencies are versioned, and change is an
explicit act. You pin, you review a diff, you upgrade deliberately.

That assumption is failing, and not slowly.

A hosted model is a dependency that ships no release notes and cannot be pinned. Providers
update weights, safety tuning and serving infrastructure without changing the endpoint or the
identifier. GPT-4's direct code-execution success rate went from **52% to 10% over three months
in 2023 with no version change**. There was no artifact for a lockfile to hold and no diff for
a reviewer to read. The dependency changed underneath everyone consuming it, and nothing
recorded that it had.

Then there is what the models write. Across a 576,000-sample study, **19.7% of AI-suggested
packages do not exist**. The detail that turns that from a quality problem into a supply-chain
attack: re-running identical prompts ten times, **43% of hallucinated package names reappeared
every single time**. Stable hallucinations are predictable, and predictable names are
registrable by somebody else.

## Why this gets worse rather than better

Here is the part I think is underrated, and I want to be careful to label it as an argument
rather than a measurement.

The measured inputs: Gartner puts enterprise engineers using AI code assistants at **14% in
2024, projected to 90% by 2028**. More than a quarter of new code at Google was already
model-generated as of late 2024.

The argument I draw from that: every tool bounding this problem today is bounded by a person.
A reviewer reading a diff. An engineer deciding whether an advisory applies. Generation is no
longer bounded by a person. So the quantity of code needing verification grows on one curve
while the capacity to verify it grows on a much flatter one, and those curves separate as a
function of how much code gets written — which is precisely the quantity being decoupled from
hiring.

For scale: poor software quality costs **$2.41 trillion a year in the US alone**, of which
**$1.52 trillion is accumulated technical debt** — defects already shipped and not yet paid
for. Developers spend **42% of the week** on maintenance and debugging rather than building.
Against that, application-security and composition testing together are usually put somewhere
around $10–15 billion a year, and the estimates vary enough that the range matters more than
any single figure. Most of that spend chases the security minority.

The distance between what the defects cost and what is spent finding them is the actual
opportunity. It is not a tooling-category-share argument.

## What BugMine does about it

Four stages. The third is the one everyone skips.

**Discover — four origins.** Crawl reads release notes and changelogs from 313 sources; it has
network access and no model, because those pages are written by whoever controls them. OSV
contributes advisories whose affected ranges are already declared, so direction gets read
rather than guessed. Scans feed back what customers actually hit. Evals — more on those below
— are the only origin that goes looking rather than reading.

**Catalog — one record per defect.** Deduplicated by defect, not by release, so one changelog
entry described in three places stays one record. Every record carries a *direction*:
introduced or fixed. That field sounds trivial and is not — "affected by 1.3" and "fixed in
1.3" are opposite facts that read almost identically in prose, and I got that inversion wrong
four separate times while building the extractor. Applicability is a tagged union rather than a
version range, because libraries have versions, phones have build numbers, and a hosted model
has neither.

**Reach — the stage others skip.** Your lockfile resolves locally and your imports and call
sites are parsed for Python, JavaScript, JVM and Go, following aliases and re-exports. A defect
in a package you never call is suppressed and counted, not reported.

The asymmetry here is deliberate and worth stating: suppression requires a *confirmed*
negative. Anything the parser cannot settle stays in the report at lower confidence. Suppressing
a real defect is unacceptable; a false positive is dismissible. Those two failures are not
equally bad and the system should not treat them as though they were.

**Cite — and name what is missing.** Every finding names the catalog record and revision
grounding it. A finding without one is a bug in us, not a judgement call.

And components the catalog has never seen are listed as *not covered* rather than omitted. This
is the piece I care most about, and it is the title of this post. If you scan a repository and
we hold nothing about half its dependencies, a report that simply omits them is indistinguishable
from a clean bill of health. One of those states is good news. The other is us not knowing. A
system that renders them identically is lying by layout.

## What one real scan looks like

Numbers from claims are worth very little in this category, given it is defined by a 92% error
rate. So: `python-poetry/poetry`, scanned by the deployed system.

- **80** dependencies resolved from the lockfile
- **224** catalog records matched those dependencies
- **168 suppressed** — the code never calls them. 75%.
- **56 reported**, each citing its record
- **76 not covered** — we hold nothing about them, and the report says so

The 75% suppression rate is in range of the 61.9% published for reachability analysis, which is
mild corroboration that the narrowing is doing real work rather than dropping things it should
keep.

The 76 uncovered components are the honest weakness. A thin catalog is a real limitation. Naming
it in the output is the only defensible way to ship one.

## Evals: the origin that goes looking

Three of the four origins are downstream of somebody else's writing. If nobody published a
changelog entry, nothing arrives. Evals are how a defect enters the catalog when no such entry
will ever exist.

For hosted models this is the only mechanism that can work. There is no release note. There is
no version. Behaviour changes under a fixed endpoint name, so the only way to know a model
regressed is to have measured it before and to measure it again.

But this is not an LLM-only story, and framing it that way undersells it. The same
probe-and-compare method applies to anything whose behaviour can be exercised: a database's
handling of an edge-case query, an API's response to a malformed request, a runtime's behaviour
across a version bump. Anywhere behaviour can be tested, a defect can be *found* rather than
awaited.

The hard part is corroboration. Probabilistic systems fail intermittently, so "it failed twice
in a row" proves nothing at all. Our approach: a result is promoted when the Wilson confidence
interval on its failure rate clears the tolerated floor, and refuted when the interval falls
entirely below it. Otherwise it stays undetermined and says so. That means 0 failures in 20 runs
is *undetermined*, while 0 in 500 is *refuted* — the same observation at different sample sizes
supporting genuinely different conclusions.

**Status, plainly: that corroboration logic is built and tested. The scheduler that runs probes
on a cadence is not.** Evals are in the design and in the diagram. They are not filling the
catalog today. I would rather write that sentence than have you discover it.

## What argues against all of this

An argument that only presents one side is worth less than nothing, so:

**Reachability is being commoditised.** Endor Labs, Snyk and Semgrep all ship it and market it
on exactly this alert-fatigue argument. The 92% figure is a known problem under active attack by
well-funded incumbents. BugMine is not first and does not get to pretend otherwise.

**The raw material is free.** NVD, OSV and GitHub Advisory are public. Aggregation is not
defensible. What we add has to be the reachability, the non-security bug classes and original
discovery — not the collecting.

**Eval platforms already exist.** Langfuse, LangSmith, Braintrust and Arize are established.
The distinction I am drawing is real but narrow: they evaluate *your application*, one customer
at a time. None maintains a shared, versioned catalog of model behaviour. That is a genuine gap,
but it is a narrower one than "nobody does evals."

**And models may get good enough** that generated code carries a lower defect rate than human
code — plausible, and arguably already true for some classes. If so, volume stops implying
proportional risk and the argument above weakens considerably. It does not disappear: a correct
call into a dependency that changed underneath you is still broken, and no amount of generation
quality addresses that.

## Try it without talking to anyone

The catalog is searchable with no account, no trial, and no form. It is the same catalog a scan
queries.

Search `sqlalchemy`, or `kafka`, or whatever you actually depend on. If we hold nothing about
it, you will get told that in those words — which is the whole point.

---

*24,363 records · 263 components · 313 sources · 83% with no CVE. Figures measured 2026-08-24
and live on the site, because a number typed into a page drifts and a number read from the
database cannot.*
