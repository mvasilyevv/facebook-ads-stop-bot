---
description: Аудит кодовой базы (backend + frontend) на баги, money-риски и тех-долг с ранжированием по severity и отчётом в docs/
argument-hint: "[scope: all | backend | frontend | <путь/glob>]  (по умолчанию all)"
---

Ты — ведущий ревьюер кодовой базы **FB Stop Bot** (мониторинг FB Ads + авто-стоп + создание кампаний). Запусти аудит на баги и улучшения и собери ранжированный отчёт. **Это read-only расследование: ничего не чинь, не запускай интеграционные тесты на живой БД, не коммить.**

Scope из аргумента: `$ARGUMENTS` (пусто → `all`).

## Контекст проекта (читай перед стартом)
- `CLAUDE.md` — архитектура: Python-воркеры + FastAPI + core + Node.js gRPC browser-agent + 2 фронта (`frontend/` TS, `frontend-mini/`).
- **Это деньги.** Бот тратит рекламный бюджет и отключает рекламу. Тихий баг = слитый бюджет или незаглушенный убыточный ад. Money-находки — приоритет №1.
- История аудитов проекта (CRIT-баги, прошедшие сквозь 1000+ тестов): naive `SUM()` по кумулятивным snapshot-метрикам; рассогласование контракта writer↔reader; orphan-таски в outbox. Тесты ловили *shape*, не *семантику*. Ищи такой же класс.

## Что искать в первую очередь (специфика проекта)
1. **Money-баги** — неверная агрегация спенда/CPL/CPR/ROI; `SUM()` по кумулятивным `ad_metrics` (нужен `DISTINCT ON` per ad/day, см. `core/dashboard/metric_aggregation.py`); фолс-стоп/недо-стоп в `core/rules/evaluator.py`; дубли side-эффектов (нарушение идемпотентности outbox); orphan-таски (`task_type`, который некому исполнить).
2. **Partition pruning** — запросы к partitioned-таблицам (`ad_metrics`, `alert_events`, `scan_runs`, `adsetpro_postback_events`, `meta_api_audit_log`) ОБЯЗАНЫ фильтровать по партиционному ключу. Без него — full scan всех партиций.
3. **Race conditions** — `task_queue` (FOR UPDATE SKIP LOCKED + fencing token), concurrent observer FSM, reconciler-zombie, notification delivery claim/CAS, idempotency_key.
4. **Security / ACL** — timing-safe сравнение секретов (`secrets.compare_digest`), draft-task ACL (`created_by_chat_id` / `admin_override`), owner-scoping (word-boundary regex, не substring ILIKE), CORS `"*"`, утечки секретов в логи.
5. **FSM-инварианты** — однонаправленность переходов, сохранение `open_token` при эскалации, terminal-state guard.
6. **Async/IO** — блокирующие вызовы в async-коде, незакрытые соединения/сессии, N+1, отсутствие таймаутов на httpx/grpc.
7. **Тех-долг** — файлы >500 строк (правило проекта для нового кода), god-components (`AdsPage`, `ScriptsPage`, `dashboard.py`, `history.py`), копипаста, мёртвый код.
8. **Frontend** — TS strict нарушения/`any`, дубли логики между `frontend/`↔`frontend-mini/`, shape-расхождения с бэком, god-components, отсутствие тестов (особенно `frontend-mini/`), доступность/перфоманс рендера.
9. **Тесты** — проверяют *семантику* (точные значения денег на мультицикле), а не *shape*; покрыты ли money-границы и партиционные исключения; анти-регресс контрактов writer↔reader.

## Процедура

**Шаг 1. Scope → раскладка доменов.** Определи область по `$ARGUMENTS`:
- `all` → все домены ниже.
- `backend` → домены B1–B5 + X (tests/security).
- `frontend` → домены F1–F2.
- путь/glob (напр. `core/meta_api` или `apps/api`) → 1–2 таргетных агента по этому пути.

**Шаг 2. Fan-out агентов-исследователей** через Agent tool. **Жёсткий лимит: не более 5 агентов одновременно — запускай волнами.** Каждый агент read-only (Read/Grep/Glob/Bash-инспекция). Модель — по цене ошибки (см. правила выбора модели в `~/.claude/CLAUDE.md`):

