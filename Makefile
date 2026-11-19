# Developer interface. Every target runs tools pinned in uv.lock.
.DEFAULT_GOAL := help
COMPOSE := docker compose --env-file .env --file infrastructure/docker/compose.yaml
.PHONY: help setup lint format typecheck test check dbt-parse env up down ps logs reset

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

setup: ## Install the locked environment and the git hooks
	uv sync --locked
	uv run pre-commit install

lint: ## Ruff lint, Ruff format check, import-layer contracts
	uv run ruff check .
	uv run ruff format --check .
	uv run lint-imports

format: ## Apply Ruff fixes and formatting
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## mypy --strict over src/ and tests/
	uv run mypy

test: ## Unit and property tests
	uv run pytest

check: lint typecheck test ## Everything CI runs on Python code

dbt-parse: ## Parse the dbt project (needs a profile: warehouse/dbt/README.md)
	cd warehouse/dbt && uv run dbt parse

env: ## Create .env with generated local secrets (never overwrites)
	uv run python scripts/create_local_env.py

.env:
	uv run python scripts/create_local_env.py

up: | .env ## Start the local stack and wait until every service is healthy
	$(COMPOSE) up --detach --wait

down: ## Stop the local stack (data volumes are kept)
	$(COMPOSE) down

ps: ## Show local stack services and health
	$(COMPOSE) ps

logs: ## Follow the local stack logs
	$(COMPOSE) logs --follow

reset: ## Stop the local stack and DELETE its data volumes (needed after regenerating .env)
	$(COMPOSE) down --volumes
