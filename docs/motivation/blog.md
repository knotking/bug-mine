# BugMine

**Your scanner is wrong 92% of the time. That's not the problem.**

*A single document covering why BugMine should exist, what it is, and how it is designed. Every
statistic is sourced at the end. Every design decision here is recorded in more detail elsewhere
in `docs/`; this is the union of all of it in one place.*

---

# Part I — The Problem

## Ninety-two percent

Earlier this year a group of researchers did something tedious and useful. They took 2,414
open-source repositories, generated high-fidelity software bills of materials for every one —
using lock files and strong package managers, so nobody could blame the input — and ran
vulnerability scanners across the lot.

Ninety-two percent of what came back was wrong.

Not wrong in the sense of a badly tuned threshold. Wrong in the specific sense the authors
identified: the flagged vulnerability sat in code the project never reached. The dependency was
there. The defect was real. The path from the application to the broken function did not exist.

If you have ever inherited a security backlog and worked through it with a growing suspicion that
almost none of it mattered, that suspicion was calibrated.

### It is not a tuning problem

It is tempting to read 92% as a quality issue that better engineering grinds down. It isn't,
because the tools answer a different question from the one you have.

A scanner asks: *does your dependency graph contain a package with a known defect?* You want to
know: *am I affected?* Those questions have different answers 92% of the time, and no amount of
threshold-tweaking closes a gap that comes from the shape of the question.

The effect is worse than wasted afternoons. When most alerts are noise, people stop reading
alerts. The 8% that were real get triaged with the same weary skepticism as everything else — a
high false-positive rate does not merely fail to help, it degrades the response to the findings
that matter. You have built an expensive machine for teaching your engineers to ignore warnings.

There is a fix, and it is genuinely good: reachability analysis, tracing whether the vulnerable
function is called from anywhere in your code. The same study measured it. Function call analysis
pruned **61.9%** of the false alarms.

Sixty-one point nine. Not a hundred. About four in ten false positives survive it, and anyone
telling you reachability eliminates alert noise is selling past the evidence.

## The actual problem

Suppose you fixed all of it. Suppose reachability reached 100% and every security alert you
received was real, exploitable, and yours.

You would still be missing most of what breaks your software.

Security defects are a category. They are not the category. Things that take a working system and
stop it working include: a library changing a default, a function quietly returning something
else, a deprecation becoming a removal, a performance cliff at a scale you just reached, a build
failing on a new base image. Almost none of that gets a CVE.

And it is not rare:

| Finding | Figure |
| --- | --- |
| Maven packages that have violated semantic versioning at least once | **67%** |
| Dependency updates carrying a client-impacting breaking change | **11.58%** |
| Those breaking changes arriving in a **non-major** version | **41.58%** |
| Leading cause of breaking changes | **Transitive** dependencies |

Non-major. The upgrades you automated. The ones your bot opens and you approve on a Friday because
the version number said it was safe.

Semantic versioning is the contract underneath every dependency tool you own. Two thirds of
packages have broken it, and four in ten breaking changes land precisely where the contract
promised there would be none.

## The number that ties it together

**70% of vulnerable dependencies require a minor or major version update that either potentially
or actually breaks source code.**

Your scanner produces a ticket. The ticket says upgrade. Seven times in ten, taking that advice
means absorbing a breaking change — and the tool that created the ticket will not tell you which
one, where it lands, or whether it touches anything you use.

The remediation advice and the cost of following it live in two different universes, and only one
has a product category. This is why security backlogs rot. Not because engineers don't care about
security, but because *"upgrade this"* is an unpriced instruction, and unpriced work goes to the
bottom of the list.

## AI is adding a new defect class

Package hallucination — models confidently importing packages that don't exist — is now measured
rather than anecdotal. Across a 576,000-sample study, **19.7%** of AI-suggested packages did not
exist: 21.7% for open-source models, 5.2% for commercial ones, above 33% for the worst families.

The finding that turns a quality issue into a supply-chain attack: when identical prompts were
re-run ten times, **43% of hallucinated names reappeared every time.** Stable hallucinations are
predictable, and predictable names are registrable by an attacker. In early 2026 researchers
tracked one hallucinated npm name from 47 AI-generated agent skills in a single commit through
forks into **over 230 repositories** before anyone noticed.

