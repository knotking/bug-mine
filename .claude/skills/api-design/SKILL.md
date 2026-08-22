---
name: api-design
description: Design or review an API contract before it is implemented — resources and operations, request/response shapes, the error model, auth, pagination, idempotency, and versioning — and emit a machine-readable spec (OpenAPI, protobuf, or GraphQL SDL) where one fits. Use when the user asks to "design the API", "what endpoints do we need", "write the OpenAPI spec", "review this API", or is about to expose functionality to another service, a client app, or third parties. Not for the persistence schema behind it (use `data-model`) and not for documenting an API that already exists (use `documentation`).
user-invocable: true
---

# api-design — the contract, settled before it is expensive to change

An API is the one artifact you can't quietly refactor later: once something is calling it,
every change costs a coordinated migration. That asymmetry is the whole reason to design it
up front. The most common mistake is exposing the database schema through the wire format,
which welds two things together that need to evolve at completely different speeds.

## Workflow

1. **Identify the consumers and what they can be asked to do.** A first-party frontend you
   deploy alongside the server tolerates breaking changes an external partner never will.
   This single fact drives how conservative the versioning and deprecation strategy needs to
   be, so establish it before designing anything.

2. **Collect the real use cases.** List what each consumer needs to accomplish, then design
   operations that serve those directly. Designing endpoint-by-endpoint from the entity list
   reliably produces an API where common tasks take four round trips.

3. **Pick the style deliberately.** REST, RPC/gRPC, or GraphQL — chosen against the consumers
   and use cases above, and consistent with whatever the project already exposes. Don't
   introduce a second paradigm without saying why the existing one doesn't fit.

4. **Model resources and operations.** Name resources as plural nouns and keep them stable;
   put behavior in the method, not the path. Deviate for genuine actions that aren't CRUD
   rather than contorting them into it. Define the request and response shape for each
   operation, and keep the response shape distinct from the stored entity — omit internal
   fields, and don't leak columns you'll want to rename later.

5. **Design the error model once, and apply it everywhere.** A single error envelope: a
   stable machine-readable code, a human-readable message, and where relevant, per-field
   validation detail. Map codes to HTTP status deliberately — 400 vs 409 vs 422 carry
   different meanings to a client's retry logic. Clients branch on the code, never on prose,
   so the codes are part of the contract and can't be reworded casually.

6. **Settle the cross-cutting concerns explicitly**, because retrofitting any of them is a
   breaking change:
   - **Auth** — the scheme, where credentials go, and the permission model per operation
   - **Pagination** — cursor or offset, page size limits and defaults, response envelope
   - **Filtering & sorting** — which fields, and what happens to unsupported ones
   - **Idempotency** — for every non-GET operation: safe to retry, and how the client says so
   - **Rate limits** — the limits and the headers that communicate them
   - **Versioning** — where the version lives, what counts as breaking, deprecation policy
   - **Timestamps and IDs** — one format each, used consistently across every endpoint

7. **Write the spec file.** OpenAPI YAML, `.proto`, or SDL — with examples for each operation,
   including at least one error response. The spec is the artifact; prose around it should
   explain the decisions, not restate the schema.

8. **Review against evolution.** For each part of the contract, ask what a future change
   would cost. Required fields, enums without an unknown case, and unpaginated list
   endpoints are the three that most often turn out to be one-way doors.

## Output

Write the narrative to `docs/api/<name>.md` and the machine-readable spec alongside it
(adapt to the project's existing docs layout if it differs). Keep entity and field names
aligned with `docs/data-model/` where they refer to the same concept, and note deliberately
where the wire name differs from the stored name.
