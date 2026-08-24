# Documentation

The reasoning behind BugMine, in the order it is worth reading.

Start with [`motivation/`](motivation/) if you want to know why the system exists, or
[`adr/`](adr/) if you want the decisions that shaped it. Everything else is reference.

---

## Start here

| | |
| --- | --- |
| [**motivation/problem.md**](motivation/problem.md) | What current tooling actually does, with measurements — the 92% false-positive rate, package hallucination, and hosted models breaking the versioning assumption every dependency tool rests on |
| [**motivation/blog.md**](motivation/blog.md) | The whole argument in one piece, if you would rather read prose than a document tree |
| [**requirements/bugmine.md**](requirements/bugmine.md) | What the system must do — FR-1 to FR-86, each written to be testable |

## Decisions

Numbered, append-only, and the most useful thing here — each records what was considered and
rejected, which the code cannot tell you.

| | |
| --- | --- |
| [0001](adr/0001-advisor-input-normalization.md) | Normalising every intake type into one stack profile |
| [0002](adr/0002-grounding-and-provenance.md) | **Every finding cites a record**, enforced as a schema property rather than a prompt instruction |
| [0003](adr/0003-catalog-seeding-strategy.md) | Seeding a catalog that starts empty |
| [0004](adr/0004-scan-derived-catalog-entries.md) | Corroboration before one customer's observation becomes everyone's |
| [0005](adr/0005-untrusted-content-in-model-pipelines.md) | **The worker with network access has no model; the one with a model has no network** |
| [0006](adr/0006-reachability-analysis.md) | Narrow statically, judge with a model — the largest cost fork in the system |
| [0007](adr/0007-firebase-auth-and-api-gateway.md) | Holding no passwords, and reaching the app past an org policy |

## Reference

| Path | Contents |
| --- | --- |
| [`requirements/`](requirements/) | FR / NFR with stable IDs. [`discovery.md`](requirements/discovery.md) is the one to read: three origins, and why LLM models have only one |
| [`architecture/`](architecture/) | Components, boundaries, failure modes — one document per subsystem |
| [`data-model/`](data-model/) | [`stack-profile.md`](data-model/stack-profile.md): applicability as a tagged union, because SaaS and models have no semver |
| [`api/`](api/) | The contract, plus [`openapi.yaml`](api/openapi.yaml) |
| [`plan/`](plan/) | Implementation plans with technology choices and cost estimates |
| [`motivation/`](motivation/) | Problem, trajectory, market, solution — researched, sourced, and arguing both sides |

## Conventions

**IDs are stable.** `FR-n` and `NFR-n` are separate namespaces and never renumbered — the
architecture, API and tests reference them, and renumbering breaks those links silently.

**ADRs are append-only.** A decision that changes gets a new record superseding the old one
rather than an edit. The rejected alternative is the part worth keeping.

**Requirements are testable.** "Fast" and "secure" are not requirements. A number at a named
percentile under a named load is.

**Counter-arguments are included.** [`motivation/problem.md`](motivation/problem.md) ends with
what argues against its own case, and [`solution.md`](motivation/solution.md) names the
incumbents already shipping reachability. A document that only argues one side is worth less
than nothing, because a reader who spots the omission stops believing the rest.

## Where the docs and the code disagree

The code is further along than some documents suggest, and that gap is worth knowing about
before you trust a status line:

- Several plans in [`plan/`](plan/) describe work now built and deployed
- [ADR-0006](adr/0006-reachability-analysis.md) is still marked **Proposed**; stage one — static
  narrowing — is built for Python, JavaScript, Java/Kotlin and Go, and measured at 75%
  suppression. The model-judgment stage is not built
- [`requirements/discovery.md`](requirements/discovery.md) calls eval corroboration by
  consecutive runs the sharpest open problem. It is solved: corroboration is a failure rate with
  a Wilson confidence interval, decided on the lower bound
