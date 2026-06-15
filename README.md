# FB Stop Bot

Бот для real-time мониторинга и автоматической остановки объявлений Facebook
Ads по стоп-правилам. Подключается к Ads Manager через anti-detect браузер
(Vision + Playwright + Node.js gRPC), оценивает 7 стоп-правил, шлёт алерты
в Telegram, отключает объявления и подключает Marketing API для latency-
tolerant операций (создание кампаний, изменение бюджетов).

---

## Возможности

- **Real-time мониторинг** Ads Manager через Vision-сессию (интервал по умолчанию 90 с).
- **7 стоп-правил** с двухуровневой WARNING (80% от порога) и STOP логикой:
  CPC / CPL / CPR / реги без депов / расход без депа / расход с депом / frequency-anomaly (opt-in).
- **Telegram-бот**: алерты с inline-кнопками `Отключить` / `Отложить`, команды `/start`, `/help`, `/spy <slot> <country>` (Ad Library).
- **Автоматическое отключение** через очередь `task_queue` (outbox-pattern) с retry exponential backoff.
- **Marketing API** через Vision-сессию (`page.evaluate(fetch)`) для mutations: pause/activate/duplicate campaign, set budget, bulk operations, create campaign.
- **Ad Library spy** (по команде `/spy`): сканирование рекламы конкурентов, классификация, S/A/B/C-ранжирование, markdown-отчёт.
- **Ежедневный дайджест** в Telegram в 9:00 UTC (агрегации по `alert_events`, `task_queue`, `ad_metrics`).
- **Рекомендации на включение**: воркер анализирует выключенные ads через 5+ минут после disable и предлагает revert если метрики восстановились.
- **AI-ассистент** в Telegram (`/ask`): READ-tools (insights, find_ads, account_health) и DRAFT-tools (создание `task_queue` со `status='draft'` для подтверждения пользователем).

---

## Архитектура (краткое)

```
   Vision browser           Telegram API           AdSet.pro tracker
        |                        |                        |
   [browser-agent]          [tg_poller]             [FastAPI postback]
   Node.js gRPC :50051                                    |
        |                        |                        v
        +-----[observer_worker]--+--[disable/enable]--[task_queue]
        |                        |          ^             |
   [meta_api_worker]    [telegram_alerts]    +--[reconciler]
        |                                                 |
        +---[health_watchdog]---[Redis :6380]<--heartbeats+
                                       |
                                  [Postgres :5433]
                                  35 таблиц, 7 partitioned
```

12 Python воркеров + FastAPI + Node.js gRPC + 2 фронта. Полный список —
в [CLAUDE.md](CLAUDE.md) § Architecture.

---

## Быстрый старт

```bash
cp .env.example .env             # заполнить POSTGRES_PASSWORD, ENCRYPTION_KEY,
                                  # VISION_X_TOKEN, VISION_PROFILE_ID, TELEGRAM_BOT_TOKEN
make bootstrap                    # Docker + venv + установка зависимостей + apply schema
./run.sh                          # старт всех воркеров через supervisord
./run.sh --down                   # остановка
./run.sh --logs                   # tail -20 каждого *.log
```

Развёрнутая инструкция (внешние зависимости, секреты, production checklist) —
[DEPLOYMENT.md](DEPLOYMENT.md). Подробности по архитектуре, воркерам,
конвенциям кода — [CLAUDE.md](CLAUDE.md).

---

## Тесты и линтинг

```bash
make verify                       # lint + unit + integration
make test-unit
make test-integration             # требует поднятого Postgres
ruff check .
ruff format .
cd services/browser-agent && npm test
```

---

## Команды Makefile

```bash
make help                         # список всех целей
make api                          # uvicorn apps.api.main:app --reload (порт 8000)
make observer | disable-worker | enable-worker | meta-api-worker
make telegram | health-watchdog | digest-scheduler | enable-reco-worker
make cleanup-worker | reconciler-worker | creator-worker | creator-recorder
make backup-secrets | restore-secrets | apply-schema
make proto-compile                # перегенерация gRPC stubs
make docker-build | helm-install  # k8s deployment
```

Полный workflow — в [CLAUDE.md](CLAUDE.md) § Commands.

---

## Документация

| Документ | Назначение |
|----------|-----------|
| [DEPLOYMENT.md](DEPLOYMENT.md) | Развёртывание с нуля + production checklist |
| [docs/playbooks/RUNBOOKS.md](docs/playbooks/RUNBOOKS.md) | Реакция на инциденты, восстановление |
| [docs/playbooks/](docs/playbooks/) | Операционные playbooks: залив, креативы, PWA, рынок гео |
| [CLAUDE.md](CLAUDE.md) | Архитектура, воркеры, конвенции (источник правды) |
| [META_INTEGRATION_PLAN.md](META_INTEGRATION_PLAN.md) | Marketing API интеграция, этапы |
| [DB_REDESIGN.md](DB_REDESIGN.md) | Схема БД, партиционирование, retention |

---

## Требования

- Linux / macOS
- Python 3.12+, Node.js 20+
- Docker + Docker Compose v2
- Vision anti-detect browser (внешний, `:3030`)

См. [DEPLOYMENT.md § 1](DEPLOYMENT.md) для полного списка.

---

## Лицензия

Внутренний проект. Не для внешнего использования без согласования.
