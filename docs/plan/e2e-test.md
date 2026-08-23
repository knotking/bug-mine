# End-to-End Test Plan

**Status:** Draft
**Target:** `bugmine-dev`
**Date:** 2026-08-22

What has to be true before the deployment can be called working. Ordered so each stage depends
only on the ones above it — a failure stops the run rather than producing a misleading later
result.

---

## The bias in this plan

Most of these checks are **negative**. That is deliberate: every failure mode that has actually
bitten during this build was one that *failed open* — a security property that was absent while
every positive test passed, a migration that reported success while the database had no tables,
an empty result that read as a clean bill of health.

A test that only proves the happy path would have passed in all of those cases.

---

## Stage 0 — Preconditions

| # | Check | Pass |
| :-: | --- | --- |
| 0.1 | `terraform plan` | `No changes` |
| 0.2 | Deployed image tag matches the intended build | equal |
| 0.3 | Migration executed | `succeededCount = 1`, read as a scalar, never from a table format |

**0.3 exists because it already went wrong.** A `table[no-heading]` format collapses empty
fields and shifts columns, so `failed=1` was read as `succeeded=1` and the deployment was
described as working while `bug_record` did not exist.

## Stage 1 — Isolation, in Cloud SQL

The highest-severity stage, and the one most likely to fail open.

| # | Check | Pass |
| :-: | --- | --- |
| 1.1 | Application role is **not** a superuser and does **not** have `BYPASSRLS` | `rolsuper=f`, `rolbypassrls=f` |
| 1.2 | RLS enabled and **forced** on every tenant-scoped table | all true |
| 1.3 | Tenant B cannot read tenant A's rows | 0 rows |
| 1.4 | Anonymous session sees no tenant rows | 0 rows |
| 1.5 | A `tenant`-scoped record never appears in a public-scope query | 0 rows |

**1.1 is the whole stage.** Cloud SQL grants `cloudsqlsuperuser` to the application user, and a
role that bypasses RLS makes 1.3 through 1.5 pass against a database enforcing nothing. This
exact trap already caught the local test suite once, where connecting as `postgres` made every
isolation test green while RLS was inert.

## Stage 2 — Ingestion

| # | Check | Pass |
| :-: | --- | --- |
| 2.1 | Crawl fetches a real source and writes an artifact to GCS | object exists, keyed by content hash |
| 2.2 | Job row records the outcome | terminal state, no exception |
| 2.3 | **Re-crawling unchanged content writes no new artifact and no new version** | version count unchanged |
| 2.4 | Extraction produces records classified on **both** axes | `subject_domain` and `bug_type` set |
| 2.5 | Extraction output is schema-validated before persistence | invalid input rejected, nothing written |
| 2.6 | Records carry provenance to the artifact | `raw_artifact_uri` populated |

**2.3 is the cost control.** Without it, catalog size tracks crawl frequency rather than
reality — roughly 20× the extraction bill, growing with how often we look rather than how often
things change.

## Stage 3 — Retrieval and the API

| # | Check | Pass |
| :-: | --- | --- |
| 3.1 | `POST /v1/check/dependencies` returns a match for a seeded component | ≥1 match |
| 3.2 | **Every match carries a citation** with a resolvable evidence URL | no uncited match |
| 3.3 | A version outside the affected range does **not** match | 0 matches |
| 3.4 | An unknown version matches but is `version_confirmed: false` | flagged |
| 3.5 | **An uncovered component returns `not_covered`, never an empty list** | reason present |
| 3.6 | An unsupported ecosystem is reported distinctly | `ecosystem_unsupported` |
| 3.7 | A bad API key is rejected with the app's error envelope, not Cloud Run HTML | `code` present, 401 |

**3.5 is the check an LLM-backed system fails silently.** Returning nothing for an unknown
component is indistinguishable from returning nothing for a healthy one.

## Stage 4 — Client path

