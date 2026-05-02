{{/* Шаблон Deployment для каждого воркера */}}
{{- define "fb-stop-bot.workerDeployment" -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fb-stop-bot-{{ .workerType | replace "_" "-" }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "fb-stop-bot.labels" .root | nindent 4 }}
    app.kubernetes.io/component: worker-{{ .workerType | replace "_" "-" }}
spec:
  replicas: 1
  selector:
    matchLabels:
      {{- include "fb-stop-bot.selectorLabels" .root | nindent 6 }}
      app.kubernetes.io/component: worker-{{ .workerType | replace "_" "-" }}
  template:
    metadata:
      labels:
        {{- include "fb-stop-bot.labels" .root | nindent 8 }}
        app.kubernetes.io/component: worker-{{ .workerType | replace "_" "-" }}
    spec:
      containers:
        - name: worker
          image: "{{ .root.Values.image.registry }}/{{ .root.Values.image.workers.repository }}:{{ .root.Values.image.workers.tag }}"
          imagePullPolicy: {{ .root.Values.image.pullPolicy }}
          env:
            - name: WORKER_TYPE
              value: {{ .workerType | quote }}
            {{- if eq .workerType "observer" }}
            - name: BROWSER_AGENT_HOSTS
              valueFrom:
                configMapKeyRef:
                  name: fb-stop-bot-config
                  key: BROWSER_AGENT_HOSTS
            {{- end }}
          envFrom:
            - configMapRef:
                name: fb-stop-bot-config
            - secretRef:
                name: fb-stop-bot-secrets
          resources:
            {{- toYaml .root.Values.resources.worker | nindent 12 }}
{{- end }}
