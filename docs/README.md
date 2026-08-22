# docs

All project documentation lives here.

## Layout

Each design skill in `.claude/skills/` writes into its own subdirectory:

| Path | Contents | Skill |
| --- | --- | --- |
| `docs/requirements/` | Functional and non-functional requirements, with stable `FR-n` / `NFR-n` IDs | `/requirements` |
| `docs/data-model/` | Entities, relationships, keys, indexes, migrations, ERDs | `/data-model` |
| `docs/api/` | API contracts plus the machine-readable spec (OpenAPI / proto / SDL) | `/api-design` |
| `docs/architecture/` | Components, boundaries, data flow, technology choices, failure modes | `/architecture` |
| `docs/adr/` | Numbered architecture decision records, `NNNN-title.md`, append-only | `/adr` |
| `docs/design/` | Top-level design docs that link the above together | `/design-doc` |

## Conventions

- Requirement IDs are stable once written — other documents cite them, so renumbering breaks
  those references.
- An entity keeps the same name across the requirements, data model, and API docs. Where the
  wire name deliberately differs from the stored name, say so at the point of difference.
- ADRs are append-only. Supersede a decision with a new ADR rather than editing the old one.
