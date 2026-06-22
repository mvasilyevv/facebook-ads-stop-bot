# Findings: слой данных (модели, миграции, агрегации спенда)

Дата: 2026-06-22. Read-only аудит. Подсистема: `core/models/`, `migrations/versions/`, `core/dashboard/`, `core/adset_pro/`.

**Итог: подсистема в крепком состоянии.** Money-граница (кумулятивный SUM) и partition-pruning закрыты дисциплинированно во ВСЕХ местах (проверено 11 query-сайтов ad_metrics — везде фильтр по cycle_ts; все 8+ агрегаций spend идут через `latest_per_ad_per_day_cte`). Цепочка миграций линейна (single head 0024, без multiple-heads). CRIT не найдено. Найденное — преимущественно семантика трекера и тех-долг.

---

## HIGH

### H1 — Дедуп ingest подавляет легитимные повторные депозиты (redep) по тому же click_id → недосчёт revenue/deposits в tracker_aggregate
- **severity**: HIGH
- **location**: `core/adset_pro/ingest.py:71-107` (пред-INSERT SELECT окна 24h по `(click_id, event_type)`)
- **problem**: Дедуп ловит ЛЮБУЮ запись с тем же `(click_id, event_type)` за 24h и отбрасывает INSERT. Но `DEPOSIT_EVENT_TYPES = ("ftd","redep","baddep")` (`queries.py:21`) включает `redep` — «повторный депозит». Один и тот же click_id (= один игрок) легитимно делает несколько `redep` за сутки; все, кроме первого, молча отбрасываются как дубль.
- **impact**: `tracker_aggregate.deposits` и `revenue` НЕДОСЧИТЫВАЮТ повторные депозиты → искажение ROI-аналитики и revenue-отчётов (деньги в UI занижены). Для STOP-решений безопасно: evaluator смотрит `external_deposits >= 1` булевым гейтом, а не точное число. Комментарий `ingest.py:36` рассуждает только про FTD («повторный FTD — нонсенс»), но не про redep.
- **fix**: Для повторяемых event_type (redep, hold) дедупить по более узкому ключу — добавить в ключ дедупа уникальный идентификатор транзакции из payload (`raw->>'transaction_id'`/`txid`/сумму+timestamp), либо исключить redep/hold из оконного SELECT-дедупа, оставив только UNIQUE-защиту от точного двойного приёма. Сверить с реальным контрактом AdSet.pro: переиспользует ли он click_id между депозитами.
- **confidence**: med (зависит от того, шлёт ли AdSet.pro уникальный click_id per транзакцию; контракт в коде не зафиксирован)

---

## MID

### M1 — Потеря партиции при простое cleanup_worker на стыке месяца → отказ INSERT в ad_metrics (остановка записи метрик)
- **severity**: MID
- **location**: `apps/cleanup_worker/worker.py:109-141` (`create_next_partition_if_missing` — единственный регулярный создатель партиций; запускается раз в сутки 04:00 UTC)
- **problem**: Партиции на следующий месяц создаёт ТОЛЬКО cleanup_worker. Если воркер не дышит в последний день месяца (или DROP/CREATE упал), на 1-е число партиция отсутствует → INSERT в `ad_metrics`/`alert_events`/`scan_runs`/`adsetpro_postback_events` падает `no partition of relation ... found`. Запись метрик и алертов прекращается до ручного вмешательства.
- **impact**: Тихая остановка money-критичного потока (метрики не пишутся → evaluator работает на устаревших данных, авто-стоп слепнет). health_watchdog ловит мёртвый cleanup_worker, но не отсутствие партиции напрямую.
- **fix**: Создавать партицию текущего+следующего месяца в каждом воркере записи перед стартом цикла (или ленивый CREATE IF NOT EXISTS в writers перед первым INSERT нового месяца), либо безопасный DEFAULT-партишн как ловушка с алертом. Минимум — отдельный health-чек «партиция на текущий месяц существует».
- **confidence**: high

