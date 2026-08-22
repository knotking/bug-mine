# Your Scanner Is Wrong 92% of the Time. That's Not the Problem.

Earlier this year a group of researchers did something tedious and useful. They took 2,414
open-source repositories, generated high-fidelity software bills of materials for every one of
them — using lock files and strong package managers, so nobody could blame the input — and then
ran vulnerability scanners across the lot.

Ninety-two percent of what came back was wrong.

Not wrong in the sense of a badly tuned threshold. Wrong in the specific sense the authors
identified: the flagged vulnerability sat in code the project never reached. The dependency was
there. The defect was real. The path from the application to the broken function did not exist.

If you have ever inherited a security backlog and worked through it with a growing suspicion that
almost none of it mattered, that suspicion was calibrated. Roughly nine in ten of those tickets
were about code you never call.

## This is not a tuning problem

It is tempting to read 92% as a quality issue that better engineering will grind down. It isn't,
because the tools are answering a different question from the one you have.

A scanner asks: *does your dependency graph contain a package with a known defect?* You want to
know: *am I affected?* Those questions have different answers 92% of the time, and no amount of
threshold-tweaking closes a gap that comes from the shape of the question.

The effect is worse than wasted afternoons. When most alerts are noise, people stop reading
alerts. The 8% that were real get triaged with the same weary skepticism as everything else, which
means a high false-positive rate does not merely fail to help — it actively degrades the response
to the findings that matter. You have built an expensive machine for teaching your engineers to
ignore warnings.

There is a fix, and it is genuinely good: reachability analysis, which traces whether the
vulnerable function is called from anywhere in your code. The same study measured it. Function
call analysis pruned **61.9%** of the false alarms.

Sixty-one point nine. Not a hundred. About four in ten false positives survive it, and anyone
telling you reachability eliminates alert noise is selling past the evidence. It is a large,
measurable improvement to a problem it does not solve.

## Now the actual problem

Suppose you fixed all of it. Suppose reachability got to 100% and every security alert you
received was real, exploitable, and yours.

You would still be missing most of what breaks your software.

Security defects are a category. They are not the category. Things that take a working system and
stop it working include: a library changing a default, a function quietly returning something
else, a deprecation becoming a removal, a performance cliff at a scale you just reached, a build
failing on a new base image. Almost none of that gets a CVE. Almost none of it is in any database
you subscribe to.

And it is not rare. Research on the Maven ecosystem found that **67% of packages have violated
semantic versioning at least once** — meaning two thirds of the libraries you depend on have, at
some point, shipped a breaking change in a release that promised not to contain one. Roughly
**11.6% of dependency updates carry a breaking change that hits the client**, and — the number
that should worry you most — **41.6% of those arrive in a non-major version**.

Non-major. The upgrades you automated. The ones your bot opens and you approve on a Friday because
the version number said it was safe.

Semantic versioning is the contract underneath every dependency tool you own. Two thirds of
packages have broken it, and four in ten breaking changes land precisely where the contract
promised there would be none.

## The number that ties it together

Here is the finding I cannot stop thinking about.

**70% of vulnerable dependencies require a minor or major version update that either potentially
or actually breaks source code.**

Read that alongside everything above. Your scanner produces a ticket. The ticket says upgrade.
Seven times in ten, taking that advice means absorbing a breaking change — and the tool that
created the ticket will not tell you which one, where it lands, or whether it touches anything you
use.

The remediation advice and the cost of following it live in two different universes, and only one
of them has a product category. This is why security backlogs rot. Not because engineers don't
care about security, but because "upgrade this" is an unpriced instruction, and unpriced work goes
to the bottom of the list.

## Meanwhile, some of your dependencies stopped having versions

Every supply-chain tool ever built rests on one assumption: **dependencies are versioned, and
change is a deliberate act.** You pin a version. You read a diff. You upgrade when you choose to.

Hosted language models do not work this way. Providers update weights, safety tuning, and serving
infrastructure without changing the endpoint or the model identifier. Your dependency changes
underneath you and nothing anywhere records that it happened.

The measurements are not subtle. One study documented GPT-4's direct code-execution success rate
falling **from 52% to 10% over three months, with no version change**. A five-fold degradation in
a load-bearing capability, invisible to every versioning mechanism that exists. Anthropic's August
2025 incident involved routing errors affecting up to 16% of requests. Researchers examining
behavioral drift across model transitions found it concentrated in structured output and format
compliance — which is to say, in exactly the machine-consumable behaviors that production systems
are built on top of.