## Some of your dependencies stopped having versions

Every supply-chain tool ever built rests on one assumption: **dependencies are versioned, and
change is a deliberate act.** You pin a version. You read a diff. You upgrade when you choose to.

Hosted language models do not work this way. Providers update weights, safety tuning, and serving
infrastructure without changing the endpoint or the identifier. Your dependency changes underneath
you and nothing records that it happened.

The measurements are not subtle:

- GPT-4's direct code-execution success rate fell **from 52% to 10% over three months, with no
  version change.**
- Anthropic's August 2025 incident involved routing errors affecting **up to 16% of requests**,
  plus output corruption injecting non-ASCII characters.
- Drift concentrates in **structured output and format compliance** — exactly the
  machine-consumable behaviors production systems are built on.

And it is close to undetectable by normal means. As one practitioner put it: drift rarely
announces itself, because the model keeps responding, users keep getting answers, and *dashboards
stay reassuringly green.*

There is no bug tracker to subscribe to. No changelog to diff. No version to pin. **The only
signal a silent model change generates is a test that used to pass and now doesn't** — which means
the only way to know is to have been measuring beforehand.

Fifty-seven percent of surveyed teams now run agents in production. A growing share of the
critical path has quietly exited the versioned world, and the tooling has not noticed.

## What it costs

| Measure | Figure |
| --- | --- |
| Median hourly IT downtime cost, mid-size and large enterprise | **>$300,000/hr** |
| Large enterprises reporting $1M–$5M+ per hour | **41%** |
| Organizations whose last impactful outage cost >$1M | **1 in 5** |
| CrowdStrike outage cost to the Fortune 500, July 2024 | **$5.4B** in days |

The trend matters as much as the level. Per-site outage frequency has fallen for **five
consecutive years** while per-incident cost rises. Failures are rarer and each hurts more — which
shifts value toward prevention and away from response.

---

# Part II — Where This Goes

Every trend pushes the same way: **the volume and variety of things that break grows, while the
tooling stays CVE-shaped.**

**Code volume decouples from review capacity.** More generated code means more dependencies pulled
in by someone who did not evaluate them. The 92% is a *rate*; the absolute alert volume it
produces scales with the graph.

**LLMs become load-bearing dependencies with no versioning discipline.** As agents move from
experiment to production, silent model drift graduates from a quality annoyance to an incident
class with its own postmortems. Existing tools watch *your application's* outputs, one customer at
a time. Nobody maintains shared, cross-customer knowledge of which model changed and when.

**Agents add failure modes nothing catalogs** — tool-calling errors, non-termination, error
recovery failures, multi-step task collapse. New enough that a shared vocabulary barely exists.

**Rarer, costlier failures favor prevention.** When failures were frequent and cheap, fast
response dominated. When they are rare and expensive, preventing one is worth more than responding
to several. This also makes the sale *harder* — prevented incidents are invisible, and a product
that stops things happening has a permanent attribution problem.

**Regulation pulls the other way.** SBOM mandates ask what you *contain*, not what affects you.
Compliance pressure drives demand for the 92%-false-positive behavior, because an inventory is
what gets audited. Worth knowing before building a compliance story on a precision claim.

## What would falsify all this

- **Reachability becomes table stakes and free.** Endor Labs, Snyk, and Semgrep already ship it.
- **Model providers adopt real versioning.** Immutable versioned snapshots with changelogs would
  shrink drift to something crawlable.
- **Teams keep not paying for non-security bug intelligence.** The likeliest quiet failure: the
  problem is real, everyone agrees, and it never gets a budget line because nothing forces it.

---

# Part III — The Money

| Market | 2026 size | Forecast | CAGR |
| --- | --- | --- | --- |
| Software Composition Analysis | $0.41B – $1.5B *(sources differ ~4×)* | $1.8–5.7B by 2033–35 | 15.7–17.8% |
| Application Security Testing | ~$6.4B | $14.2B by 2034 | ~10.9% |
| DevSecOps | $11.07B | $26.05B by 2033 | 13.0% |
| **LLM observability** | **$2.69B** | **$9.26B by 2030** | **36.2%** |
| AI observability (broad) | $3.86B | $44.20B by 2035 | 31.1% |

