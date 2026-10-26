# Decisions

Choices made where the spec was ambiguous or left room, recorded per spec rule 2.
Format: question → options → choice → reason.

## D1 — Python version

- **Options:** 3.12, 3.13, 3.14.
- **Choice:** Python **3.13** (`requires-python >=3.13`, image `python:3.13-slim-trixie`).
- **Reason:** meets "3.12+", matches the interpreter installed on the build machine (3.13.7), and
  every dependency (including `lupa` for fakeredis Lua support and `moto`) ships wheels for it.

## D2 — Pinned versions (looked up 2026-10-26)

Python packages (exact pins in `pyproject.toml`, full tree in `uv.lock`):

| Package | Version | | Package | Version |
|---|---|---|---|---|
| fastapi | 0.162.0 | | pytest | 9.1.1 |
| uvicorn | 0.54.0 | | pytest-cov | 7.1.0 |
| redis | 8.1.0 | | fakeredis[lua] | 2.40.0 |
| pydantic-settings | 2.16.1 | | moto[sqs] | 5.2.4 |
| prometheus-client | 0.26.0 | | mypy | 2.4.0 |
| boto3 | 1.43.118 | | ruff | 0.17.2 |
| boto3-stubs[sqs] | 1.43.118 | | httpx2 | 2.16.0 |

Container images (tag + digest):

| Image | Digest |
|---|---|
| `python:3.13-slim-trixie` | `sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962` |
| `ghcr.io/astral-sh/uv:0.13.5` | `sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff` |
| `redis:8.10.2-alpine` | `sha256:0bf6577d7774e592b29d093e3ac9390eb91e13d72c21e506afce736de5e0eeb0` |
| `prom/prometheus:v3.15.1` | `sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2` |
| `prom/alertmanager:v0.34.1` | `sha256:e9733bafb1bdef9b00e25a21f8f99dc26a22224bf16641ad754d1649f4c3357a` |
| `grafana/grafana:13.2.4` | `sha256:dbbc38ea21103cefb50396340cd88afec779c7e070b0a7bf5a75b4ce46651d3f` |
| `koalaman/shellcheck:v0.11.0` | `sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d` |

CLI tools on the build machine: uv 0.13.5, kind 0.33.0, kubectl 1.36.1, Helm 4.3.0, k6 2.3.0,
gitleaks 8.30.1, kubeconform 0.8.0, kubectl-argo-rollouts 1.10.1, GNU Make 4.4.1, Docker 29.6.1.
Kubernetes components (kind node image, Calico, ingress-nginx, metrics-server, kube-prometheus-stack,
Argo CD, Argo Rollouts) are recorded when they are added in M4/M5.

## D3 — `httpx2` instead of `httpx` for the test client

- **Options:** keep `httpx` and accept a deprecation warning, or use `httpx2`.
- **Choice:** `httpx2`.
- **Reason:** the current Starlette `TestClient` emits `StarletteDeprecationWarning` with `httpx`
  and recommends `httpx2`. Zero warnings is the bar.

## D4 — `promtool` and `shellcheck` run from pinned container images when not installed

- **Choice:** `scripts/lib/tool.sh` runs the local binary if it is on `PATH`, otherwise the pinned
  image (`prom/prometheus`, `koalaman/shellcheck`).
- **Reason:** the Prometheus Windows release download was corrupt on this machine, and the image
  route gives identical versions locally and in CI without extra installs.

## D5 — `terraform fmt` / `tflint` are not part of `make lint` in this build

- **Reason:** section 0.1 forbids creating Terraform in this run, so there is nothing to lint. Add
  both to `make lint` when `infra/` is created in M7.

## D6 — Redis Streams: acked entries are deleted

- **Options:** keep acked entries and trim with `MAXLEN`, or `XACK` + `XDEL`.
- **Choice:** `XACK` + `XDEL` (and the same on dead-lettering).
- **Reason:** `MAXLEN` trimming can silently drop undelivered events during a burst. Deleting acked
  entries means `XLEN events` is exactly the queue depth (waiting + in flight), and the first entry's
  ID gives the oldest message's enqueue time for `queue_oldest_event_age_seconds`.

## D7 — Who exports the queue gauges

- **Problem:** the stall alert (`queue_oldest_event_age_seconds > 10` for 2 m) must fire when the
  worker is stopped, but a stopped worker exports nothing.
