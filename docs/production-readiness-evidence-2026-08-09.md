# Production-readiness evidence — 2026-08-09

This is local release-candidate evidence, not authorization to switch
production.

## Green

- clean PostgreSQL target migrated to the sole
  `0001_safety_first_baseline` revision;
- full PostgreSQL integration suite: `779 passed, 2 skipped, 3 deselected`;
- adoption, safety and Telegram acceptance subset: `78 passed`;
- Telegram burst: 100 incidents × 3 recipients produced exactly 300 durable
  deliveries and zero duplicate `(event_id, recipient_id)` pairs;
- PostgreSQL restart preserved the baseline revision and the operator snapshot
  recovered on the first probe;
- lost `LISTEN/NOTIFY`, queue crash, concurrency, lease and fencing contracts
  passed in the integration/unit suites;
- operator snapshot load: 301/301 HTTP 200 at 20 requests/second, zero failed
  requests, p95 28 ms and p99 225.51 ms;
- PostgreSQL proxy latency: baseline 200, a 3-second downstream delay could not
  produce a false successful response within the 2-second client deadline,
  and the first post-recovery probe returned 200;
- production Compose, Prometheus rules, Alertmanager, Loki, blackbox and Caddy
  validators passed with container-backed validators.

## Still external

- immutable release-image workflow and digest artifact for the final commit;
- off-host backup plus isolated restore/PITR evidence;
- unified desktop disposable runtime and physical-device matrix;
- production-like k6/Toxiproxy suite against the candidate host;
- real-user Web Vitals and the final adoption bundle made from the reviewed
  production source.

See `docs/production-cutover-runbook.md` for the stop conditions and execution
sequence. Old production data remains outside the new runtime and must not be
deleted without separate owner confirmation.
