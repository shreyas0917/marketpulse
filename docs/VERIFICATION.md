# MarketPulse M1–M6 independent verification

Branch `verify-m1-m6` from `main` @ `a8f65b0`. Run on 2026-11-06 (UTC) on the build machine:
Windows 11, Docker Desktop 29.6.1 (VM: 8 vCPU, 10 GB), Git Bash. Every result below comes from a
command run in this session; `docs/PROGRESS.md` was treated as a claim to check, never as a source.
Raw output for every row is under [`docs/verification/evidence/`](verification/evidence/): each
`*.txt` file starts with the command and its UTC start time and ends with its end time, exit code
and duration.

**Pre-flight.** `make kind-down` and `make down` from a state where both stacks had auto-restarted
with Docker Desktop (`A0-pre-state.txt`, `A0-clean-state.txt`). Host RAM: 2.4–3.2 GB available at
first (below the 4 GB bar, so the run stopped); after Edge was closed, 7.8 GB, so the full run
(Phases A–E) was started. Disk: D: 88 GB free, C: 13.5 GB free; `docker system df` in
`A0-clean-state.txt`. RAM is re-sampled before each phase (`A0-host-ram.txt`).

Evidence paths below are relative to `docs/verification/evidence/`.

## M1 — Application

| Check | Result | Measured now | PROGRESS.md claim | Command | Evidence |
|---|---|---|---|---|---|
| `make lint` zero warnings (ruff, mypy `--strict`, shellcheck, helm lint + kubeconform) | PASS | ruff "All checks passed", 66 files formatted, mypy "no issues in 23 source files", shellcheck silent, helm lint 0 failed, kubeconform 31/31 valid (both variants) | ✅ | `make lint` | `A2-lint.txt` |
| `make test`, coverage of `app/` ≥ 80 % | PASS | 72 passed, **92.06 %** | ✅ 92 % | `make test` | `A3-test.txt` |
| Required behaviour tests exist and pass (9 behaviours) | PASS | 20 tests covering all 9 behaviours, all in the 72 passed | ✅ | `pytest --collect-only` + `make test` | `A3b-required-tests.txt` |
| `docker build` succeeds; runs as non-root | PASS | `uid=10001(app)`; image `User` 10001:10001; starts with `--read-only` | ✅ uid 10001 | `make image`; `docker run --rm --entrypoint id marketpulse:dev` | `A5-image.txt`, `A6-uid.txt` |
| CI green on latest `main` | PASS | 3 latest runs `completed/success`, newest `a8f65b0` (= `main`) | ✅ (run 40260646828, an older commit) | `gh run list --branch main -L 3` | `A7-ci.txt` |

## M2 — Local stack (Docker Compose)

| Check | Result | Measured now | PROGRESS.md claim | Command | Evidence |
|---|---|---|---|---|---|
| `make up` healthy within 2 min | PASS | **44 s** from clean (7/7 healthy); second run 40 s | ✅ 49 s | `make up` | `B1-make-up.txt`, `B6-second-up.txt` |
| `make smoke` | PASS | 10 passed | ✅ 10 passed | `make smoke` | `B2-smoke.txt` |
| Every Prometheus target `up == 1` | PASS | 5/5 up, 0 series with `up == 0` | ✅ 5/5 | `make verify-stack` | `B3-verify-stack.txt` |
| Grafana lists API + Ingestion dashboards; their queries return data | PASS | API 13/13 + Ingestion 16/16 = **29/29** (SLO overview also 22/22) | ✅ "30/30" | `make verify-stack VERIFY_ARGS="--dashboards marketpulse-slo,marketpulse-api,marketpulse-ingestion"` | `B3-verify-stack.txt` |
| `make down` removes everything; second `make up` works | PASS | 0 containers / 0 networks / 0 volumes after each `make down`; second up 40 s, smoke 10 passed | ✅ | `make down`, `docker ps/network/volume ls --filter`, `make up && make smoke` | `B5-down-clean.txt`, `B6-second-up.txt`, `B7-second-smoke.txt`, `B8-final-down.txt` |

Delta: the claim of 30 dashboard queries is a miscount; the two dashboards have 29 queries (13 + 16)
and all return data.

## M3 — SLOs and alerting

