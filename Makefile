SHELL := bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

IMAGE ?= marketpulse:dev
TOOL := scripts/lib/tool.sh
SHELL_SCRIPTS = $(shell git ls-files '*.sh')

.PHONY: help setup lint chart-assets chart-lint test image dashboards up down smoke verify-stack rules-test slo-rules kind-up kind-down deploy-local gitops-up load burst drill

help: ## Show available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

setup: ## Install Python deps and check required tools
	uv sync --frozen
	@for t in docker kubectl helm kind k6; do command -v $$t >/dev/null || echo "WARN: $$t not found on PATH"; done

CHART := deploy/helm/marketpulse
KUBECONFORM_SCHEMAS := -schema-location default 	-schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

lint: ## ruff, mypy --strict, shellcheck, helm lint + kubeconform, chart asset sync
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	$(TOOL) shellcheck -x $(SHELL_SCRIPTS)
	$(MAKE) --no-print-directory chart-lint

chart-assets: ## Copy SLO rules and dashboards into the Helm chart (single source: observability/)
	rm -rf $(CHART)/files && mkdir -p $(CHART)/files/rules $(CHART)/files/dashboards
	cp observability/prometheus/rules/*.yml $(CHART)/files/rules/
	cp observability/grafana/dashboards/*.json $(CHART)/files/dashboards/

chart-lint: ## helm lint, kubeconform -strict, and check chart assets match observability/
	diff -r --strip-trailing-cr observability/prometheus/rules $(CHART)/files/rules
	diff -r --strip-trailing-cr observability/grafana/dashboards $(CHART)/files/dashboards
	helm lint --strict $(CHART) -f $(CHART)/values-kind.yaml
	helm lint --strict $(CHART) -f $(CHART)/values-kind.yaml --set api.rollout.enabled=true
	helm template mp $(CHART) -f $(CHART)/values-kind.yaml | kubeconform -strict -summary $(KUBECONFORM_SCHEMAS)
	helm template mp $(CHART) -f $(CHART)/values-kind.yaml --set api.rollout.enabled=true | kubeconform -strict -summary $(KUBECONFORM_SCHEMAS)

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

TARGET ?= compose
smoke: ## Smoke tests: compose stack (default) or kind ingress with TARGET=kind
ifeq ($(TARGET),kind)
	scripts/smoke-kind.sh
else
	uv run pytest tests/smoke -p no:cacheprovider
endif

verify-stack: ## Check Prometheus targets and Grafana dashboard queries via their HTTP APIs
	uv run python scripts/verify_stack.py $(VERIFY_ARGS)

rules-test: ## promtool check + unit tests for all rules, and runbook links
	$(TOOL) promtool check rules observability/prometheus/rules/*.yml
	$(TOOL) promtool test rules observability/prometheus/tests/*.yml
	uv run python scripts/check_runbooks.py

slo-rules: ## Regenerate SLO recording and burn-rate rules from scripts/gen-slo-rules.py
	uv run python scripts/gen-slo-rules.py

LOAD_ENV ?=
load: ## k6 steady quote load (override with LOAD_ENV="-e RATE=50 -e DURATION=10m -e BASE_URL=...")
	k6 run $(LOAD_ENV) load-tests/steady.js

burst: ## k6 market-open burst; set LOAD_ENV="-e PRODUCER_URL=..." to burst the producer too
	k6 run $(LOAD_ENV) load-tests/burst.js

kind-up: ## Create kind cluster with Calico, metrics-server, ingress-nginx, kube-prometheus-stack
	scripts/kind-up.sh

kind-down: ## Delete the kind cluster
	scripts/kind-down.sh

deploy-local: ## Build + kind-load the image and helm upgrade --install onto kind
	scripts/deploy-local.sh

gitops-up: ## Install Argo CD + Argo Rollouts on kind and register the Application (M5)
	scripts/gitops-up.sh

N ?=
drill: ## Run reliability experiment N from spec section 9 on kind (1-6, 8): make drill N=4
	@test -n "$(N)" || { echo "usage: make drill N=<1-6|8>" >&2; exit 2; }
	scripts/drills/run.sh $(N)
