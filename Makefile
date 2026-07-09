# Industry AI OS — developer shortcuts.
# All stack operations go through docker compose in ./deploy.

COMPOSE      := docker compose -f deploy/docker-compose.yml --env-file .env
COMPOSE_INFRA:= docker compose -f deploy/docker-compose.infra.yml --env-file .env

.PHONY: help up up-infra down logs ps health seed smoke test lint fmt migrate env

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from template if missing
	@test -f .env || (cp .env.example .env && echo "created .env from template")

up: env ## Bring up the FULL stack (infra + all services) with health checks + seed
	$(COMPOSE) up -d --build
	@echo "Stack starting. Run 'make health' once containers settle."

up-infra: env ## Bring up infrastructure only (fast inner loop for service dev)
	$(COMPOSE_INFRA) up -d

down: ## Stop and remove the full stack
	$(COMPOSE) down

down-v: ## Stop the stack AND delete volumes (full reset)
	$(COMPOSE) down -v

logs: ## Tail logs for all services
	$(COMPOSE) logs -f --tail=100

ps: ## List running containers
	$(COMPOSE) ps

health: ## Hit /healthz on every app service
	@bash deploy/scripts/health.sh

seed: ## Run the seed job (demo tenant + users + roles)
	$(COMPOSE) run --rm seed

smoke: ## Run smoke tests against the running stack
	uv run --with pytest --with pytest-asyncio --with httpx pytest tests/smoke -v

test: ## Run shared unit tests (no stack required)
	uv run --with pytest --with pytest-asyncio pytest packages/shared/tests -v

lint: ## Lint with ruff
	uv run ruff check .

fmt: ## Format with ruff
	uv run ruff format .

migrate: ## Run DB migrations
	$(COMPOSE) run --rm seed alembic upgrade head
