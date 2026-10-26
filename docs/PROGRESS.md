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

## M3 — SLOs and alerting ✅ (tag `m3-done`)

| Check | Status | Date | Verified with |
|---|---|---|---|
| `make rules-test` passes (`promtool check rules` + `promtool test rules`) | ✅ 37 rules, 3 test files | 2026-10-26 | `make rules-test` |
| Rule tests cover a must-fire and a must-not-fire case for each SLI | ✅ availability, latency, freshness, ingestion lag (+ slow-burn-only and recovery cases, and every gap alert) | 2026-10-26 | `observability/prometheus/tests/*.yml` |
| Every alert has a `runbook_url` pointing to an existing file | ✅ 17/17 (file and anchor) | 2026-10-26 | `uv run python scripts/check_runbooks.py` (part of `make rules-test`) |
| Live: under load, `FAULT_API_ERROR_RATE=0.2` fires `AvailabilityFastBurn` (Alertmanager API); clearing resolves it | ✅ fault on 03:32:32Z → `startsAt` 03:32:47Z (15 s), seen in Alertmanager 03:32:58Z; cleared 03:34:07Z → resolved 04:03:21Z (29 m 14 s, held by the 6h/30m pair) | 2026-10-26 | `scripts/m3-live-check.sh` (50 req/s k6); evidence `docs/experiments/evidence/m3-*.json` |
| Live: stopping the worker fires the stall alert | ✅ worker stopped 04:03:23Z → `IngestionStalled` `startsAt` 04:05:42Z (2 m 19 s; `for: 2m`); worker started 04:05:55Z → resolved 04:06:16Z | 2026-10-26 | `scripts/m3-live-check.sh` |
| SLO overview dashboard provisioned; every query returns data | ✅ after fixing `le` selectors (DECISIONS D17) | 2026-10-26 | `make verify-stack VERIFY_ARGS="--dashboards marketpulse-slo,marketpulse-api,marketpulse-ingestion"` |

Note on the 15 s detection: the stack had been up only ~2 minutes under load, so the 1 h and 6 h
windows contained ~2 minutes of history. With a full hour of clean traffic, 20 % errors need about
4.3 minutes to push the 1 h ratio over 14.4 × 0.1 % (0.2 × t / 60 > 0.0144). Measured, not
modelled, numbers come from the kind drills in M6.

## M4 — Local Kubernetes ✅ (tag `m4-done`)

| Check | Status | Date | Verified with |
|---|---|---|---|
| `helm lint` and `helm template \| kubeconform -strict` pass | ✅ 29 resources valid (30 with the Rollout variant) | 2026-10-26 | `make chart-lint` (part of `make lint`) |
| `make kind-up && make deploy-local` deploys everything; all pods Ready | ✅ 3 nodes Ready, 30/30 pods Ready across all namespaces | 2026-10-26 | `make kind-up` (1098 s, mostly first-time image pulls), `make deploy-local`, `kubectl get pods -A` |
| `make smoke` passes against the kind ingress | ✅ 10 passed | 2026-10-26 | `make smoke TARGET=kind` (API via 127.0.0.1:18080 + Host header; worker/producer via port-forward) |
| Containers non-root with requests and limits | ✅ uid 10001 (redis 999), `runAsNonRoot`, read-only root FS, requests + limits on every container | 2026-10-26 | `scripts/m4-checks.sh security` (`kubectl get pod -o jsonpath`) |
| Pod outside the allowed set cannot reach Redis; API and worker can | ✅ intruder probe timed out (exit 143); positive control with worker labels got `PONG`; API and worker pods `PING` OK | 2026-10-26 | `scripts/m4-checks.sh netpol` |
| Under `make load` the API HPA scales up, and back down after load stops | ✅ 400 req/s: 2→3 replicas 42 s after load start, 6 at 53 s (CPU 351–486m/pod); back to 2 113 s after load stopped | 2026-10-26 | `scripts/m4-checks.sh hpa`; log `docs/experiments/evidence/m4-hpa.txt` |
| Deleting one API pod causes no failed requests in a concurrent k6 run (2+ replicas) | ✅ 1901 requests, 0 failed checks, 0 `http_req_failed` | 2026-10-26 | `scripts/m4-checks.sh pod-delete` (k6 `STRICT=1` threshold); `docs/experiments/evidence/m4-pod-delete-k6-summary.json` |
| kube-prometheus-stack loads the same rules and dashboards | ✅ 6 groups / 37 rules; 4 dashboards listed; every query returns data; 27/27 targets up | 2026-10-26 | `scripts/verify_stack.py --prometheus <pf> --grafana <pf> --dashboards marketpulse-slo,marketpulse-api,marketpulse-ingestion,marketpulse-platform` |

