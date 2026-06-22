# Аудит — Money-критичные воркеры (2026-06-22)

Read-only. Подсистема: observer/reconciler/health_watchdog/cabinet_scheduler/meta_api воркеры.
Не дублирует закрытое в AUDIT_2026-06-17 (H1–H5, M1–M3) и «Известные тех-долги» CLAUDE.md.

Сводка: CRIT 0 · HIGH 1 · MID 4 · LOW 3.

---

## HIGH

### H1 — Каталог `fb_ads.is_active`/`fb_campaigns.is_active` НИКОГДА не сбрасывается в FALSE → автостарт включает мёртвые/удалённые объявления
- **location:** `core/observer/writers.py:137,217,253` (только SET TRUE) + `core/meta_api/bulk.py:104` (`a.is_active = TRUE` фильтр) + `apps/cabinet_scheduler/main.py:144`
- **problem:** В каталоге `is_active` выставляется в TRUE на каждом скане и НИГДЕ не сбрасывается в FALSE (grep по core/apps/migrations — ни одного `SET is_active=false` для fb_ads/fb_campaigns). Автостарт (`resolve_owner_ad_ids_by_campaign_ids`) фильтрует `a.is_active = TRUE`, считая это «живыми» — но флаг монотонно-истинный, поэтому в bulk-activate попадают ВСЕ когда-либо отсканированные ad_id выбранных кампаний, включая удалённые в Meta и объявления прошлых cabinet-дней.
- **impact:** Money/корректность: автостарт `bulk_status_change activate` каждое утро пытается включить объявления, которые давно сняты. Часть отработает на Meta (partial bulk), но: (а) могут включиться старые объявления, которые НЕ должны были стартовать в этот день → нецелевой открут бюджета; (б) «защита» `is_active=TRUE` — мёртвая (всегда true), реальной фильтрации нет; (в) bulk упирается в `MAX_BULK=50`, лишние мёртвые id вытесняют валидные. Owner-scoping и allowlist частично спасают, но дату/актуальность объявления не проверяет НИКТО.
- **fix:** Либо сбрасывать `is_active=FALSE` для ad/campaign, не попавших в последний скан кабинета (cleanup_worker или observer-cycle по `last_seen_at < cabinet_day_start`), либо в `resolve_owner_ad_ids_by_campaign_ids` дополнительно фильтровать `last_seen_at >= NOW() - INTERVAL '1 day'` (или по delivery_status). Минимально — заменить мёртвый `is_active=TRUE` на свежесть по `last_seen_at`.
- **confidence:** med (поведение Meta на устаревший id зависит от того, существует ли объект; денежный эффект реален, если старые объявления реактивируются)

---

## MID

### M1 — `_finish_scan_run` UPDATE по partitioned `scan_runs` без partition-key (`started_at`) — на горячем пути каждого скана
- **location:** `apps/observer_worker/main.py:175-199` (`UPDATE scan_runs ... WHERE id = :id`)
- **problem:** `scan_runs` партиционирована `RANGE(started_at)`, PK = `(id, started_at)`. `_finish_scan_run` обновляет по `WHERE id = :id` без `started_at` → Postgres не может выполнить partition-pruning и обходит ВСЕ живые партиции (index scan по ведущей колонке PK в каждой). Выполняется на КАЖДОМ scan-цикле (≈90с) и на КАЖДЫЙ кабинет.
- **impact:** Не full-scan (ведущая колонка PK `id` индексирована per-partition), но обход всех партиций ретеншна на самом частом write-пути. С ростом числа партиций деградирует. Класс «partition full-scan на горячем пути».
- **fix:** Пробросить `started_at` из `_begin_scan_run` в `_finish_scan_run` и добавить `AND started_at = :started_at` в WHERE (точечный pruning в одну партицию). `_begin_scan_run` уже знает `started_at`.
- **confidence:** high