**The uncomfortable finding: SCA is the smallest and slowest market here.** LLM observability is
2–6× larger and growing at more than twice the rate. Gartner expects LLM observability attached to
**50% of GenAI deployments by 2028, up from 15% in early 2026**. And **89% of teams running agents
already fund observability** — the budget line exists, so this is competing for established spend
rather than creating it.

At $300k/hour, a single prevented four-hour incident pays for a substantial annual contract. That
is the value argument — easy to make, hard to prove.

**Where BugMine sits:** there is no established category for "versioned catalog of non-security
defects across an entire stack". SCA does dependency vulnerabilities but not non-security classes,
LLM models, or SaaS behavior changes. LLM observability platforms measure *your* app but maintain
no shared catalog. Status aggregators cover only what is currently on fire. Upgrade bots bump
versions and say nothing about what breaks. Being between categories cuts both ways: no incumbent
to displace, and no budget line to land in.

---

# Part IV — What BugMine Is

A catalog of what actually breaks, with several ways in and several ways out.

## The shape

```
     THREE ORIGINS                ONE CATALOG              FOUR SURFACES
  ┌──────────────────┐                                  ┌──────────────────┐
  │ crawl            │                                  │ search/subscribe │
  │ scans            │ ───▶  Bug Service ──▶ Bug DB ──▶ │ scan             │
  │ evals            │       (sole writer)   (versions) │ advise           │
  └──────────────────┘                                  │ evals            │
                                                        └──────────────────┘
```

Everything narrows to one writer, through one store, out through one reader. Every constraint in
the system is enforced at one of those two waists.

## What counts as a bug

A record is classified on **two orthogonal axes** — the subject it is in, and the kind of defect
it is. Either alone is useless for retrieval.

| Subject ↓ / Type → | Security | Functional | Performance | Compat | Breaking | Deprecation | Build |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| LLM models | ● | ●● | ●● | ● | ●● | ● | — |
| Operating systems | ●● | ● | ● | ●● | ● | ● | ● |
| Databases | ● | ●● | ●● | ● | ● | ● | — |
| Messaging queues | ● | ●● | ●● | ●● | ● | ● | — |
| SaaS platforms | ● | ●● | ● | ●● | ●● | ●● | — |
| Languages / runtimes | ● | ● | ● | ●● | ●● | ●● | ●● |
| Repos / libraries | ●● | ●● | ● | ●● | ●● | ● | ●● |

`—` marks cells that are **structurally empty**, not merely unpopulated: a SaaS platform cannot
have a build failure because you never build it. The dense columns for BugMine's positioning are
**functional** and **breaking change** — the ones CVE-shaped tooling does not report at all.
Security is the crowded column and the one BugMine competes least on.

Concrete examples of the record shapes:

- **LLM model, breaking change** — a floating alias repoints to a new snapshot with different
  refusal behavior
- **LLM model, compatibility** — a tokenizer revision changes token counts, silently breaking cost
  and budget assumptions
- **Database, performance** — an index stops being selected after a minor upgrade; unrelated query
  latency collapses
- **Database, functional** — a planner regression returns wrong results for one query shape
- **Queue, functional** — message loss under a specific failover sequence
- **SaaS, functional** — webhook retry semantics change; previously idempotent handlers start
  double-processing
- **Language, breaking** — Java 17 removes `SecurityManager`, breaking sandboxed frameworks
- **Library, deprecation** — a package is unpublished, breaking installs

**What is not covered**, because the boundary is as useful as the contents: whether your code does
what your business wants (BugMine has no access to intent), style and formatting (linters own
this), and software absent from the catalog — which is reported as *"not covered"*, never as
silence. That last rule matters most early, when a thin catalog would otherwise return an empty
result that reads as a clean bill of health.

## How bugs get in

Three origins that differ in something more fundamental than mechanism — **what each is capable of
knowing.**

