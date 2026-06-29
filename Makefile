.DEFAULT_GOAL := help
VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy
PYTEST := $(VENV)/bin/pytest

.PHONY: help setup lint fmt fmt-check typecheck test check clean

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv and install the package + dev tools
	python3 -m venv $(VENV)
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"

lint: ## Ruff lint
	$(RUFF) check .

fmt: ## Ruff format (write)
	$(RUFF) format .

fmt-check: ## Ruff format check (no write)
	$(RUFF) format --check .

typecheck: ## Mypy (strict, package only)
	$(MYPY)

test: ## Pytest + coverage
	$(PYTEST)

check: lint fmt-check typecheck test ## Run all CI gates locally (do this before pushing)

clean: ## Remove venv and tool caches
	rm -rf $(VENV) .mypy_cache .ruff_cache .pytest_cache .coverage htmlcov
