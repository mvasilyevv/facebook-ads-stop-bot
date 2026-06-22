# Аудит: Вспомогательные воркеры

> Дата: 2026-06-22 | Ревьюер: deep-audit subagent
> Подсистема: digest_scheduler / cleanup_worker / tracker_aggregator_worker /
>              enable_recommendation_worker / creator_worker / creator_recorder / telegram_poller

---

## CRIT

### CRIT-1 — enable_recommendation_worker: потерянная рекомендация при сбое TG после INSERT

**Файл**: `apps/enable_recommendation_worker/main.py:399-434`

**Problem**: Если `insert_recommendation` успешно создала запись (RETURNING id → `new_id != None`), но следующий за ней `send_alert` вернул `False` (TG недоступен) — Redis-ключ `enable_reco:last:{ad_id}` НЕ ставится (строка 430 пропускается через `continue` на 427). В следующем цикле:
1. `is_recently_recommended` → False (нет Redis ключа) → идём дальше
2. `insert_recommendation` с тем же `idem_key` → `ON CONFLICT DO NOTHING` → `RETURNING` возвращает 0 строк → `new_id = None`
3. `new_id is None` → `skipped_decision` → алерт **никогда не будет отправлен** до следующей FSM-транзиции объявления

**Impact**: Если TG недоступен в момент цикла enable_reco — рекомендация об убыточном объявлении теряется навсегда для данной FSM-транзиции. Владелец не получает уведомление о возможности включения объявления. Это прямой money-эффект: потенциально прибыльная реклама остаётся выключенной.

**Fix**: После неудачного `send_alert` удалять запись из `enable_recommendations` (чтобы следующий цикл смог создать новую), либо хранить в `enable_recommendations` флаг `tg_sent=False` и при `new_id is None` — проверять, был ли уже алерт отправлен. Проще всего: перенести `insert_recommendation` ПОСЛЕ успешного `send_alert`, а Redis-дедуп использовать для защиты от дублей в период повторной попытки. Либо: при `new_id is None` — пробовать повторно отправить TG (SELECT существующей записи по idem_key → send).

**Confidence**: high

---

### CRIT-2 — enable_recommendation_worker: наивный SUM кумулятивных снимков ad_metrics

**Файл**: `core/enable_reco/analyzer.py:77-84`, `apps/enable_recommendation_worker/main.py:116-125`

**Problem**: `_aggregate_spend(metrics)` суммирует поле `spend` из всех метрик в списке: `total += m.spend`. Но `ad_metrics.spend` — кумулятивный снимок (нарастающий итог с начала cabinet-дня). После паузы рекламы на всех снимках в окне стоит одинаковое значение S (последний реальный spend до паузы). При 3-часовом окне и 15-минутном интервале сканирования это даёт ~12 снимков с одним и тем же S → `_aggregate_spend` = 12*S.

Правило 1 (`total_spend <= cpa * 0.5`): при S=100 и cpa=300 → cap=150 → 12*100=1200 >> 150 → правило НЕ срабатывает, хотя реальный spend_after_disable ≈ 0.

**Impact**: Подавляются валидные рекомендации включения (false negative). Объявления, которые стоит включить, остаются выключенными. Денежный эффект: недополученная выручка от корректно работающей рекламы. Это тот же класс бага что CRIT-1 (Round 10), только в другом месте — `analyzer.py`, а не dashboard.

**Fix**: Заменить `_aggregate_spend` на `_latest_spend`: использовать значение последнего снимка (или max, они равны для паузированного объявления). Аналогично DISTINCT ON в `metric_aggregation.py` — для spend_window_check достаточно `latest.spend or Decimal(0)`. Формулировка Rule 1 становится: «текущий (последний) spend с начала cabinet-дня не превысил cpa*0.5 — значит дневной бюджет ещё не исчерпан».

**Confidence**: high

---

## HIGH

### HIGH-1 — enable_recommendation_worker: нет верхней границы cycle_ts в запросе ad_metrics

**Файл**: `apps/enable_recommendation_worker/main.py:116-123`

**Problem**:
```sql
WHERE ad_id = :aid AND cycle_ts > :since
```
Отсутствует верхняя граница (`AND cycle_ts < NOW()`). Для PostgreSQL partition pruning это означает, что планировщик вынужден открывать все партиции с `since` до последней существующей. В типичном проде: 1 текущая партиция + 0–1 следующая (еще пустая) — риск минимален. Но при `since = 3h ago` и наличии 2–3 партиций (старые данные не pruned) — Seq Scan по всем.

**Impact**: В пиковые периоды (50 кандидатов × 50 запросов по ad_metrics без pruning) — дополнительная нагрузка на БД. Не money-критично, но замедляет цикл enable_reco.

**Fix**: Добавить `AND cycle_ts <= :now` в `_METRICS_SQL`. `now` уже доступен в вызывающем коде (`run_once` строка 342).

**Confidence**: high

---

### HIGH-2 — creator_recorder: TG client не ротируется при смене bot_token

**Файл**: `apps/creator_recorder/main.py:319-343`

