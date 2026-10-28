COMPOSE      = docker compose
COMPOSE_DEV  = docker compose -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: env up up-dev down logs ps test test-integration eval lint typecheck check fmt

env:            ## generate .env with random secrets
	python3 scripts/make_env.py

up:             ## full stack
	$(COMPOSE) up -d --build --wait

up-dev:         ## stack + fixture site + localhost debug ports (DEV/TEST ONLY)
	$(COMPOSE_DEV) up -d --build --wait

down:
	$(COMPOSE_DEV) down

logs:
	$(COMPOSE_DEV) logs -f --tail=100

ps:
	$(COMPOSE_DEV) ps

test:           ## unit + contract tests (no stack needed)
	uv run pytest

test-integration: ## needs `make up-dev`
	uv run pytest -m integration -o addopts="" tests/integration

eval:           ## run the Tier A eval suite
	uv run python eval/run_eval.py

lint:
	uv run ruff check src tests services migrations
	uv run ruff format --check src tests services

fmt:
	uv run ruff format src tests services
	uv run ruff check --fix src tests services

typecheck:
	uv run mypy

check: lint typecheck test
