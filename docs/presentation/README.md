# Presentation

`bugmine.pptx` — the BugMine deck, 13 slides, 16:9.

## Rebuilding

```
uv run --with python-pptx python docs/presentation/build_deck.py
```

`python-pptx` is deliberately not a project dependency. It is needed to build a document, not
to run the software, and adding it to the runtime image to produce a file nobody's server reads
would be the wrong trade.

## Why the generator is checked in

A `.pptx` is a zip of XML. Committed on its own it is a binary nobody can diff, review or
correct — a colleague who spots a wrong figure has no way to fix it except to rebuild the whole
deck by hand. The script is the source; the `.pptx` is the artifact.

## Where the numbers come from

Catalog figures (records, components, sources, share beyond CVEs) are read from
`/v1/public/stats` at build time, so a rebuilt deck cannot quietly quote last month's catalog.
If the endpoint is unreachable the script falls back to values recorded in `FALLBACK`, with the
date they were measured.

Everything else is either a measured result from our own deployment — the `python-poetry/poetry`
scan on slide 8 — or carries the study it comes from in the slide's own caption. Nothing in the
deck is an unattributed number.

## Related

- [`docs/blog.md`](../blog.md) — the same argument in prose, longer on the reasoning
- [`docs/motivation/`](../motivation) — the research the figures are drawn from
