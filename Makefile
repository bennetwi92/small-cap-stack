.DEFAULT_GOAL := help
VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
RUFF := $(VENV)/bin/ruff
SHELLCHECK := $(VENV)/bin/shellcheck
ACTIONLINT := $(VENV)/bin/actionlint
MYPY := $(VENV)/bin/mypy
PYTEST := $(VENV)/bin/pytest
# The coverage gate, in one place: `make check` and CI's push-to-main run must agree, or the
# local gate stops predicting the remote one. Branch coverage included — line coverage alone
# called portfolio/extract.py 92% while its branch figure was 80%.
COV := --cov --cov-report=term-missing --cov-fail-under=90
# Listed, not globbed. `shellcheck $(wildcard ...)` with a typo'd path expands to nothing and
# exits 0, which is a lint step that lints nothing. `tests/test_deployment.py` checks this list
# against the scripts actually in the tree.
SHELL_FILES := scripts/backup.sh scripts/board.sh scripts/box-job.sh scripts/fetch_fixtures.sh \
	scripts/harvest.sh deploy/setup-swap.sh .claude/hooks/session-setup.sh

# The interpreter `setup` builds the venv from. Bare `python3` is whatever is first on PATH,
# which on a machine with several installed is not necessarily the 3.11 `requires-python` asks for
# — and a venv on the wrong minor resolves a different dependency set than CI and the image do.
# Falls back rather than hard-requiring 3.11: `.claude/hooks/session-setup.sh` runs `make setup` on
# hosted runners where only `python3` exists, and it swallows the failure, so a hard requirement
# would leave a delegated agent silently without a venv and unable to run `make check`.
PYTHON ?= $(shell command -v python3.11 2>/dev/null || command -v python3)

.PHONY: help setup lock lint lint-sh fmt fmt-check typecheck test cov check clean reports strategy fetch-fixtures

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv and install the package + dev tools
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"

lock: ## Re-resolve uv.lock and regenerate requirements.lock from it
	uv lock
	uv export --frozen --no-dev --no-emit-project --format requirements-txt -o requirements.lock

lint: ## Ruff lint
	$(RUFF) check .

lint-sh: ## Shellcheck every script + actionlint every workflow
	$(SHELLCHECK) $(SHELL_FILES)
	PATH="$(CURDIR)/$(VENV)/bin:$$PATH" $(ACTIONLINT)

fmt: ## Ruff format (write)
	$(RUFF) format .

fmt-check: ## Ruff format check (no write)
	$(RUFF) format --check .

typecheck: ## Mypy (strict, package only)
	$(MYPY)

test: ## Pytest (fast — no coverage)
	$(PYTEST)

cov: ## Pytest with the coverage gate (what CI enforces on main)
	$(PYTEST) $(COV)

reports: ## Rebuild docs/reports/index.json from the report markdown (run after adding one)
	$(PY) -m small_cap_stack.reports build

strategy: ## Regenerate research/strategy.md from config.py (run after changing a rule)
	$(PY) -m small_cap_stack.strategy_doc build

fetch-fixtures: ## Pull a sanitized sample dataset into data/fixtures/ (set FIXTURES_URI)
	./scripts/fetch_fixtures.sh

check: lint lint-sh fmt-check typecheck cov ## Run all CI gates locally (do this before pushing)

clean: ## Remove venv and tool caches
	rm -rf $(VENV) .mypy_cache .ruff_cache .pytest_cache .coverage htmlcov