**Problem**: `_build_tg_client(engine)` вызывается один раз при старте `main_loop`. После этого `tg_client` хранит `bot_token` из БД на момент старта. Если токен ротируется через UI (Settings → Telegram), creator_recorder продолжает использовать старый токен — TG-подтверждения о сохранении плана перестают доходить. Требует ручного рестарта процесса.

**Impact**: Пользователь не получает подтверждение об успешной записи плана в БД. Может считать запись неудавшейся и повторить, создавая дубли планов (хотя UTC-suffix retry в `_insert_plan` смягчает это). Не money-критично, но нарушает UX-инвариант, который явно держит `telegram_poller` (hot reload токена раз в 60с).

**Fix**: В `pubsub_loop` перед каждым `handle_record_stop` перечитывать telegram_config и пересоздавать tg_client если токен изменился. Либо по аналогии с `telegram_poller`: отдельный refresh в главном цикле с интервалом.

**Confidence**: high

---

### HIGH-3 — digest_scheduler: частичный сбой рассылки не сигнализируется и блокирует повтор

**Файл**: `apps/digest_scheduler/main.py:97-122, 183-188`

**Problem**: `_send_digest_to_recipients` итерируется по всем recipients, ловит ошибки per-recipient, возвращает `(ok, fail)`. После рассылки Redis-флаг `digest:sent:YYYY-MM-DD` ставится всегда (строка 186), даже если `fail > 0` и часть recipients не получила дайджест. Повторный прогон в тот же день видит флаг → `"already_sent"` → пропускает.

**Impact**: Recipient, которому не удалось доставить дайджест из-за временного сбоя TG, не получит его в этот день. Для monitoring-дайджеста это потеря операционной видимости (сколько потрачено, сколько остановлено).

**Fix**: Не устанавливать Redis-флаг, если `ok == 0` (никто не получил). Или хранить список `failed_recipient_ids` и делать повтор только для них в следующем тике. Минимальный fix: лог WARNING с `fail/ok` счётчиком уже есть (строка 183), но флаг надо ставить только при `ok > 0`.

**Confidence**: high

---

## MID

### MID-1 — cleanup_worker: нет catch-up при пропущенном запуске

**Файл**: `apps/cleanup_worker/main.py:55-60`

**Problem**: `_seconds_until_next_run` планирует следующий запуск только на **завтра в 04:00 UTC**, если текущее время > 04:00 UTC. Нет аналога catch-up семантики как в `digest_scheduler`. Если воркер рестартует в 05:00 после краша, следующий cleanup — через 23ч.

**Impact**: Партиции не создаются, старые не удаляются в течение суток. При высоком потоке событий (тысячи postback/алертов в час) диск может заполниться. Не критично при правильной настройке retention, но повышает риск нехватки места в час пик.

**Fix**: Добавить catch-up аналогично digest_scheduler: если `now > target_today` и в системе нет ключа `cleanup:done:YYYY-MM-DD` в Redis — запустить сразу. При CLEANUP_RUN_ON_START=true уже есть немедленный старт, но это env override, не автоматический catch-up.

**Confidence**: med

---

### MID-2 — enable_recommendation_worker: MetricSnapshot.spend используется как delta в Rule 1

**Файл**: `core/enable_reco/analyzer.py:141-145`

**Problem** (детализация CRIT-2): Rule 1 semantically некорректна даже после замены SUM на latest. Интент правила — «расход с момента отключения небольшой» (бюджет восстановился). Но `ad_metrics.spend` — кумулятивный итог с cabinet-reset (типично 09:00 UTC), не с момента отключения. Если объявление отключили в 20:00 UTC (за 11ч до cabinet reset), `latest.spend` может быть 200 при cpa=300 — Rule 1 провалится, хотя реальный spend после отключения = 0.

**Impact**: False negative рекомендаций для объявлений с высоким spend до момента отключения. Дополняет CRIT-2, но менее критичен (Rule 2/3/4 могут компенсировать).

**Fix**: Для «spend восстановился» правильнее смотреть на `spend` первой метрики ПОСЛЕ отключения (`metrics[0].spend`) vs последней (`latest.spend`). Delta ≈ 0 → бюджет не рос = реклама не показывалась. Это требует передачи `last_transition_at` метрики для сравнения.

**Confidence**: med

---

### MID-3 — telegram_poller: save_poller_offset вызывается per-update, не per-batch

**Файл**: `apps/telegram_poller/main.py:247-250`

**Problem**: `save_poller_offset(engine, offset)` вызывается **один раз после всего батча** обновлений (строки 244-250 вне внутреннего цикла). Это корректно. Однако если `handle_update` упадёт с необработанным исключением (строка 242 ловит, логирует, продолжает цикл), offset всё равно сохраняется — это защита от reprocessing. Но если `save_poller_offset` сам упадёт (строка 248 ловит и логирует) — при рестарте offset не восстановлен. При Telegram 24h дедуп это безопасно (updates не будут обработаны повторно), но в редких случаях edge-update'ы с малым id могут обработаться дважды.

