# Threat model (local build)

Scope: the local Docker Compose and kind environments built in M1–M6. AWS-specific controls (IAM,
OIDC, IRSA/Pod Identity, ECR scanning) are listed in the spec for the later AWS phase and are not
implemented here.

## Assets

| Asset | Why it matters |
|---|---|
| Latest-quote store (Redis) | A tampered price is served to every consumer; correctness is the product. |
| Event queue (Redis streams) | Injected events become prices; deleting events causes stale quotes. |
| Git repository (`main`) | Argo CD deploys whatever is on `main`; Git is the change-control boundary. |
| Observability data | Alerts and canary analysis decide promotions and pages. |

## Trust boundaries

1. **Client → ingress → API.** Untrusted HTTP. Only `GET /v1/quotes/{symbol}`, `POST /v1/events`,
   health and metrics are exposed.
2. **Pods → Redis.** Inside the namespace. Restricted by NetworkPolicy.
3. **GitHub → Argo CD.** Argo CD pulls a public repo over HTTPS; no write credentials in the cluster.
4. **Operator → cluster.** `kubectl` with the local kind admin kubeconfig.

## Threats and mitigations

| Threat | Mitigation in this build | Gap / later phase |
|---|---|---|
| Arbitrary pod in the cluster reads or writes prices in Redis | Default-deny ingress NetworkPolicy; Redis accepts only API, worker and producer pods (Calico enforces it; verified in M4) | Redis has no AUTH/TLS locally; ElastiCache with AUTH + TLS on AWS |
| Malicious or malformed events poison prices | Pydantic validation (positive price, bounded lengths, served symbols only); invalid events retried then dead-lettered; DLQ alert | `POST /v1/events` is unauthenticated (it exists for testing); disable or authenticate it outside dev |
| Replayed or late events overwrite fresher prices | Dedup on `event_id` (SET NX + TTL) and last-write-wins by event time in one atomic Lua script | Replays older than the dedup TTL (1 h) are still rejected by the timestamp check |
| Container escape / privilege escalation | Non-root UID 10001, read-only root filesystem, all capabilities dropped, no privilege escalation, `RuntimeDefault` seccomp | — |
| Service-account token abuse | One ServiceAccount per component, `automountServiceAccountToken: false` (no workload talks to the API server) | — |
| Secrets leaked in Git or images | No secrets exist in the app; `.gitignore` covers env/kubeconfig/credentials; `gitleaks` scan before every push; image contains only the venv and `app/` | Trivy image scanning in CI is a later-phase item (section 11) |
| Bad release reaches all users | Canary with Prometheus analysis on canary-only series; automatic abort; no-data counts as failure | — |
| Configuration drift / manual changes | Argo CD auto-sync with self-heal reverts `kubectl edit` (verified in M5) | — |
| Supply chain: dependency or image tampering | Exact pins in `pyproject.toml` + `uv.lock`; base and infra images pinned by tag **and** digest; Helm chart versions pinned | No signature verification (cosign) |
| Denial of service on the API | Resource limits, HPA on CPU, ingress in front | No rate limiting; out of scope for local |
| Alert suppression / blind spots | Gap alerts for stalled worker, no traffic, Redis down, DLQ, scrape failures; rules unit-tested | Alertmanager has no external receiver locally |

## Assumptions

- The kind cluster and Docker host are single-user developer machines; the kind admin kubeconfig
  is trusted and never committed.
- Producer and worker clocks are in sync (same host locally). Clock skew would distort ingestion lag.
- All market data is synthetic; there is no PII and no real financial data.