| Origin | Knows | Cannot know | Cost | Breadth |
| --- | --- | --- | --- | --- |
| **Crawled** | What someone published | Anything unpublished; structurally always behind | Low | Very broad |
| **Scan-derived** | What breaks real systems | Anything nobody in the customer base hit | Marginal | Grows with customers |
| **Eval-derived** | What a probe provokes | Anything no eval tests for | High | Narrow, deliberate |

**A catalog built only by crawling is a search engine over other people's bug reports** —
replicable by anyone with a crawler and permanently downstream. The other two origins produce
records that exist nowhere else.

Coverage varies by subject, and one domain has only one viable origin:

| Subject | Crawl | Scan | Eval |
| --- | :-: | :-: | :-: |
| Repos / libraries | ●● | ●● | ● |
| Languages | ●● | ● | ●● |
| Databases, queues | ●● | ●● | ●● |
| Operating systems | ●● | ● | ● |
| SaaS platforms | ● | ●● | ●● |
| **LLM models** | ✗ **no bug tracker exists** | ●● | ●●● **primary** |

**For LLM models, evals are not an enhancement — they are the only viable origin.** LLM models
were the first subject listed when this product was first written down. The catalog cannot serve
its own stated scope by crawling alone.

### The scan flywheel, and the rule that makes it safe

Analyzing a customer's repository produces knowledge about third-party software, and that
knowledge belongs in the catalog where the next customer's scan can use it. Each scan makes the
catalog better, which makes every subsequent scan better.

One rule does all the work: **a scan-derived record is shareable if and only if its subject is
third-party software.** "langchain 0.1.2 mishandles X" is a fact about langchain and belongs to
everyone. "This service leaks connections under retry" is a fact about the customer and belongs to
them alone — never eligible, under any amount of corroboration.

Even then, sharing requires **corroboration**: independent observation across *k* unaffiliated
tenants, or a match to a public source. Two independent reasons, one mechanism:

- **Privacy.** A single-tenant observation can identify its source even after code is stripped. A
  record saying "fails when combined with these two rare components" may describe exactly one
  customer. Requiring *k* unaffiliated tenants makes it a statement about software rather than
  about anyone.
- **Quality.** A scan finding is a model's inference about code it read once. Promoted directly,
  one model error reaches every tenant — and arrives *wearing a citation*, which is the authority
  the whole provenance mechanism exists to confer. A hallucination laundered into evidence is
  worse than a hallucination.

**Accepted cost:** rare bugs never promote. A real defect hit by one customer stays invisible to
everyone else — and rare, hard-to-find bugs are exactly the ones most worth sharing. This is a
genuine loss, accepted because the alternative is an unbounded re-identification risk.

### Evals

Four classes: **LLM models**, **AI agents** (tool-calling, termination, error recovery, multi-step
completion), **software versions**, and **language runtimes**. They run on the same job substrate
as everything else, re-run automatically against new versions so regressions are detected rather
than reported, and carry enough provenance to be reproduced.

The design turns on one distinction: **eval results are measurements, not observations.** A
crawled record is a claim someone published. A scan finding is an inference. An eval result is a
measurement, and measurements have distributions.

So the store holds **runs**, not verdicts, and a catalog candidate is created from an *aggregate*
carrying a failure rate and a confidence interval. This is what makes a 4%-of-the-time defect
expressible at all — a naive "reproduces *n* times in a row" rule would reject a real and
expensive bug. Corroboration differs by origin because the evidence does:

| Origin | Corroborated by | Fails when |
| --- | --- | --- |
| Crawled | The publishing source | The source is wrong, stale, or hostile |
| Scan-derived | *k* unaffiliated tenants | The bug is rare, so it never reaches *k* |
| Eval-derived | Reproduction rate with confidence | The defect is non-deterministic — the common case for LLMs |

The **regression detector** is the point. A suite that passed at rate *r* last week and rate *r′*
today, with non-overlapping confidence intervals, is a detected behavioral change — and for hosted
models that comparison is the *only* signal such a change ever generates. Which means the moat is
temporal: **you can only detect that behavior changed if you were measuring before it changed.**
That compounds, and cannot be bought later.

