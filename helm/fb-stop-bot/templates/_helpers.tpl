{{/*
Вспомогательные шаблоны для FB Stop Bot Helm chart
*/}}

{{/* Полное имя релиза */}}
{{- define "fb-stop-bot.fullname" -}}
{{- printf "%s" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Общие labels для всех ресурсов */}}
{{- define "fb-stop-bot.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Selector labels */}}
{{- define "fb-stop-bot.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Имя образа с registry */}}
{{- define "fb-stop-bot.image" -}}
{{- $registry := .Values.image.registry -}}
{{- $repo := .repo -}}
{{- $tag := .tag | default "latest" -}}
{{- if $registry }}
{{- printf "%s/%s:%s" $registry $repo $tag }}
{{- else }}
{{- printf "%s:%s" $repo $tag }}
{{- end }}
{{- end }}

{{/* Генерация списка BROWSER_AGENT_HOSTS из browserAgents */}}
{{- define "fb-stop-bot.browserAgentHosts" -}}
{{- $hosts := list -}}
{{- range .Values.browserAgents -}}
{{- $hosts = append $hosts (printf "browser-agent-%s:50051" .slug) -}}
{{- end -}}
{{- join "," $hosts }}
{{- end }}
