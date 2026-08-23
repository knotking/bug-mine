# BugMine API — Contract

**Status:** Draft, for approval
**Date:** 2026-08-22
**Spec:** [`openapi.yaml`](openapi.yaml) — OpenAPI 3.1, the authoritative artifact
**Requirements:** FR-6 – FR-8, FR-12 – FR-13, FR-38 – FR-46, FR-57 – FR-63, FR-72 – FR-79

This document explains the decisions. It does not restate the schema — read the spec for that.

---

## 1. Consumers, and what they tolerate

Versioning conservatism is driven by who is calling, so this comes first.

| Consumer | Deploy control | Breaking change costs |
| --- | --- | --- |
| Web console | Ours, deployed together | Nothing — ship both at once |
| **MCP server** (Cursor, Claude Code) | **Installed on developer machines** | **High** — old versions run indefinitely |
| **CLI** | Installed by users and pinned in CI | **High** |
| GitHub App | Ours, hosted | Nothing |
| Generated SDKs | Published, pinned by users | **High** |

**Three of five are outside our deploy control**, which settles the versioning question in §7:
this behaves like an external API from day one, not an internal one that hardens later.

## 2. Style

**REST over JSON.** The operations are resource-shaped, the consumers are heterogeneous, and
generated SDKs across four languages are a first-class requirement — which OpenAPI serves and
GraphQL serves poorly. Long-running work is modelled as a resource plus a job handle rather than
as an RPC, so one async pattern covers scans, reports, and later the advisor.

## 3. The two decisions that shape everything else

### Public is a separate path prefix, not a scope parameter

`/v1/public/*` is a distinct, unauthenticated route tree, rather than `/v1/bugs?scope=public`.

A parameter makes correct behaviour depend on every handler applying a filter. A path prefix
makes it depend on routing. Since this is the only surface reachable **without an account**
(FR-73), and a leak of `tenant`-scoped data through it is unrecoverable, the boundary should be
somewhere a code reviewer can see rather than distributed across handlers.

The cost is a small amount of duplication between public and authenticated read endpoints. That
is the intended trade.

### Batch dependency check is the primary endpoint

`POST /v1/check/dependencies` takes a list of `{ecosystem, name, version}` and returns known
bugs. It is not a convenience wrapper — it is what makes **client-side scanning** work.

The IDE resolves the dependency graph locally and posts only names and versions. Source code
never leaves the machine, which answers the hardest question a security-conscious buyer asks, on
the surface where adoption starts. It is also the highest-volume endpoint in the system, so it
takes the tightest latency budget and the most aggressive caching.

Designing this as `GET /v1/bugs?component=X&version=Y` repeated per dependency would mean 400
round trips for a mid-sized lockfile. It is a `POST` despite being a read because the request
body is large and structured; that is a deliberate deviation from REST convention, not an
oversight.

## 4. Error model

One envelope everywhere:

```json
{
  "error": {
    "code": "version_unparseable",
    "message": "Could not order version 'v2-final' under scheme 'semver'.",
    "details": [{ "field": "dependencies[3].version", "issue": "unparseable" }],
    "request_id": "req_01J8..."
  }
}
```

**Clients branch on `code`, never on `message`.** Codes are part of the contract and cannot be
reworded casually; messages are free to change.

| Status | Used for | Not for |
| --- | --- | --- |
| `400` | Malformed request | Semantically invalid but well-formed |
| `401` | Missing or invalid credentials | Valid credentials lacking permission |
| `403` | Authenticated but not permitted | Resources that should be invisible — see below |
| `404` | Absent, **or present but out of scope** | |
| `409` | Conflicts with current state | |
| `422` | Well-formed but semantically invalid | |
| `429` | Rate limited — always with `Retry-After` | |

**A record outside the caller's privacy scope returns `404`, never `403`.** A `403` confirms the
record exists, which leaks the existence of another tenant's data — a subtler version of exactly
what FR-74 forbids.

## 5. Async

Every long-running operation returns `202` with a `JobHandle`, read back through
`GET /v1/jobs/{job_id}` (FR-6). Scans, reports, and later advice all use it — one pattern, so a
client that can wait on one can wait on all.

The job resource distinguishes `failed` from `budget_exceeded`, because a user who hit a spending
ceiling needs a different remedy from one who hit a bug.