---

# Part V — How It Is Built

## Three planes and one substrate

The system separates into **control** (what should run, when, and what happened), **ingestion**
(turning the outside world into versioned records), and **query** (answering users) — plus a job
substrate all of them share.

Two consequences do most of the work.

**Ingestion and query cannot share a service.** Crawling arrives in bulk bursts and can wait; a
user's search is interactive and cannot. Sharing means a large crawl degrades search for everyone
and removes the ability to scale them independently.

**Crawl jobs, scan jobs, advise jobs and eval jobs are one abstraction.** They all need a queue,
workers, and job state. Build the substrate once, generic over job type. Adding a capability means
adding a job type, never a parallel pipeline.

## Ingestion

`www → crawler worker → raw artifacts → extraction worker (LLM) → Bug Service → Bug DB → indexer →
search engine`

Three decisions in that chain are load-bearing:

**Raw artifacts are stored before extraction.** Fetching and interpreting are separate stages with
durable storage between them. When the extraction prompt or model improves, the whole corpus can
be re-extracted **without re-crawling the internet**. Fuse them — hang the model directly off the
crawler — and every model improvement becomes a full re-crawl.

**Content-hash dedup gates versioning.** If the fetched content is unchanged, the pipeline stops.
Without this, versioning creates a new version on every crawl of every source forever, and catalog
size becomes a function of crawl frequency rather than of how often software actually changes.

**The indexer is a named component fed by an outbox or change stream** — not an unspecified copy.
It has a staleness bound, because search results promise a version and timestamp.

## The advisor — an hourglass

Four input types — a declared stack, a design document, an existing repo, a free-text intention —
must produce one kind of output. Four pipelines would produce four behaviors that drift apart.

Instead: everything normalizes into one **Stack Profile** before anything downstream runs, and
nothing downstream knows where the input came from. Adding an input type is one adapter and zero
downstream change.

```
design doc ─┐
stack ──────┤
repo ───────┼──▶ Stack Profile ──▶ sufficiency ──▶ retrieve ──▶ reason ──▶ provenance ──▶ rank ──▶ report
free text ──┘        (IR)              gate                                    gate
```

**The sufficiency gate is a feature, not validation.** It must distinguish *"no known problems"*
from *"not enough information to say"*, and ask for the specific missing fact rather than issuing
a generic request for detail. An advisor that bluffs on thin input is worse than no advisor,
because its confident output is indistinguishable from its grounded output.

**The provenance gate is structural, not a prompt.** You cannot instruct a model into being
grounded. Instead: the reasoner receives *only* the retrieved records, its output schema
*requires* a record ID per finding, and any finding citing an ID outside the retrieved set fails a
set-membership check. Hallucinated citations do not need to be detected — they cannot pass. This
holds regardless of model, prompt, or context length, and it is the difference between a product
and a demo.

## The scanner — two engines in one pipeline

Catalog matching and own-code analysis look like one feature and are not:

| | Catalog engine | Own-code engine |
| --- | --- | --- |
| Answers | Which known third-party bugs reach this code? | What is defective in the code itself? |
| Evidence | A cited catalog record | The system's own judgment |
| Provenance gate applies | Yes | No — nothing to cite |
| Can produce catalog candidates | Yes | **Never** |

They share intake and reporting and share nothing in evidence model. **The separation is a privacy
control, not a presentation preference** — merging them would let un-citable output flow into the
candidate path.

Three things shape the design:

**The dependency graph is a first-class artifact.** Reachability, blast radius, and just-in-time
package resolution all require it, and nothing modelled it. It is also exactly the advisor's repo
intake, so it should be one component serving both rather than two that disagree about what a
project depends on.

**The sandbox cannot fetch.** The analysis stage feeds third-party code to a model, so it must
have no network egress. But scans need missing package data fetched. These contradict in one
component, so fetch and analysis are **separate jobs in separate trust zones** — the graph is
completed before the sandbox opens.

**Secret redaction sits at the sandbox exit.** Customer repos routinely contain credentials, and
once a secret is in a log line or an OTel span attribute it has escaped. Redaction is a gate every
output crosses, the same structural position as the provenance gate, and for the same reason.