- **Choice:** the worker **and** the producer both export `queue_depth`, `dlq_depth` and
  `queue_oldest_event_age_seconds`; alerts use `max()` across jobs.
- **Reason:** on AWS this gauge comes from CloudWatch (YACE), independent of the worker. Locally the
  producer is the natural independent observer because it already holds a queue connection.

## D8 — Duplicates and out-of-order events

- **Choice:** a duplicate `event_id` or an event older than the stored quote is acked and counted as
  `events_processed_total{result="ok"}`, but records **no** `ingestion_lag_seconds` observation.
- **Reason:** the spec observes lag on events the worker successfully *writes*. These events are
  handled correctly (not a failure), so they are `ok`; they are logged at DEBUG.

## D9 — Events for unknown symbols

- **Choice:** the worker treats them as processing failures, so they are retried and then
  dead-lettered. The API rejects them up front with `400 UNKNOWN_SYMBOL`.
- **Reason:** keeps the store bounded to configured symbols (the local cache is sized by them).

## D10 — Route labels for unmatched paths and warm-up traffic

- **Choice:** unmatched paths are labelled `route="unmatched"`; requests with
  `X-Traffic-Class: warmup` are labelled `route="warmup"` and record no quote age.
- **Reason:** bounded label cardinality, and the spec requires warm-up traffic to be excluded from
  SLIs without hiding it entirely.

## D11 — `GET /v1/quotes/{symbol}` for a configured symbol with no quote yet

- **Choice:** `404 NO_QUOTE`.
- **Reason:** it is not a service fault (nothing has been ingested), and 4xx counts as good for the
  availability SLI, matching the spec's treatment of client errors.

## D12 — `POST /v1/events` publishes to the queue

- **Choice:** the event goes through the queue and the worker, not straight into the store.
- **Reason:** the smoke test ("a produced event appears as a quote within 5 s") then exercises the
  whole ingestion path.

## D13 — Commit attribution

- **Choice:** commits are authored with the owner's git identity only, with no co-author trailers.
- **Reason:** owner's instruction.

## D14 — Host ports for the compose stack

- **Problem:** ports 3000 and 8000 were already taken on the build machine by unrelated projects.
- **Choice:** publish on a dedicated range: API 18000, worker 19100, producer 19200, Prometheus
  19090, Alertmanager 19093, Grafana 13000. Each can be overridden with `MP_*_PORT`.
- **Reason:** avoids conflicts without touching other containers; container ports stay standard.

## D15 — Reserved smoke-test symbol

- **Choice:** `SYMBOLS` includes `SMOKETEST`; `PRODUCER_SYMBOLS` leaves it out.
- **Reason:** the smoke test must prove *its* event became the quote. With a symbol the producer
  never publishes, the returned `event_id` is unambiguous.

## D16 — The producer may reach Redis (NetworkPolicy)

- **Spec:** "only the API and worker may reach Redis".
- **Problem:** locally the queue *is* Redis (Redis Streams), and the producer must publish to it.
- **Options:** (a) route producer events through the API; (b) run a second Redis just for the
  queue; (c) allow the producer on the Redis port.
- **Choice:** (c). The Redis NetworkPolicy allows exactly `api`, `worker` and `producer` pods;
  everything else (verified with an intruder pod) is blocked.
- **Reason:** simplest option that keeps the spec's data flow. On AWS the producer only talks to
  SQS, so the rule "only API and worker reach the store" holds there as written.

## D17 — Histogram `le` selectors use Prometheus 3's normalised form

- **Problem:** the spec writes `le="2"` and `le="1"`. Prometheus 3 normalises classic-histogram
  `le` labels to float form on ingestion (`"2.0"`, `"1.0"`; `"0.25"` is unchanged), so those
  selectors matched nothing: the freshness and ingestion-lag SLIs, burn alerts and dashboard panels
  were silently empty. Found during the M3 dashboard check (`make verify-stack`).
- **Choice:** use `le="0.25"`, `le="2.0"`, `le="1.0"` everywhere; `tests/unit/test_slo_buckets.py`
  fails if any rule, dashboard or canary query uses an `le` that is not a bucket boundary in that
  exact form.
- **Reason:** the bucket boundaries in the spec are unchanged; only their string form differs.

## D18 — One runbook per SLI for burn alerts, anchored per severity

