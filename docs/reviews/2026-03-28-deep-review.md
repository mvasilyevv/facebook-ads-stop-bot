# Глубокое ревью backend, DB, Telegram, frontend и тестов

Дата: 2026-03-28

## Объём проверки

Проверка охватила:

- backend: `apps/api`, `apps/observer_worker`, `apps/disable_worker`, `core/*`;
- Telegram-контур: `apps/telegram_poller`, `core/telegram/*`;
- фронт: `frontend/src/*`;
- тесты: `tests/*`;
- инфраструктурные точки запуска: `run.sh`, `Makefile`, `pyproject.toml`, `frontend/package.json`, `README.md`.

## Фактический статус проверок

| Проверка | Результат |
|---|---|
| `ruff check apps core tests` | `failed`, найдено 14 проблем, включая `ASYNC230`, `ASYNC109`, несортированные импорты и неиспользуемый импорт |
| `pytest --collect-only -q tests` | 127 тестов собрано |
| `pytest tests/unit/test_patchright_imports.py -q -vv` | 2 падения: `ModuleNotFoundError: No module named 'patchright'` |
| `pytest tests/unit/test_observer_improvements.py -q -vv` | зависание после `test_reconnect_on_browser_error` |
| `npm run build` | `passed`, но основной JS chunk ≈ 667.19 kB (`gzip` ≈ 195.32 kB) |

Дополнительно:

- пофайловый прогон unit-наборов показал, что большинство файлов укладываются примерно в 2-4 секунды;
- `tests/unit/test_observer_improvements.py` оказался явным outlier и не завершился в контрольном окне;
- полный `pytest tests -q` в исследовательском прогоне не дошёл до нормального завершения в ожидаемое время.

## Findings

### P1. Инструменты запуска не гарантируют заявленный Python 3.12, из-за чего тестовый контур уже расходится с контрактом проекта

Где:

- `pyproject.toml:10-24`
- `run.sh:97-147`
- `Makefile:3-45`
- `tests/unit/test_patchright_imports.py:6-16`

Почему это важно:

- проект декларирует `requires-python = ">=3.12"`, но локальный `pytest` в проверке стартовал под Python 3.11.14;
- `run.sh` и `Makefile` опираются на просто `python3`, то есть фактическая версия зависит от системного default-интерпретатора;
- в результате уже сейчас тестовый контур даёт ложные/средовые падения, в том числе на `patchright`, и перестаёт быть надёжным сигналом качества.

Что делать:

- жёстко валидировать версию Python на bootstrap;
- переводить все команды на `.venv/bin/python` после проверки версии;
- отделить проблему среды от проблемы кода в тестах `patchright`.

### P1. Observer ставит мониторинг на паузу при любой `RETRYING`-задаче, даже если retry ещё не наступил

Где:

- `apps/observer_worker/main.py:294-341`
- `apps/observer_worker/main.py:1039-1048`

Почему это важно:

- `get_disable_queue_pause_reason()` считает активной и очередь `RETRYING`, даже если `next_retry_at` далеко в будущем;
- `observer_loop()` на этом основании останавливает новый скан;
- при backoff на минуты мониторинг получает слепое окно без фактической работы disable worker и без объективной необходимости держать браузер в паузе.

Что делать:

- разделить `RUNNING`/`PENDING` и `RETRYING`;
- ставить observer на паузу только когда браузер действительно нужен disable/enable-контуру прямо сейчас;
- будущие retry учитывать как диагностику, но не как блокировку сканирования.

### P1. Observer трактует любой `RuntimeError` как браузерный обрыв и уходит в reconnect-flow

Где:

- `apps/observer_worker/main.py:1326-1370`

Почему это важно:

- в reconnect-ветку попадает не только транспортный сбой браузера, но и любой `RuntimeError` из парсинга, диагностики, бизнес-логики или сторонних helper-ов;
- это маскирует реальные application-баги под “ошибку связи с браузером”;
- побочный эффект — ложные reconnect storm и деградация observability.

Что делать:

- сузить исключения до реально браузерных/сетевых;
- для бизнес-ошибок логировать и завершать цикл отдельно;
- разнести transport errors и domain/application errors по разным обработчикам.

### P1. Reconnect-тесты observer изолированы не полностью и зависают вместо детерминированного завершения

Где:

- `tests/unit/test_observer_improvements.py:595-732`
- косвенно: `apps/observer_worker/main.py:251-341`

Почему это важно:

- файл `tests/unit/test_observer_improvements.py` не завершился в контрольном окне и завис именно на reconnect-сценариях;
- эти тесты патчат не все внешние зависимости observer loop;
- итог: медленный или подвисающий suite, который сложно использовать в CI как быстрый guardrail.

