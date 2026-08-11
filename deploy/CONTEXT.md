# Platform context

## Назначение

Простой повторяемый downtime deploy одного production runtime без скрытых
источников конфигурации.

## Владеет

- стабильными `infra`, `app`, `desktop` и `monitoring` Compose-проектами;
- content-addressed image builds и digest-only release manifest;
- строгим candidate/active configuration contract;
- forward-only migrations, однократным bootstrap/adoption и Telegram webhook;
- Caddy на фиксированных портах и production smoke checks.

## Инварианты

- Production images собираются только в CI.
- Все production Compose-вызовы проходят через `fbctl` и один candidate/active
  env contract.
- Preflight и pull завершаются до остановки app/desktop.
- После ошибки app/desktop остаются остановленными; повторный deploy идемпотентен.
- Нет colors, rollback journal, worker handoff, candidate telemetry и backup gate.
- PostgreSQL и Redis — единственные durable runtime volumes.
- Money safety, fencing и freshness не зависят от release-механики.

## Термины

- **Release manifest** — текущий commit и immutable image digests.
- **Source config** — root-only owner configuration без image references.
- **Candidate config** — release-specific derived Compose configuration,
  недоступная active runtime до smoke.
- **Adoption receipt** — транзакционное доказательство import в PostgreSQL.
- **Runtime** — один полностью проверенный control bundle без release archive.
