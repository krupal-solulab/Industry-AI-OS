# Deployment

Three supported targets from one set of images.

## 1. On-prem / dev — Docker Compose (baseline)

```bash
cp .env.example .env
make up        # infra + all services, health checks, seed
make health
```

`deploy/docker-compose.yml` is the single source of truth for the full stack. A
one-shot `seed` container runs migrations and inserts one demo tenant + users/roles.
`make up-infra` starts infrastructure only, for fast service iteration.

### Services & ports (dev)

| Component | Port (host) |
|---|---|
| Gateway (public API) | 8000 |
| Keycloak | 8081 |
| Cerbos (HTTP / gRPC) | 3592 / 3593 |
| Postgres | 5432 |
| MinIO (API / console) | 9000 / 9001 |
| Redis / Valkey | 6379 |
| NATS | 4222 |
| Temporal (gRPC) / UI | 7233 / 8088 |
| Langfuse | 3000 |
| LiteLLM | 4000 |
| OTel Collector (OTLP) | 4317 |

## 2. Cloud / Kubernetes — Helm

`deploy/helm` mirrors the compose stack: one subchart per service plus dependency
charts for Postgres, Keycloak, MinIO, Redis, NATS, and Temporal. Object storage can
point at real S3 (MinIO is S3-compatible, so no code change). Configure via values.

## 3. SaaS (multi-tenant)

Same images as cloud; tenants are Keycloak Organizations sharing the platform.
Individual tenants can be promoted to stricter isolation (schema/DB) without code
changes — see [MULTI_TENANCY.md](MULTI_TENANCY.md).

## Configuration & secrets

All config is via environment variables documented in `.env.example`. In cloud/SaaS,
secrets come from **Infisical/Vault**, injected as env at runtime — never baked into
images or committed.
