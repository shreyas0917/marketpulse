SHELL := bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

IMAGE ?= marketpulse:dev
TOOL := scripts/lib/tool.sh
SHELL_SCRIPTS = $(shell git ls-files '*.sh')

.PHONY: help setup lint test image up down smoke rules-test kind-up kind-down deploy-local load burst drill

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

up down smoke rules-test kind-up kind-down deploy-local load burst drill:
	@echo "target '$@' is added in a later milestone" >&2; exit 1
