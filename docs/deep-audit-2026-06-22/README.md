# Deep Audit FB Stop Bot — 2026-06-22

Глубокий архитектурный аудит: не только баги, но **карта работы сервиса** (зависимости,
последовательности вызовов, взаимодействия) + стратегия улучшений. Read-only расследование,
ничего не чинилось. Команда — 23 агента (архитекторы + ревьюеры + скептики-верификаторы) по
11 подсистемам, с адверсариальной верификацией каждого CRIT/HIGH.

Не дублирует `docs/audit/AUDIT_2026-06-17.md` — здесь только новое/углублённое.

## Структура модуля

| Файл | Что внутри |
|---|---|
| [`00-system-map.md`](00-system-map.md) | Сквозная топология: как сервисы взаимодействуют + полный жизненный цикл scan→warning→stop→pause→enable через все слои |
| [`99-risk-synthesis.md`](99-risk-synthesis.md) | Кросс-каттинг риск-синтез (после верификации): таблица severity×подсистема, топ-риски, сквозные паттерны, порядок устранения |
| `arch/<подсистема>.md` ×11 | Карта архитектуры подсистемы: компоненты / последовательности вызовов / зависимости / потоки данных / внешние взаимодействия / инварианты |
| `findings/<подсистема>.md` ×11 | Находки по severity с `файл:строка`, impact, fix, confidence |
| [`improvements/00-improvement-roadmap.md`](improvements/00-improvement-roadmap.md) | **Дорожная карта улучшений**: quick-wins vs big-bets vs skip, привязка к корневым причинам |
| `improvements/<тема>.md` ×5 | библиотеки / архитектура / язык-рантайм / перф-масштаб / DX-тесты-наблюдаемость — каждая рекомендация с why/benefit/effort/risk/verdict |

## Итог Фазы 1 (после адверсариальной верификации)

| | CRIT | HIGH | MID | LOW |
|---|:--:|:--:|:--:|:--:|
| **ИТОГО** | **1** | **11** | **38** | **41** |

Костяк (race-safe outbox claim/mark, attempt_count канон, partition-pruning, batch JSONPath-encode,
FSM-guards, owner-ACL) — подтверждён качественным. Проблемы сконцентрированы на **стыках слоёв**
(outbox↔каталог, observer↔meta_api, session↔cabinet) и на **необратимых money-действиях без последней
проверки**.

### Топ money/security (проверены по коду вручную)

1. **CRIT — orphan Meta-мутации после bulk-delete** (`apps/api/routers/v1/ads_admin.py`). `DELETE FROM fb_ads`
   не отменяет задачи в `task_queue` (outbox не FK-связан) → orphan `activate_ad` вслепую ре-включает открут
   на удалённом из дашборда объявлении.
2. **HIGH — NULL owner_tag в мульти-кабинете** (`core/observer/queries.py`). Пустой owner_tag → owner-фильтр
   пропускает всё → авто-стоп **чужой** рекламы в shared-кабинете без draft (необратимо).
3. **HIGH — автостарт включает мёртвые ады** (`cabinet_scheduler` + `is_active` монотонно-TRUE). Документированной
   `resolve_owner_ad_ids_by_dates` нет; резолв по `is_active=TRUE` без даты → ранее снятые ады активируются каждое утро.
4. **HIGH — bulk-стоп с полным отказом Meta = succeeded** (`apps/meta_api_worker/main.py`). `mark_succeeded` без
   чтения `result['success']` → тихий слив бюджета на ручном/AI bulk-pause.
5. **HIGH — naive SUM кумулятивных метрик в enable-reco** (`core/enable_reco/analyzer.py`). 3-й рецидив класса CRIT-1.
6. **HIGH ×2 — self-heal браузер-сессии лечит не ту вкладку** (`browser-agent`). В мульти-кабинете канал авто-стопа
   мёртвого кабинета не восстанавливается.

### Сквозные паттерны (корневые причины)

1. Naive SUM кумулятивных `ad_metrics` — правило держится review, не типами/линтером (3-й рецидив).
2. Контракт writer↔reader держится дисциплиной, не типами (heartbeat-имена, observer:runtime, result['success']).
3. Сессионное/глобальное состояние там, где нужно per-cabinet/per-entity (heal-state, is_active, module-global кэши).
4. Идемпотентность/owner-проверка не на исполнении (orphan-задачи, NULL owner_tag).
5. Документация расходится с кодом (`resolve_owner_ad_ids_by_dates`, topics-handlers).

## Главные выводы Фазы 2 (стратегия улучшений)

**Ядро трогать не надо** — переписывать костяк, менять язык, тащить тяжёлые фреймворки нет смысла.
Окупаемость — в закрытии **рецидивирующих классов тихих money-багов**, которые проходят сквозь
1000+ тестов, потому что держатся дисциплиной/комментариями, а не **типами и CI**.

- **Quick wins (усилие S, делать первым):** типизированный `MutationResult` вместо `dict['success']`
  (закрывает money-дыру R3 по типу); `latest.spend` вместо SUM в enable-reco (R2); 2 теста на эти границы;
  CI grep-guard против naive-SUM и full-scan; `started_at` в `_finish_scan_run`; таймаут на AI-роутер;
  `setup_sentry()` в entry-points; Frontend Vitest+tsc и `gen:api` diff в CI.
- **Big bets (усилие M/L, стратегические):** типизированные Redis-контракты (observer:runtime/heartbeat/pubsub);
  единый `heartbeat_loop`+worker-каркас (−250 LOC дубля); реестр воркеров как источник `EXPECTED_WORKERS`;
  wire `core/metrics.py` + money-метрики (autostop rate, outbox depth) в Grafana; Redis leader-lock перед
  multi-replica; кодоген/контракт-тесты ScannedAdRow и Graph-error TS↔Python. Самый крупный — `procrastinate`
  как движок очереди (−600…900 LOC), но только `consider`/L и пилот на не-money воркере.
- **Skip (оставить как есть):** circuit breaker, TG-клиент (aiogram/PTB), готовый outbox, APScheduler,
  переезд browser-agent на Playwright-Python, Rust/Go/PyO3 для evaluator, buf/protovalidate, msgspec,
  горизонтальное масштабирование observer — кастом обоснован спецификой или переписывание не окупается.

## Статус
- **Фаза 1 (карта + риск-аудит)** — ✅ завершена 2026-06-22 (23 агента, 1 CRIT / 11 HIGH / 38 MID / 41 LOW).
- **Фаза 2 (стратегия улучшений)** — ✅ завершена 2026-06-22 (5 research-агентов, context7+web).
- **Исправления** — НЕ начаты (нужен явный go от владельца, money/security вперёд).
