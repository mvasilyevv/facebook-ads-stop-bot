# Facebook Ads Stop Bot

Сервис для наблюдения и автоматического управления объявлениями в Facebook Ads Manager через anti-detect браузер.

## Компоненты

- `api` — HTTP API для операторов и будущего интерфейса.
- `worker` — планировщик фоновых задач и публикация событий.
- `browser_host` — edge-агент для anti-detect браузера.
- `notifier` — Telegram-уведомления.
- `core` — общая доменная логика, модели и правила.

## Быстрый старт

1. Скопировать `.env.example` в `.env`.
2. Запустить `docker compose up --build`.
3. Выполнить миграции через `make migrate`.
4. Запустить тесты командой `make test`.