## Reachability — the largest open fork

Reachability is not a refinement, it *is* the product claim. Four options:

- **Manifest-level only** — what Dependabot-class tools do. Reproduces the 92% problem and makes
  the central claim false. Rejected on those grounds, not on cost.
- **Static call-graph analysis** — accurate and explainable, but a compiler-grade artifact *per
  language*, degrading badly under dynamic dispatch and reflection in exactly the languages with
  the most dependency churn.
- **LLM-judged usage** — language-agnostic day one, handles dynamic patterns, but probabilistic on
  the claim the product is sold on, and cost scales with catalog size (a better catalog means more
  candidates means a bigger bill).
- **Hybrid** — narrow with cheap symbol/import analysis, judge the residue with a model.

**Proposed: the hybrid**, because the bulk of false positives fall to a coarse test — *the project
never references the affected surface at all* — which needs a per-language **parser**, not a
per-language **compiler**. Token cost then scales with genuine candidates rather than catalog size.

Failure direction is specified: where either stage is uncertain, the finding is **reported with
reduced confidence, never suppressed.** A false positive is visible and dismissible; a false
negative is a bug the user never hears about, with no feedback path that could ever surface it.

This is deliberately recorded as *Proposed* rather than *Accepted*. Full call-graph analysis
remains defensible if ecosystem coverage is deliberately narrow — that is a product-strategy
question about breadth versus depth.

## The promotion pipeline — a privacy control

Everywhere else in the system, a bug produces a wrong answer. Here it produces a **data leak**,
irreversibly, because you cannot un-show something.

`eligibility → identity resolution → corroboration → sanitization → verification → promote`

**Identity resolution is the hard part, and corroboration silently depends on it.** Promotion
requires *k* unaffiliated tenants *or a matching public source* — both are identity problems.
Without resolving that a changelog entry, a code symptom, and an eval assertion describe one
defect, cross-origin corroboration never fires at all and the flywheel produces nothing. A
conservative heuristic will under-cluster, which starves the flywheel but does not leak. **That is
the correct direction to fail in.**

## Feedback — measuring our own noise

A product whose differentiator is precision but which cannot measure its own precision has no way
to know whether it is delivering what it sells.

Findings carry a disposition, and two of the values must stay distinct:

- **`not applicable`** — the bug is real but does not reach this user. A **reachability** failure.
- **`wrong`** — the bug is not real. A **catalog accuracy** failure.

Collapsing them into "dismissed" destroys the most useful distinction available; they indict
entirely different parts of the system. Precision is then measured and reported *by subject, type,
and origin* — "we are more precise than CVE scanners" is either a number or it is marketing.

Suppression is scoped to the **dismissed state, not the finding ID**. Keying on identity would
recreate alert fatigue one dismissal at a time: a user dismisses something as not-applicable, the
code later changes so it *is* applicable, and the system stays silent because it was told to.

## Records have a lifecycle

Bugs get fixed, and records can be wrong. Six states: `candidate`, `active`, `fixed`, `disputed`,
`retracted`, `superseded`.

**A `fixed` record still matches affected versions.** Treating "fixed upstream" as "no longer
relevant" would stop reporting to everyone still on an affected version — which is most users,
since being on an old version is the normal condition. The fix is the remedy, and naming it makes
the finding strictly more useful.

**Retraction invalidates dependent findings and notifies affected tenants.** If we told a customer
they had a problem and it turns out we were wrong, they may have scheduled an upgrade or delayed a
release on the strength of it. A retraction that quietly stops future findings while leaving past
ones standing is not a correction; it is a cover-up with a database change.

## Metering is inline, not downstream

Cost overrun must fail the job, not produce a silent charge — which means the ceiling is checked
**inside the job loop**. By the time an aggregation pipeline sees the events, the money is spent.

The reason is not tidiness. Billing runs on token usage, and the workers are model-driven loops
over content the system does not control. A pathological repository or an injected page inducing a
long generation is one bad input away from an unrecoverable bill. **Anything that bills on a
quantity it cannot cap is unbounded by construction.**

---