## M5 — GitOps and progressive delivery ✅ (tag `m5-done`)

| Check | Status | Date | Verified with |
|---|---|---|---|
| Argo CD shows the app `Synced` and `Healthy` from GitHub `main` | ✅ `Synced/Healthy` (`deploy/argocd/marketpulse.yaml` → `shreyas0917/marketpulse` `main`, path `deploy/helm/marketpulse`) | 2026-10-26 | `kubectl -n argocd get application marketpulse -o jsonpath='{.status.sync.status}/{.status.health.status}'`. Needed server-side diff (DECISIONS D29): with client-side diff 15 resources stayed `OutOfSync` on API-defaulted fields. |
| Manual `kubectl edit` of a managed resource is reverted (drift detection) | ✅ `kubectl scale deploy/marketpulse-redis --replicas=2` at 07:36:46Z; Argo CD self-heal operation `Succeeded` at 07:40:47Z; `spec.replicas` back to 1, extra pod terminated | 2026-10-26 | `kubectl -n marketpulse scale …` then `kubectl -n argocd get application … -o jsonpath='{.status.operationState}'`; `docs/experiments/evidence/m5-drift.json` |
| A good change pushed to `main` completes all canary steps and is promoted | ✅ `RELEASE_NOTE` change `fa3d726`: pushed 08:39:11Z → 10 % weight 08:39:25Z → analysis 1 `Successful` 08:48:25Z → 50 % → analysis 2 `Successful` 08:57:30Z → 100 % → **promoted 08:57:37Z** (18 m 26 s push → promote), new stable `9884c6f64`, under k6 30 req/s | 2026-10-26 | `scripts/canary.sh good`; `docs/experiments/evidence/canary-good-timeline.jsonl`, `canary-good-result.json`, `canary-good-analysisrun.yaml` |
| A bad change (`FAULT_API_ERROR_RATE=0.2`) pushed to `main` under `make load` fails analysis and is aborted automatically; traffic returns to stable; time from analysis failure to 100 % stable recorded; bad commit reverted | ✅ commit `10d5b1c`: analysis `Failed` 07:54:16Z (canary 5xx ratio ≈ 0.20 > 0.01) → `abort=true`, canary weight 0 at 07:54:20Z — **3.265 s** failure → 100 % stable; stable RS unchanged; revert `c82c426` pushed 07:54:32Z; rollout `Healthy` 07:55:14Z | 2026-10-26 | `scripts/canary.sh bad`; `docs/experiments/evidence/canary-bad-*`; write-up `docs/experiments/3-bad-deploy.md` |
| With no traffic, the canary is **not** promoted (no data counts as failure); reverted afterwards | ✅ commit `8c3481d` with **no load**: canary at 10 % 09:00:34Z; analysis 09:05:34Z → `Failed` 09:06:34Z with **empty results** (`len(result) > 0` false for both metrics) → `abort=true`, weight 0 — 314 ms failure → stable; not promoted; revert `53f3f52` pushed 09:06:42Z; rollout `Healthy` 09:06:52Z | 2026-10-26 | `scripts/canary.sh no-traffic`; `docs/experiments/evidence/canary-no-traffic-*` |

Notes: the first good-canary attempt was aborted by the no-data guard when Docker Desktop stopped
mid-run and the load died (DECISIONS D31); it was reverted and re-run, and only the re-run is
recorded above. An orphaned pre-Rollout `Deployment`/`Service` from M4's helm install was removed
by hand and `scripts/gitops-up.sh` now does this on handover (D30).

## M6 — Local drills

Not started.

## Manual steps for the owner

None yet.
