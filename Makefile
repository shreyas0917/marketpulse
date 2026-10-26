SHELL := bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

IMAGE ?= marketpulse:dev
TOOL := scripts/lib/tool.sh
SHELL_SCRIPTS = $(shell git ls-files '*.sh')

.PHONY: help setup lint test image dashboards up down smoke verify-stack rules-test kind-up kind-down deploy-local load burst drill

help: ## Show available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

setup: ## Install Python deps and check required tools
	uv sync --frozen
	@for t in docker kubectl helm kind k6; do command -v $$t >/dev/null || echo "WARN: $$t not found on PATH"; done

lint: ## ruff, mypy --strict, shellcheck (+ helm lint once the chart exists)
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	$(TOOL) shellcheck -x $(SHELL_SCRIPTS)

test: ## Unit tests with coverage (fails under 80%)
	uv run pytest --cov --cov-report=term-missing --cov-report=xml

image: ## Build the service image
	docker build -t $(IMAGE) .

dashboards: ## Regenerate Grafana dashboard JSON from observability/grafana/generate.py
	uv run python observability/grafana/generate.py

up: ## Start the docker compose stack and wait until every container is healthy
	docker compose up -d --build --wait --wait-timeout 120

down: ## Stop the compose stack and remove containers, networks and volumes
	docker compose down -v --remove-orphans

smoke: ## Smoke tests against the running stack (SMOKE_* env vars select the target)
	uv run pytest tests/smoke -p no:cacheprovider

verify-stack: ## Check Prometheus targets and Grafana dashboard queries via their HTTP APIs
	uv run python scripts/verify_stack.py $(VERIFY_ARGS)

rules-test kind-up kind-down deploy-local load burst drill:
	@echo "target '$@' is added in a later milestone" >&2; exit 1
