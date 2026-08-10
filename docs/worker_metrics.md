# Worker metrics

Production workers run only through the immutable services in
`deploy/compose/`. Each worker container has its own network namespace, so the
standard internal `WORKER_METRICS_PORT=9464` can be reused safely. Prometheus
discovers the port from the Compose labels; it is not published as a host
runtime port.

The independent desktop/browser-agent contour also exposes `9464` only inside
its own container namespace. Its lifecycle belongs to the platform desktop
scripts, not the application release or local launcher.

The root `docker-compose.yml` is local-only and contains no observer,
browser-agent, scheduler, campaign creator or Meta mutation worker. It may run
the API and Telegram inbox/outbox workers through `scripts/run-local.sh`, with
the mandatory `FB_AGENT_PROFILE=local` marker.

`autopause_worker` remains the sole production consumer of `lane=money`.
`meta_api` is explicitly limited to `interactive,bulk,background`; those
services and their lifecycle contract are defined only in
`deploy/compose/docker-compose.app.yml`.

`tracker_reconciliation_worker` drains the durable
`tracker_event_process` lane into canonical click state and performs the
seven-day provider reconciliation that backs the default analytics window.
Its heartbeat, singleton lock and Compose service use that same role name.
