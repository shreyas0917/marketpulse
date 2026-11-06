{{- define "mp.name" -}}marketpulse{{- end -}}

{{- define "mp.labels" -}}
app.kubernetes.io/name: {{ include "mp.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "mp.selector" -}}
app.kubernetes.io/name: {{ include "mp.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "mp.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}

{{/* Environment shared by every MarketPulse container. */}}
{{- define "mp.commonEnv" -}}
- name: LOG_LEVEL
  value: {{ .Values.logLevel | quote }}
- name: REDIS_URL
  value: "redis://{{ include "mp.name" . }}-redis:{{ .Values.redis.port }}/0"
- name: SYMBOLS
  value: {{ .Values.symbols | quote }}
- name: QUEUE_BACKEND
  value: {{ .Values.queue.backend | quote }}
- name: QUEUE_METRICS_INTERVAL_SECONDS
  value: {{ .Values.queue.metricsIntervalSeconds | quote }}
{{- end -}}

{{/*
Component environment: the neutral fault defaults (.Values.faults) overlaid with the component's
own env block, which wins on a name clash. Call with (dict "root" $ "env" .Values.<component>.env).
Rendered in sorted key order, so the list is stable and every name appears once.
*/}}
{{- define "mp.extraEnv" -}}
{{- $env := merge (deepCopy (.env | default dict)) (deepCopy (.root.Values.faults | default dict)) }}
{{- range $k, $v := $env }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}

{{- define "mp.topologySpread" -}}
{{- if .root.Values.topologySpread.enabled }}
topologySpreadConstraints:
  - maxSkew: {{ .root.Values.topologySpread.maxSkew }}
    topologyKey: {{ .root.Values.topologySpread.topologyKey }}
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        {{- include "mp.selector" . | nindent 8 }}
{{- end }}
{{- end -}}
