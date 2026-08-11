# FB Agent

Safety-first операторская платформа для мониторинга Facebook Ads, исполнения
денежных действий и доставки уведомлений. PostgreSQL является источником
истины для задач, leases, fencing, incidents и уведомлений; Redis не входит в
контур гарантий.

## Что входит в систему

- Observer сканирует каждый настроенный рекламный кабинет в отдельном actor.
- `autopause_worker` единолично обслуживает money-lane; остальные mutations
  исполняются в `interactive`, `bulk` и `background` lanes.
- Любая browser/Meta-операция имеет абсолютный deadline и завершение
  `CONFIRMED`, `REJECTED` или `UNKNOWN`. Неоднозначный результат сверяется с
  фактическим состоянием и не повторяется вслепую.
- Web и Telegram Mini App используют общий типизированный operator API и
  одинаковую семантику `ready | empty | partial | stale | unavailable`.
- Telegram принимает updates только через HTTPS webhook. Уведомления проходят
  через PostgreSQL outbox и один HTML gateway; incident обновляет одну карточку
  на получателя.
- Production использует один downtime-tolerant slot, digest-only образы и
  candidate configuration, которая становится active только после smoke.

## Компоненты

| Контур | Компоненты |
| --- | --- |
| Operator | FastAPI, React web, Telegram Mini App, WebSocket reconciliation |
| Safety | `task_queue`, `CommandService`, leases, fencing, deadlines, actors |
| Notifications | incidents, events/deliveries, Telegram webhook and delivery workers |
| Browser | отдельный Kasm/Vision desktop и Node.js browser-agent |
| Platform | PostgreSQL, Redis, fixed-port Caddy, Alloy, Prometheus, Loki, Tempo |

Список production-сервисов и порядок переключения описаны в
[DEPLOYMENT.md](DEPLOYMENT.md). Актуальные операционные сценарии находятся в
[docs/playbooks/RUNBOOKS.md](docs/playbooks/RUNBOOKS.md).

## Локальная разработка

Единственный поддерживаемый local runtime — `scripts/run-local.sh`. Он требует
точный маркер `FB_AGENT_PROFILE=local` и запускает только PostgreSQL, Redis,
locked migrator, API и Telegram inbox/outbox workers. Observer, browser-agent,
Meta mutations, campaign/cabinet schedulers и любые money workers в корневом
Compose физически отсутствуют.

```bash
cp .env.local.example .env
make install
make start
# make logs
# make stop
```

Первичное создание схемы в пустой dev-базе:

```bash
make migrate
```

`make migrate` вызывает locked Alembic runner: advisory lock удерживается на
протяжении `upgrade head`, `current --check-heads`, `alembic check` и проверки
PostgreSQL objects. Команда ничего не удаляет, принимает пустую БД или известного
предка единственного линейного head и отклоняет неизвестную/разветвлённую
историю до DDL.
Если disposable dev/test-базу действительно нужно пересоздать, это отдельная
трёхфакторная команда. Она игнорирует `.env` и обычный `DATABASE_URL`, принимает
только loopback/local Compose DSN и имя с суффиксом `_dev`/`_test`:

```bash
export FB_AGENT_DISPOSABLE_DATABASE_URL='postgresql+asyncpg://user:pass@127.0.0.1:5433/fb_stop_bot_dev'
export FB_AGENT_ALLOW_DESTRUCTIVE_RESET='I_UNDERSTAND_THIS_DELETES_DATA'
make reset-disposable-db CONFIRM_DATABASE=fb_stop_bot_dev
```

`0001` — irreversible fresh-install baseline. После первого production он
заморожен; дальнейшие изменения добавляются только линейными forward revisions.
Историческая pre-safety схема не поддерживается: первый cutover использует
проверяемый export → recreate → import, а новый runtime затем обновляется
обычными Alembic revisions.

Frontend:

```bash
pnpm install
pnpm gen:api
pnpm --filter fb-stop-bot-frontend dev
pnpm --filter fb-agent-mini dev
```

Browser-agent:

```bash
cd services/browser-agent
npm ci
npm run build
npm test
```

## Проверки

```bash
ruff check .
make test-unit
make test-integration
pnpm -r typecheck
pnpm -r lint
pnpm -r test
pnpm -r build
./scripts/fbctl doctor
```

Тесты интеграции должны использовать отдельную disposable PostgreSQL-базу.
Нельзя направлять test suite на production DSN.

## Production

Production images собираются один раз в CI и передаются по digest. VPS не
собирает приложения из исходников. Production topology существует только в
`deploy/compose/`. CI доставляет самодостаточный control bundle; на сервере
единственный интерфейс — `fbctl`:

```bash
sudo /opt/fb-agent/runtime/fbctl bootstrap --manifest release.json  # один раз
sudo /opt/fb-agent/runtime/fbctl deploy --manifest release.json
```

Во время выпуска допустим 502. Preflight и pull выполняются до остановки,
после чего routine deploy последовательно запускает forward migration,
desktop/app/workers, Telegram webhook и доказательные smoke checks. Adoption и
desktop seed принадлежат только одноразовому bootstrap. Ошибка не запускает
money workers; повторный запуск идемпотентен. Backup automation и runtime
rollback намеренно отсутствуют по решению owner. Подробнее:
[DEPLOYMENT.md](DEPLOYMENT.md).

## Источники правды

- ORM и Alembic migrations — фактический контракт БД.
- OpenAPI — контракт API; клиенты генерируются, ручные response interfaces не
  добавляются.
- `fbctl/`, `deploy/compose/` и immutable release manifest — production topology.
- `packages/shared/` и `packages/operator-api/` — общая семантика web/TMA.
- `docs/playbooks/` — только актуальные операционные сценарии.

## Требования

- Python 3.12+
- Node.js 22+ и pnpm 11.6
- Docker Engine и Docker Compose v2
- PostgreSQL 16 для production-like проверок

Внутренний проект. Не для внешнего использования без согласования.
