# ADR-0010: Object storage

- **Status:** Accepted
- **Date:** 2026-07-08
- **Decision:** Build vs **Buy/Reuse** — MinIO (S3 API)

## Context

The platform stores blobs — uploaded documents, parsed artifacts, model outputs — that
don't belong in Postgres. It must run on-prem with no cloud dependency, yet deploy to
cloud without code changes. The S3 API is the universal object-storage standard, so the
real decision is which S3-compatible implementation to run on-prem while keeping the option
to point at real S3 in cloud.

## Options considered

- **Direct AWS S3** — the managed standard, but hard cloud lock-in and no on-prem option;
  disqualified as the primary because on-prem must stay possible.
- **Ceph (RADOS Gateway)** — powerful and S3-compatible, but heavyweight to operate for
  object storage alone.
- **SeaweedFS** — lightweight and fast, but a smaller ecosystem and less battle-tested S3
  compatibility for enterprise use.
- **Build custom** — reinventing durable, replicated blob storage is not something we own.
- **MinIO** — mature, drop-in S3-compatible object store, self-hosted, cloud-portable.
  *(chosen)*

## Decision

MinIO as the S3-compatible object store, accessed via the S3 API so the same code runs
against MinIO on-prem and real S3 in cloud with no change.

## Seven-criteria evaluation

| Criterion | Assessment |
|---|---|
| Build vs Buy | Buy/reuse; no custom blob store. |
| Complexity | Low: single binary/container, familiar S3 semantics. |
| Effort | Low: standard S3 SDK usage behind one storage interface. |
| Scalability | High: MinIO scales to distributed multi-node deployments. |
| Lock-in risk | Very low: the S3 API is the standard; MinIO and AWS S3 are interchangeable. |
| Cost | Free OSS on-prem; in cloud, pay for S3 usage only. |
| Community maturity | Very high: widely deployed, de-facto on-prem S3. |

## Consequences

- All blob access goes through `services/knowledge/storage.py`, which talks the S3 API, so
  switching between MinIO (on-prem) and AWS S3 (cloud) is a config/endpoint change with no
  code change.
- On-prem stays fully possible because MinIO needs no external dependency.
- Swap path: the S3 API abstraction means moving to Ceph, SeaweedFS, or real S3 is a matter
  of endpoint/credentials configuration confined to `storage.py`; callers are unaffected.
- Trade-off accepted: we operate MinIO ourselves on-prem rather than outsourcing storage,
  the necessary cost of the on-prem requirement.
