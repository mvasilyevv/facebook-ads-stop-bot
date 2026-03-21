# Facebook Ads Stop Bot

Сервис для наблюдения и автоматического управления объявлениями в Facebook Ads Manager через anti-detect браузер.

## Запуск

Основная точка входа для локального запуска всего проекта одной командой:

```bash
cp .env.example .env
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
./run.sh
```

`./run.sh` вызывает `scripts/bootstrap.sh`, который:

1. проверяет Docker, Python и `.env`;
2. поднимает `Postgres` и `Redis` через Docker Compose;
3. ждет готовности `Postgres`;
4. применяет миграции Alembic;
5. сам запускает `api`, `worker`, `browser_host` и React UI;
6. печатает ссылки на UI и API после готовности.

Полезные режимы запуска:

```bash
./run.sh --check   # только проверки окружения
./run.sh --down    # остановить стек
```

Если нужен запуск через `Makefile`, доступны актуальные обертки:

- `make up` — полный запуск через `scripts/bootstrap.sh`
- `make down` — остановка через `scripts/bootstrap.sh --down`
- `make logs` — логи docker compose

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
