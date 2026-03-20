# Facebook Ads Stop Bot

Сервис для наблюдения и автоматического управления объявлениями в Facebook Ads Manager через anti-detect браузер.

## Запуск

Локальная разработка без Docker для приложений:

```bash
cp .env.example .env
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
make dev
```

Команда `make dev` пытается поднять `Postgres` и `Redis` через Docker Compose, применяет миграции, а затем запускает `api`, `worker`, `browser_host` и React UI. Если Docker Compose недоступен, скрипт предполагает, что инфраструктура уже поднята отдельно.

Compose-сценарий для инфраструктуры и backend-сервисов:

```bash
make compose-up
```

Остановить compose-стек можно командой `make compose-down`, а смотреть логи - `make compose-logs`.

Для отдельных процессов доступны команды:

- `make dev-backend`
- `make dev-worker`
- `make dev-browser-host`
- `make dev-frontend`

## Компоненты

- `api` — HTTP API для операторов и будущего интерфейса.
- `worker` — планировщик фоновых задач и публикация событий.
- `browser_host` — edge-агент для anti-detect браузера.
- `notifier` — Telegram-уведомления.
- `core` — общая доменная логика, модели и правила.

## Проверки

- `make test` - полный прогон тестов.
- `make lint` - `ruff check`.
- `make format` - `ruff format`.
- `make precommit` - локальные pre-commit проверки.
