# Build progress

Tracks every milestone and acceptance check from [SPEC.md](SPEC.md) section 15.
✅ = verified on the build machine with the listed command. ❌ = failed or not yet run.
Machine: Windows 11, Docker Desktop 29.6.1 (8 CPU, 10 GB), Git Bash.

## M1 — Application ✅ (tag `m1-done`)

| Check | Status | Date | Verified with |
|---|---|---|---|
| `make lint` passes with zero warnings (ruff, mypy `--strict`, shellcheck) | ✅ | 2026-10-26 | `make lint` |
| `make test` passes; line coverage of `app/` ≥ 80% | ✅ 92% | 2026-10-26 | `make test` |
| Unit test: quote from Redis → 200, `stale: false`, cache refreshed | ✅ | 2026-10-26 | `test_quote_served_from_redis_refreshes_cache` |
| Unit test: Redis down, cached entry ≤ 30 s → 200, `stale: true` | ✅ | 2026-10-26 | `test_redis_down_with_fresh_cache_serves_stale` |
| Unit test: Redis down, no entry / entry > 30 s → 503 `QUOTE_UNAVAILABLE` + `Retry-After` | ✅ | 2026-10-26 | `test_redis_down_without_cache_returns_503`, `test_redis_down_with_cache_older_than_max_age_returns_503`, `test_redis_timeout_is_treated_as_unavailable` |
| Unit test: `/health/ready` 200 while Redis is down | ✅ | 2026-10-26 | `test_readiness_ignores_redis` |
| Unit test: duplicate `event_id` processed once | ✅ | 2026-10-26 | `test_duplicate_event_id_processed_once` |
| Unit test: older event does not overwrite newer quote | ✅ | 2026-10-26 | `test_older_event_does_not_overwrite_newer` |
| Unit test: dead-lettered after 5 attempts, counted `dead_lettered` (Redis) | ✅ | 2026-10-26 | `test_poison_dead_lettered_after_five_attempts_redis` |
| Unit test: dead-lettered after 5 attempts, counted `dead_lettered` (SQS via moto) | ✅ | 2026-10-26 | `test_poison_dead_lettered_after_five_attempts_sqs` |
| Unit test: each fault variable changes behaviour and sets `fault_injection_active` | ✅ | 2026-10-26 | `test_api_fault_gauge[*]`, `test_fault_api_error_rate_returns_500`, `test_fault_api_extra_latency_delays_quotes`, `test_fault_readiness_fail`, `test_fault_worker_fail_rate`, `test_fault_worker_extra_latency` |
| Unit test: `/metrics` exposes every metric; buckets `0.25`, `2`, `1` on the right metrics | ✅ | 2026-10-26 | `test_metrics_endpoint_exposes_required_metrics_and_buckets`, `test_worker_metrics_exposed_with_buckets` |
| `docker build` succeeds; container runs non-root | ✅ uid 10001 | 2026-10-26 | `make image && docker run --rm --entrypoint id marketpulse:dev` |
| CI workflow passes on GitHub for pushed `main` | ✅ run 40260646828 | 2026-10-26 | `gh run list --branch main`, `gh run watch` |

## M2 — Local stack ✅ (tag `m2-done`)

| Check | Status | Date | Verified with |
|---|---|---|---|
| `make up` brings every container to healthy within 2 minutes | ✅ 49 s (7/7 healthy) | 2026-10-26 | `time make up` (`docker compose up --wait --wait-timeout 120`). An earlier run took 154 s while an unrelated 7 GB process left the host with 0.8 GB free RAM; re-measured once memory was available. |
| `make smoke` passes (health 200, produced event served within 5 s, `/metrics` reachable) | ✅ 10 passed | 2026-10-26 | `make smoke` |
| Every Prometheus target `up == 1` (Prometheus HTTP API) | ✅ 5/5 targets up | 2026-10-26 | `make verify-stack` (`/api/v1/targets`, `up == 0` query) |
| Grafana API lists API and Ingestion dashboards; their queries return data | ✅ 2 dashboards, 30/30 queries with data | 2026-10-26 | `make verify-stack` (`/api/search`, `/api/dashboards/uid/*`, `/api/v1/query`) |
| `make down` removes everything; second `make up` works | ✅ 0 containers/networks/volumes left; second up healthy, smoke passed | 2026-10-26 | `make down && docker ps -a --filter name=marketpulse-`, `make up && make smoke` |

## M3 — SLOs and alerting

Not started.

## M4 — Local Kubernetes

Not started.

## M5 — GitOps and progressive delivery

Not started.

## M6 — Local drills

Not started.

## Manual steps for the owner

None yet.