| Check | Result | Measured now | PROGRESS.md claim | Command | Evidence |
|---|---|---|---|---|---|
| `make rules-test` (promtool check + test) | PASS | 3 rule files: 20 + 12 + 5 = **37 rules**; 3 test files `SUCCESS` | ✅ 37 rules | `make rules-test` | `A4-rules-test.txt` |
| Must-fire and must-not-fire case per SLI | PASS | availability 1/2 (+ slow-burn-only), latency 1/1, freshness 1/1, ingestion lag 2/1, plus every gap alert | ✅ | read `observability/prometheus/tests/*.yml` | `A4b-rule-test-cases.txt` |
| Every alert has a `runbook_url` to an existing file | PASS | **17/17** (file and anchor) | ✅ 17/17 | `scripts/check_runbooks.py` (in `make rules-test`) | `A4-rules-test.txt` |
| Live: `FAULT_API_ERROR_RATE=0.2` under load fires `AvailabilityFastBurn`; clearing resolves it | PASS | fault on 11:14:51Z → `startsAt` 11:15:07.9Z (**16.9 s**), in Alertmanager 11:15:21Z; cleared 11:16:29Z → resolved 11:45:50Z (**29 m 21 s**) | 15 s; resolved after 29 m 14 s | `scripts/m3-live-check.sh` (k6 50 req/s) | `B4-m3-live/` |
| Live: stopping the worker fires the stall alert | PASS | stop 11:45:51Z → `IngestionStalled` `startsAt` 11:48:12.5Z (**2 m 21.5 s**, `for: 2m`); restart 11:48:23Z → resolved seen 11:48:53Z (**30 s**) | 2 m 19 s; resolved after 21 s | `scripts/m3-live-check.sh` | `B4-m3-live/` |
| SLO overview dashboard queries return data | PASS | 22/22 | ✅ | `make verify-stack` | `B3-verify-stack.txt` |

Deltas are within one Prometheus evaluation plus the script's 5 s Alertmanager poll. The 16.9 s
detection again comes from a fresh stack with ~2 minutes of history in the 1 h / 6 h windows; it is
not representative of a long-running service (see PROGRESS.md's own note).

## Code review (Phase A, read not run)

| Item | Result | Notes | Evidence |
|---|---|---|---|
| a) API degraded mode | PASS (1 edge case → F4) | Redis read wrapped in `asyncio.wait_for(…, 50 ms)` plus socket timeouts with retries disabled (`app/api/main.py:103-105,184`); cache served only while `now − event_ts ≤ STALE_QUOTE_MAX_AGE_SECONDS` (`app/api/cache.py:33`), else `503 QUOTE_UNAVAILABLE` + `Retry-After` (`main.py:195-200`); `/health/ready` never touches Redis (`main.py:159-164`) | `A9ab-code-review.txt` |
| b) Worker | PASS (2 edge cases → F5, F6) | dedup (`SET NX EX`) and last-write-wins compare are one atomic Lua script (`app/common/store.py`); bounded at `WORKER_MAX_ATTEMPTS=5`; both backends dead-letter through the same `Worker.handle()`; `XAUTOCLAIM` scans from `0-0` so dead consumers' messages are reclaimed; `NOGROUP` → group recreated and the read retried | `A9ab-code-review.txt` |
| c) Buckets and SLI definitions | PASS | buckets contain 0.25 / 2 / 1 (`le="0.25"`, `"2.0"`, `"1.0"` as Prometheus 3 stores them, guarded by `tests/unit/test_slo_buckets.py`); 4 SLIs × 5 windows; every good/total expression matches `docs/slos.md` exactly | `A9c-sli-definitions.txt`, `A3-test.txt` |
| d) Burn-rate math | PASS | 12/12 rules: 1h/5m at 14.4×, 6h/30m at 6× (page), 3d/6h at 1× (ticket); budgets 0.001 (99.9 %) and 0.01 (99 %); thresholds 0.0144 / 0.006 / 0.001 and 0.144 / 0.06 / 0.01; 0 mismatches | `A9d-burn-math.txt` |
| e) Chart copies identical to `observability/` | PASS | committed content identical (empty diff ignoring CR); `make chart-assets` is a no-op once the two CRLF working copies are re-checked out (F9) | `A8-chart-assets-noop.txt` |
| f) Helm hardening | PASS | every container: requests + limits, `runAsNonRoot`, `readOnlyRootFilesystem`, `allowPrivilegeEscalation: false`, `drop: [ALL]`, `RuntimeDefault` seccomp, no SA token; API startup/readiness/liveness, worker/producer/redis readiness/liveness; PDBs `minAvailable: 1` on the two multi-replica workloads; topology spread on API and worker; default-deny ingress + Redis allow-list (api/worker/producer) | `A9f-helm-review.txt` |
| g) Secrets / credentials / personal paths | PASS | `gitleaks git` over full history: no leaks; working-tree scan: 120 hits, **all** in the gitignored `.venv/` (third-party packages), none in tracked files; no personal paths or credential-like strings tracked; `deploy/kind/versions.env` only holds chart versions | `A9g-secrets.txt` |

## M4 — Local Kubernetes (kind)

