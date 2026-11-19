# Developer interface. Every target runs tools pinned in uv.lock.
.DEFAULT_GOAL := help
COMPOSE := docker compose --env-file .env --file infrastructure/docker/compose.yaml
WITH_ENV := set -a && . ./.env && set +a &&
TF_BOOTSTRAP := infrastructure/terraform/bootstrap
.PHONY: help setup lint format typecheck test test-integration check dbt-parse dbt-build sql-lint env up down ps logs reset e2e tf-validate tf-init-bootstrap tf-plan-bootstrap

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

test: ## Unit, property, and contract tests (no database needed)
	uv run pytest

test-integration: ## Integration tests against the running local stack
	$(WITH_ENV) uv run pytest -m integration

check: lint typecheck test ## Everything CI runs on Python code

dbt-parse: ## Parse the dbt project
	$(WITH_ENV) cd warehouse/dbt && uv run dbt parse

dbt-build: ## Build and test the dbt models against the local warehouse
	$(WITH_ENV) cd warehouse/dbt && uv run dbt build

sql-lint: ## Lint the dbt SQL with SQLFluff (dbt templater)
	$(WITH_ENV) cd warehouse/dbt && uv run sqlfluff lint models tests

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

e2e: | .env ## Sprint 1 slice: simulate, post, load, dbt build, KPI-01 summary (needs make up)
	$(WITH_ENV) uv run northstar run-slice --scenario config/simulation/sprint1_scenario.yaml --seed 42 --out data/generated
	$(MAKE) dbt-build
	$(WITH_ENV) uv run northstar ledger-summary

tf-validate: ## Terraform format check and validation (no AWS access)
	terraform -chdir=$(TF_BOOTSTRAP) fmt -check -recursive
	terraform -chdir=$(TF_BOOTSTRAP) init -backend=false -input=false
	terraform -chdir=$(TF_BOOTSTRAP) validate

tf-init-bootstrap: ## Initialise the bootstrap stack against its S3 state (needs AWS login)
	$(WITH_ENV) terraform -chdir=$(TF_BOOTSTRAP) init -input=false \
		-backend-config="bucket=northstar-tfstate-$$(aws sts get-caller-identity --query Account --output text)-ca-central-1"

tf-plan-bootstrap: tf-init-bootstrap ## Plan the AWS bootstrap; never applies (needs AWS login)
	$(WITH_ENV) terraform -chdir=$(TF_BOOTSTRAP) plan -input=false -out=bootstrap.tfplan