Что делать:

- для reconnect-сценариев инъецировать все I/O-helper-ы явно;
- отказаться от частично реальных DB/helper путей в unit-тестах цикла;
- вынести reconnect policy в отдельную тестируемую функцию без бесконечного цикла.

### P2. `spend-history` декларирует агрегированную историю, но возвращает сырые `AdSnapshot`-строки

Где:

- `apps/api/main.py:1563-1592`
- `frontend/src/api.js:99-101`

Почему это важно:

- docstring обещает “агрегацию по временным бакетам”, но реализация возвращает по одной точке на каждый snapshot объявления;
- при росте числа объявлений endpoint быстро раздувает payload;
- frontend в текущем коде этот endpoint вообще не использует, то есть публичный контракт уже дрейфует от реальных потребителей.

Что делать:

- либо реализовать настоящую агрегацию по бакетам;
- либо удалить/скрыть endpoint до появления реального сценария;
- синхронизировать контракт и потребителей.

### P2. Telegram-рендеринг статусов делает лишние round-trip к БД вместо групповых агрегаций

Где:

- `core/telegram/bot_handler.py:265-347`
- `core/telegram/bot_handler.py:350-403`

Почему это важно:

- `_render_start()` и `_render_status()` запрашивают статистику серией отдельных `count()`/`sum()` вызовов;
- бот — это интерактивный контур, поэтому каждое открытие меню множит запросы;
- масштаб пока небольшой, но паттерн уже закреплён и будет мешать дальнейшему росту.

Что делать:

- собрать живой summary в один агрегирующий запрос;
- вынести Telegram query layer отдельно от render layer;
- переиспользовать dashboard-style aggregation там, где это возможно.

### P2. Frontend создаёт лишнюю сетевую нагрузку: две polling-петли и отсутствие cache/dedupe/cancel

Где:

- `frontend/src/pages/DashboardPage.jsx:912-954`
- `frontend/src/api.js:5-48`

Почему это важно:

- на dashboard параллельно живут две независимые polling-петли: полная каждые 30 секунд и частичная каждые 5 секунд;
- fetch-слой не умеет отменять устаревшие запросы, дедуплицировать одинаковые запросы и кешировать данные по ключам;
- при деградации сети это будет давать гонки состояний и лишнюю нагрузку на API.

Что делать:

- перенести данные в domain hooks;
- добавить request cache/dedupe;
- перейти на библиотечный слой наподобие `@tanstack/react-query`.

### P2. `SettingsPage` превратилась в монолит и размывает дизайн-систему inline-style'ами

Где:

- `frontend/src/pages/SettingsPage.jsx:96-119`
- `frontend/src/pages/SettingsPage.jsx:617-635`
- `frontend/src/pages/SettingsPage.jsx:853-1105`

Почему это важно:

- файл вырос до 1364 строк и содержит 95 inline-style блоков;
- визуальные правила расползлись по JSX и стали хуже переиспользоваться;
- в этом же модуле живут clipboard fallback на `document.execCommand('copy')`, polling авторизации, управление тостами, invite-flow, Telegram, Vision и observer-настройки.

Что делать:

- разбить страницу хотя бы на доменные секции и общие UI-примитивы;
- переносить стиль в CSS tokens/classes;
- форму и network-orchestration вынести из page-level компонента.

### P2. API startup смешивает `Alembic`-подход, `create_all()` и небезопасную CORS-конфигурацию

Где:

- `apps/api/main.py:65-84`

Почему это важно:

- `lifespan` всегда делает `Base.metadata.create_all()`, хотя в проекте уже есть миграции;
- это маскирует отсутствие миграции и делает схему менее предсказуемой;
- `allow_origins=["*"]` вместе с `allow_credentials=True` — плохая долгосрочная конфигурация и база для путаницы в поведении браузера.

Что делать:

- оставить `create_all()` только для изолированных тестовых/локальных сценариев;
- в runtime опираться на миграции;
- сузить CORS до явных origin.

## Горячие пути БД

### Dashboard

- `apps/api/main.py:1606-1679` читает все `AlertEvent` и заметный срез `AdSnapshot` в память, а потом агрегирует на Python-стороне.
- На текущем масштабе это работает, но при росте истории быстро упрётся в latency и payload size.

### Observer

- `apps/observer_worker/main.py:402-473` каждый rollover тянет текущий day-slice snapshot-ов целиком.
- `apps/observer_worker/main.py:576-646` делает N+1-паттерн по `DisableTask` для stuck state reconcile.
- `apps/observer_worker/main.py:721-790` делает N+1-паттерн при сборе reminder alerts.