Host available RAM fell from 7.68 GB at the start of Phase C to **2.63–2.68 GB** while the cluster
came up (Docker VM `vmmemWSL` grew to 7.8 GB; `A0-host-ram.txt`). Per the owner's rule the current
check was finished, and everything after it is BLOCKED (low memory). The kind cluster is left
running for inspection.

| Check | Result | Measured now | PROGRESS.md claim | Command | Evidence |
|---|---|---|---|---|---|
| `helm lint` + `helm template \| kubeconform -strict` | PASS (with F1) | 31/31 valid in **both** variants | 29 (30 with Rollout) | `make lint` (`chart-lint`) | `A2-lint.txt` |
| `make kind-up && make deploy-local`, all pods Ready | **FAIL** (1 attempt) | cluster + Calico + metrics-server up; ingress-nginx controller still `ContainerCreating` (pulling `controller:v1.15.1`) when Helm's 5 m `--wait` expired; kube-prometheus-stack not installed (F2) | ✅ 30/30 pods | `make kind-up` (836 s, exit 2) | `C1-kind-up.txt`, `C1b-kind-up-diagnosis.txt` |
| `make smoke TARGET=kind` | BLOCKED (low memory) | — | ✅ 10 passed | — | — |
| Non-root + requests/limits on live pods | BLOCKED (low memory) | (static equivalent PASS, see f) | ✅ | — | — |
| NetworkPolicy: outsider blocked, API/worker allowed | BLOCKED (low memory) | — | ✅ | — | — |
| HPA scales up under load and back down | BLOCKED (low memory) | — | 42 s up, 113 s down | — | — |
| Pod delete under k6, 0 failed requests | BLOCKED (low memory) | — | 1901 req, 0 failed | — | — |
| kube-prometheus-stack loads 37 rules, 4 dashboards return data | BLOCKED (low memory) | — | ✅ | — | — |

## M5 — GitOps and progressive delivery

| Check | Result | PROGRESS.md claim |
|---|---|---|
| Argo CD `Synced/Healthy` from GitHub `main` | BLOCKED (low memory) | ✅ |
| Drift self-heal | BLOCKED (low memory) | ~4 min |
| Good canary promoted | BLOCKED (low memory) | 18 m 26 s |
| Bad canary aborted + reverted, failure → stable time | BLOCKED (low memory) | 3.265 s |
| No-traffic canary not promoted + reverted | BLOCKED (low memory) | 314 ms |

No canary commits were pushed to `main` in this session; `main` is unchanged at `a8f65b0`.

## M6 — Local drills

