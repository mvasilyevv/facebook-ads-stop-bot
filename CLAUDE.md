# Repository guide

Код, тесты и комментарии пишутся по-русски там, где это помогает оператору;
имена типов, API-полей и технические идентификаторы остаются английскими.

Этот файл описывает текущую архитектуру. Датированные планы и аудиты в
`docs/` являются историческими материалами и не задают runtime contract.

## Agent skills

### Issue tracker

Задачи и спецификации ведутся в GitHub Issues репозитория
`mvasilyevv/facebook-ads-stop-bot`. См. `docs/agents/issue-tracker.md`.

### Domain docs

Используется multi-context layout: корневая карта `CONTEXT-MAP.md` направляет
к контекстам safety/control, Telegram, operator UI, browser/Vision и platform.
См. `docs/agents/domain.md`.

## Рабочий цикл

1. Проверить `git status` и не затрагивать чужие изменения.
2. Для money-пути сначала зафиксировать инвариант и regression test.
3. Менять один архитектурный слой или один вертикальный slice за PR.
4. Прогнать узкие тесты, затем соответствующий полный suite.
5. Сверить OpenAPI, generated clients и оба frontend, если менялся контракт.
6. Merge, production release и денежные действия остаются за человеком.

## Команды

```bash
# Backend
ruff check .
pytest tests/unit -q
pytest tests/integration -q

# API contract and frontend workspace
python scripts/export_openapi.py
pnpm gen:api
pnpm -r typecheck
pnpm -r lint
pnpm -r test
pnpm -r build

# Browser agent
cd services/browser-agent
npm ci
npm run lint
npm run build
npm test

# Production model validation
./scripts/validate-platform-configs.sh --containers
```

`make start|stop|logs|bootstrap` делегируют единственному local launcher
`scripts/run-local.sh`, который требует `FB_AGENT_PROFILE=local`. Корневой
Compose содержит только PostgreSQL, Redis, locked migrator, API и Telegram
inbox/outbox workers; money-capable сервисы в нём запрещены.

Обычные локальные и container migrations идут только через
`python -m scripts.run-migrations-locked`. `scripts/apply_schema.py --confirm-drop`
остаётся отдельной явной командой только для disposable development database.

Production topology задаётся только `deploy/compose/`, а lifecycle — platform
scripts. Удалённый release запускается через
`scripts/server-platform-release.sh`; детали в `DEPLOYMENT.md` и
`deploy/bluegreen/README.md`.

## Архитектура

### Source of truth

PostgreSQL хранит задачи, leases, fencing tokens, cabinet runtime, incidents,
notification outbox, Telegram inbox и action tokens. `LISTEN/NOTIFY` и Redis
могут ускорять доставку сигнала, но после пробуждения consumer всегда сверяет
состояние в БД. Недоступность Redis не должна останавливать control или
notification plane.

### Control plane

- `task_queue` разделена на `money`, `interactive`, `bulk` и `background`.
- `autopause_worker` — единственный consumer `money`.
- Claim выполняется через `FOR UPDATE SKIP LOCKED`; порядок — priority,
  `available_at`, `created_at`, immutable ID.
- Финализация требует совпадения `task_id`, `lease_owner` и `lease_token`.
- Deadline проходит через очередь, Python, gRPC, `AbortController` и Meta fetch.
- Результат внешней операции: `CONFIRMED`, `REJECTED` или `UNKNOWN`.
- После `UNKNOWN` pause/activate сверяются с Meta; create/duplicate не
  повторяются без подтверждения отсутствия side effect.
- UI, TMA, Telegram и auto-pause вызывают один `CommandService`.

### Observer

`apps/observer_worker/` создаёт actor на каждый явно настроенный кабинет.
Пустой набор кабинетов означает fail-closed skip, а не скан произвольной
текущей вкладки. `cabinet_runtime` хранит owner, fencing token, lease, stage и
timestamps snapshots. Ошибка scan-page не должна блокировать control-page.

### Browser and Meta

`services/browser-agent/` — Node.js gRPC слой рядом с независимым Kasm/Vision
desktop. DOM scan и session-tunneled Marketing API выполняются внутри нужной
browser session. Browser-agent не владеет задачами и не подтверждает business
success без результата внешней операции.

### Notifications

Telegram работает через три durable звена:

1. HTTPS webhook сохраняет update в `telegram_updates_inbox` и только после
   commit отвечает `204`.
2. `telegram_update_worker` валидирует opaque callback token и вызывает
   `CommandService`.
3. `telegram_delivery_worker` доставляет `notification_deliveries` через один
   HTML gateway и редактирует message slot incident.

Бизнес-код не вызывает Bot API напрямую. Event создаётся в одной транзакции с
task/FSM/incident. Callback содержит только `a:<opaque-token>`; в БД хранится
SHA-256. `401`, `403`, `429`, invalid HTML и потерянная карточка имеют явные
состояния delivery/incident, а не скрытую повторную отправку.

### Operator API and frontend

Основные контракты:

- `/api/operator/snapshot`, `/actions`, `/ads`;
- command endpoints для pause/activate/ack;
- `/ws/operator` с sequence и snapshot revision;
- `/api/v1/integrations/telegram/webhook`;
- Telegram preferences и diagnostics.

Общие типы и view-models находятся в `packages/shared/`, типизированный client
— в `packages/operator-api/`, UI primitives — в `packages/operator-ui/`.
`frontend/` и `frontend-mini/` используют общую data/feature модель, но разные
shell и chart renderer.

Обязательные семантики:

- `DataState = ready | empty | partial | stale | unavailable`;
- `Severity = ok | warning | critical | unknown`;
- `ActionState = queued | running | confirmed | failed | cancelled | unknown`;
- `null` означает unknown, а `0` — подтверждённый ноль;
- деньги и точные ratios передаются decimal strings;
- HTTP `202` означает queued, а не completed;
- gap в WS sequence вызывает одно snapshot reconciliation;
- `partial`, `stale` и `unavailable` никогда не выглядят зелёными.

### Production platform

- Durable `infra`, цветные `app_blue/app_green` и отдельный `desktop`.
- Images выпускаются по immutable digest; на VPS нет production build.
- Migrator работает отдельно под advisory lock.
- Caddy переключается только после health, readiness и contract checks.
- Rollback возвращает traffic и singleton leases, не понижая БД.
- pgBackRest: weekly full, daily differential, continuous WAL и off-host
  encrypted/versioned repository.
- Alloy собирает logs/traces; Prometheus, Loki, Tempo, Alertmanager, Grafana и
  blackbox probes размещаются независимо от application host.

## Инварианты данных

- Для cumulative `ad_metrics` используется latest-per-ad или
  latest-per-ad-per-cabinet-day; наивный `SUM` запрещён.
- Timezone, cabinet-day boundaries и freshness вычисляет сервер.
- Пропуск метрики не превращается в ноль; пропуск точки на графике остаётся
  разрывом.
- Любое destructive действие по рекламе проходит owner/recipient/role checks и
  идемпотентный command lifecycle.
- Raw exception, traceback, UUID, bot token и секреты не попадают в operator UI,
  Telegram, URL, логи или breadcrumbs.

## Production gates

- Нулевая потеря committed events и duplicate money tasks.
- Full backup, post-backup WAL marker и isolated PITR до migration-capable
  cutover.
- Кандидат поднимается без money workers; leases передаются после traffic gate.
- Нельзя считать physical mobile/TMA acceptance выполненным без реальных
  iOS/Android проверок.
- HA и автоматический DB failover включаются только после доказанных SLO,
  restore drills и fencing/quorum.