### M2 — Автостарт по `no_owner_ads` форсирует observer-скан КАЖДУЮ минуту до конца суток
- **location:** `apps/cabinet_scheduler/main.py:194-204` (ветка `else`: `_trigger_observer_scan` без `_set_autostart_done`)
- **problem:** Когда в окне автостарта owner-объявлений не нашлось, done-ключ осознанно НЕ ставится (catch-up на случай, что объявления появятся). Но каждый тик (60с) при этом публикует `fb_agent:observer:trigger`. До конца суток (catch-up окно от HH:MM до 23:59 UTC) это форсирует немедленный скан observer'а каждую минуту.
- **impact:** Observer сканирует Vision/Ads Manager раз в 60с весь остаток дня вместо адаптивного интервала (anti-detect ломается, нагрузка на Vision-сессию, риск rate-limit/детекта). TG-спам погашен дедупом алерта, но трафик скана — нет. На типовом дне, где автостарт «не нашёл» (опечатка в датах/allowlist), это часы лишних сканов.
- **fix:** Не триггерить скан на каждом тике no_owner_ads — либо ставить отдельный короткий дедуп на trigger (Redis SET NX, напр. раз в 10-15 мин), либо триггерить scan один раз при входе в окно, а ретраить только резолв.
- **confidence:** high

### M3 — Тройное дублирование детекции отказа канала авто-стопа без общего дедупа → шквал разнородных алертов на один инцидент
- **location:** `apps/health_watchdog/main.py:459-489` (БД-детектор) + `apps/health_watchdog/main.py:492-594` (probe) + `apps/meta_api_worker/main.py:535-548` (channel-down) + `apps/meta_api_worker/main.py:652-663` (per-ad escalate)
- **problem:** При одном инциденте «Vision-канал лёг» срабатывают независимо: (1) watchdog `check_autostop_channel` (stuck pause + desync), (2) watchdog `meta_probe` (GET /me), (3) meta_api_worker `maybe_alert_autostop_channel_down`, (4) meta_api_worker per-ad `escalate_undelivered`. У каждого свой дедуп-ключ, общего нет.
- **impact:** Не money-потеря, но при реальном отказе owner получает 4 разных CRITICAL/«выключи вручную» сообщения за короткое окно от двух воркеров → шум, риск «alert fatigue» и пропуска. На массовом отказе (много stuck ads) — десятки per-ad сообщений вдобавок к channel-level.
- **fix:** Согласовать иерархию: при активном channel-level CRITICAL (probe/channel-down) подавлять per-ad escalate и БД-детектор (проверять общий ключ `health:alerted:meta_channel`/`autostop:*` перед отправкой). Либо явно задокументировать, что это разные сигналы (probe=проактивный, БД=симптом, per-ad=точечный) и оставить как есть.
- **confidence:** med

### M4 — `load_scanning_enabled` + `load_owner_tag` читаются из БД на КАЖДУЮ задачу meta_api_worker (2 запроса/task) без кэша
- **location:** `apps/meta_api_worker/main.py:349` (`load_scanning_enabled`) + `:374` (`load_owner_tag` → `load_observer_config`)
- **problem:** На обработку каждой задачи делается минимум 2 отдельных коннекта/SELECT к `observer_config`/`system_config` (асимметричный стоп + owner_tag), плюс owner-scoping делает ещё батч-резолв каталога. При всплеске очереди (autostart bulk, массовый pause) это N задач × 2 конфиг-запроса.
- **impact:** Не корректность (намеренно без кэша — money-настройка должна применяться немедленно), но лишняя нагрузка на БД на money-всплесках. На холодной очереди незаметно.
- **fix:** Короткий TTL-кэш (1-3с) на `observer_config` внутри task_loop, инвалидируемый по `fb_agent:task:changed`/config-change pubsub. Либо один SELECT, возвращающий и scanning_enabled, и owner_tag (оба из observer_config).
- **confidence:** med

