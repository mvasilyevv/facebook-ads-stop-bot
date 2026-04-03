# 🛑 FB Stop Bot

Бот для мониторинга и автоматической остановки объявлений Facebook Ads по стоп-правилам.

Подключается к Ads Manager через anti-detect браузер (Vision), парсит метрики, оценивает 6 стоп-правил, отправляет алерты в Telegram, отключает объявления по команде и поддерживает отдельный worker рекомендаций на включение.

## Возможности

### 📊 Мониторинг в реальном времени
- Автоматическое сканирование таблицы Ads Manager с настраиваемым интервалом (по умолчанию 90 сек)
- Парсинг через `data-surface` атрибуты Facebook — campaign, adset, ad name, spend, clicks, CPC, leads, CPL, registrations, CPR
- Плавный скролл с имитацией поведения человека
- Обновление таблицы через кнопку «Обновить» без перезагрузки страницы

### ⚡ 6 стоп-правил с двумя уровнями
Каждое правило имеет **WARNING** (80% от порога) и **STOP** порог:

| # | Правило | По умолчанию (STOP) |
|---|---------|---------------------|
| 1 | **CPC** — цена клика > X% CPA | 2% CPA |
| 2 | **CPL** — цена лида > X% CPA | 10% CPA |
| 3 | **CPR** — цена регистрации > X% CPA | 20% CPA |
| 4 | **Реги без депов** — N рег при 0 депозитов | 5 регистраций |
| 5 | **Расход без депа** — расход X-Y% CPA, 0 депозитов | 50–70% CPA |
| 6 | **Расход с депом** — есть деп, расход X-Y% CPA | 70–90% CPA |

Специальная логика: если расход превышает порог, но событий 0 — немедленный STOP (первое событие уже будет выше порога).

### 🔔 Telegram-бот
**Команды:**
- `/start` — главное меню с кнопками
- `/status` — статус мониторинга (объявления, алерты, расход)
- `/ads` — список объявлений с метриками и пагинацией
- `/offers` — офферы с CPA
- `/rules` — текущие стоп-правила
- `/disabled` — отключённые объявления
- `/settings` — настройки бота
- `/set interval 60` / `/set warning 75` — быстрая настройка

**Алерты:** сгруппированные сообщения с метриками + inline-кнопка «Отключить» для STOP-алертов.

### 🚫 Автоматическое отключение объявлений
- Нажатие кнопки «Отключить» в Telegram → задача в очереди (DisableTask)
- Disable Worker выполняет Playwright-клик в Ads Manager
- Retry с exponential backoff при ошибках (30с → 60с → ... → 5 мин)
- Отбивка в Telegram после успешного отключения

### 🟡 Рекомендации на включение
- Отдельный worker рекомендаций на включение анализирует выключенные объявления и готовит события для ручного возврата в работу
- Рекомендации можно просматривать и обрабатывать отдельно от disable-flow

### 📈 Dashboard (Web UI)
- Сводная статистика: объявления, алерты, расход, отключённые
- Таблица объявлений с фильтрами по статусу и офферу
- CRUD офферов и настройка правил для каждого оффера
- История алертов и задач на отключение
- Настройки Observer и Telegram

### 🧠 Матчинг офферов
Оффер автоматически привязывается к объявлению по вхождению кода в название кампании/объявления:
- Оффер `DRC_CR2` → объявление `DRC_CR2_CR002` ✅
- При нескольких совпадениях — приоритет у самого длинного кода

### 🔒 Конечный автомат (FSM)
Однонаправленные переходы состояний:
```
NORMAL → WARNING_SENT → STOP_SENT → CLAIMED → DISABLED
```
- UUID-токены для идемпотентности
- Повторные алерты одной стадии не отправляются
- Нельзя вернуться из терминальных состояний

## Быстрый старт

### 1. Настройка окружения
```bash
cp .env.example .env
# Отредактируйте .env — заполните VISION_X_TOKEN, VISION_PROFILE_ID, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

### 2. Запуск одной командой
```bash
./run.sh
```

Скрипт автоматически:
- Поднимет Docker-контейнеры (Postgres)
- Создаст Python venv и установит зависимости
- Применит миграции БД
- Запустит API, Observer Worker, Disable Worker, Enable Recommendation Worker, Enable Worker, Telegram Poller и Frontend

### 2a. Workflow через Makefile
Если удобнее работать как с Gradle/Maven-задачами, используйте:

```bash
make help             # список всех команд
make bootstrap        # docker + зависимости + миграции
make test-telegram    # быстрый Telegram-набор тестов
make verify           # lint + Telegram smoke + frontend build
make start            # полный запуск через run.sh
make stop             # остановка всех сервисов
make enable-recommendation-worker  # worker рекомендаций на включение
```

### 3. Остановка
```bash
./run.sh --down
```

### Ручной запуск (для разработки)
```bash
docker compose up -d                                              # Postgres
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'        # зависимости
uvicorn apps.api.main:app --host 0.0.0.0 --port 8100 --reload    # API
python run_observer.py                                             # Observer
python -m apps.telegram_poller.main                                # Telegram
python run_disable_worker.py                                       # Disable Worker
python run_enable_recommendation_worker.py                         # Worker рекомендаций на включение
python run_enable_worker.py                                        # Enable Worker
cd frontend && npm install && npm run dev                          # React UI
```

То же самое через `make`:

```bash
make bootstrap
make api
make observer
make telegram
make disable-worker
make enable-recommendation-worker
make enable-worker
make frontend
```

## Архитектура

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│  Vision Browser │    │   Telegram API   │    │   React UI      │
│  (anti-detect)  │    │                  │    │   :5173         │
└────────┬────────┘    └────────┬─────────┘    └────────┬────────┘
         │                     │                        │
    ┌────▼────┐           ┌────▼─────┐            ┌─────▼─────┐
    │Observer │           │ TG Poller│            │  FastAPI  │
    │ Worker  │           │          │            │   :8100   │
    └────┬────┘           └────┬─────┘            └─────┬─────┘
         │                     │                        │
         └─────────┬───────────┴────────────────────────┘
                   │
         ┌──────────▼──────────┐
         │ Enable Recommendation│
         │ Worker               │
         └──────────┬──────────┘
                    │
            ┌───────▼───────┐
            │   Postgres    │
            │    :5433      │
            └───────┬───────┘
                    ▲
                    │
            ┌───────┴───────┐
            │  Disable      │
            │  Worker       │
            └───────┬───────┘
                    │
            ┌───────┴───────┐
            │  Enable       │
            │  Worker       │
            └───────────────┘
```