- **Choice:** `AvailabilityFastBurn` and `AvailabilitySlowBurn` share
  `docs/runbooks/availability-burn.md`, with `#fast-burn` / `#slow-burn` anchors (same for the other
  SLIs). Gap alerts each have their own runbook. `make rules-test` checks every alert's
  `runbook_url` file **and** anchor exist.
- **Reason:** the spec's own example points `AvailabilityFastBurn` at `availability-burn.md`; the
  diagnosis for fast and slow burn of the same SLI is the same.
- `runbook_url` is a full GitHub URL so it is clickable from Alertmanager and Grafana.

## D19 — Extra gap alerts

- **Choice:** besides the three gap alerts in the spec, `DeadLetterQueueNotEmpty` (the spec says DLQ
  depth "is alerted on separately") and `TargetDown` (a scrape failure hides SLI data).
- `QuoteTrafficAbsent` uses `(sum(rate(...[5m])) or vector(0)) == 0` rather than plain `absent()`,
  because a counter series keeps existing after traffic stops.

## D20 — Fast-burn alerts have no `for:` clause

- **Reason:** follows the SRE Workbook: the short window already guards against blips and gives fast
  reset. Gap alerts use the `for` durations from the spec.

## D21 — Liveness of the worker means "the loop is turning"

- **Found while designing drill 4:** the heartbeat was only refreshed after a successful poll, so a
  Redis outage would fail `/health/live` after 30 s and Kubernetes would restart healthy workers;
  and a slow batch (50 events with injected latency) could look hung.
- **Choice:** refresh the heartbeat per message and after a handled dependency error. A dependency
  outage is not a hung process (same reasoning as API readiness ignoring Redis).

## D22 — Recreate the consumer group after a Redis restart

- **Found while designing drill 4:** dev Redis has no persistence, so a restart loses the stream and
  the `workers` group. The worker cached "group exists" and would fail with `NOGROUP` forever.
- **Choice:** on `NOGROUP`, recreate the group and retry once. Covered by a unit test.

## D23 — Kubernetes platform versions (looked up 2026-10-26)

| Component | Version |
|---|---|
| kind node image | `kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5` |
| Calico (manifest) | v3.33.0 |
| ingress-nginx chart | 4.15.1 (controller 1.15.1) |
| metrics-server chart | 3.14.0 (app 0.9.0) |
| kube-prometheus-stack chart | 92.13.1 (operator 0.94.1) |
| Argo CD chart | 10.11.1 (app v3.5.6) |
| Argo Rollouts chart | 2.43.8 (app v1.10.1) |

Pinned in `deploy/kind/versions.env` and `deploy/kind/cluster.yaml`.

## D24 — ingress-nginx despite its retirement

- **Context:** the community ingress-nginx project stopped receiving releases in March 2026
  (last release 1.15.1).
- **Choice:** still use it locally.
- **Reason:** the spec requires `trafficRouting.nginx` for the kind canary, the cluster is local
  and disposable, and the AWS phase uses the ALB controller instead. Revisit (e.g. Gateway API
  traffic routing in Argo Rollouts) before any long-lived environment.

## D25 — kind networking details

- Ingress is published on `127.0.0.1:18080` (host ports 80 and 8000 were in use) and the API is
  addressed with `Host: marketpulse.localtest.me`; tools send the Host header instead of relying on
  public DNS for `localtest.me`.
- Calico is installed from its manifest with pod CIDR `192.168.0.0/16` (`disableDefaultCNI: true`)
  because kindnet does not enforce NetworkPolicies. Kubelet probes keep working under the
  default-deny ingress policy.

## D26 — Images on kind

- The MarketPulse image is built locally and loaded with `kind load docker-image`; its tag comes
  from `values-kind.yaml` (`0.1.0`), so Helm and later Argo CD deploy the same image. Bump the tag
  when the code changes.
- Pre-loading the digest-pinned Redis image fails with Docker Desktop's containerd image store
  ("content digest not found" for other platforms), so it is best effort and the nodes pull Redis.

## D27 — Grafana on kind: one provider-level folder

- Folders generated from the dashboard sidecar's file structure were not visible to the anonymous
  Viewer role in Grafana 13, so dashboards are provisioned into one provider-level `MarketPulse`
  folder, matching the compose setup.
- No Grafana admin password is committed; the chart generates one into the `kps-grafana` Secret.

## D28 — Chart copies of rules and dashboards

