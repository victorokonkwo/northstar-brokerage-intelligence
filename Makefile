# Developer interface. Every target runs tools pinned in uv.lock.
.DEFAULT_GOAL := help
.PHONY: help setup lint format typecheck test check dbt-parse

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
