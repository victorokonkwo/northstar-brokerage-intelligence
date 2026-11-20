# Developer interface. Every target runs tools pinned in uv.lock.
.DEFAULT_GOAL := help
# The Airflow image is built with the code version it records (W4.3).
export NBI_CODE_VERSION ?= $(shell git rev-parse HEAD 2>/dev/null)$(shell git status --porcelain --untracked-files=no 2>/dev/null | grep -q . && echo -dirty)
COMPOSE := docker compose --env-file .env --file infrastructure/docker/compose.yaml
WITH_ENV := set -a && . ./.env && set +a &&
TF_BOOTSTRAP := infrastructure/terraform/bootstrap
DEV_DATASET := data/generated/dev/seed=42
# One run ID per make invocation, shared by ingest and load (override with RUN_ID=...).
ifndef RUN_ID
RUN_ID := dev-$(shell date -u +%Y%m%dT%H%M%SZ)
endif
DATASET_VARS := '{"raw_schema": "raw_dev", "ledger_schema": "ledger_dev"}'
.PHONY: help setup lint format typecheck test test-integration check dbt-parse dbt-build sql-lint env up down ps logs reset e2e simulate ingest simulate-load dbt-build-dataset publish benchmark tf-validate tf-init-bootstrap tf-plan-bootstrap

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
	mkdir -p data/generated
	$(COMPOSE) up --detach --wait --build

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

simulate: ## Generate the dev dataset (seed 42): canonical events, external records, defects
	uv run northstar simulate --dataset config/simulation/datasets/dev.yaml --seed 42 \
		--defects config/simulation/defects/dev.yaml --out data/generated

ingest: | .env ## Validate the dev dataset into the raw layer and write a manifest (needs make up, make simulate)
	$(WITH_ENV) uv run northstar ingest --dataset-dir $(DEV_DATASET) --run-id $(RUN_ID)

simulate-load: ingest ## Ingest the dev dataset, load it into raw_dev/ledger_dev, dbt build, print KPI-01
	$(WITH_ENV) uv run northstar load-run --run-id $(RUN_ID) --raw-schema raw_dev --ledger-schema ledger_dev
	$(MAKE) dbt-build-dataset
	$(WITH_ENV) uv run northstar ledger-summary --dbt-schema dbt_dataset

dbt-build-dataset: | .env ## dbt build over raw_dev/ledger_dev into dbt_dataset (needs make up)
	$(WITH_ENV) export NBI_DBT_SCHEMA=dbt_dataset && cd warehouse/dbt && uv run dbt build --vars $(DATASET_VARS)

publish: | .env ## Publish every reporting date of the dev dataset as versioned snapshots (after simulate-load)
	$(WITH_ENV) uv run northstar publish --dbt-schema dbt_dataset --published-schema published_dev

benchmark: | .env ## Load-test benchmark at config/simulation/datasets/load.yaml; writes docs/performance/load-test.md (needs make up; minutes)
	$(WITH_ENV) uv run python scripts/benchmark.py --dataset config/simulation/datasets/load.yaml --write-doc docs/performance/load-test.md
