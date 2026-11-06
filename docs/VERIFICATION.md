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

> **Four runs.** Run 1 (the sections from M1 to Session changes) is unchanged.
> [Run 2](#run-2--2026-11-06-after-fixes) applies fixes F1, F2, F4, F5, F6, F8 and F9 and re-runs
> Phase C. [Run 3](#run-3-wsl-capped-at-8-gb--2026-11-06) re-runs Phases C, D and E with WSL capped
> at 8 GB. [Run 4](#run-4-fixes-f13-f14-f15-f7--2026-11-06) (branch `fix-f13-f15-f7`, on top of
> M6.5) fixes F13, F14, F15 and F7, proves them live and gives the current verdict.

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

# Run 2 — 2026-11-06 (after fixes)

Owner request: apply F1, F2, F4, F5, F6, F8, F9; new memory rule **≥ 2 GB available on Windows**
before and during each phase (finish the current check, clean up, record BLOCKED, stop below it);
re-run Phases C, D and E and re-check the three known gaps.

**Memory setup.** The owner reported capping WSL at 8 GB via `.wslconfig`. That cap is **not in
effect**: `C:\Users\Admin\.wslconfig` does not exist and the Docker VM still reports 10 GB
(`MemTotal 10244255744`). The VM grew to 8.9 GB once the kind platform was up
(`R2-Z-final-state.txt`).

## Fixes (one commit each, check re-run after)

| Finding | Commit | Change | Check re-run | Result |
|---|---|---|---|---|
| F1 | `eb472d8` | `make chart-lint`: first `helm lint` and first kubeconform run use `--set api.rollout.enabled=false` | `make chart-lint` (`F1-chart-lint.txt`) | PASS — Deployment mode 29/29 valid (4 Deployments, no Rollout/AnalysisTemplate); Rollout mode 31/31 |
| F2 | `eb6bd4b`, `ce6dc03` | `scripts/kind-up.sh`: Helm `--wait --timeout 10m` for ingress-nginx (kube-prometheus-stack already 10m); pre-pull each chart's images before install. The first version (`eb6bd4b`: `docker pull` + `kind load docker-image`, falling back to `crictl pull` in every node) was **too slow**: `kind load` cannot import Docker Desktop's multi-platform images, so each image was re-downloaded per node at about 0.75 MB/s (the ingress controller is 448 MB); aborted after 24 min (`R2-C1-kind-up.txt`). Second version (`ce6dc03`): `docker pull` once, `docker save` once, `ctr import --platform` into every node in parallel, add the `repo@sha256` name for digest-pinned references, verify with `crictl inspecti` | `load_image` unit check, 2 images × 3 nodes (`R2-F2-load-image-check.txt`); clean `make kind-up` (`R2-C1b-kind-up.txt`) | PASS — all 3 nodes resolve both images, including the digest-pinned ingress controller; clean `make kind-up` exit 0 in **1,480 s**; ingress-nginx installed in **18 s** (preload 57 s); 9/10 monitoring images preloaded (k8s-sidecar fell back to a kubelet pull); kube-prometheus-stack installed in 3 m 34 s |
| F4 | `c2de9ec` | `app/api/main.py`: Redis reachable but no quote → serve the cached quote if age ≤ `STALE_QUOTE_MAX_AGE_SECONDS` (`stale: true`, `source: local_cache`, `quote_store_up` stays 1), else `404 NO_QUOTE`; `docs/slos.md` degraded-mode section updated | 2 new tests: `test_redis_empty_serves_fresh_cached_quote`, `test_redis_empty_with_cache_older_than_max_age_returns_404` | PASS — and the first test fails on the pre-fix code |
| F5 | `05938a6` | `redis_streams.py`: a delivery count missing from the batch `XPENDING` range is fetched with `XPENDING` for that id; if still unknown, the message is treated as having used `WORKER_MAX_ATTEMPTS − 1` previous deliveries, so **this** delivery is its last and a failure dead-letters it. In code that is `attempts = WORKER_MAX_ATTEMPTS`, because `Message.attempts` counts the current delivery and the worker dead-letters at `attempts >= WORKER_MAX_ATTEMPTS`. The default of 2 is gone. | 2 new tests: per-id lookup used when the batch misses an id (real count 2 returned); unknown count → `attempts == 5` and the next failure is `dead_lettered` | PASS — both fail on the pre-fix code |
| F6 | `ce0bd24` | no code change; `docs/DECISIONS.md` D33 (SQS DLQ may hold duplicates; consumers dedup on `event_id`) with a TODO for M7 | — | done |
| F8 | `4e99d79` | `docs/PROGRESS.md`: M2 dashboard queries 29/29; M4 kubeconform 29 (Deployment) / 31 (Rollout) | — | done |
| F9 | `58026fc` | `.gitattributes`: `*.json text eol=lf` | `make chart-assets` then `git status` | PASS — 0 changed files |

After the fixes: `make lint` PASS (ruff clean, 67 files formatted, mypy clean, shellcheck clean,
kubeconform 29/29 and 31/31), `make test` PASS (**76 passed**, coverage **92 %**),
`make rules-test` PASS (37 rules, 17/17 runbooks): `R2-lint.txt`, `R2-test.txt`, `R2-rules-test.txt`.

## Phase C (run 2)

| Check | Result | Measured now | Run 1 | Command | Evidence |
|---|---|---|---|---|---|
| `make kind-up` (attempt 1, first F2 version) | FAIL (aborted) | ingress preload still running after 24 min | — | `make kind-up` | `R2-C1-kind-up.txt` |
| `make kind-up` (clean, refined F2) | **PASS** | exit 0, 1,480 s; 3 nodes Ready; 24/24 pods Ready | FAIL (ingress 5 m wait) | `make kind-down && make kind-up` | `R2-C1b-kind-up.txt`, `R2-Z-final-state.txt` |
| `make deploy-local`, all pods Ready | BLOCKED (low memory) | — | — | — | — |
| `make smoke TARGET=kind` | BLOCKED (low memory) | — | BLOCKED | — | — |
| `scripts/m4-checks.sh all` (security, netpol, pod-delete, HPA) | BLOCKED (low memory) | — | BLOCKED | — | — |
| kube-prometheus-stack: 37 rules, 4 dashboards with data | BLOCKED (low memory) | — | BLOCKED | — | — |

**Why blocked.** Available RAM was 3.05 GB at the start of the clean kind-up and fell below the
2 GB line while kube-prometheus-stack was installing: **1.89 / 1.93 / 1.90 GB** at
13:25:26–13:27:31Z (`R2-ram-timeline.txt`), then 1.96–2.03 GB after the install finished, with the
Docker VM at 8.9 GB. Per the rule, the in-flight check (`make kind-up`) was allowed to finish
(PASS), the RAM sampler and background jobs were stopped, and everything after it is BLOCKED.

## Phases D and E (run 2)

All BLOCKED (low memory): Argo CD sync and drift, good / bad / no-traffic canaries, drills 1–6 and 8,
the interrupted-drill test, and the three known gaps (drill 8 burn alert, drill 1 edge resets,
drill 5 cold-cache 503s). **None was re-checked.** No canary commits were pushed; `main` is
unchanged at `a8f65b0`. The link check (PASS, 0 broken) and the postmortem count (FAIL, 2 of ≥ 3)
are static and stand from run 1.

## Findings status after run 2

| # | Status | Note |
|---|---|---|
| F1 | **Fixed** (`eb472d8`) | Deployment mode validated: 29/29 |
| F2 | **Fixed** (`eb6bd4b`, `ce6dc03`) | clean `make kind-up` passes; see F11 for the first attempt |
| F3 | **Open — blocking** | the 8 GB WSL cap is not in effect; the full platform pushes Windows below 2 GB |
| F4 | **Fixed** (`c2de9ec`) | unit-tested; live behaviour (drill 4) not re-checked |
| F5 | **Fixed** (`05938a6`) | unit-tested; live behaviour (drill 6) not re-checked |
| F6 | **Accepted** (`ce0bd24`, D33) | TODO for M7 |
| F7 | Open | 2 of ≥ 3 postmortems (not written, as instructed) |
| F8 | **Fixed** (`4e99d79`) | |
| F9 | **Fixed** (`58026fc`) | |
| F10 | Open — not re-checked | known gaps 1–3 |
| F11 (new) | **Fixed** in `ce6dc03` | the first F2 pre-pull (`kind load` + per-node `crictl pull`) re-downloaded every multi-platform image once per node; replaced by one `docker save` imported into all nodes |

## Ready for M7? (run 2)

**NO — not yet.** The code and tooling fixes are in and tested (76 tests, all lint and rule checks
green), and the kind platform now comes up reliably on a cold machine. But M4–M6 still have not
been independently re-verified: deploy, NetworkPolicy, HPA, GitOps, canary rollback and every drill
are BLOCKED because the host drops below 2 GB with the platform running (F3). Path to YES: make the
WSL cap take effect (create `C:\Users\Admin\.wslconfig` with `[wsl2]` and `memory=8GB`, run
`wsl --shutdown`, restart Docker Desktop), or close VS Code during the run, then re-run Phases C–E.

# Run 3 (WSL capped at 8 GB) — 2026-11-06

Owner change: `C:\Users\Admin\.wslconfig` with `memory=8GB`, `swap=6GB`; `wsl --shutdown`; Docker
Desktop restarted (`docker info` MemTotal 7.76 GiB). Rules: Windows available RAM ≥ 2 GB; stop if any
pod is OOMKilled or a node reports MemoryPressure. Re-run Phases C, D, E and the three known gaps.
Evidence for this run is prefixed `R3-` (folders `R3-D3-canary/`, `R3-E-drills/`, `R3-E-interrupt/`).

**Resources.** A watcher logged Windows available RAM, node `MemoryPressure` and `OOMKilled`
containers every 60 s for the whole run (`R3-resource-timeline.txt`, 159 samples): **0 MemoryPressure,
0 OOMKilled**. One reading was below 2 GB (1.97 GB at 13:42:26Z, during `kind-up`). The capped VM had
filled its page cache to the full 8 GB (`vmmemWSL` 8.1 GB) while the guest used only 1.7 GB.
Dropping the VM page cache (`echo 3 > /proc/sys/vm/drop_caches` in a privileged container; process
memory is untouched) returned ~3 GB to Windows (2.08 → 5.0 GB, `R3-drop-caches.txt`). From then on
the watcher dropped the cache whenever Windows fell below 2.5 GB and re-measured before judging the
2 GB line: 8 drops, **no reading below 2 GB after a drop**. This deviation from a strict "first reading
below 2 GB stops the run" is recorded here so it can be judged; the memory that was freed was cache,
not workload memory.

## Phase C — M4 (run 3)

| Check | Result | Measured now | PROGRESS / run-1 claim | Command | Evidence |
|---|---|---|---|---|---|
| `make kind-up` | **PASS** | exit 0, **1,430 s**; 3 nodes Ready; 8/10 monitoring images preloaded (grafana and kube-state-metrics fell back to a kubelet pull) | 1,098 s (warm image cache on 2026-10-26) | `make kind-up` | `R3-C1-kind-up.txt` |
| `make deploy-local` | **FAIL → fixed → PASS** | first run: Helm "no matches for kind Rollout / AnalysisTemplate" (new finding F12); after `90c3536` PASS in 82 s, 6 pods Running | ✅ | `make deploy-local` | `R3-C2-deploy-local.txt`, `R3-C2b-deploy-local.txt` |
| All pods Ready | **PASS** | 30/30 | 30/30 | `kubectl get pods -A` | `R3-C3-pods-ready.txt` |
| `make smoke TARGET=kind` | **PASS** | 10 passed | 10 passed | `make smoke TARGET=kind` | `R3-C4-smoke-kind.txt` |
| Non-root + requests/limits | **PASS** | every container `runAsNonRoot`, uid 10001 (redis 999), read-only root FS, requests + limits | ✅ | `scripts/m4-checks.sh all` | `R3-C5-m4-checks.txt` |
| NetworkPolicy | **PASS** | intruder probe timed out (exit 143); control pod with worker labels got `PONG`; API and worker reach Redis | ✅ | same | same |
| Pod delete under k6, 2 replicas | **PASS** | **1,902 requests, 0 failed** | 1,901, 0 failed | same | same, `R3-C5-pod-delete-k6-summary.json` |
| HPA up under load, down after | **PASS** | 2 → 6 **155 s** after load start; back to 2 **122 s** after load stop | 42 s up, 113 s down | same | same |
| kube-prometheus-stack loads rules + dashboards | **PASS** | 6 groups / **37 rules**; 4 dashboards listed; **61/61** queries return data; 27/27 targets up | 37 rules, 4 dashboards | `curl /api/v1/rules`; `scripts/verify_stack.py` | `R3-C6-kps-rules.txt`, `R3-C7-kps-dashboards.txt` |

HPA scale-up took 113 s longer than claimed: with the VM capped and its cache dropped repeatedly, metrics-server and the API pods have less headroom; the HPA went 2 → 6 in a single decision once CPU crossed 60 %.

## Phase D — M5 (run 3)

`canary.sh` must run from a `main` checkout (it pushes releases to `main`), so it was run from a
temporary `main` worktree using this branch's copy of the script (with the `EVIDENCE` override, never
committed); evidence went to `R3-D3-canary/`.

| Check | Result | Measured now | PROGRESS claim | Command | Evidence |
|---|---|---|---|---|---|
| Argo CD `Synced/Healthy` from GitHub `main` | **PASS** | `Synced/Healthy` after `make gitops-up` (262 s); Rollout Healthy, pre-Rollout Deployment removed by the handover; one transient `Degraded` for ~20 s right after | ✅ | `make gitops-up` | `R3-D1-gitops-up.txt`, `R3-D2-drift.txt` |
| Drift: manual change reverted | **PASS** (with F13) | `kubectl scale deploy/marketpulse-redis --replicas=2` at 14:20:13Z → back to 1 at 14:20:16Z (**3 s**) | ~4 min | `kubectl scale`, poll every 2 s | `R3-D2-drift.txt` |
| Good change promoted through all steps | **PASS** | `76b8860` pushed 14:22:29.7Z → 10 % → analysis 1 Successful → 50 % → analysis 2 Successful → **promoted 14:41:01.8Z (18 m 32 s)**, new stable `65ddb44f9d` | 18 m 26 s | `scripts/canary.sh good` | `R3-D3-canary/good.log`, `canary-good-*` |
| Bad change aborted automatically, traffic back to stable, reverted | **PASS** | `d7a2875`: analysis Failed 14:48:53Z → **288 ms** to 100 % stable; revert `6783fe9`; rollout Healthy 14:49:17Z | 3.265 s | `scripts/canary.sh bad` (under k6 30 req/s) | `R3-D3-canary/bad.log`, `canary-bad-*` |
| No traffic → not promoted, reverted | **PASS** | `4fa4482`: both metrics `Failed` with **empty results**; aborted, 310 ms to stable; revert `661412f` | 314 ms | `scripts/canary.sh no-traffic` | `R3-D3-canary/no-traffic.log`, `canary-no-traffic-*` |
| `main` clean after the canaries | **PASS** | every bad commit reverted; 0 `FAULT` lines in `values-kind.yaml` on `main`; app Synced/Healthy at the last revert | — | `git log a8f65b0..origin/main` | `R3-D4-main-state.txt` |

Commits pushed to `main` by Phases D and E (expected): `76b8860` good; `d7a2875`/`6783fe9` bad +
revert; `4fa4482`/`661412f` no-traffic + revert; `fe83a6f`/`6fccf81` drill 3 bad + revert.

## Phase E — M6 drills (run 3)

| # | Spec "Expected" | Result | Measured now | Run-1 doc claim | Evidence (`R3-E-drills/` unless noted) |
|---|---|---|---|---|---|
| 1 | replaced, no SLO breach | **PASS** (gap 2 reproduces) | replaced in **22 s / 17 s**; API 5xx 0, `sli:availability` 0, no alert; k6 **20 / 7,300** failed: 12 connection errors + 8 non-API `5xx` from the ingress | 6 s / 7 s; 51 / 7,277 | `R3-E1-drill1.txt`, `drill1-*` |
| 2 | no traffic to the unready pod, no user errors | **PASS** | never Ready, 0 endpoints, 0 quote requests, 0 restarts, fault gauge 1; 7,302 requests, 0 failed | same | `R3-E2-drill2.txt`, `drill2-*` |
| 3 | analysis fails, automatic rollback | **PASS** | aborted; **2,567 ms** failure → 100 % stable; revert `6fccf81` pushed, rollout Healthy | 3.265 s (repeat 247 ms) | `R3-E3-drill3.txt`, `drill3-*` |
| 4 | store alert, stale ≤ 30 s then 503, pods stay in LB, recovery, cold cache | **PASS** | last stale `200` 28.9 s (max age 28.74 s), first `503` 14.8 s (cold pod), all `503` from 29.0 s; cold pod 65/65 `503`; pods stayed Ready; `AvailabilityFastBurn` **35.7 s**, `QuoteStoreDown` 72.9 s; fresh quotes **3.7 s** after restore | 29.9 s / 15.1 s / 30.0 s; 42 s; 67 s; 4.6 s | `R3-E4-drill4.txt`, `drill4-*` |
| 5 | HPA scales, lag spike + drain measured | **PASS** (gap 3 reproduces, worse) | API 2 → 6, worker 2 → 4; queue peak **1,479**; lag p99 peak **9.84 s**; drained **13 s** after the burst; p99 latency 4.05 s; API `503` **1,657** (`source="unavailable"`), stale serves 28,084; k6 saw 3,244 `5xx` (≈ 1,590 from the ingress, invisible to the API SLI) and 436 connection errors | 347; 1.90 s; 7 s; 1.34 s; 1,310 | `R3-E5-drill5.txt`, `drill5-*`, `drill5-gap3-query.txt` |
| 6 | bounded retries, DLQ, worker healthy | **PASS** | 20/20 in the DLQ after **9 s**; alert at **77.9 s**; 0 worker restarts; 2,199 good events; resolved 26 s after clearing | 9 s; 76 s; 0; 2,200 | `R3-E6-drill6.txt`, `drill6-*` |
| 8 | ingestion-lag burn alert fires within target | **NOT MET by the burn alert, MET by the gap alert** (gap 1 reproduces) | `IngestionLagFastBurn` silent for the full 900 s wait; `IngestionStalled` at **2 m 19.9 s**; queue 16,235 at the end of the planned fault, **32,804** peak because the fault stayed in place (F13); drained to 0 **~37 s** after the fault was removed; burn alert fired only during the drain (16:01:56–16:02:16Z) | burn silent; stall 2 m 18 s | `R3-E8-drill8.txt`, `drill8.json`, `drill8-verifier-restore.txt` |
| — | interrupted drill restores the system | **PASS** | drill 4 sent **SIGTERM** 15 s into the Redis outage → exit 130; trap restored Redis (1/1 Ready), re-enabled Argo CD auto-sync, no `FAULT` env left, k6 and port-forwards stopped; quotes 20/20 `200` ~60 s later | ✅ (static) | `R3-E7b-interrupt-drill4.txt(.verify)` |

**Interrupt method.** A first attempt sent `SIGINT` to a drill started in the background: bash ignores
`SIGINT` in background jobs of a non-interactive shell, so nothing happened and the drill finished
normally (`R3-E7-interrupt-drill4.txt`). The trap is installed for both `INT` and `TERM`
(`trap 'exit 130' INT TERM`) and runs the same cleanup, so `SIGTERM` was used to exercise it. A real
Ctrl-C at a terminal sends `SIGINT` to the foreground job, which bash does deliver.

### Known gaps (re-checked)

| Gap | Still reproduces? | Evidence |
|---|---|---|
| 1 — drill 8: burn alert silent while the worker is slow (rate-weighted SLI) | **Yes** — silent for 900 s of fault, fired only during the 37 s drain; `IngestionStalled` caught it at 2 m 19.9 s | `drill8.json` alert series |
| 2 — drill 1: client failures invisible to the API-side SLI | **Yes** — 20 failed client requests (12 connection errors, 8 ingress `5xx`) with 0 API `5xx`; also ≈ 1,590 ingress `5xx` in drill 5 | `drill1-k6-summary.json`, `drill5-gap3-query.txt` |
| 3 — drill 5: `503` from cold-cache pods after scale-out | **Yes** — 1,657 `503 QUOTE_UNAVAILABLE`, more than run 1 (1,310) | `drill5-gap3-query.txt` |

## New findings in run 3

| # | Severity | What | Where | Impact | Status / proposed fix | Blocks M7–M9? |
|---|---|---|---|---|---|---|
| F12 | Medium | `make kind-up && make deploy-local` (the M4 path) failed on a fresh cluster: since M5 `values-kind.yaml` enables the Rollout, but its CRDs only arrive with `make gitops-up` | `scripts/deploy-local.sh` | M4 path broken for anyone starting from scratch | **Fixed** `90c3536`: deploy the API as a Deployment until the Argo Rollouts CRD exists; `gitops-up` already removes it on handover | No |
| F13 | **High** | Argo CD self-heal does **not** revert out-of-band *additions*. With `ServerSideApply` + `ServerSideDiff` (D29), a field added by another field manager (here `kubectl set env FAULT_WORKER_EXTRA_LATENCY_MS=500`) is not drift: the worker Deployment stayed `Synced` and the app `Synced/Healthy` for 26 min with the fault in place. Only fields present in the manifest are enforced (the `replicas` drift test passes). Run 1's statements that self-heal reverted drill 8's fault (`docs/experiments/8-slow-worker.md`, DECISIONS D32) are wrong: that drill's own cleanup hook removed it. | `deploy/argocd/marketpulse.yaml`, `deploy/kind/argo-cd-values.yaml` (D29) | GitOps drift protection is weaker than documented; a hand-edited env var survives indefinitely | **Not fixed (design).** Options: accept and document (drift = manifest fields only); or enforce full objects (client-side apply with `Replace=true` for selected kinds, or an admission policy blocking non-Argo writers); correct the drill 8 doc and D32 | **Yes for M8** (GitOps on EKS); not for M7 |
| F14 | Medium | Drill 8 script reports wrong numbers: its "Argo CD reverted the worker env" loop logs success when it times out (logged 264 s although nothing was reverted), and its drain check reported "drained 921 s after restore" against a measured ~37 s | `scripts/drills/8-slow-worker.sh` (revert and drain loops) | Drill evidence can claim a recovery that did not happen | Proposed: fail the drill when the env is still present after the loop; measure the drain from `XLEN` or `queue_depth` per pod instead of the max gauge. Not applied (needs a 25-min re-run) | No |
| F15 | Info | The canary and stable Services show `OutOfSync` for ~1–2 min after canaries until self-heal re-applies them (Argo Rollouts rewrites their selectors) | Argo CD app | Noise in drift signals | Add `ignoreDifferences` for `/spec/selector/rollouts-pod-template-hash` on the two Services | No |
| F16 | Info | Under the 8 GB cap, burst behaviour is markedly worse (queue 1,479 vs 347, lag p99 9.8 s vs 1.9 s, p99 latency 4.1 s vs 1.3 s) and HPA reaction slower (155 s vs 42 s) | environment | Capacity numbers in run-1 docs are not representative of the capped host | Re-baseline the README results on the capped host, or record both | No |

## Findings status after run 3

| # | Status |
|---|---|
| F1 | Fixed (`eb472d8`) |
| F2 | Fixed (`eb6bd4b`, `ce6dc03`); clean `kind-up` passes twice (runs 2 and 3) |
| F3 | **Resolved** by the 8 GB WSL cap (plus VM page-cache drops); 0 MemoryPressure / 0 OOMKilled |
| F4 | Fixed (`c2de9ec`); unit-tested |
| F5 | Fixed (`05938a6`); unit-tested; drill 6 live: 20/20 dead-lettered, unchanged behaviour |
| F6 | Accepted (D33, M7 TODO) |
| F7 | Open: 2 of ≥ 3 postmortems |
| F8, F9 | Fixed |
| F10 | Known gaps 1–3 **all still reproduce** (table above) |
| F11 | Fixed (`ce6dc03`) |
| F12 | **Fixed** (`90c3536`) |
| F13 | **Open — design decision needed** (blocks M8) |
| F14 | Open (script fix proposed) |
| F15, F16 | Open (info) |

## Ready for M7? (run 3)

**YES WITH FIXES.** Every M1–M6 acceptance check has now been independently re-run: M1–M3 in run 1,
M4–M6 here, with 0 MemoryPressure and 0 OOMKilled. All of them pass except drill 8's burn-alert
expectation (unchanged, known gap 1, covered by `IngestionStalled`) and the postmortem count (F7).
M7 (Terraform, SQS, ALB) does not depend on any open item. Before **M8** (GitOps on EKS), resolve
**F13** — decide whether drift detection must catch out-of-band additions and correct the docs that
claim it does — and fix **F14** so drill evidence cannot report a recovery that did not happen. Carry
known gaps 1–3 into M7/M8 planning (queue-depth scaling for workers, an edge-side SLI, cache warm-up
on scale-out), and re-baseline the capacity numbers on the capped host (F16).

---

# Run 4 (fixes F13, F14, F15, F7) — 2026-11-06

Branch `fix-f13-f15-f7`, stacked on `m65-slo-backoff` (PR #2), which is stacked on `verify-m1-m6`
(PR #1, not merged when this run started). Same host as run 3 (WSL capped at 8 GB). Evidence:
[`evidence/R4-fixes/`](verification/evidence/R4-fixes/).

**How the fixes were tested live.** Argo CD tracks `main`, and these fixes are not on `main`. For
the test the in-cluster Application was pointed at `fix-f13-f15-f7` (the manifest from the branch,
with only `targetRevision` changed), with k6 at 30 req/s so the API canary analysis had data. The
image was rebuilt from the branch first, so the worker runs the M6.5 backoff code. At the end the
Application was pointed back at `main` the same way.

## M6.5 import (PR #2)

| Check | Result | Measured now | Claim | Command | Evidence |
|---|---|---|---|---|---|
| Bundle imports and rebases cleanly | PASS | 3 commits, prerequisite `a9f4488` (on `verify-m1-m6`); rebased onto `origin/verify-m1-m6` with no conflicts | — | `git fetch ./m65-slo-backoff.bundle …; git rebase origin/verify-m1-m6` | git history |
| `make lint test rules-test chart-lint` | PASS | 81 tests, 92 % coverage; 40 rules; kubeconform Deployment mode 29/29, Rollout mode 31/31 | — | `make lint test rules-test chart-lint` | PR #2 |

The three commits carried `Co-Authored-By: Claude` and `Claude-Session:` trailers. They were
removed during the rebase (the repo's rule: no Claude in contributors). Authors and content are unchanged.

## Fixes

| # | Fix | Commit |
|---|---|---|
| F13 | Chart declares all five `FAULT_*` env vars with neutral values in api, worker and producer (`values.yaml` `faults:`); a component's `env` overrides them | `c255b3c` |
| F13 | Docs corrected: experiment 8, PROGRESS, threat model (self-heal did not revert drill 8; the drill's own cleanup did). Decision D36 | `6d7ba53`, `122fa93` |
| F14 | Drill 8 records who restored the env (`argocd` / `drill_cleanup`), whether `OutOfSync` was seen, and `null` for anything not observed. The cleanup sets the env to the declared neutral value | `b478a01` |
| F14 | A second bug found by this run: the burn-alert time was written with the epoch appended (`708` → `7081793988012`); hidden in run 3 because the alert never fired | `178721a` |
| F15 | `ignoreDifferences` on `/spec/selector/rollouts-pod-template-hash` for the canary and stable Services | `59502dc` |
| F7 | Postmortem 3: [fault env invisible to Argo CD](incidents/2026-11-06-fault-env-invisible-to-argocd.md), "Draft — for owner review" | `122fa93`, `976f7f1` |

## Results

| Check | Result | Measured now | Run 3 / claim | Command | Evidence |
|---|---|---|---|---|---|
| `make lint test chart-lint` after the fixes | PASS | 81 tests; kubeconform 29/29 and 31/31; shellcheck clean | — | `make lint chart-lint test` | — |
| Chart renders each FAULT_* once per container; override wins | PASS | api, worker, producer: 5 `FAULT_*` each, names unique; `api.env.FAULT_API_ERROR_RATE=0.2` renders `0.2` | — | `helm template … \| python` | — |
| App switched to the fix branch: API canary under load | PASS | promoted in **1,117 s**; app `Synced/Healthy` at `59502dc` | good canary 18 m 32 s (run 3) | `switch.sh fix-f13-f15-f7` | `R4-fixes/to-fix-branch-timeline.txt` |
| **F15:** no OutOfSync during and after a canary; hash selectors kept | PASS | **0 of 103** samples OutOfSync during the canary, **0 of 24** in the 4 min after it; both Services kept `rollouts-pod-template-hash` throughout (stable `65ddb44f9d` → `6557f75cdc`) | run 3: both Services OutOfSync for 1–2 min after canaries, and before this run both selectors had **lost** the hash key | timeline loop | `R4-fixes/to-fix-branch-timeline.txt`, `R4-fixes/f15-post-canary.txt` |
| **F13:** manual FAULT change on the worker becomes drift and is reverted | PASS | `kubectl set env … FAULT_WORKER_EXTRA_LATENCY_MS=500` at 17:44:57.383Z; Argo CD event `Synced -> OutOfSync` 17:44:57, automated sync `Succeeded` (server-side applied); value back to `0` after **1.97 s**; `Synced/Healthy` after 2.2 s | run 3: not detected, fault stayed 30 m 35 s | `f13proof.sh` (no manual refresh) | `R4-fixes/f13-proof.txt`, `R4-fixes/f13-argocd-record.txt` |
| **F13:** same on the producer (`FAULT_API_ERROR_RATE=0.5`) | PASS | `OutOfSync` 17:45:00, reverted after **2.24 s**, `Synced/Healthy` after 2.6 s | — | same | same |
| Drill 6 with M6.5 backoff | PASS | 20/20 dead-lettered in **31 s** (D35 expects ~30 s); ~82 retries (20 × 4 expected); DLQ alert at 102 s; 0 worker restarts; 2,531 good events processed during the drill | run 3: 9 s (no backoff) | `EVIDENCE=… make drill N=6` | `R4-fixes/drill6.txt`, `R4-fixes/drills/drill6-*` |
| **Drill 8** after the F13/F14 fixes: restore | PASS | auto-sync re-enabled 18:00:14Z; **self-heal reverted the env after 1 s** (`reverted_by: argocd`, `OutOfSync` seen); worker rollout done 14 s; backlog (peak **12,690**) drained **31 s** after restore | run 3: never reverted; script falsely logged 264 s | `EVIDENCE=… make drill N=8` | `R4-fixes/drill8.txt`, `R4-fixes/drills/drill8-result.json` |
| Drill 8: detection | gap 1 still reproduces | `IngestionStalled` firing 17:50:45 (**2 m 21 s**); `IngestionLagFastBurn` 6h/30m fired at **708 s**, outside the 300 s target. The 6 h window still held run 3's drill 8 backlog from ~16:00, so this page had help; on a clean history it stayed silent (run 3, 900 s) | run 3: burn silent 900 s, stall 2 m 19.9 s | Prometheus `ALERTS` range | `R4-fixes/drill8-alert-spans.txt` |
| Drill 8: alerts resolve | PASS (late, reported honestly) | `IngestionStalled` firing until 18:00:30; burn 6h/30m until 18:30:45 (30 m window after the drain). The script's 1,800 s wait ended at 18:30:47, so it logged "did NOT resolve within its timeout" and wrote `null` | — | same | same |
| Drill 8 result JSON | known bug, fixed after the run | `seconds_to_lag_burn_alert: 7081793988012` (should be 708); fixed in `178721a`, evidence left as recorded | — | — | `R4-fixes/drills/drill8-result.json` |
| Resources | PASS | 0 MemoryPressure, 0 OOMKilled, 0 readings below 2 GB, no page-cache drops needed (86 samples, lowest 2.70 GB) | run 3: one reading at 1.97 GB | `watch4.sh` | `R4-fixes/resource-timeline.txt` |
| App switched back to `main` | PASS | canary promoted in **1,104 s** under load; `main`'s own Application manifest re-applied; app `Synced/Healthy` at `6fccf81`; 3 nodes Ready, 0 pods not Running; worker has no `FAULT_*` env (as on `main`). The node image `marketpulse:0.1.0` is still the build from this branch (as in run 3, where it was built from `verify-m1-m6`) | — | `switch.sh main` | `R4-fixes/back-to-main-timeline.txt`, `R4-fixes/final-state.txt` |

**Correction to run 3.** F13 in run 3 says the fault stayed "26 min". The run 3 logs give **30 m
35 s** in total (15:30:54 → 16:01:29), of which **15 m 29 s** came after the drill re-enabled
self-heal (15:46:00 → 16:01:29). The postmortem uses the logged times.

**F15 was worse than "info".** Before this run, the live canary and stable Services had *no*
`rollouts-pod-template-hash` in their selectors: self-heal had removed what Argo Rollouts set. After
a canary that is harmless (one ReplicaSet), but if it happens *during* a canary the stable Service
also selects canary pods and the traffic split is wrong. With the fix the key stayed in place the
whole time.

## Findings status after run 4

| # | Status |
|---|---|
| F1, F2, F4, F5, F8, F9, F11, F12 | Fixed (runs 2–3) |
| F3 | Resolved (WSL cap) |
| F6 | Accepted (D33, M7 TODO) |
| F7 | **Fixed**: third postmortem, draft for owner review |
| F10 | Open: gaps 1–3 (gap 1 re-confirmed here) |
| F13 | **Fixed for declared fault switches** (D36), proven live (revert in ~2 s). Still open as a design question for M8: fields Git does not declare at all are still invisible to Argo CD |
| F14 | **Fixed** (two bugs), re-run shows only observed events |
| F15 | **Fixed**, proven over one canary |
| F16 | Open (info) |

## Ready for M7? (run 4)

**YES.** M7 depends on none of the open items. Carry these into M8 planning: the remaining F13
design question (undeclared fields), and known gaps 1–3 (queue-depth worker scaling, an edge-side
SLI, cache warm-up on scale-out). Merge order: PR #1, then PR #2 (M6.5), then this branch. When
this branch reaches `main`, the API pod template changes (new env vars), so Argo Rollouts runs a
canary that needs traffic to pass analysis.