| Check | Result | PROGRESS.md claim |
|---|---|---|
| Drills 1, 2, 3, 4, 5, 6, 8 vs SPEC section 9 "Expected" | BLOCKED (low memory) | 6 met, drill 8 not met by the burn alert |
| Interrupted drill restores the system (`trap`) | BLOCKED (low memory) | ✅ (static + drill 8's own abort) |
| Known gap 1 — drill 8 burn alert silent while worker is slow | NOT RE-VERIFIED (blocked) | reproduced 2026-10-26 |
| Known gap 2 — drill 1 connection resets invisible to the SLI | NOT RE-VERIFIED (blocked) | reproduced 2026-10-26 |
| Known gap 3 — drill 5 cold-cache 503s after scale-out | NOT RE-VERIFIED (blocked) | reproduced 2026-10-26 |
| ≥ 3 postmortems (SPEC section 9) | **FAIL** | 2 exist (`docs/incidents/2026-10-26-*.md`); SPEC asks for at least three (M6's own check asks for two) — F7 |
| Link check over README.md and docs/ | PASS | 28 files, 91 local links (incl. anchors), 0 missing targets, 0 bad anchors (`E9-link-check.txt`) |

## Findings

Ranked by severity. None changes design, SLOs, thresholds or app behaviour in this session; the
only commit is the `EVIDENCE` override in three scripts (below).

| # | Severity | What | Where | Impact | Proposed fix | Blocks M7–M9? |
|---|---|---|---|---|---|---|
| F1 | Medium | `chart-lint` no longer validates Deployment mode. `values-kind.yaml` now sets `api.rollout.enabled: true`, so the "plain" kubeconform variant renders the Rollout too (both 31/31). The Deployment rendering is never checked. | `Makefile:38`; `deploy/helm/marketpulse/values-kind.yaml:16-17` | A broken `Deployment` template (the path `make deploy-local` uses before Argo CD, and a likely EKS starting point) would pass CI. | Render the first variant with `--set api.rollout.enabled=false`. Small script fix, not applied because the run stopped on low memory. | No, but fix before M7 adds `values-aws-dev.yaml`. |
| F2 | Medium | `make kind-up` is flaky on a cold machine: ingress-nginx is installed with `--wait --timeout 5m`, and the controller image pull alone took > 5 min, so the script exits 2 with a half-built cluster (no kube-prometheus-stack). | `scripts/kind-up.sh:40-42` | Fresh setups fail; re-running usually succeeds once images are cached. | Use the same `10m` as kube-prometheus-stack, or pre-pull/`kind load` the add-on images. Small fix, not applied (stopped). | No (local only). |
| F3 | Medium | Host capacity: a full kind platform (3 nodes + Calico + monitoring + Argo) needs the Docker VM at ~8 GB, leaving < 3 GB for Windows with VS Code open. The M4–M6 verification could not run. | environment | M4–M6 not independently re-verified. | Raise the WSL memory cap only if the host can spare it, or close VS Code during runs, or run kind with 1 worker for verification. | **Yes, for the verdict:** M4–M6 must be re-verified before relying on them. |
| F4 | Low | Redis up but empty (just restarted, no persistence) returns `404 NO_QUOTE` even when the pod holds a fresh cached quote; degraded mode only triggers on errors/timeouts. | `app/api/main.py:204-205` | Brief 404s after a Redis restart until the producer republishes each symbol. 4xx counts as good, so SLIs don't see it. | On a store miss, fall back to `cache.get_fresh()` (served `stale: true`). **App behaviour: not changed, awaiting decision.** | No. |
| F5 | Low | If a reclaimed message's delivery count is missing from the bounded `XPENDING` range (`count=len(ids)*4+10`), its attempts default to **2** forever, so it is retried without bound and never dead-lettered. | `app/common/queue/redis_streams.py:78,110` | Only with a large pending backlog (hundreds of poison messages); a retry storm instead of DLQ. | Query delivery counts per id (or the exact range) and treat "missing" as an error, not as attempt 2. **App behaviour: not changed.** | No (Redis path; SQS uses `ApproximateReceiveCount`). |
| F6 | Low | SQS dead-lettering is send-then-delete, not atomic: a crash between the two redelivers the message, which is dead-lettered again (duplicate in the DLQ). | `app/common/queue/sqs.py:83-93` | Duplicate DLQ entries; no data loss. Redis path is atomic (`MULTI`). | Accept and document, or add a dedup attribute (original message id) and make the redrive runbook idempotent. Relevant to M7 (SQS). | No, but document in M7. |
| F7 | Low (docs) | SPEC section 9 asks for at least **three** postmortems; two exist. | `docs/incidents/` | Spec gap. | Write a third (drill 8's deferred burn or drill 5's burst are good candidates). Not written here, as instructed. | No. |
| F8 | Info | PROGRESS.md claims that no longer match: M2 "30/30 queries" (29 exist, all pass); M4 "29 resources (30 with Rollout)" (31/31 since M5 enabled the Rollout in `values-kind.yaml`). | `docs/PROGRESS.md:33,56` | Misleading numbers only. | Correct the two cells when M4 is re-verified. | No. |
| F9 | Info | Two dashboard JSON files had CRLF in this Windows working copy (committed content is LF), so `make chart-assets` copied CRLF and `git status` showed changes. | working copy only | Confusing on Windows; no content difference. | Re-checked out with LF in this session; `make chart-assets` is a no-op. Optional: `newline="\n"`-safe copy in the Makefile. | No. |
| F10 | Known, not re-verified | Drill 8 (rate-weighted burn alert silent while worker throttled), drill 1 (edge resets invisible to the SLI), drill 5 (cold-cache 503s after scale-out). | `docs/experiments/{8,1,5}-*.md` | As documented on 2026-10-26. | As in those docs (queue-depth worker scaling, edge SLI, cache pre-warm). | Drill 8 matters for M7 (KEDA/SQS scaling). |

## Ready for M7?

**NO — not yet.** Phases A and B passed cleanly: static quality, unit/rule tests, code review of
the SLO math, degraded mode, worker semantics and Helm hardening, and the full Compose stack
including the live M3 alerts, all re-measured within seconds of the original claims. But M4–M6
(kind, GitOps/canary, drills) could not be re-verified: the host ran out of headroom (F3), and the one
kind check that ran failed on a cold-cache timeout (F2). The canary and rollback behaviour that M7–M8
build on is therefore still only claimed, not independently confirmed.

To get to YES: free memory (or reduce the cluster footprint), apply the two small script fixes
F1 and F2, re-run Phases C–E, and decide F4–F6 before the SQS work in M7.

## Session changes

- `2ab2b53` chore(scripts): `EVIDENCE` override in `scripts/m3-live-check.sh`, `scripts/canary.sh`,
  `scripts/drills/lib.sh` (default unchanged), so verification evidence never overwrites
  `docs/experiments/evidence/`. Re-checked with shellcheck; the M3 live check then wrote only to
  `docs/verification/evidence/B4-m3-live/`.
- This file and `docs/verification/evidence/`.
