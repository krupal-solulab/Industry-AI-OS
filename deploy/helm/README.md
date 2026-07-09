# Helm chart — Industry AI OS

Mirrors the docker-compose stack for Kubernetes. The nine application services are
rendered generically from `.Values.services`; infrastructure comes from community
subcharts (toggled in `values.yaml`).

## Install

```bash
helm dependency build deploy/helm
kubectl create secret generic aios-secrets \
  --from-literal=DATABASE_URL='postgresql+asyncpg://...' \
  --from-literal=INTERNAL_CONTEXT_SECRET='...' \
  --from-literal=KEYCLOAK_CLIENT_SECRET='...' \
  --from-literal=ANTHROPIC_API_KEY='...' \
  --from-literal=OPENAI_API_KEY='...'
helm install aios deploy/helm -n aios --create-namespace
```

## Design notes

- **Only the gateway is public** (Ingress). All other services are ClusterIP —
  reachable only in-cluster, matching the compose network boundary.
- **Secrets** are never in values: they live in the `aios-secrets` Secret, which in
  real environments is projected from Infisical/Vault (see ADR-0014).
- **Migrations + seed** run as a `post-install,post-upgrade` Helm hook Job
  (`seed-job.yaml`), the K8s equivalent of the compose `seed` service.
- **Infra parity:** Postgres, Keycloak, MinIO, Redis, and NATS ship as subchart
  dependencies. Temporal, Langfuse, LiteLLM, and the OTel Collector are deployed from
  their own upstream charts/manifests (no stable Bitnami chart) — wire their in-cluster
  service names into `.Values.config`. In cloud, disable a subchart and point the
  corresponding `config` URL at the managed equivalent (e.g. RDS, ElastiCache) with no
  application change.
- **Scaling:** stateless services (gateway, orchestrator) set `replicas > 1`; the
  Temporal worker (`workflows`) scales horizontally on the same task queue.
```