| # | Check | Pass |
| :-: | --- | --- |
| 4.1 | CLI resolves this repository's own lockfile | dependency count > 0 |
| 4.2 | CLI reports findings against the deployed API | matches printed with evidence |
| 4.3 | CLI states uncovered counts rather than omitting them | count shown |
| 4.4 | **Only names and versions leave the machine** | request body contains no file contents |
| 4.5 | MCP server lists its tools and answers `check_dependency` | tools present, answer returned |

**4.4 is the product's central privacy claim.** It should be verified by inspecting the actual
request payload, not by reading the code that builds it.

## Stage 5 — Boundaries

| # | Check | Pass |
| :-: | --- | --- |
| 5.1 | Cloud SQL has no public IP | `ipv4Enabled = false` |
| 5.2 | Egress deny rule covers `169.254.0.0/16` | present |
| 5.3 | Only model-bearing workers hold `aiplatform.user` | crawl/fetch absent |
| 5.4 | Snapshot bucket deletes at 1 day | lifecycle present |

---

## Results — 2026-08-23

| Stage | Outcome |
| :-: | --- |
| **0** Preconditions | **Pass** |
| **1** Isolation in Cloud SQL | **Pass 14/14** — `rolsuper=False`, `rolbypassrls=False`, cross-tenant reads 0 rows |
| **2** Ingestion | **Pass 4/4** — 3 real records from two crawls, dedup confirmed |
| **3** Retrieval | **Blocked** — see below |
| **4** Client path | **Blocked** by the same cause |
| **5** Boundaries | Spot-checked: no public IP, egress denies present, IAM split correct |

### Stage 3 is blocked by a header collision, not by the code

Cloud Run consumes the `Authorization` header for its own IAM check. The tenant API key uses
the same header. Both cannot be present, so no authenticated route is reachable from outside
this deployment.

This only arises because `constraints/iam.allowedPolicyMemberDomains` forbids granting
`run.invoker` to `allUsers`, which forces every request to carry a Google identity token. In a
deployment where the service is anonymously invokable and the application does its own
authentication — which is the design and what the OpenAPI contract specifies — there is no
conflict.

Resolution, best first:

1. **An org-policy exception for `bugmine-dev`.** The app's auth then works exactly as
   specified, and nothing in the contract changes.
2. **Move API keys to a custom header**, as the operator token already does. Works, but
   diverges from the contract and from standard practice, and would be undone under option 1.
3. **A load balancer in front**, with the service kept internal.

Noted honestly: the same collision was hit and fixed for the operator token earlier in the
session, and the fix was applied only to the endpoint in hand rather than recognised as
applying to every authenticated route.

### Defects this run surfaced

Eight, none of which any unit test caught, all requiring the deployed environment:

| # | Defect |
| :-: | --- |
| 1 | Cloud Tasks queue IDs reject underscores |
| 2 | Cloud SQL defaults to ENTERPRISE_PLUS, rejecting the intended tier |
| 3 | `uv.lock` gitignored, so absent from the build context |
| 4 | BuildKit cache mounts unsupported by the Cloud Build docker builder |
| 5 | Alembic routing the URL through configparser, which treats `%` as interpolation |
| 6 | RLS refusing system-borne jobs, which legitimately have no tenant |
| 7 | Workers swallowing exceptions, making a live failure undiagnosable |
| 8 | Truncation at artifact read corrupting structured documents |

Defects 6, 7 and 8 were self-inflicted. The RLS class is now caught structurally by
`tests/test_rls_coverage.py`; the other two are covered by regression tests.

## Known to be out of scope

- **Public anonymous access** — blocked by the org policy on `allUsers`; tested with an
  identity token instead, which proves the app but not the anonymous path.
- **Reachability** — not built; every match is currently manifest-level.
- **Scan workers** — `scan_fetch` and `scan_analyze` have queues and identities but no code.

## What a pass means, and what it does not

A full pass means the catalog can be filled from a real source, queried correctly, and reached
from a developer's machine without shipping code — with tenant isolation enforced by the
database rather than by the application remembering to filter.

It does **not** mean the findings are useful. Precision depends on reachability, which does not
exist yet, so every match here is the manifest-level answer the product exists to improve on.
