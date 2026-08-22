# Repository guide

Код, тесты и комментарии пишутся по-русски там, где это помогает оператору;
имена типов, API-полей и технические идентификаторы остаются английскими.

Этот файл описывает текущую архитектуру. Датированные планы и аудиты в
`docs/` являются историческими материалами и не задают runtime contract.

## Agent skills

### Issue tracker

Задачи и спецификации ведутся в GitHub Issues репозитория
`mvasilyevv/facebook-ads-stop-bot`. См. `docs/agents/issue-tracker.md`.

Единая доска: <https://github.com/users/mvasilyevv/projects/2>, правила —
`docs/agents/board.md`. Она обязательна для всех агентов:

- перед заведением задачи сверяться с доской и дописывать в существующую
  карточку вместо второй такой же;
- взяв работу — «В работе» и назначить себя, закончив — «Готово»;
- найденное по ходу, но не сделанное, заводить карточкой сразу, а не
  перечислять в конце ответа;
- ставить метки `area:*`, `prio:*` и `level:*` и те же значения в поля доски;
  задачу, трогающую и бэкенд, и интерфейс, делить на две связанные карточки.

Порядок работ задаёт не приоритет, а уровень готовности —
`docs/agents/priorities.md`. Уровни закрываются по одному сверху вниз; уровень
закрывает наблюдаемое событие, а не набор закрытых карточек; найденное по ходу
уходит в парковку, а не в текущий уровень. Уровень карточки живёт меткой
`level:0`…`level:3` или `level:parking`, читаемый разрез по уровням — issue
[#268](https://github.com/mvasilyevv/facebook-ads-stop-bot/issues/268).

### Исполнители

Реализация по готовой спеке уходит внешним исполнителям — AntiGravity (`agy`)
и Claude CLI (`claude -p`); архитектура, money-путь, приёмка и merge остаются
за управляющей сессией. Кого на какую задачу, как устроено рабочее место,
порядок приёмки и замеренная цена — `docs/agents/executors.md`.

### Инженерная команда

Девять ролей, по одной на границу владения из `CONTEXT-MAP.md`: `eng-lead`,
`eng-safety`, `eng-backend`, `eng-browser`, `eng-frontend`, `eng-data`,
`eng-platform`, `eng-test`, `eng-review`. Состав и порядок взаимодействия —
`docs/agents/engineering-team.md`, обязательные каноны —
`docs/agents/engineering-standards.md`. Определения ролей лежат в
`.claude/agents/eng-*.md`.

Money-путь не проходит мимо `eng-safety`, приёмку выносит `eng-review`.

Изменение стоп-правил, порогов и всего, что решает судьбу объявления, не
проходит мимо роли `buyer`: инженерные роли отвечают, работает ли правило
правильно, а она — имеет ли оно смысл при живой закупке. Её отказ не
отменяется техническим согласием. Значения по умолчанию берутся из
`docs/knowledge/leadgenerals/` со ссылкой на источник; числа нет в корпусе —
это открытый вопрос к владельцу, а не повод придумать.

### Domain docs

Используется multi-context layout: корневая карта `CONTEXT-MAP.md` направляет
к контекстам safety/control, Telegram, operator UI, browser/Vision и platform.
См. `docs/agents/domain.md`.

## Рабочий цикл

1. Проверить `git status` и не затрагивать чужие изменения.
2. Для money-пути сначала зафиксировать инвариант и regression test.
3. Ужесточая контракт, сначала перечислить вызывающих. Запрет, очевидный для
   нового кода, может быть обычным путём для уже существующего вызова.
4. Меняя модуль, посмотреть, чем он покрыт помимо тестов, которые гоняешь.
   Узкие тесты проходят там, где ломается integration.
5. Менять один архитектурный слой или один вертикальный slice за PR.
6. Прогнать узкие тесты, затем соответствующий полный suite.
7. Сверить OpenAPI, generated clients и оба frontend, если менялся контракт.
8. Pull request, merge и production release делает агент. За человеком
   остаются денежные действия: включение сканирования, трата бюджета,
   удаление рекламных объектов со спендом.

## Команды

```bash
# Всё, что гоняет CI, одной командой (перед пушем)
scripts/preflight.sh          # полный прогон, ~12-15 минут
scripts/preflight.sh --fast   # без Playwright, Storybook и docker-сборок

# Backend
ruff check .
PYTHONDONTWRITEBYTECODE=1 pytest tests/unit -q
PYTHONDONTWRITEBYTECODE=1 pytest tests/integration -q

# API contract and frontend workspace
PYTHONDONTWRITEBYTECODE=1 python scripts/export_openapi.py
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
./scripts/fbctl doctor
```

`make start|stop|logs|bootstrap` делегируют единственному local launcher
`scripts/run-local.sh`, который требует `FB_AGENT_PROFILE=local`. Корневой
Compose содержит только PostgreSQL, Redis, locked migrator, API и Telegram
inbox/outbox workers; money-capable сервисы в нём запрещены.

Обычные локальные и container migrations идут только через
`PYTHONDONTWRITEBYTECODE=1 python -m scripts.run-migrations-locked`.
`scripts/apply_schema.py --confirm-drop`
остаётся отдельной явной командой только для disposable development database.

Production topology задаётся только `fbctl/` и `deploy/compose/`. CI доставляет
маленький control bundle, а host исполняет только `scripts/fbctl`; детали в
`DEPLOYMENT.md`.

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

`services/browser-agent/` — Node.js gRPC слой рядом с независимым Vision
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

- Стабильные single-slot `infra`, `app`, `desktop` и `monitoring` projects.
- Images выпускаются по immutable digest; на VPS нет production build.
- Migrator работает отдельно под advisory lock.
- Все Compose-вызовы выполняет `fbctl` через строгий candidate/active env.
- Caddy всегда направляет трафик на фиксированные порты; downtime допустим.
- Ошибка оставляет app/desktop остановленными; повторный deploy идемпотентен.
- Runtime rollback и PostgreSQL backup automation отсутствуют по решению owner.
- Alloy собирает logs/traces; Prometheus, Loki, Tempo, Alertmanager, Grafana и
  blackbox probes работают отдельным monitoring project.

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
- Preflight и image pull завершаются до остановки production runtime.
- Migration, adoption, desktop, app, webhook и workers проходят по одному
  наблюдаемому deploy sequence.
- Нельзя считать physical mobile/TMA acceptance выполненным без реальных
  iOS/Android проверок.
- HA и автоматический DB failover не входят в текущий runtime.
