# ADR-0009: Document parsing

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision:** Build vs **Buy/Reuse** — Docling (Unstructured as fallback)

## Context

Before documents can be chunked and embedded (ADR-0007), they must be parsed from PDFs,
Office files, and scans into clean, structured text that preserves layout (headings,
tables, reading order). Parse quality directly caps RAG quality. We want layout-aware
extraction without standing up and maintaining a custom OCR/layout stack, and it must run
on-prem with no document-parsing SaaS in the path.

## Options considered

- **Unstructured** — broad format coverage and popular; kept as the fallback, but layout
  fidelity on complex PDFs/tables is weaker than Docling in our evaluation.
- **LlamaParse** — strong quality, but a hosted SaaS that sends documents off-box; fails the
  on-prem and data-residency requirement.
- **Apache Tika** — mature and self-hosted, but text-extraction oriented and weak on modern
  layout/table structure.
- **Custom OCR** — assembling OCR + layout models ourselves is a specialist stack we refuse
  to own and maintain.
- **Docling** — layout-aware parsing (tables, structure, reading order), self-hosted, no
  bespoke OCR stack. *(chosen)*

## Decision

Docling for document parsing, self-hosted, with Unstructured as a documented fallback for
formats or edge cases Docling handles poorly.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Buy/reuse; no custom OCR/layout stack. |
| Complexity | Low-moderate: a parsing library, not an OCR platform to operate. |
| Effort | Low: invoked behind one parsing interface. |
| Scalability | Good: parsing is CPU-bound and scales horizontally with ingestion workers. |
| Lock-in risk | Low: output is plain structured text; Unstructured fallback proves the abstraction. |
| Cost | Free OSS; only compute for parsing. |
| Community maturity | Good and rising (IBM-backed); Unstructured is the mature backstop. |

## Consequences

- All parsing goes through `services/knowledge/parsing.py`, which exposes one interface
  returning structured text regardless of the underlying engine.
- On-prem and data-residency requirements hold because documents never leave the deployment
  for parsing, unlike LlamaParse.
- Swap path: because callers only touch `parsing.py`, switching to Unstructured (already the
  fallback), Tika, or a future parser is confined to that module; ingestion and retrieval
  are unaffected.
- Trade-off accepted: Docling is newer than Tika/Unstructured, so we keep Unstructured wired
  as a fallback and route difficult formats to it when needed.