**Impact**: Двойная обработка callback (например, `dis:` создаёт 2 задачи на отключение) при crash+restart в узком окне. Защита: `idempotency_key` в `_create_toggle_mutation` предотвращает дубль в task_queue.

**Confidence**: low

---

### MID-4 — digest_scheduler: DIGEST_WINDOW_MINUTES — мёртвый параметр

**Файл**: `apps/digest_scheduler/main.py:46, 62, 74-75, 284`

**Problem**: `DigestWindow.window_minutes` заполняется из `DIGEST_WINDOW_MIN` (env), логируется при старте, но **не используется в `is_in_send_window`** (функция явно документирует это в docstring: «window.window_minutes сохранён в API только для обратной совместимости»). Изменение `DIGEST_WINDOW_MIN` не влияет на поведение.

**Impact**: Операторский tech-debt. Можно ввести в заблуждение при отладке (думаешь что изменил окно, а поведение не изменилось). Нет денежного риска.

**Fix**: Убрать поле из `DigestWindow` и `DIGEST_WINDOW_MINUTES` из констант. Если понадобится ограничить окно — реализовать в `is_in_send_window`.

**Confidence**: high

---

## LOW

### LOW-1 — creator_recorder: _insert_plan при IntegrityError без uq_creator_plans в тексте исключения

**Файл**: `apps/creator_recorder/main.py:206-208`

**Problem**: Условие `if "creator_plans" in str(exc).lower() or "uq_creator_plans" in str(exc).lower()` может не сработать для всех СУБД-драйверов если сообщение об ошибке имеет другой формат (например, asyncpg `DuplicateTableError` vs `UniqueViolationError`). При ложном `else` — `logger.exception("не удалось сохранить план")` и `return None` без retry.

**Impact**: Редкий edge case — при конфликте имени план не сохраняется и нет retry. Не money-критично (Vision-сессия не потеряна, можно повторить).

**Confidence**: low

---

### LOW-2 — enable_recommendation_worker: `send_failed` инициализируется через `counts.get(...)`, а не в основном словаре

**Файл**: `apps/enable_recommendation_worker/main.py:352-353, 426`

**Problem**: Все счётчики в `counts` инициализированы явно кроме `send_failed` (он появляется через `counts.get("send_failed", 0) + 1`). В логах суммаризации `if any(v > 0 for v in summary.values())` — проверяет все ключи, но `send_failed` может отсутствовать при нулевых ошибках. Непоследовательно.

**Impact**: Только читаемость кода и мониторинг. При нулевых сбоях — ключ просто отсутствует в логе.

**Fix**: Добавить `"send_failed": 0` в инициализацию `counts`.

**Confidence**: high

---

### LOW-3 — digest_builder: N+1 запросов (5 последовательных SQL)

**Файл**: `core/telegram/digest_builder.py:252-284`

**Problem**: `build_digest` выполняет 5 SQL-запросов последовательно через `await`. Каждый — отдельный round-trip к БД. Все запросы независимы.

**Impact**: Digest строится один раз в сутки. Задержка 5×10ms=50ms несущественна. Рефакторинг через `asyncio.gather` снизил бы латентность, но это LOW-приоритет.

**Confidence**: high

---

## Что проверено и признано чистым

- **Tracker aggregator absolute recompute**: `ON CONFLICT DO UPDATE SET = EXCLUDED.*` (не инкремент) — идемпотентен, деньги не задваиваются. DEPOSIT_EVENT_TYPES импортируется из `queries.py` — единый контракт с evaluator'ом.
- **Partition pruning в aggregator**: `received_at >= :day_floor AND received_at < :day_ceil` — корректный партиционный ключ для `adsetpro_postback_events`.
- **Digest catch-up**: `is_in_send_window` [09:00, 23:59 UTC] + Redis-ключ по дате — корректная реализация.
- **Cleanup idempotency**: `DROP TABLE IF EXISTS` + `CREATE TABLE IF NOT EXISTS` — safe повтор.
- **rsplit("_", 2) для имён партиций**: корректно обрабатывает многочастичные имена (`adsetpro_postback_events_2026_05`).
- **Redis dedup в enable_reco**: SET NX — атомарный, не задваивает при параллельном запуске двух инстансов воркера.
- **Heartbeat architecture**: все 7 воркеров корректно пишут `worker:heartbeat:<name>` каждые 30с (TTL/2), не дожидаясь основного цикла (основной цикл у cleanup и enable_reco — 300с, что >> TTL 60с).
- **creator_worker circuit-breaker**: BrowserUnavailableError → requeue_for_retry — транзитные ошибки gRPC не теряют задачи.
- **telegram_poller idle-режим**: при отсутствии token поллер не падает, heartbeat продолжается.
- **ACL в telegram_poller**: `_OWNER_ONLY_CALLBACKS` + `_OWNER_ONLY_COMMANDS` — money-действия защищены проверкой `recipient.is_owner()`.
- **snoozed_until timezone**: asyncpg возвращает TIMESTAMPTZ как timezone-aware datetime, `now = datetime.now(timezone.utc)` — сравнение корректно.
