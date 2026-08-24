# Scanning container images

## The thing to get right before anything else

There are four mature, free container scanners — Trivy, Grype, Clair, Snyk — and every one of
them does the same job well: take an image, enumerate installed packages, match them against
CVE databases. That job is finished. Building a fifth one would produce a worse Trivy.

So the question this plan has to answer is not "how do we scan an image" but **what does BugMine
know about an image that a CVE scanner structurally cannot?**

The answer is the same one that justifies the rest of the product, and it has two halves.

### Half one: the base image is an unpinned dependency

`FROM python:3.12` is not a version. It is a moving tag that resolves to a different digest
week to week, and the change arrives with no lockfile entry, no diff, and nothing for a reviewer
to read. The image you built on Monday and the image you rebuild on Friday are different
software and your repository records them identically.

This is structurally the same problem as a hosted model changing weights behind a stable
endpoint — the case BugMine already argues is the reason versioned tooling is failing. A
container registry even gives us the artifact the model providers do not: the digest is
addressable and the change is *observable*, we just have to be watching.

What that makes possible, and what no CVE scanner offers:

> Your base image `python:3.12` moved from `sha256:a1b2…` to `sha256:c3d4…` on 14 August. That
> bump carried OpenSSL 3.0.13 → 3.2.1, which changed default cipher selection — a documented
> breaking change with no CVE. Here is the record.

That is a `breaking_change` record about an `operating_system` component with a build-range
applicability, all of which the catalog already models. The scanner category cannot express it
because a CVE is the only thing those tools carry.

### Half two: most of what an image contains is never executed

A `python:3.12` image ships a compiler toolchain, `git`, `curl`, dozens of libraries, and your
application. A CVE scanner reports on all of it. Almost none of it is reachable from the
container's entrypoint.

This is the reachability argument, one layer down, and the failure direction is identical: a
false positive is dismissible, a suppressed real defect is not. The honest position — and the
one this plan takes — is that image reachability is **weaker** than source reachability, and the
system must say so rather than borrow the confidence of the repository scanner.

## What is actually determinable without running the image

Running an attacker-supplied container to inspect it is not on the table. Everything below is
readable from the pulled artifact.

| Signal | Strength | How |
| --- | --- | --- |
| Package present in the flattened filesystem | **Strong** | Apply layer whiteouts; a package added in layer 2 and deleted in layer 6 is not in the running image |
| Base image identity and digest drift | **Strong** | Image config `history` plus registry manifest; compare digest against last scan |
| Application source is present and importable | **Strong** | `site-packages`, `node_modules` — the existing Python and JavaScript analysers apply unchanged |
| Binary on `PATH`, reachable from `ENTRYPOINT`/`CMD` | **Weak but real** | Config metadata; narrows "installed" to "invocable" |
| A compiled binary's actual call graph | **Not determinable** | Report `unknown`. Never suppress. |

That last row is the one that decides whether this ships honestly. For a Go or Rust binary with
no source in the image, we cannot narrow, and `Reach.unknown()` already exists to say exactly
that — findings go out at reduced confidence rather than being hidden.

## Architecture

It reuses the existing split rather than inventing one.

```
image_fetch  (egress, no model)   →  image_analyze  (no egress, model)
  pull manifest + config             flatten layers
  pull layers                        read package databases
  extract package DBs only           match catalog
  store selective snapshot           narrow by reachability
                                     write findings
```

`image_fetch` sits exactly where `scan_fetch` does and for the same reason (ADR-0005, NFR-42):
an image is content a tenant chose, and a model reachable from a process that can also make
outbound requests is what prompt injection needs to become exfiltration.

**We do not store the whole image.** The current snapshot cap is 256 MB and a real image is
several gigabytes, but we do not need the filesystem — we need the package databases, the
config, and the layer metadata. Selective extraction keeps a scan in the tens of megabytes and
keeps the cap meaningful rather than raising it until it stops protecting anything.

## Inventory sources

One image carries several ecosystems at once, which is new — a repository scan sees one project.

| Ecosystem | Read from | Subject domain |
| --- | --- | --- |
| `apk` | `/lib/apk/db/installed` | `operating_system` |
| `deb` | `/var/lib/dpkg/status` | `operating_system` |
| `rpm` | `/var/lib/rpm/` (BerkeleyDB or sqlite) | `operating_system` |
| `pypi` | `*.dist-info/METADATA` | `repo_library` |
| `npm` | `node_modules/*/package.json` | `repo_library` |
| `go` | Build info embedded in the binary | `repo_library` |

`ECOSYSTEM_DOMAIN` gains three entries. That map has already caused one silent failure — an
ecosystem missing from it made every dependency report "not covered" while the catalog held
records — so the additions come with a test asserting every ecosystem the inventory can emit
has a domain.

## Phases

**Phase 1 — pull and inventory.** Manifest, config, layers, whiteout handling, the three OS
package databases. Ends with: given an image reference, a correct list of what is installed in
the *running* filesystem. Verified against a fixture image with a package deliberately deleted
in a later layer.

**Phase 2 — catalog matching and findings.** Wire the inventory into `analyse()`. Mostly
existing code; the new work is OS-package version comparison, which is its own dialect — Debian
epochs (`1:2.3-4`), Alpine's `-r` revisions, RPM's `release` field. A naive semver comparison on
`1:2.3-4` is wrong in a way that reads as a clean result.

**Phase 3 — base image drift.** Record the resolved digest per scan, and report what changed
when a tag moves. This is the half that no other tool does, and it needs Phase 1's data to be
worth anything.

**Phase 4 — narrowing.** Whiteout-aware presence (Phase 1 gives this), `PATH`/entrypoint
reachability, and running the existing source analysers where application source is present.

**Deliberately not in scope:** running the image, private registry credentials (Phase 5 at the
earliest, via Workload Identity rather than stored passwords), image signing and provenance
attestation, and anything resembling runtime behavioural analysis.

## What would make this a bad idea

- **Trivy is free, good, and already installed.** If a user's question is "does this image have
  known CVEs", we should tell them to run Trivy. Our answer is only better for the non-CVE
  question, and if that question turns out not to matter to people, this feature does not
  either.
- **Registry egress is expensive and slow.** Pulling gigabytes per scan is the most costly
  operation in the system after evals. Selective extraction helps; it does not make it cheap.
- **OS package version comparison is a swamp.** Three dialects, each with edge cases, each of
  which fails silently in the direction of a clean report.
- **The base-image drift argument assumes people use moving tags.** Teams that pin by digest
  have already solved this, and those are disproportionately the teams who would pay us.
