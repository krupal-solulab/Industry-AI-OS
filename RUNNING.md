# Running the Backend

The backend is the **Industry AI OS** platform: 9 FastAPI services + infrastructure
(PostgreSQL, Keycloak, Temporal, MinIO, Cerbos, Redis, LiteLLM, Langfuse, OTel),
orchestrated by Docker Compose.

> **TL;DR for teammates**
>
> ```bash
> cd ai-backend
> cp .env.example .env            # Windows: copy .env.example .env
> docker compose -f deploy/docker-compose.yml --env-file .env up -d --build   # first time (~5-8 min)
> # after that, every day:
> docker compose -f deploy/docker-compose.yml --env-file .env up -d           # no --build; starts in seconds
> ```
>
> Gateway (the only public API) → http://localhost:8000  ·  API docs → http://localhost:8000/docs

---

## Prerequisites

- **Docker Desktop** running, with **~15 GB free disk**.
- Nothing else — you do NOT need Python, uv, or Keycloak installed locally; everything
  runs in containers.

## Which `.env`? (the #1 gotcha)

| You run the backend with…                    | Copy this to`.env`       | Why                                                            |
| --------------------------------------------- | -------------------------- | -------------------------------------------------------------- |
| **Docker Compose** (normal / teammates) | **`.env.example`** | URLs use Docker service names (`postgres`, `keycloak`, …) |
| Bare`uvicorn` on the host (advanced)        | `.env.local.example`     | URLs use`localhost` + published ports                        |

> ⚠️ With `.env.local.example` under compose, the containers try to reach `localhost`
> (themselves) instead of the `postgres`/`keycloak` containers and fail. For
> `docker compose`, use **`.env.example`**.

## First run (one time)

```bash
cd ai-backend
cp .env.example .env                                   # Windows: copy .env.example .env
docker compose -f deploy/docker-compose.yml --env-file .env up -d --build
```

Builds the 9 service images and starts everything. A one-shot **`seed`** container runs
DB migrations and registers the demo tenant, then exits (`Exited (0)` = success).

Optional: add LLM keys to `.env` so chat/embeddings work — `ANTHROPIC_API_KEY` and/or
`OPENAI_API_KEY`. Without them the app runs; only AI features show "not configured".

## Everyday use

**Pausing / resuming for the day — prefer `stop`/`start`, not `down`/`up`:**

```bash
docker compose -f deploy/docker-compose.yml stop      # pause — keeps containers
docker compose -f deploy/docker-compose.yml start     # resume — warm, ready in seconds
```

> ⚠️ `down` *removes* the containers, so the next `up` cold-starts everything. LiteLLM
> re-registers its models on a cold start and takes ~2–3 min before it reports healthy
> (Compose waits for it — that's normal, not a hang). `stop`/`start` avoids that wait
> entirely. Use `down` only when you actually want to recreate containers (e.g. after
> editing `docker-compose*.yml` or `.env`).

**Other commands:**

```bash
docker compose -f deploy/docker-compose.yml --env-file .env up -d      # first start / after config changes
docker compose -f deploy/docker-compose.yml ps                          # status — wait for "healthy"
docker compose -f deploy/docker-compose.yml logs -f gateway workflows   # tail logs
docker compose -f deploy/docker-compose.yml down                        # remove containers (keeps data volumes)
```

- Add `--build` **only** when backend code / a Dockerfile changes (incremental: only the
  changed service rebuilds).
- Rebuild a single service: `docker compose -f deploy/docker-compose.yml --env-file .env build <service>`.
- **Never** `down -v` unless you intend to wipe the database (Postgres/MinIO volumes).

## Ports

| Service                        | URL                                    |
| ------------------------------ | -------------------------------------- |
| **Gateway (public API)** | http://localhost:8000  (docs`/docs`) |
| Keycloak (login/admin)         | http://localhost:8081  (admin / admin) |
| Temporal UI                    | http://localhost:8088                  |
| MinIO console                  | http://localhost:9001                  |
| Langfuse                       | http://localhost:3000                  |

## Demo logins (seeded, realm `industry-ai-os`, password `Passw0rd!`)

`owner@demo.aios.local` · `admin@demo.aios.local` · `member@demo.aios.local` · `viewer@demo.aios.local`

## Frontend (`Landing-Page/`)

```bash
cd Landing-Page
cp .env.example .env      # VITE_API_URL=http://localhost:8000 (the gateway)
npm install
npm run dev               # http://localhost:8080
```

## Health check

```bash
curl http://localhost:8000/healthz     # gateway
curl http://localhost:8000/readyz      # gateway + dependencies
```

## Troubleshooting

- **`port is already allocated`** — an old manual `uvicorn`/`keycloak` is on 8000/8001/8081.
  Stop it; compose runs its own.
- **Page shows "Failed to load" / 500s** — a downstream service isn't healthy. Run
  `docker compose ps` and check the red one's logs.
- **`no space left on device` / `unpigz: corrupted` / `snapshot not found` during build** —
  disk full or a BuildKit cache glitch. Free space, then `docker builder prune -af` and
  re-run `up -d --build`; if it persists, restart Docker Desktop.
- **Chat/knowledge say "not configured"** — set `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` in
  `.env` and `up -d` again.
- **Signup fails "Organization not found" / login says "JWT carries no organization/tenant
  claim"** — the `seed` job never provisioned the demo Keycloak org. Usually because the
  **`seed` image is stale after a new migration was added** (it fails with
  `Can't locate revision '<id>'`). Rebuild + rerun it:
  `docker compose -f deploy/docker-compose.yml --env-file .env build seed` then
  `... run --rm seed`. **Rule: rebuild `seed` (or `up -d --build`) whenever you add a
  migration under `deploy/migrations/versions/`.**
- **`up` seems to hang / aborts on `deploy-litellm-1 is unhealthy`** — litellm's `/health`
  warms up ~2-3 min on a cold boot; that's expected. It no longer aborts the stack, but if
  you see it, litellm is fine — just re-run `up -d`. Prefer `stop`/`start` over `down`/`up`
  for day-to-day (see "Everyday use").

> Design docs: `docs/ARCHITECTURE.md`, `docs/adr/`, `docs/MILESTONE_2.md`.
> Operational state / history: `PROJECT_MEMORY.md`.
