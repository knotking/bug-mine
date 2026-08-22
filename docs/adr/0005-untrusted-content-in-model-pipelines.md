# ADR-0005 — Treat crawled content and third-party code as untrusted input to models

**Status:** Accepted
**Date:** 2026-08-22
**Decided by:** Parag Agarwal (product direction), drafted in session
**Related:** [ADR-0002](0002-grounding-and-provenance.md), [ADR-0004](0004-scan-derived-catalog-entries.md), NFR-41 – NFR-46

## Context

Two BugMine pipelines feed attacker-influenceable content directly to a language model.

**Extraction.** The extraction worker fetches arbitrary web pages — issue trackers, changelogs,
forums, vendor status pages — and asks a model to turn them into structured bug records. Page
content is authored by whoever controls the page.

**Scanning.** A scanned repository contains third-party code, vendored dependencies, README files
and comments. The customer is trusted; the third-party content inside their repo is not.

Indirect prompt injection is therefore not hypothetical here — it is the normal operating
condition. And ADR-0004 makes the consequence severe rather than local: a poisoned extraction
writes a record into a **shared** catalog, which then grounds findings for other tenants. Under
ADR-0002 that record arrives carrying a citation, wearing precisely the authority the provenance
mechanism exists to confer. **The flywheel that makes the catalog valuable is also a distribution
channel for a successful injection.**

The realistic attack needs no sophistication: publish a page about a popular library containing
text that induces the extractor to emit a fabricated critical bug in a competitor's package, or a
"safe version" pointing at an attacker-controlled release.

## Options

**A. Prompt-level defences.** Instruct the model to ignore instructions in content; wrap content
in delimiters. Cheap and standard. Also well-known to be bypassable, and it degrades invisibly —
there is no point at which the system can assert it held.

**B. Detection.** Classify content for injection attempts before extraction. Catches known
patterns, misses novel ones, and adds a model call per artifact. Useful as a signal, insufficient
as a control.

**C. Structural containment.** Assume injection succeeds, and design so that a successful
injection cannot do anything worth doing: constrain what the model can emit, remove the worker's
capabilities, restrict which sources are read at all, and make every record traceable and
reversible.

## Decision

**Option C as the primary control**, with B retained as a signal and A as hygiene rather than as
a defence. Five specific controls, recorded as NFR-41 – NFR-46:

1. **Source allowlist.** Crawling reads only explicitly configured, vetted sources (FR-3 already
   makes sources configuration). No open-ended crawling. This is the strongest single control,
   because it shrinks the attack surface from "the web" to "sources we chose".
2. **Capability removal.** Extraction and scan workers have no tool access and no network egress
   beyond their model endpoint. An injection that fully controls the model's output still cannot
   reach anything.
3. **Schema-constrained output with validation.** The model emits a fixed schema, and every field
   is validated before persistence. An injected instruction cannot produce a field that does not
   exist, a component outside the registry, or a version outside a parseable form.
4. **Corroboration before sharing.** ADR-0004 already requires it for scan-derived records; this
   extends the reasoning to crawled ones — a record no other origin or source corroborates is
   weaker evidence, and cross-origin agreement is hard for an attacker controlling one page.
5. **Traceability and reversibility.** Every record traces to its raw artifact (NFR-31), and any
   record can be retracted with its findings invalidated
   ([`../requirements/record-lifecycle.md`](../requirements/record-lifecycle.md) FR-67, FR-68).
   Containment fails eventually; being able to find and undo every affected record is the control
   that assumes it did.

The organizing principle is that **the blast radius of a successful injection, not its
probability, is what gets engineered.** Probability is not something anyone can currently bound.

## Consequences

**Easier.** A successful injection is contained to a fabricated record from one allowlisted
source, which is traceable, corroborable, and retractable. No injection can exfiltrate data or
reach another system, because the workers cannot reach anything. Schema constraints make most
payloads structurally inexpressible.

**Harder.** The allowlist is ongoing curation and directly caps catalog breadth — every new
source is a decision, which is friction on the thing the product needs most. Capability removal
constrains the architecture permanently: extraction can never be given a tool to "go look
something up", however useful that becomes.

**Accepted downsides.** Coverage grows more slowly than open crawling would allow — accepted,
because an unvetted source is an unvetted author. Schema constraints limit how much nuance a
record can carry. And an allowlisted source that is *itself* compromised bypasses control 1
entirely, which is why controls 3–5 exist and why none of them may be dropped as redundant.

**New obligations.** A quarantine and alerting path for suspected injection (NFR-45) — suspicious
content is a security signal and must not be silently dropped, since silent dropping means never
learning you are a target. Adding a crawl source needs a documented vetting step. And a poisoning
incident needs a rehearsed response: identify affected records by artifact, retract, notify.

**Not covered here.** The advisor's document and free-text intake (FR-22, FR-24) accept content
from the tenant itself. The tenant is the principal there, so injection is largely self-directed —
but a design document pasted from an untrusted source is a real case, and it deserves its own
consideration rather than being assumed safe by analogy.