## 6. Pagination, filtering, idempotency, rate limits

- **Cursor pagination.** `?limit=` and `?cursor=`, response carries `next_cursor`. Offset
  pagination over a catalog that is being written to concurrently silently skips and repeats rows.
- **Filtering** on named fields only. An unsupported filter is a `422`, never silently ignored —
  a silently dropped filter returns *more* data than the caller asked for, which they will not
  notice.
- **Idempotency.** Every non-GET accepts `Idempotency-Key`. Required on `POST /v1/scans` and
  `POST /v1/sources/{id}/trigger`, since both spend money.
- **Rate limits** via `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`. The anonymous
  bucket on `/v1/public/*` is substantially tighter — it is the only surface an attacker can probe
  without an account.

## 7. Versioning

- Version in the **path**, `/v1`.
- **Breaking:** removing or renaming a field, adding a required request field, removing an enum
  value, changing a type or an error code's meaning, tightening validation.
- **Non-breaking:** adding an optional request field, adding a response field, **adding an enum
  value**.
- Deprecation: 6 months' notice, `Deprecation` and `Sunset` headers, one release of overlap
  (NFR-37).

**All enums carry an `unknown` variant and clients must tolerate unseen values.** Without this,
adding an eighth bug type breaks every pinned SDK — and since three of five consumers are outside
our deploy control (§1), that would make the taxonomy effectively frozen. This is the single
cheapest thing to get right now and the most expensive to retrofit.

## 8. IDs and timestamps

- IDs are **ULIDs**, prefixed by type: `bug_01J8...`, `scan_01J8...`, `job_01J8...`. Prefixes make
  a misrouted ID a client-side error rather than a server 404.
- Timestamps are **RFC 3339, always UTC, always with an offset**. No naive datetimes anywhere.
- **Component versions are opaque strings.** The API never parses or normalises a version on the
  client's behalf, because version semantics are per-ecosystem and normalising would discard
  information the matcher needs.

## 9. Where wire names differ from stored names

Kept aligned with [`../data-model/stack-profile.md`](../data-model/stack-profile.md) except:

| Stored | Wire | Why |
| --- | --- | --- |
| `bug_record` + current `bug_version` | Flattened into one `Bug` | Callers want the current state; version history is a sub-resource |
| `privacy_scope` | `scope` | Shorter, unambiguous in context |
| `content_hash` | *omitted* | Internal dedup mechanism, not contract |
| `identity_key` | *omitted* | Internal; resolution strategy is unsolved (L1) and should not be frozen into the wire format |
| `subject_ref` | `component.ref` | Nested under the resolved component, since callers need the canonical name |

Omitting `identity_key` is deliberate. Exposing it would commit us publicly to an identity model
that `record-lifecycle.md` records as the hardest unsolved problem in the design.

## 9a. Principals

Every credential resolves to exactly one **principal** — a user or a team (FR-80). There is no
tenant-wide key.

This is an accounting constraint before it is a security one. Billing is per team and per user
(FR-17), metrics are per team and per user (FR-19), and the token ledger has `team_id` and
`user_id` columns. A shared tenant key cannot populate them, so per-team reporting would be
structurally impossible rather than merely unimplemented. Revocation granularity follows for free.

`POST /v1/tenant/api-keys` therefore requires a `principal`. A user key can only be minted for the
calling user; a team key requires `tenant_admin`.

## 10. Evolution review

The three shapes that most often become one-way doors:

| Risk | Handling |
| --- | --- |
| **Enums without an unknown case** | All enums have `unknown`; documented as non-breaking to extend (§7) |
| **Unpaginated list endpoints** | Every list is paginated from day one, including ones that look small |
| **Required fields added later** | New request fields are optional forever; a genuinely required input means a new endpoint |

One accepted risk: `POST /v1/check/dependencies` takes a list with no documented maximum. It needs
one before launch, and adding a limit later is a breaking change for anyone already exceeding it.

## 11. Not in this version

Subscriptions (FR-9), reports (FR-10), the advisor (FR-21 – FR-35), evals (FR-47 – FR-56), and
own-code analysis (FR-36 – FR-37). Each will add resources rather than change these, which is why
they are absent rather than stubbed.