### M2 — `ingest_postback` резолвит fb_ad_fk в отдельном соединении ВНЕ транзакции дедупа → возможен NULL при гонке с observer-upsert
- **severity**: MID
- **location**: `core/adset_pro/ingest.py:67-73` (`_resolve_fb_ad_fk` conn #1) vs `73-134` (BEGIN dedup+INSERT conn #2)
- **problem**: fb_ad_fk резолвится отдельным `engine.connect()` ДО открытия транзакции INSERT. Если postback пришёл раньше, чем observer успел upsert'нуть ad → `fb_ad_fk=NULL`. Запись остаётся NULL навсегда (нет ре-резолва) → `aggregator.py:109` явно исключает `fb_ad_fk IS NULL` из tracker_aggregate.
- **impact**: Депозит реального ad'а, чей postback опередил первый скан, НЕ попадёт в tracker_aggregate (revenue/deposits per ad теряются). Для evaluator менее критично: `load_external_deposits` фильтрует по СЫРОМУ `fb_ad_id` (VARCHAR), а не fb_ad_fk — там депозит виден. Расхождение между двумя путями чтения одного факта.
- **fix**: Backfill fb_ad_fk: периодический UPDATE постбэков с `fb_ad_fk IS NULL` где `fb_ad_id` теперь резолвится в fb_ads (можно в tracker_aggregator_worker перед recompute). Либо в aggregator резолвить ad_id по сырому fb_ad_id через JOIN fb_ads, а не полагаться только на fb_ad_fk.
- **confidence**: high

### M3 — Округление revenue 12,4 → 12,2 при агрегации; потеря микро-сумм
- **severity**: MID
- **location**: `core/models/trackers/aggregate.py:45` (`revenue Numeric(12,2)`) vs `core/models/trackers/adsetpro_postback.py:84` (`revenue Numeric(12,4)`)
- **problem**: Postback хранит revenue с 4 знаками («на случай микро-amount» — комментарий модели), а `tracker_aggregate.revenue` — 2 знака. `SUM(revenue)` в aggregator (`aggregator.py:126`) суммирует 4-знаковые значения, результат UPSERT'ится в 12,2 → Postgres округляет. При большом числе микро-сумм накапливается погрешность.
- **impact**: Revenue-аналитика per (ad,country,day) теряет точность до центов. Денежно мало (центы), но при крипто/микро-payout валютах может быть заметно; контракт «4 знака на postback» нарушен на агрегате.
- **fix**: Привести `tracker_aggregate.revenue` к `Numeric(12,4)` (миграция, безопасно расширяет), либо документировать осознанное округление до центов.
- **confidence**: high

---

## LOW

### L1 — Семантика hour-bucket в /dashboard/chart-data: кумулятивный «sawtooth» вместо per-hour дельты
- **severity**: LOW
- **location**: `apps/api/routers/v1/dashboard_timeseries.py:170-197` (`bucket='hour'`, multi-day окно)
- **problem**: Для hour-бакета берётся latest snapshot В ЧАСЕ. Spend кумулятивен внутри суток кабинета и обнуляется в полночь → линия спенда по часам рисует пилу (растёт за день, падает на полночь), а не почасовой прирост. Математически корректно (не задвоено), но визуально вводит в заблуждение: оператор видит «провал спенда» в полночь.
- **impact**: Только UX-интерпретация графика; денежной ошибки в числах нет.
- **fix**: Для hour-бакета считать дельту (`spend - LAG(spend) внутри ad×day`) если нужен «прирост за час», либо документировать что это running-total. Опционально.
- **confidence**: med

### L2 — `tracker_aggregate.ad_id` FK CASCADE → revenue-история удаляется вместе с ad
- **severity**: LOW
- **location**: `core/models/trackers/aggregate.py:35-39` (`ondelete="CASCADE"`)
- **problem**: При удалении fb_ads (ручная чистка/каскад от offer) tracker_aggregate уносит revenue-историю этого ad. В отличие от ad_library winner_archive (hold-forever), revenue-агрегаты не защищены.
- **impact**: Потеря исторической revenue-аналитики при удалении ad. Низко: ad'ы со спендом обычно не удаляют (MEMORY-правило «no delete campaigns with spend»).
- **fix**: Рассмотреть `ondelete="SET NULL"` + nullable ad_id (как adsetpro_postback_events.fb_ad_fk), либо осознанно принять CASCADE.
- **confidence**: med

### L3 — Дублирование 280-строкового SELECT между `_build_sql` и `_build_sql_cursor` (snapshot.py)
- **severity**: LOW
- **location**: `core/dashboard/snapshot.py:229-318` и `399-478`
- **problem**: Два почти идентичных SELECT'а (offset-пагинация vs keyset) с полным повтором 40+ колонок и 4 LATERAL'ов. Расхождение при правке одного (добавил колонку только в один) → тихий рассинхрон ответа между эндпоинтами.
- **impact**: Тех-долг на пути изменений; риск рассинхрона при добавлении полей.
- **fix**: Вынести общий SELECT-body + JOIN-блок в константу/функцию, различать только WHERE/ORDER/LIMIT.
- **confidence**: high

### L4 — Правило «no naive SUM» держится только комментариями, не типами/тестом-линтером
- **severity**: LOW
- **location**: `core/dashboard/metric_aggregation.py` (вся дисциплина) + рассеяно по роутерам
- **problem**: Защита от money-bug №1 (naive SUM кумулятива) — соглашение в комментариях CRIT-1 и code-review. Часть роутеров (`dashboard_performance.py`, `dashboard_timeseries.py`) повторяют DISTINCT ON inline, а не зовут хелпер `metric_aggregation` → новый разработчик может написать `SUM(spend)` напрямую, всё скомпилируется.
- **impact**: Регресс money-bug №1 не блокируется автоматически. Прецедент уже был (CRIT-1 прошёл сквозь 974 теста).
- **fix**: Перевести inline-DISTINCT-ON в `dashboard_performance`/`dashboard_timeseries` на общий хелпер; добавить grep-guard в CI («`SUM(.*spend` без `per_ad`/`DISTINCT ON` в том же файле → fail»).
- **confidence**: med
