# FB Agent observability

The central stack contains Prometheus, Loki, Tempo, Alertmanager, Grafana,
blackbox-exporter, node-exporter, cAdvisor and Grafana Alloy. Promtail is gone;
Alloy owns Docker log collection and OTLP ingestion.

Both central and host-agent Compose files reject mutable image tags. Reviewed
multi-architecture digests live in the example env files and are updated as an
independent monitoring release.

## Deployment modes

Preferred production topology:

- central `docker-compose.monitoring.yml` on an off-host monitoring node;
- `docker-compose.agent.yml` on each application host;
- communication only over a private Tailscale/WireGuard/VPC network;
- Grafana reachable through SSH or an authenticated reverse proxy;
- no ingest port exposed to the public Internet.

For a single-host canary, run the central stack with
`docker-compose.local-app.yml`. Prometheus discovers containers carrying
`com.fb-agent.metrics=true` and scrapes the label-provided port (`8100` for API,
`9464` for workers). The overlay adds a narrow Alloy scraper for
`vision-webtop:9464`, because browser-agent shares Vision's network namespace
and therefore has no Docker-discoverable IP. Do not also run the full
application-host agent on that same host, or metrics and logs will be written
twice.

## Central start

```bash
cd /opt/fb-agent/current/deploy/monitoring
cp .env.monitoring.example .env.monitoring
chmod 600 .env.monitoring
# Generate once, then provision the same value as ALERTMANAGER_WEBHOOK_SECRET
# in /opt/fb-agent/shared/.env on the application host.
install -d -o root -g root -m 0700 /opt/fb-agent/monitoring-secrets
openssl rand -hex 32 | install -o root -g 65534 -m 0640 /dev/stdin \
  /opt/fb-agent/monitoring-secrets/alertmanager_webhook_token
docker compose -f docker-compose.monitoring.yml config --quiet
docker compose -f docker-compose.monitoring.yml up -d --wait
```

On the application host, append the local overlay to both `config` and `up`:

```bash
docker compose -f docker-compose.monitoring.yml \
  -f docker-compose.local-app.yml up -d --wait
```

The default bind is loopback:

- Grafana `127.0.0.1:3000`;
- Prometheus/remote-write `127.0.0.1:9090`;
- Loki ingest `127.0.0.1:3100`;
- OTLP gRPC/HTTP `127.0.0.1:4317/4318`;
- Alertmanager `127.0.0.1:9093`;
- Alloy debug UI `127.0.0.1:12345`.

On a separate monitoring node, set `MONITORING_INGEST_BIND_ADDRESS` to its
private address and restrict these ports at the firewall. Terminate private TLS
in front of the three ingest paths before pointing an app agent at them.

For a single-host installation, bind ingest only to the Docker bridge
(`172.17.0.1`) and set `MONITORING_TRANSPORT=same_host` in the application-host
agent environment. The agent then uses the fixed `host.docker.internal`
gateway endpoints; telemetry never leaves the host and ingest is not exposed on
a public interface.

## Application-host agent

```bash
install -m 600 .env.agent.example /opt/fb-agent/shared/alloy-agent.env
# Set MONITORING_TRANSPORT and the matching fixed private endpoints.
docker compose --env-file /opt/fb-agent/shared/alloy-agent.env \
  -f docker-compose.agent.yml up -d --wait
```

The agent also runs node-exporter and cAdvisor on each application host; their
remote-written series carry the required `node` and `role="application"`
labels. Central exporters use `role="monitoring"`, so monitoring-node health
cannot satisfy application-host alerts. Alloy discovers
app containers through the read-only Docker socket and persists Docker log
offsets in its volume. Python services use the OpenTelemetry SDK for FastAPI,
HTTPX, SQLAlchemy and async gRPC, exporting OTLP/gRPC to `alloy-agent:4317`.
Alloy batches and forwards spans to the private HTTPS `TEMPO_OTLP_HTTP_URL`;
off-host credentials never enter application containers. HTTP trace URLs drop
userinfo/query strings and redact Telegram bot-token path segments.
Private DNS, TLS certificates and firewall reachability are provisioning tasks,
not application release gates. In the current single-host deployment the local
overlay supplies the canonical `alloy-agent` network alias directly.

### Expected application hosts

`prometheus/targets/application-hosts.json` is the durable inventory used by
missing-host alerts. Keep one entry per application host and make its `node`
label exactly equal to that host agent's `NODE_NAME`. Multiple entries may use
the same `localhost:9090` target: Prometheus scrapes itself only to keep the
inventory series alive and discards all scraped samples.

Do not auto-generate or prune this file from recently observed metrics. A host
that has stopped reporting remains critical indefinitely, including after
24 hours and after Prometheus restarts. Removing its entry is an explicit
decommissioning action and must happen only after the application host is
retired.

## Truthful alerts

Rules live in `prometheus/rules/fb-agent.yml`. Dashboards may use zero-filled
recording rules, but alerts never do. Missing worker instrumentation produces
`FBWorkerMetricsAbsent`, while an unreachable endpoint produces
`FBWorkerMetricsTargetDown`; neither state can look green.

Coverage includes public health/readiness, worker heartbeat, money queue age,
snapshot freshness, critical notification delay, notification dead letters and
missing application hosts. The checked-in expected-host inventory means a
missing application node cannot silently age out after a fixed lookback window.
Alertmanager groups/inhibits alerts and sends them with a scoped Bearer token to
`/api/v1/integrations/alertmanager/webhook`. The API updates `incidents` and
creates `notification_events`/`notification_deliveries` in one PostgreSQL
transaction before acknowledging the request. Direct Alertmanager-to-Telegram
delivery is forbidden. The app-host env secret and monitoring-host credentials
file must match; this is an explicit fail-closed provisioning gate.

## Validation

```bash
./scripts/fbctl doctor
```

This validates every production Compose model from one strict synthetic
configuration, rejects mutable images/fixed container names, checks the
supported `fbctl` control bundle and validates immutable Docker bases. The
release CI additionally validates central Prometheus and Alloy configurations.

Prometheus keeps 15 days/2 GB; Loki and Tempo keep seven days. Backup data is
not stored in this stack. Monitoring volumes should themselves be snapshotted,
but loss of them must never affect the control or notification planes.
