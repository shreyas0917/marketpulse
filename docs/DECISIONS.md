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