---

## LOW

### L1 — `asyncio.gather(...)` без `return_exceptions=True` в трёх воркерах
- **location:** `apps/cabinet_scheduler/main.py:321`, `apps/meta_api_worker/main.py:705`, `apps/health_watchdog/main.py:787`
- **problem:** Если любой под-loop выбросит исключение из своего setup/while-условия (не из тела — тела обёрнуты try/except), gather отменит остальные таски (включая heartbeat) и процесс упадёт целиком.
- **impact:** На практике loop-тела exception-safe (вечный цикл с try/except), поэтому событие маловероятно. Но при редком исключении вне тела — тихая смерть heartbeat вместе с воркером; watchdog заметит, но устойчивость ниже, чем могла бы быть.
- **fix:** `asyncio.gather(..., return_exceptions=True)` + лог упавшего таска, либо supervisor-перезапуск отдельных тасков.
- **confidence:** med

### L2 — observer_worker `main.py` — god-file 1226 строк
- **location:** `apps/observer_worker/main.py` (весь файл)
- **problem:** Один модуль держит scan_runs writers, runtime publishers, heartbeat, prepare-workspace, degraded-алерты, sleep-refresh, pubsub-handlers, default-factories и main_loop. Нарушает правило «никаких файлов >500 строк в новом коде».
- **impact:** Тех-долг на пути изменений: высокая когнитивная нагрузка, риск регрессий при правке money-логики (как M1).
- **fix:** Вынести scan_runs writers (`_begin/_finish_scan_run`) и runtime-публикацию в `core/observer/` модули; default-factories — отдельно. Поведение не меняется.
- **confidence:** high

### L3 — Дублирование heartbeat_loop в каждом воркере (копипаста)
- **location:** `apps/observer_worker/main.py:287-303`, `apps/reconciler_worker/main.py:31-45`, `apps/cabinet_scheduler/main.py:256-267`, `apps/meta_api_worker/main.py:606-617`, `apps/health_watchdog/main.py:658-669`
- **problem:** Идентичный `heartbeat_loop` (SET ex=60, sleep TTL/2, try/except) скопирован в 5+ воркерах с минимальными отличиями.
- **impact:** Тех-долг: правка контракта heartbeat (напр. имя/TTL) требует синхронных правок в 5 местах; риск рассинхрона с health_watchdog EXPECTED_WORKERS (история Round 11).
- **fix:** Единый `core/workers/heartbeat.py::run_heartbeat(redis, name, stop)` + контрактный тест уже есть (`test_heartbeat_contract.py`).
- **confidence:** high

---

## Проверено — чисто (не баг)

- **claim FOR UPDATE SKIP LOCKED + mark под `WHERE status='running'`** — race-safe, bool-контракт соблюдён.
- **attempt_count bump единожды** — только в каноническом `reconcile_stuck_running`; reconciler — обёртка.
- **Необратимые kinds** — fail без retry и в worker, и в reconciler (двойная защита от дубля кампании).
- **idempotency_key автостарта** включает день — гонка двух тиков не задвоит bulk activate.
- **Пустой allowlist автостарта → no_campaigns** (НЕ весь кабинет), done-ключ ставится — корректно.
- **Redis-ошибка в дедупе автостарта** → `redis_error` (retryable), done-маркер не ставится — не теряет день.
- **Асимметричный стоп** — на паузе пропускаются только выключающие мутации (обе формы bulk, `is_deactivating_bulk`).
- **owner-scoping bulk** резолвит по `params.ad_ids`, а не по фиктивному `target_id="autostart:N"` — корректно.
- **partition-pruning** в `escalate_undelivered` (`ad_metrics` с `cycle_ts >= NOW() - 7d`) и health БД-детекторе (task_queue не партиционирована) — соблюдён.
- **dedup-after-send** в worker_notify/health_watchdog — сбой TG не съедает алерт на TTL.