### Telegram

- `core/telegram/bot_handler.py:265-403` рендерит UI через серию счётчиков и сумм вместо одного агрегирующего слоя.

## Тестовый аудит

### Что уже покрыто

- правила и FSM;
- dashboard helper-ы и часть API-логики;
- disable worker;
- Telegram poller, renderer, handler, service;
- parser и часть observer-сценариев.

### Что не покрыто или покрыто недостаточно

- интеграционный контур FastAPI + Postgres;
- миграции Alembic как реальные upgrade/downgrade сценарии;
- enable worker;
- frontend interaction/render tests;
- browser arbitration между observer и disable/enable worker;
- restart flow из API;
- реальные контракты Telegram/Vision через изолированные mocks/adapters.

### Дополнительные наблюдения

- `tests/conftest.py` сейчас фактически пустой, общей тестовой инфраструктуры почти нет.
- `frontend/package.json:6-21` не содержит test script и вообще не настроен под frontend testing.

## Быстрые победы

- Зафиксировать Python 3.12 как обязательный preflight в `run.sh` и `Makefile`.
- Починить или временно quarantine-ить `tests/unit/test_patchright_imports.py` и зависающие reconnect-тесты.
- Убрать паузу observer для будущих `RETRYING` задач.
- Сузить reconnect catch block и разделить transport/application ошибки.
- Вынести из `SettingsPage` хотя бы Telegram и Vision секции в отдельные компоненты.
- Закрыть `ruff`-ошибки по async file I/O и импорту.

## Средние рефакторинги

- Разбить `apps/api/main.py` на routers/schemas/services/query helpers.
- Вынести repository/service слой для `observer settings`, `offers`, `dashboard`, `disable tasks`, `telegram settings`.
- Разбить `core/telegram/bot_handler.py` на command handlers, query layer и keyboard/render helpers.
- Добавить domain hooks на фронте и убрать page-level orchestration из `DashboardPage` и `SettingsPage`.

## Крупные архитектурные работы

- Нормализовать процесс-менеджмент: убрать ручной restart worker через PID-файл из HTTP-эндпоинта.
- Явно описать арбитраж браузера между observer, disable worker и enable worker.
- Ввести integration test harness с временным Postgres и отдельными contract tests для Telegram/Vision.
- Пересобрать frontend на более модульную data-модель с query/cache слоем и route-level code splitting.

## Рекомендации по библиотекам

| Библиотека | Зона | Польза | Цена внедрения | Риск | Рекомендация |
|---|---|---|---|---|---|
| `@tanstack/react-query` | frontend data | cache, dedupe, refetch policy, отмена гонок | средняя | низкий | внедрять в первой волне |
| `react-router-dom` | frontend navigation | нормальные route state, code splitting, deep links | низкая-средняя | низкий | внедрять вместе с декомпозицией фронта |
| `react-hook-form` + `zod` | frontend forms | меньше ручного state, лучше валидация настроек | средняя | низкий | полезно для `Settings` и `Offers` |
| `clsx` | frontend UI | дешёвая очистка conditionals в JSX | низкая | низкий | можно добавить сразу |
| `vitest` + `@testing-library/react` | frontend tests | нормальный render/interaction слой | средняя | низкий | обязательно в первой тестовой волне |
| `msw` | frontend/API tests | стабильные сетевые моки без ручного fetch monkeypatch | средняя | низкий | добавлять вместе с `vitest` |
| `testcontainers[postgresql]` | backend integration | реальные integration tests против Postgres | средняя | средний | лучший следующий шаг для API/DB |
| `pytest-httpx` или `respx` | backend contract tests | предсказуемые моки для Telegram/Vision/httpx | низкая | низкий | выбрать одну библиотеку и стандартизовать |
| `pytest-cov` | test quality | видимость покрытия по слоям | низкая | низкий | добавить сразу |
| `pyright` | static typing | ранний сигнал по async и typed contracts | средняя | низкий-средний | вводить после стабилизации hot paths |

## Рекомендуемая последовательность работ

1. Стабилизировать среду и тесты: Python 3.12, `patchright`, reconnect tests, `ruff`.
2. Исправить observer hot paths: pause logic, reconnect exception boundary.
3. Ввести integration тесты на Postgres и контрактные моки для Telegram/Vision.
4. Разбить `api/main.py` и `telegram/bot_handler.py` на сервисные слои.
5. Перевести frontend на query layer и декомпозировать `SettingsPage`/`DashboardPage`.
6. После стабилизации — заняться bundle size и route-level code splitting.