## Требования

- Python 3.12+
- Docker & Docker Compose
- Node.js (для frontend)
- Vision anti-detect browser (внешний сервис, порт 3030)

## Структура проекта

```
├── apps/
│   ├── api/main.py              # FastAPI — 14 эндпоинтов с БД
│   ├── observer_worker/main.py  # Цикл мониторинга
│   ├── disable_worker/main.py   # Цикл отключения
│   ├── enable_recommendation_worker/main.py # Цикл рекомендаций на включение
│   └── telegram_poller/main.py  # Long polling TG
├── core/
│   ├── browser/                 # Vision API + Playwright CDP
│   ├── models/                  # SQLAlchemy ORM (7 моделей)
│   ├── observer/                # Сервис оценки + FSM
│   ├── rules/                   # 6 стоп-правил + типы
│   ├── scanner/                 # Парсер DOM Ads Manager
│   ├── telegram/                # Клиент + рендеринг + бот
│   ├── db/                      # Engine + session factory
│   └── config.py                # Pydantic Settings
├── frontend/                    # React 19 + Vite
├── migrations/                  # Alembic
├── tests/                       # pytest + pytest-asyncio
├── run.sh                       # Единый скрипт запуска
├── run_observer.py              # Точка входа Observer
├── run_disable_worker.py        # Точка входа Disable Worker
├── run_enable_recommendation_worker.py # Точка входа Enable Recommendation Worker
├── run_enable_worker.py         # Точка входа Enable Worker
├── docker-compose.yml           # Postgres
└── .env.example                 # Переменные окружения
```

## Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `POSTGRES_HOST` | Хост Postgres | `localhost` |
| `POSTGRES_PORT` | Порт Postgres | `5433` |
| `POSTGRES_DB` | Имя БД | `fb_stop_bot_v2` |
| `POSTGRES_USER` | Пользователь | `fb_stop_bot_v2` |
| `POSTGRES_PASSWORD` | Пароль | `fb_stop_bot_v2` |
| `API_KEY` | Ключ аутентификации API | — (без ключа API открыт) |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота | — |
| `TELEGRAM_CHAT_ID` | ID чата для алертов | — |
| `VISION_X_TOKEN` | X-Token Vision API | — |
| `VISION_API_URL` | URL Vision API | `http://127.0.0.1:3030` |
| `VISION_PROFILE_ID` | ID профиля Vision | — |
| `DEFAULT_OBSERVER_INTERVAL_SECONDS` | Интервал сканирования | `90` |

## Тестирование

```bash
pytest tests/ -x          # все тесты
ruff check .              # линтер
ruff format .             # форматирование
```

Или через `make`:

```bash
make test
make test-unit
make test-telegram
make lint
make format
make frontend-build
make verify
```

## Варианты улучшения

### Приоритет 1 — Надёжность
- **Systemd / Docker Compose для воркеров** — автоперезапуск при падении вместо ручного `run.sh`
- **Alembic-миграция** — сгенерировать начальную миграцию (`alembic revision --autogenerate`), сейчас таблицы создаются через `create_all`
- **Rate limiting Telegram** — backoff при 429 ошибках от Telegram API
- **Healthcheck эндпоинты** — `/health` для каждого воркера, мониторинг liveness

### Приоритет 2 — Функциональность
- **Deposits из внешнего источника** — парсер не видит deposits в стандартной таблице Ads Manager, нужна интеграция с трекером или API
- **Расписание мониторинга** — пауза ночью (2–6 UTC), чтобы не тратить ресурсы
- **Dry-run режим** — показать что сработало бы, без реального отключения
- **Временные правила** — STOP если объявление работает > N дней без конверсий
- **Массовые операции** — выбрать несколько объявлений, применить действие

### Приоритет 3 — UX
- **WebSocket / SSE** — обновление dashboard в реальном времени без ручного refresh
- **Графики расходов** — визуализация трендов spend/CPC/CPL за период
- **Уведомления в другие каналы** — Slack, Discord, webhook на произвольный URL
- **Аудит-лог** — кто и когда нажал «Отключить», история изменений правил
- **Multi-user** — роли (admin, viewer), авторизация в dashboard