And it is close to undetectable by normal means. As one practitioner put it: drift rarely
announces itself, because the model keeps responding, users keep getting answers, and *dashboards
stay reassuringly green.*

There is no bug tracker to subscribe to. No changelog to diff. No version to pin. The only signal
a silent model change generates is a test that used to pass and now doesn't — which means the only
way to know is to have been measuring beforehand.

Fifty-seven percent of surveyed teams now run agents in production. A growing share of the
critical path has quietly exited the versioned world, and the tooling has not noticed.

## What a tool would have to do

Three things, and no existing product does all three.

**Know about the defects nobody files a CVE for.** Functional regressions, breaking changes,
deprecations, performance cliffs, build failures — across everything you run on, not just the
packages in your manifest. Your database, your queue, your OS, your SaaS providers, your language
runtime, your models.

**Know whether they reach you.** Not "you contain this version" but "you call this function, on
this path, under these conditions." This is the difference between 92% noise and something worth
reading — and it is worth being honest that it gets you a large fraction of the way, not all of
it.

**Know about things nobody has published.** Crawling changelogs and issue trackers is, by
definition, always behind. And for an entire class of dependency — the hosted models — there is
nothing to crawl at all. The only way to catalog those defects is to run evaluations continuously
and notice when the numbers move.

That third one has a property worth dwelling on: **you can only detect that behavior changed if
you were already measuring.** It is not something you can start doing retroactively when it
becomes urgent.

## What we're building

BugMine is a catalog of what actually breaks — bugs and live outages across your whole stack,
classified by what software they're in *and* what kind of defect they are, versioned over time so
you can see when something changed.

Three things feed it. We crawl, like everyone else, which is broad and cheap and permanently
behind. We learn from scanning real codebases, so knowledge about third-party software compounds
as more systems are examined — and never, under any circumstances, does anything about your own
code leave your tenant. And we run evaluations against models, agents, runtimes, and software
versions, which is the only way anyone finds out that a model quietly got worse at emitting valid
JSON.

On top of that catalog: scanning that tells you which known defects reach your code, and an
advisor that answers the question your security tooling refuses to — *what will this change cost
me* — before you make it.

Two commitments, because in a category defined by a 92% error rate they seem like the ones worth
making. Every finding cites the specific catalog record behind it; if we can't point at evidence,
we say so rather than dressing up a guess. And we measure our own precision and report it, broken
down by where the finding came from, because "we're more accurate" is either a number or it's
marketing.

We're early, and the catalog is thin in places. When we don't have coverage for something you
depend on, we'll tell you that too — rather than returning an empty result and letting you read it
as a clean bill of health.

---

## Sources

- [A Reality Check on SBOM-based Vulnerability Management: An Empirical Study and A Path Forward](https://arxiv.org/abs/2511.20313) — CODASPY '26. 2,414 repositories; 92.0% false positive rate; 61.9% pruned by function call analysis.
- [Semantic Versioning versus Breaking Changes: A Study of the Maven Repository](https://www.researchgate.net/publication/276269673_Semantic_Versioning_versus_Breaking_Changes_A_Study_of_the_Maven_Repository) — 67% of packages violated semver at least once.
- [Automatically Fixing Dependency Breaking Changes](http://www0.cs.ucl.ac.uk/staff/j.krinke/publications/fse25.pdf) — 11.58% of updates carry client-impacting breaking changes; 41.58% arrive in non-major versions.
- [Dependency Updates: Version Bumps Don't Keep Code Secure — Moderne](https://moderne.ai/blog/security-dependency-updates-unmasked) — 70% of vulnerable dependencies require breaking updates.
- [Test Before You Deploy: Governing Updates in the LLM Supply Chain](https://arxiv.org/html/2604.27789v1) — GPT-4 52%→10% with no version change; Anthropic August 2025 incident; drift by task category.
- [Catching Silent LLM Degradation — Traceloop](https://www.traceloop.com/blog/catching-silent-llm-degradation-how-an-llm-reliability-platform-addresses-model-and-data-drift) — "dashboards stay reassuringly green".
- [Top LLM Observability and Evaluation Platforms in 2026 — MarkTechPost](https://www.marktechpost.com/2026/08/09/top-llm-observability-and-evaluation-platforms-in-2026-langfuse-langsmith-braintrust-arize-and-more-compared/) — LangChain survey: 57% of respondents run agents in production.
