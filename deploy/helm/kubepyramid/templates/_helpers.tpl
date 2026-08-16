{{/* Chart name / fullname / labels */}}
{{- define "kubepyramid.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "kubepyramid.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "kubepyramid.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "kubepyramid.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "kubepyramid.labels" -}}
helm.sh/chart: {{ include "kubepyramid.chart" . }}
{{ include "kubepyramid.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "kubepyramid.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kubepyramid.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "kubepyramid.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "kubepyramid.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* Component names */}}
{{- define "kubepyramid.engine.fullname" -}}{{ include "kubepyramid.fullname" . }}-engine{{- end -}}
{{- define "kubepyramid.ui.fullname" -}}{{ include "kubepyramid.fullname" . }}-ui{{- end -}}
{{- define "kubepyramid.postgres.fullname" -}}{{ include "kubepyramid.fullname" . }}-postgres{{- end -}}

{{/* Database secret + DSN */}}
{{- define "kubepyramid.dbSecretName" -}}
{{- if .Values.database.existingSecret -}}
{{- .Values.database.existingSecret -}}
{{- else -}}
{{- include "kubepyramid.fullname" . }}-db
{{- end -}}
{{- end -}}

{{- define "kubepyramid.dbSecretKey" -}}
{{- .Values.database.existingSecretKey | default "dsn" -}}
{{- end -}}

{{- define "kubepyramid.dbDsn" -}}
{{- if .Values.postgres.enabled -}}
postgres://{{ .Values.postgres.auth.username }}:{{ .Values.postgres.auth.password }}@{{ include "kubepyramid.postgres.fullname" . }}:5432/{{ .Values.postgres.auth.database }}?sslmode=disable
{{- else -}}
postgres://{{ .Values.database.user }}:{{ .Values.database.password }}@{{ .Values.database.host }}:{{ .Values.database.port }}/{{ .Values.database.name }}?sslmode={{ .Values.database.sslmode }}
{{- end -}}
{{- end -}}

{{/* Reusable env: DB driver + DSN (from the secret) */}}
{{- define "kubepyramid.dbEnv" -}}
- name: KUBEPYRAMID_DB_DRIVER
  value: postgres
- name: KUBEPYRAMID_DB_DSN
  valueFrom:
    secretKeyRef:
      name: {{ include "kubepyramid.dbSecretName" . }}
      key: {{ include "kubepyramid.dbSecretKey" . }}
{{- end -}}

{{/* Hardened container security context */}}
{{- define "kubepyramid.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}
