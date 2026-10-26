#!/usr/bin/env bash
# Drill 6: poison messages. 20 malformed messages are published to the queue.
# Expected: each is retried with backoff and dead-lettered after 5 attempts; DLQ alert fires;
# the worker stays healthy (no restarts) and keeps processing good events.
# shellcheck source=scripts/drills/lib.sh
source "$(dirname "$0")/lib.sh"
drill_start 6 "poison messages"
port_forwards

NAME=drill6
COUNT="${COUNT:-20}"
redis_cli() { kubectl -n "$NS" exec deploy/marketpulse-redis -- redis-cli "$@"; }
on_exit "redis_cli XTRIM events-dlq MAXLEN 0 >/dev/null && log 'DLQ cleared'"

restarts() { kubectl -n "$NS" get pods -l app.kubernetes.io/component=worker -o jsonpath='{range .items[*]}{.status.containerStatuses[0].restartCount}{" "}{end}'; }
dlq_before=$(redis_cli XLEN events-dlq)
log "worker restarts before: $(restarts); DLQ length before: $dlq_before"
ok_before=$(prom_value 'sum(events_processed_total{result="ok"})')

T0=$(epoch)
curl -fsS -X POST "$PRODUCER/poison?count=$COUNT" >/dev/null
log "published $COUNT poison messages at $(now)"

dlq=0
for _ in $(seq 1 120); do
  dlq=$(redis_cli XLEN events-dlq)
  (( dlq - dlq_before >= COUNT )) && break
  sleep 1
done
T_DLQ=$(epoch)
log "DLQ +$(( dlq - dlq_before )) after $(( T_DLQ - T0 ))s"
redis_cli XRANGE events-dlq - + COUNT 3 > "$EVIDENCE/$NAME-dlq-sample.txt"
"${ALERTS[@]}" wait --alert DeadLetterQueueNotEmpty --state firing --timeout 300 | tee "$EVIDENCE/$NAME-dlq-alert-firing.json"
T_ALERT=$(epoch)
sleep 20
ok_after=$(prom_value 'sum(events_processed_total{result="ok"})')
log "worker restarts after: $(restarts); good events processed during drill: $(python -c "print(int(float('$ok_after') - float('$ok_before')))")"
log "retried: $(prom_value 'sum(increase(events_processed_total{result="retried"}[5m]))') dead_lettered: $(prom_value 'sum(increase(events_processed_total{result="dead_lettered"}[5m]))')"
cat > "$EVIDENCE/$NAME-result.json" <<EOF
{"poison_published": $COUNT, "dlq_added": $(( dlq - dlq_before )), "seconds_to_all_dead_lettered": $(( T_DLQ - T0 )),
 "seconds_to_dlq_alert": $(( T_ALERT - T0 )), "worker_restarts_after": "$(restarts)",
 "good_events_processed_during_drill": $(python -c "print(int(float('$ok_after') - float('$ok_before')))")}
EOF
export_evidence "$NAME" \
  --expr 'sum by (result) (rate(events_processed_total[1m]))' --expr 'max(dlq_depth)' \
  --expr 'max(queue_depth)' --expr 'sli:ingestion_lag:error_ratio_rate5m'
log "clearing DLQ so the alert resolves"
redis_cli XTRIM events-dlq MAXLEN 0 >/dev/null
"${ALERTS[@]}" wait --alert DeadLetterQueueNotEmpty --state resolved --timeout 300 | tee "$EVIDENCE/$NAME-dlq-alert-resolved.json"
