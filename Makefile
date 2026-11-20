# Developer interface. Every target runs tools pinned in uv.lock.
.DEFAULT_GOAL := help
# The Airflow image is built with the code version it records (W4.3).
export NBI_CODE_VERSION ?= $(shell git rev-parse HEAD 2>/dev/null)$(shell git status --porcelain --untracked-files=no 2>/dev/null | grep -q . && echo -dirty)
COMPOSE := docker compose --env-file .env --file infrastructure/docker/compose.yaml
WITH_ENV := set -a && . ./.env && set +a &&
TF_BOOTSTRAP := infrastructure/terraform/bootstrap
TF_DATA := infrastructure/terraform/data
TF_BATCH := infrastructure/terraform/batch
TF_STATE_BUCKET = northstar-tfstate-$$(aws sts get-caller-identity --query Account --output text)-ca-central-1
DEV_DATASET := data/generated/dev/seed=42
# One run ID per make invocation, shared by ingest and load (override with RUN_ID=...).
ifndef RUN_ID
RUN_ID := dev-$(shell date -u +%Y%m%dT%H%M%SZ)
endif
DATASET_VARS := '{"raw_schema": "raw_dev", "ledger_schema": "ledger_dev", "recon_schema": "recon_dev", "published_controls_schema": "published_controls_dev"}'
# Redshift targets: temporary AWS credentials from `aws login`, and the warehouse's
# endpoint and bucket from the Terraform data stack (scripts/redshift_env.py).
REDSHIFT_ENV := eval "$$(aws configure export-credentials --format env)" && eval "$$(uv run python scripts/redshift_env.py)" &&
.PHONY: help setup lint format typecheck test test-integration test-redshift redshift-bootstrap dbt-build-redshift compare-warehouses dbt-build-full compare-incremental parity check dbt-parse dbt-build sql-lint env up down ps logs reset e2e simulate ingest simulate-load dbt-build-dataset reconcile publish serving dashboard-check benchmark tf-validate tf-init-bootstrap tf-plan-bootstrap tf-init-data tf-plan-data tf-init-batch tf-plan-batch batch-image

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

test-redshift: ## Loading, append-only, and dbt-check controls on the live Redshift workgroup (needs aws login; costs cents)
	$(REDSHIFT_ENV) PATH="$$PWD/.venv/bin:$$PATH" uv run pytest -m redshift

redshift-bootstrap: ## As the Redshift admin: create the dev dataset's append-only tables and grant the pipeline (needs aws login)
	$(REDSHIFT_ENV) uv run northstar --warehouse redshift redshift-bootstrap \
		--raw-schema raw_dev --ledger-schema ledger_dev --recon-schema recon_dev

dbt-build-redshift: ## dbt build over raw_dev/ledger_dev on Redshift into dbt_dataset (needs aws login; costs compute)
	$(REDSHIFT_ENV) cd warehouse/dbt && NBI_DBT_TARGET=redshift NBI_DBT_SCHEMA=dbt_dataset \
		uv run dbt build --vars $(DATASET_VARS)

compare-warehouses: | .env ## Compare every dbt_dataset model between local Postgres and Redshift, row for row
	$(WITH_ENV) $(REDSHIFT_ENV) uv run python scripts/compare_warehouses.py --schema dbt_dataset

parity: simulate | .env ## 4F-c gate: the same pipeline from nothing on Postgres and Redshift, then compared (needs make up and aws login; costs compute)
	$(WITH_ENV) scripts/parity.sh postgres
	$(WITH_ENV) $(REDSHIFT_ENV) scripts/parity.sh redshift
	$(WITH_ENV) $(REDSHIFT_ENV) scripts/parity.sh compare

check: lint typecheck test ## Everything CI runs on Python code

dbt-parse: ## Parse the dbt project for both targets, local Postgres and Redshift (no database needed)
	$(WITH_ENV) cd warehouse/dbt && uv run dbt parse
	$(WITH_ENV) cd warehouse/dbt && NBI_DBT_TARGET=redshift uv run dbt parse

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

tf-validate: ## Terraform format check and validation of every stack (no AWS access)
	terraform -chdir=$(TF_BOOTSTRAP) fmt -check -recursive
	terraform -chdir=$(TF_BOOTSTRAP) init -backend=false -input=false
	terraform -chdir=$(TF_BOOTSTRAP) validate
	terraform -chdir=$(TF_DATA) fmt -check -recursive
	terraform -chdir=$(TF_DATA) init -backend=false -input=false
	terraform -chdir=$(TF_DATA) validate
	terraform -chdir=$(TF_BATCH) fmt -check -recursive
	terraform -chdir=$(TF_BATCH) init -backend=false -input=false
	terraform -chdir=$(TF_BATCH) validate

tf-init-bootstrap: ## Initialise the bootstrap stack against its S3 state (needs AWS login)
	$(WITH_ENV) terraform -chdir=$(TF_BOOTSTRAP) init -input=false -backend-config="bucket=$(TF_STATE_BUCKET)"

tf-plan-bootstrap: tf-init-bootstrap ## Plan the AWS bootstrap; never applies (needs AWS login)
	$(WITH_ENV) terraform -chdir=$(TF_BOOTSTRAP) plan -input=false -out=bootstrap.tfplan

tf-init-data: ## Initialise the data stack (S3 bronze, Redshift Serverless) against its S3 state (needs AWS login)
	$(WITH_ENV) terraform -chdir=$(TF_DATA) init -input=false -backend-config="bucket=$(TF_STATE_BUCKET)"