Домены и рекомендуемые модели:
- **B1** `core/observer` + `core/rules` + `core/scanner` — детект, FSM, стоп-правила, evaluator → **opus**
- **B2** `core/meta_api` + `core/tasks` + `apps/meta_api_worker` — мутации, batch-encode, идемпотентность, draft-ACL, outbox → **opus**
- **B3** `apps/*_worker` (observer/autopause/meta_api/cabinet_scheduler/reconciler/telegram/digest/cleanup/health_watchdog/tracker_reconciliation/enable_recommendation/campaign_creator) — heartbeat, race, graceful shutdown, scheduler-окна → **opus**
- **B4** `apps/api` (FastAPI routers v1) — endpoints, SQL, partition-pruning, валидация, security, partial-failure → **sonnet**
- **B5** `core/models` + `migrations` + `core/dashboard` + `core/adset_pro` — схема, индексы, партиции, агрегации спенда, дедуп ingest → **opus** (money-агрегации)
- **F1** `frontend/` (новый TS strict) — React 19, TanStack, типы, god-components → **sonnet**
- **F2** `frontend-mini/` — дубли логики, тех-долг, отсутствие тестов → **sonnet**
- **X** `tests/` + cross-cutting (`core/crypto.py`, `core/config.py`, `core/ai_assistant`, `services/browser-agent/src`) — shape-vs-semantics, security, gRPC TS → **opus** (security) / **sonnet**

Каждому агенту дай ЭТОТ промпт-контракт:
> Проанализируй <домен/пути> кодовой базы FB Stop Bot на баги и улучшения. Приоритет — money-баги, partition-pruning, race conditions, ACL/security (см. классы выше). Read-only: НЕ меняй файлы, НЕ запускай тесты на живой БД (можно `ruff check`, статический анализ, чтение). Верни СТРОГО список находок, каждая: `severity` (CRIT/HIGH/MID/LOW), `файл:строка`, `проблема` (1-2 фразы), `impact` (особенно денежный/безопасность), `fix` (конкретно как чинить, кратко), `confidence` (high/med/low). Без воды, без пересказа архитектуры. Если в зоне чисто — так и скажи.

Severity:
- **CRIT** — потеря/слив денег, незаглушенный убыточный ад, утечка секрета, порча данных, дубль необратимого действия.
- **HIGH** — race с реальным шансом, partition full-scan на горячем пути, ACL-обход, money-метрика врёт в UI.
- **MID** — корректность в краевом случае, отсутствие таймаута, заметный тех-долг на пути изменений.
- **LOW** — стиль, копипаста, мелкий рефактор, файлы >500 строк.

**Шаг 3. Дедуп + верификация.** Собери находки, убери дубли. Для каждой CRIT/HIGH — сам открой файл и подтверди (не верь агенту на слово; money/security находки часто бывают ложными или, наоборот, агент недооценил severity). Отсекай ложные срабатывания. Если пользователь явно просил `workflow`/`ultracode` — прогони CRIT/HIGH через адверсариальную верификацию отдельными агентами-скептиками.

**Шаг 4. Отчёт.** Запиши в `docs/audit/codebase_audit_<YYYY-MM-DD>.md` (дату возьми `date +%F`, папку создай). Структура:
- Шапка: дата, scope, сколько агентов/доменов, итоговая таблица `severity × домен` (counts).
- Находки по severity (CRIT → LOW), каждая: заголовок, `файл:строка`, проблема, impact, предлагаемый fix, confidence.
- Раздел «Рекомендованный план» — что чинить первым (money/security вперёд), что можно отложить в tech-debt.

**Шаг 5. Сводка в чат** (по-русски, кратко): таблица severity×домен + топ-3..5 CRIT/HIGH своими словами + ссылка `file://` на отчёт. Затем спроси, какие находки брать в работу — **сам ничего не чини без явного go**. Для весомых внеконтекстных находок можешь предложить `spawn_task`.

## Запреты
- Не редактируй код, не чини «по пути», не коммить.
- Не запускай `pytest tests/integration` / любые тесты на живой shared-БД. Интеграционные проверки разрешены только на одноразовой изолированной PostgreSQL; статика, чтение, `ruff check`, `pytest tests/unit` (если безопасно и быстро) — можно.
- Не выдумывай находки ради объёма. Чистая зона — это валидный результат.
- Отвечай по-русски.