# Part VI — What We Require Of It

Full non-functional requirements exist; six are load-bearing enough to state here.

**The query plane stays available while ingestion degrades.** BugMine reports on *other providers'*
outages, so users reach for it exactly when their infrastructure is having a bad day. A correlated
failure would make it useless at the only moment it matters.

**Bug version history may never be lost.** A re-crawl recovers a source's *current* state, never
its past states. Losing the catalog costs a re-crawl; losing the history costs the thing that
cannot be bought back.

**Per-tenant inference routing is a guarantee, not a setting.** A tenant pinned to self-hosted
inference has its code sent to no third-party model, ever, on any path — including retries and
fallbacks. A pinned tenant whose local model is down gets a **failed job**, because a silent
fallback would breach the commitment.

**Secrets in scanned code must never reach findings, reports, logs, or telemetry.** Crawled content
is public; customer code is not, and it routinely contains credentials.

**Alert on the absence of success, not on errors.** A dead crawler produces *no errors*. The
catalog keeps serving, dashboards stay green, and the data quietly ages. Error-rate alerting is
structurally blind to this.

**Self-hosting the catalog is continuous distribution, not a software licence.** The catalog *is*
the product, so a self-hosted deployment must keep receiving it — packaging, delta updates,
licensing, revocation. None of that exists in a SaaS-only world.

## Untrusted content is the security problem nobody expects

Two pipelines feed attacker-influenceable content directly to a model: extraction reads arbitrary
web pages, and scanning reads third-party code inside customer repos.

Indirect prompt injection here is not hypothetical — it is the normal operating condition. And
because records get promoted to a *shared* catalog, a successful injection propagates to other
tenants **wearing a citation**. The flywheel that makes the catalog valuable is also a distribution
channel for a successful attack.

The response is to engineer the **blast radius, not the probability** — nobody can bound the
probability:

1. **Source allowlist** — only vetted, configured sources. Shrinks the attack surface from "the
   web" to "sources we chose".
2. **Capability removal** — extraction and scan workers have no tools and no network egress beyond
   their model endpoint. Full control of the model's output still reaches nothing.
3. **Schema-constrained output**, validated before persistence. Most payloads become structurally
   inexpressible.
4. **Corroboration before sharing** — hard for an attacker controlling one page.
5. **Traceability and reversibility** — every record traces to its raw artifact and can be
   retracted with its findings invalidated. This is the control that assumes the other four failed.

---

# Part VII — What Is Still Open

Stated plainly, because a document that omits its own uncertainties is a pitch deck.

| | |
| --- | --- |
| **Reachability** | Hybrid vs full call-graph is proposed, not decided. Largest cost fork in the system. |
| **Bug identity across origins** | Unsolved. A changelog, a code symptom, and an eval assertion describe one defect in three vocabularies — and corroboration cannot fire without matching them. |
| **The value of *k*** | Too low leaks tenant information; too high starves the catalog exactly when it is thinnest. Probably cannot be one number. |
| **Probabilistic eval corroboration** | LLM defects reproduce at a *rate*. The statistical rule — minimum runs, confidence level, effect-size floor — is undecided. |
| **Cold start** | Every surface depends on the catalog. Crawling cannot cover LLMs, evals cost money per target, scans need customers. Early coverage is thin, and we report that honestly, which makes early demos weaker. |
| **Disclosure policy** | Once evals find real unpublished defects in commercial products, we are security researchers whether we intend to be or not. This needs answering *before* the first finding. |
| **The buyer** | "Engineering teams" is a segment, not a buyer. Platform, security, and individual developer imply three different products. |

## What would have to be true

1. **Teams will pay for non-security bug intelligence.** Nothing compels them to. Security scanning
   has compliance behind it; this does not.
2. **Reachability stays hard enough to matter.** If it commoditizes fully, only the bug taxonomy
   remains distinctive.
3. **Model providers do not adopt real versioning.** If they ship immutable versioned snapshots
   with changelogs, the eval moat shrinks to something crawlable.
4. **Cross-tenant corroboration produces volume early enough to matter** — otherwise the flywheel
   is a late-stage benefit being used to justify early-stage work.