- Helm can only read files inside the chart, so `make chart-assets` copies
  `observability/prometheus/rules/*.yml` and `observability/grafana/dashboards/*.json` into
  `deploy/helm/marketpulse/files/`. `make lint` fails if the copies differ from the originals, so
  compose and kind always load the same rules and dashboards.

## D29 — Argo CD diffs server-side (`ServerSideDiff`)

- **Problem:** after the helm → Argo CD handover, 15 resources stayed `OutOfSync` although every
  sync "succeeded". The diffs were all API-server defaulting: Service `clusterIP`/`ipFamilies`,
  `protocol: TCP` on ports, Deployment `progressDeadlineSeconds`/`strategy`, pod-template defaults,
  HPA `scaleUp` behaviour, PodMonitor `action: replace`. Argo CD's client-side diff compared the
  rendered chart with the live object and saw every default as drift.
- **Options:** (a) template every default into the chart (impossible for pod-template defaults);
  (b) long `ignoreDifferences` lists per kind; (c) server-side diff.
- **Choice:** (c): `controller.diff.server.side: "true"` on the controller plus
  `ServerSideApply=true`, `ServerSideDiff=true`, `RespectIgnoreDifferences=true` on the Application.
  The diff is computed from a server-side-apply dry run, so fields owned by the API server are not
  drift. The app went `Synced/Healthy` immediately; drift detection (D-check in PROGRESS) still works.
- **Note:** the sync option alone was not enough in chart 10.11.1; the controller flag was required.

## D30 — Orphaned pre-Rollout Deployment removed by hand

- **Found:** the M4 `helm install` created `Deployment/marketpulse-api` and `Service/marketpulse-api`.
  When M5 switched the API to a `Rollout`, Argo CD did not prune them: it never created them, so
  they carried no `argocd.argoproj.io/tracking-id` and were treated as unmanaged, not as stale.
  Their 2 pods matched the stable Service's label selector and had no `role` label, so they took a
  share of "stable" traffic that was invisible to the role-filtered canary queries.
- **Choice:** delete both by hand; `scripts/gitops-up.sh` already removes the helm release secret
  but should also delete the superseded Deployment/Service (follow-up in the final report).
- **Lesson:** a GitOps handover must either adopt or delete every object the previous tool created;
  "not in the manifest" is not the same as "pruned".

## D31 — First good-canary run discarded (no-data abort during a host outage)

- **What happened:** the first `canary.sh good` run passed the first analysis and reached 50 %
  weight; then Docker Desktop stopped (host-level, outside the cluster), the k6 load died, and the
  second analysis saw **no canary requests**. `successCondition` requires `len(result) > 0`, so both
  metrics failed with empty values and Rollouts aborted.
- **Decision:** this is the no-data guard working exactly as specified (an untested canary is never
  promoted), but it is not evidence that a good release is promoted. The run is recorded here, the
  commit was reverted so Git matches the running stable version, and the good canary was re-run
  with sustained traffic for the acceptance check. Only the re-run's numbers appear in PROGRESS.

## D32 — How drill 8's outcome is recorded

- **Spec expectation:** "Slow worker (injected latency) → ingestion-lag burn-rate alert fires within
  target."
- **Measured:** with 500 ms per event the two workers wrote 1.99 events/s against 20/s arriving.
  Every written event was late (5 m error ratio 1.00), but the 1 h ratio stayed at 0.02–0.10 and the
  30 m ratio under 0.06 for the whole 15-minute fault, so neither fast-burn pair could fire: burn
  rate is weighted by the rate of bad events, and a throttled worker *defers* the burn into a backlog
  (16,279 events, oldest 814 s) instead of spending it. `IngestionStalled` — the gap alert the spec
  defines for exactly this — fired 2 min 18 s in. The burn page fired only during the 34-second
  drain after restore, when the deferred lateness was written out in bulk.
- **Choice:** record the drill as **not met by the burn-rate alert, met by the gap alert**, keep both
  alerts unchanged, and list queue-depth-based worker scaling (the spec's KEDA extension) and wider
  `ingestion_lag_seconds` buckets as follow-ups. Lowering the burn thresholds to make this case page
  would make the alerts noisy for every other case and was rejected.
- **Script defect fixed:** the drill treated a timed-out wait for the burn alert as a failure and
  exited (`set -e`), skipping its own stall check and drain measurement; the cleanup trap still
  restored the system. The script now records a non-firing alert as a result and continues. The
  run's evidence was exported from Prometheus immediately afterwards and is used unchanged.
