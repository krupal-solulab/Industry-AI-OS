{{/* Common labels for every rendered object. */}}
{{- define "aios.labels" -}}
app.kubernetes.io/part-of: industry-ai-os
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "aios.image" -}}
{{ .Values.global.image.repository }}:{{ .Values.global.image.tag }}
{{- end -}}
