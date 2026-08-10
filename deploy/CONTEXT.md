# Platform context

## Назначение

Контекст выпускает и восстанавливает систему без потери durable state и без
двойного исполнения money-work.

## Владеет

- durable `infra`, цветными `app_blue/app_green` и отдельным desktop-agent;
- immutable image manifests и Caddy traffic switch;
- locked migrator и expand/backfill/contract policy;
- worker lease handoff и bounded rollback;
- Prometheus, Loki, Tempo, Alertmanager, Grafana, blackbox и Alloy;
- pgBackRest, WAL archive, PITR и restore drills;
- условиями перехода к multi-host HA.

## Инварианты

- Production images собираются один раз в CI и деплоятся по digest.
- Candidate поднимается без money workers; handoff выполняется после traffic gate.
- Rollback не понижает схему БД.
- Реплика не считается backup; monitoring может находиться off-host, а первый
  production release использует локальный pgBackRest repository по решению owner.
- Release не считается безопасным без health, contract, desktop readiness и
  rollback evidence.
- Автоматический DB failover включается только после SLO, restore drills,
  chaos-теста и наличия fencing/quorum.

## Glossary

- **Color** — один из двух application runtime наборов blue/green.
- **Handoff** — передача singleton worker leases новому color.
- **Release manifest** — immutable набор image digests и desired configuration.
- **Restore drill** — проверенное восстановление backup/WAL в изолированной среде.
- **RPO/RTO** — подтверждённые пределы потери данных и времени восстановления.