tf-plan-data: tf-init-data ## Plan the data stack; never applies (needs AWS login, TF_VAR_operator_cidrs and TF_VAR_budget_alert_email in .env)
	$(WITH_ENV) TF_VAR_alert_email="$$TF_VAR_budget_alert_email" terraform -chdir=$(TF_DATA) plan -input=false -out=data.tfplan

tf-init-batch: ## Initialise the batch stack (ECR, ECS, Step Functions, Scheduler) against its S3 state (needs AWS login)
	$(WITH_ENV) terraform -chdir=$(TF_BATCH) init -input=false -backend-config="bucket=$(TF_STATE_BUCKET)"

tf-plan-batch: tf-init-batch ## Plan the batch stack for the image of HEAD; never applies (needs AWS login and the data stack's alerts topic)
	$(WITH_ENV) terraform -chdir=$(TF_BATCH) plan -input=false -var="image_tag=$$(git rev-parse HEAD)" -out=batch.tfplan

batch-image: ## Build the arm64 batch image of HEAD and push it to ECR (needs AWS login, Docker, the applied batch stack, a clean tree)
	@test -z "$$(git status --porcelain --untracked-files=no)" || { echo "commit first: the image tag is the commit" >&2; exit 1; }
	$(eval BATCH_REPO := $(shell terraform -chdir=$(TF_BATCH) output -raw ecr_repository_url))
	aws ecr get-login-password --region ca-central-1 | docker login --username AWS --password-stdin $(firstword $(subst /, ,$(BATCH_REPO)))
	docker build --platform linux/arm64 -f infrastructure/docker/batch/Dockerfile \
		--build-arg NBI_CODE_VERSION=$$(git rev-parse HEAD) -t $(BATCH_REPO):$$(git rev-parse HEAD) .
	docker push $(BATCH_REPO):$$(git rev-parse HEAD)

simulate: ## Generate the dev dataset (seed 42): canonical events, external records, defects
	uv run northstar simulate --dataset config/simulation/datasets/dev.yaml --seed 42 \
		--defects config/simulation/defects/dev.yaml --out data/generated

ingest: | .env ## Validate the dev dataset into the raw layer and write a manifest (needs make up, make simulate)
	$(WITH_ENV) uv run northstar ingest --dataset-dir $(DEV_DATASET) --run-id $(RUN_ID)

simulate-load: ingest ## Ingest the dev dataset, load it into raw_dev/ledger_dev, dbt build, print KPI-01
	$(WITH_ENV) uv run northstar load-run --run-id $(RUN_ID) --raw-schema raw_dev --ledger-schema ledger_dev --recon-schema recon_dev
	$(MAKE) dbt-build-dataset
	$(WITH_ENV) uv run northstar ledger-summary --dbt-schema dbt_dataset

dbt-build-dataset: | .env ## dbt build over raw_dev/ledger_dev into dbt_dataset (needs make up)
	$(WITH_ENV) export NBI_DBT_SCHEMA=dbt_dataset && cd warehouse/dbt && uv run dbt build --vars $(DATASET_VARS)

dbt-build-full: | .env ## Full-refresh dbt build of the same data into dbt_dataset_full, to check incremental builds against
	$(WITH_ENV) export NBI_DBT_SCHEMA=dbt_dataset_full && cd warehouse/dbt && uv run dbt build --full-refresh --vars $(DATASET_VARS)

compare-incremental: | .env ## Compare every incrementally built dbt_dataset model with dbt_dataset_full, row for row
	$(WITH_ENV) uv run python scripts/compare_warehouses.py --schema dbt_dataset \
		--right postgres --right-schema dbt_dataset_full

publish: | .env ## Publish the dev dataset's controls snapshots, build the serving views, then the financial snapshots (after reconcile); exits 2 if a date is blocked
	$(WITH_ENV) uv run northstar publish --stream controls --dbt-schema dbt_dataset --published-schema published_controls_dev
	$(MAKE) serving
	$(WITH_ENV) uv run northstar publish --stream financial --dbt-schema dbt_dataset --published-schema published_dev --recon-schema recon_dev

serving: | .env ## Build the dashboard's serving views over the published controls snapshots (after make publish)
	$(WITH_ENV) export NBI_DBT_SCHEMA=dbt_dataset && cd warehouse/dbt && uv run dbt build --select tag:serving --vars $(DATASET_VARS)

dashboard-check: | .env ## Check every serving view against the published controls snapshots and the marts (FR-M2-09)
	$(WITH_ENV) uv run python scripts/dashboard_check.py --dbt-schema dbt_dataset \
		--published-controls-schema published_controls_dev

benchmark: | .env ## Load-test benchmark at config/simulation/datasets/load.yaml; writes docs/performance/load-test.md (needs make up; minutes)
	$(WITH_ENV) uv run python scripts/benchmark.py --dataset config/simulation/datasets/load.yaml --write-doc docs/performance/load-test.md

reconcile: | .env ## Reconcile the dev dataset (dbt_dataset -> recon_dev) and verify it against its defect manifest
	$(WITH_ENV) uv run northstar reconcile --dbt-schema dbt_dataset --recon-schema recon_dev
	$(WITH_ENV) uv run northstar recon-verify --recon-schema recon_dev --manifest $(DEV_DATASET)/defects/manifest.jsonl
	$(WITH_ENV) export NBI_DBT_SCHEMA=dbt_dataset && cd warehouse/dbt && uv run dbt build --select tag:controls --vars $(DATASET_VARS)