---

## The one-paragraph version

Vulnerability scanners are wrong 92% of the time because they answer *"do you contain this"*
rather than *"does this reach you"*. The larger problem is the one they never mention: two thirds
of packages break their versioning contract, and 70% of security fixes require a change that
breaks your code — so the tool that files the ticket is silent about what it costs. Meanwhile a
whole dependency class has stopped being versioned at all, with hosted models degrading five-fold
inside three months while dashboards stay green. BugMine maintains a catalog of what actually
breaks — across seven subject domains and seven bug types, not just security — fed by crawling, by
what it observes in customer code, and by evals that find defects nobody has reported. It tells you
which of them reach you, what a proposed change will cost before you make it, and when a model you
depend on changed behavior underneath you. Every finding cites the record behind it, and its
precision is measured rather than asserted.

---

## Sources

- [A Reality Check on SBOM-based Vulnerability Management: An Empirical Study and A Path Forward](https://arxiv.org/abs/2511.20313) — CODASPY '26. 2,414 repositories; 92.0% false positive rate; 61.9% pruned by function call analysis.
- [Semantic Versioning versus Breaking Changes: A Study of the Maven Repository](https://www.researchgate.net/publication/276269673_Semantic_Versioning_versus_Breaking_Changes_A_Study_of_the_Maven_Repository) — 67% of packages violated semver at least once.
- [Automatically Fixing Dependency Breaking Changes](http://www0.cs.ucl.ac.uk/staff/j.krinke/publications/fse25.pdf) — 11.58% of updates carry client-impacting breaking changes; 41.58% in non-major versions.
- [Dependency Updates: Version Bumps Don't Keep Code Secure — Moderne](https://moderne.ai/blog/security-dependency-updates-unmasked) — 70% of vulnerable dependencies require breaking updates.
- [Test Before You Deploy: Governing Updates in the LLM Supply Chain](https://arxiv.org/html/2604.27789v1) — GPT-4 52%→10% with no version change; Anthropic August 2025 incident; drift by task category.
- [Slopsquatting: AI Code Hallucinations Fuel Supply Chain Risk — Cloud Security Alliance](https://labs.cloudsecurityalliance.org/wp-content/uploads/2026/04/CSA_research_note_slopsquatting-ai-supply-chain_20260419-csa-styled-1.pdf) — hallucination rates by model class; 43% reproduction rate.
- [Who Vets AI's Code? The Scale Challenge Facing Open Source Ingestion — BleepingComputer](https://www.bleepingcomputer.com/news/security/who-vets-ais-code-the-scale-challenge-facing-open-source-ingestion/) — react-codeshift propagation to 230+ repositories.
- [Catching Silent LLM Degradation — Traceloop](https://www.traceloop.com/blog/catching-silent-llm-degradation-how-an-llm-reliability-platform-addresses-model-and-data-drift) — "dashboards stay reassuringly green".
- [Cost of IT Downtime: $300K+/hr Median — OutageCost.com](https://outagecost.com/cost-of-it-downtime) — ITIC and Uptime Institute figures.
- [Top LLM Observability and Evaluation Platforms in 2026 — MarkTechPost](https://www.marktechpost.com/2026/08/09/top-llm-observability-and-evaluation-platforms-in-2026-langfuse-langsmith-braintrust-arize-and-more-compared/) — market size; Gartner 50%-by-2028; LangChain agent survey.
- [Large Language Model (LLM) Observability Platform Market Report 2026 — Research and Markets](https://www.researchandmarkets.com/reports/6215671/large-language-model-llm-observability)
- [DevSecOps Market Size, Trends & Forecast 2026–2033 — Coherent Market Insights](https://www.coherentmarketinsights.com/industry-reports/devsecops-market)
- [Software Composition Analysis Market Size, Trends & Forecast 2026–2033 — Coherent Market Insights](https://www.coherentmarketinsights.com/market-insight/software-composition-analysis-market-2078)
- [Application Security Testing (AST) Tools Market Size Overview, 2035 — Business Research Insights](https://www.businessresearchinsights.com/market-reports/application-security-testing-ast-tools-market-105941)
