---
name: data-model
description: Design or review the entity/data model — entities and what each represents, attributes and their types, relationships and cardinality, keys, constraints, indexes, data lifecycle, and the migration path to get there. Use when the user asks to "model the entities", "design the schema", "what tables do we need", "draw the ERD", or is adding persistent state to a system. Not for the wire contract that exposes this data (use `api-design`) and not for component-level system structure (use `architecture`).
user-invocable: true
---

# data-model — the entities, their relationships, and the migration to get there

Schemas outlive the code that reads them. A wrong type or a missing constraint is cheap to
fix before there's data and expensive after, so this phase is worth doing slowly. The
recurring failure is modelling nouns straight out of the requirements document without
asking what each one's identity actually is or how it changes over time.

## Workflow

1. **Extract candidate entities from the requirements**, then interrogate each one. An
   entity needs its own identity and independent lifecycle; if it only ever exists as part
   of something else and dies with it, it's an attribute or an embedded value, not a table.

2. **Define attributes with real types.** Precision matters and defaults are traps: money is
   an integer of minor units or a fixed-point decimal, never a float; timestamps are
   timezone-aware and stored in UTC; enums are constrained rather than free strings. For
   every column decide nullability deliberately — a nullable column is a claim that "unknown"
   is a legitimate state, and most aren't.

3. **Model relationships with explicit cardinality and ownership.** For each: one-to-one,
   one-to-many, or many-to-many; which side holds the reference; and what happens on delete
   (cascade, restrict, or orphan by design). Many-to-many always means a join table — name
   it and decide whether it carries attributes of its own.

4. **Choose keys deliberately.** Natural vs surrogate, and if surrogate, what kind (auto-
   increment leaks volume and ordering; UUIDv4 fragments index locality; UUIDv7/ULID keeps
   time ordering). Identify the real uniqueness constraints separately from the primary key —
   those are the ones that protect the data.

5. **Derive indexes from query patterns, not from intuition.** List the actual queries the
   application will run, then add the index each one needs, noting the column order for
   composite indexes. Every index is a write cost; an index with no query behind it is pure
   overhead.

6. **Decide the data lifecycle.** Hard vs soft delete, created/updated tracking, audit
   history, versioning, retention limits, and which fields are PII subject to deletion or
   export requests. Retrofitting an audit trail after the fact means reconstructing history
   that no longer exists.

7. **Plan the migration.** For an existing system, write the ordered steps — schema change,
   backfill, dual-write or read-switch, cleanup — and state explicitly whether each step is
   backwards compatible with the currently deployed code. Note how to roll back, and be
   honest where a step can't be rolled back once data has been written.

8. **Draw the ERD.** A mermaid `erDiagram` covering entities, keys, and relationships.

## Output

Write to `docs/data-model/<name>.md` (adapt to the project's existing docs layout if it
differs), with the concrete DDL or ORM model definitions where the project has a schema
format. Keep entity names consistent with the ones used in `docs/api/` and
`docs/requirements/` — the same concept under three different names across three documents
is a reliable source of bugs.
