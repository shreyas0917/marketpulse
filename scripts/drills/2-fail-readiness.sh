#!/usr/bin/env bash
# Drill 2: one API pod fails readiness (FAULT_READINESS_FAIL=true).
# A copy of a running API pod (same labels, so the Service and PodMonitor select it) is started
# with the fault on. Expected: it never becomes Ready, receives no traffic, users see no errors.
# shellcheck source=scripts/drills/lib.sh
source "$(dirname "$0")/lib.sh"
drill_start 2 "fail readiness on one pod"
port_forwards

NAME=drill2
POD=marketpulse-api-unready
template=$(kubectl -n "$NS" get pods -l app.kubernetes.io/component=api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
# The copy is owned by a throwaway ConfigMap: ReplicaSets only adopt orphan pods, so the API's
# ReplicaSet will not adopt (and then delete) it, while the Service still selects it.
kubectl -n "$NS" create configmap drill2-owner >/dev/null
OWNER_UID=$(kubectl -n "$NS" get configmap drill2-owner -o jsonpath='{.metadata.uid}')
on_exit "kubectl -n $NS delete configmap drill2-owner --ignore-not-found >/dev/null; kubectl -n $NS delete pod $POD --ignore-not-found --wait=false >/dev/null; log 'removed $POD'"

start_load 30 4m "$NAME"
sleep 30
log "starting $POD (copy of $template with FAULT_READINESS_FAIL=true)"
kubectl -n "$NS" get pod "$template" -o json | python -c "
import json, sys
p = json.load(sys.stdin)
meta = p['metadata']
p['metadata'] = {'name': '$POD', 'namespace': meta['namespace'], 'labels': meta['labels'],
                 'ownerReferences': [{'apiVersion': 'v1', 'kind': 'ConfigMap', 'name': 'drill2-owner',
                                      'uid': '$OWNER_UID', 'controller': True}]}
spec = p['spec']
for k in ('nodeName',):
    spec.pop(k, None)
c = spec['containers'][0]
c['env'] = [e for e in c.get('env', []) if e['name'] != 'FAULT_READINESS_FAIL'] + [
    {'name': 'FAULT_READINESS_FAIL', 'value': 'true'}]
p.pop('status', None)
print(json.dumps(p))
" | kubectl apply -f - >/dev/null
t_start=$(epoch)

kubectl -n "$NS" wait --for=condition=ContainersReady=false "pod/$POD" --timeout=10s >/dev/null 2>&1 || true
sleep 120
phase=$(kubectl -n "$NS" get pod "$POD" -o jsonpath='{.status.phase} ready={.status.containerStatuses[0].ready} restarts={.status.containerStatuses[0].restartCount}')
in_endpoints=$(kubectl -n "$NS" get endpointslices -l kubernetes.io/service-name -o json | python -c "
import json, sys
d = json.load(sys.stdin)
ip = '$(kubectl -n "$NS" get pod "$POD" -o jsonpath='{.status.podIP}')'
ready = [a for s in d['items'] for e in s.get('endpoints') or [] for a in e['addresses']
         if a == ip and (e.get('conditions') or {}).get('ready')]
print(len(ready))")
log "$POD after $(( $(epoch) - t_start ))s: $phase; ready endpoint entries: $in_endpoints"
quote_reqs=$(prom_value "sum(increase(http_requests_total{pod=\"$POD\", route=\"/v1/quotes/{symbol}\"}[3m])) or vector(0)")
log "quote requests served by $POD: $quote_reqs"
log "fault gauge on $POD: $(prom_value "max(fault_injection_active{pod=\"$POD\", type=\"readiness_fail\"})")"
echo "{\"pod\": \"$POD\", \"status\": \"$phase\", \"ready_endpoints\": $in_endpoints, \"quote_requests\": \"$quote_reqs\"}" > "$EVIDENCE/$NAME-pod.json"

wait_load
log "k6: $(k6_failures "$NAME")"
export_evidence "$NAME" \
  --expr "sum by (pod) (rate(http_requests_total{route=\"/v1/quotes/{symbol}\"}[1m]))" \
  --expr 'sli:availability:error_ratio_rate5m' \
  --expr "max by (pod) (fault_injection_active{type=\"readiness_fail\"})" \
  --expr "kube_pod_status_ready{namespace=\"marketpulse\", pod=\"$POD\"}"
