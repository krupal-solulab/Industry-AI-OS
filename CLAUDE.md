# Claude Code — project instructions

## Read PROJECT_MEMORY.md first (mandatory)

Before making ANY change in this repo:

1. **Open and read [PROJECT_MEMORY.md](PROJECT_MEMORY.md) in full.** It is the single
   source of truth for current status, decisions, and constraints. Do not implement
   anything until you understand it.
2. Make changes **only in alignment** with its decisions and constraints. If a request
   contradicts a recorded constraint, stop and flag it before proceeding.
3. **After finishing any change, append a Change Log entry** at the bottom of
   PROJECT_MEMORY.md (what changed, why, files modified, commands/tests run, next steps)
   and update the status/pending sections. Record what was actually *verified* vs only
   *written*.
4. If PROJECT_MEMORY.md is ever missing, recreate it and initialize it from the current
   repo state before doing other work.

## Project shape

Industry AI OS — a reusable, multi-tenant AI platform (Milestone 1 = platform core, no
industry code). Monorepo: `packages/shared` (the reuse spine) + `services/*` (9 FastAPI
services) + `deploy/*` (compose, migrations, helm) + `docs/*` (ARCHITECTURE + ADRs).

Design record: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/adr/](docs/adr/).
Operational state: [PROJECT_MEMORY.md](PROJECT_MEMORY.md).

## Working conventions

- Python 3.11+, `uv` workspace. Lint: `uvx ruff check services packages`.
- Every external tool sits behind an interface in `packages/shared` — never import a
  vendor client directly from a service.
- Multi-tenant always: `tenant_id` on every tenant row, RLS enforced, no service trusts a
  client-supplied tenant id (only the gateway-minted signed context).
- See PROJECT_MEMORY.md §4 for active constraints (e.g. commits are not made this
  session; Docker daemon state).
