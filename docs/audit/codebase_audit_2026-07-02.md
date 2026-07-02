# Аудит кодовой базы FB Stop Bot — 2026-07-02

**Scope:** полный (backend B1–B5, frontend F1–F2, кросс-каттинг X, боевые логи прода, гигиена репо). 10 доменных агентов в 2 волны + ручная верификация всех CRIT/HIGH ведущим ревьюером (каждая находка подтверждена чтением кода). Прод-часть — строго read-only (Loki/journalctl/psql SELECT). Контекст: предыдущие аудиты (Round 10/11, deep-audit 22.06, codebase 28.06) использованы как база — закрытое перепроверено, открытое переподтверждено.

## Сводная таблица

| Домен | CRIT | HIGH | MID | LOW |
|---|---|---|---|---|
| B1 observer/rules | — | 1 | 3 | 3 |
| B2 meta_api/tasks | 1* | 1* | 2 | 2 |
| B3 воркеры | — | 2 | 8 | 9 |
| B4 API | — | — | — | 4 |
| B5 модели/агрегации | — | 1 | 2 | 3 |
| F1 frontend web | — | 2 | 1 | 2 |
| F2 frontend mini | — | — | 3 | 2 |
| X кросс-каттинг | 1 | 2 | 5 | 5 |
| Логи прода | — | — | 2 | 1 |
| **Итого** | **2** | **9** | **26** | **31** |

\* R1/R3 из deep-audit 22.06 — переподтверждены, **готовые фиксы лежат в несмерженных ветках**.

**Ключевой вывод:** ядро (FSM-guards, идемпотентность необратимых, batch-encode, ACL, money-агрегации в API, partition pruning) — качественное; все ранее закрытые CRIT подтверждённо на месте. Главные риски сосредоточены в (а) 5 готовых, но НЕ смерженных money-фиксах от 22.06, (б) периферии outbox (bulk-провалы, orphan, replay), (в) наблюдаемости UI (канал авто-стопа не виден в Health).

---

## CRIT

### C-1. Гонка авто-генерации ENCRYPTION_KEY при параллельном старте воркеров
`core/crypto.py:302-312` · confidence: high · **подтверждено ревьюером**
При пустом `ENCRYPTION_KEY` каждый из 13+ одновременно стартующих процессов генерирует свой ключ и дописывает в `.env` (`open(env,"a")`) без блокировки → несколько строк ключа, побеждает последний → токены, зашифрованные другими ключами, нерасшифровываемы (`decrypt→""`) → канал авто-стопа молча слепнет. На работающем проде ключ есть (спящий риск); стреляет на свежем bootstrap (сценарий памяти «Fresh DB bootstrap»).
**Fix:** генерация ключа только в едином bootstrap-шаге (run.sh/apply_schema) под file-lock; `_get_fernet` при пустом ключе в проде — явный fail, не самогенерация.

### C-2. Orphan-задачи в task_queue переживают bulk-delete объявлений (deep-audit R1 — открыт с 22.06)
`apps/api/routers/v1/ads_admin.py:37-44` · confidence: high · **подтверждено ревьюером** (docstring прямо признаёт: «task_queue не связан FK — не трогается»)
`DELETE FROM fb_ads` не отменяет pending/retrying-задачи по удалённым ad. `pause_ad` по orphan уходит в вечный requeue; `activate_ad` при пустом owner_tag **реально ре-включит открут** на объявлении, которое оператор осознанно удалил, — необратимое money-действие.
**Fix готов:** ветка `worktree-wf_b79b3d91-2c1-1` (не смержена). Отмена задач в той же транзакции с DELETE.

---

## HIGH

### H-1. Bulk/duplicate с полным провалом суб-реквестов метится `succeeded`, money-fail DM не уходит (deep-audit R3 — открыт)
`apps/meta_api_worker/main.py:418-447` + `core/meta_api/mutations/bulk_status_change.py:132`, `duplicate_campaign.py:172-211` · confidence: high · **подтверждено ревьюером** (`success_result(...)` возвращается безусловно; worker считает успехом любой не-exception)
Массовый bulk-стоп при мёртвом канале фиксируется зелёным, объявления тратят бюджет, owner без сигнала. **Fix готов:** ветка `worktree-wf_b79b3d91-2c1-4`.

### H-2. Четыре пороговых поля оффера настраиваются, но движком правил игнорируются
`core/observer/queries.py:62-79` → `core/observer/pipeline.py:92-119` · confidence: high · **подтверждено ревьюером** (grep: `cpm_threshold|ctr_threshold|funnel_ratio_threshold|spend_no_event_threshold` в core/rules/ и build_rule_context — 0 вхождений)
Пользователь выставляет CPM/CTR/funnel-ratio/spend-no-event пороги в UI (web и mini их рендерят) — evaluator их не читает вовсе; правила считаются от процентов CPA. Молчаливое расхождение конфиг↔поведение на money-контуре: менеджер уверен, что реклама остановится по его порогу.
**Fix:** либо прокинуть поля в RuleContext + правила, либо убрать из формы/модели и задокументировать. Решение продуктовое — нужен выбор владельца.

### H-3. creator_worker: краш вне try → повторное исполнение плана → дубль FB-кампании
`apps/creator_worker/main.py:380` · confidence: high · **подтверждено ревьюером** (`await process_one_task(...)` в task_loop без обёртки, в отличие от campaign_creator_worker)
`plan_run` не в IRREVERSIBLE_TASK_TYPES → reconciler через 30 мин вернёт `running→retrying` → повторный залив = двойной открут.
**Fix:** try/except в task_loop (как в campaign_creator) + добавить `plan_run` в IRREVERSIBLE_TASK_TYPES.

### H-4. Кнопка `dis:` не сверяет token с активным инцидентом — replay старой кнопки паузит восстановленное объявление
`core/telegram/handlers/alerts.py:66` · confidence: med-high · **подтверждено ревьюером** (token участвует только в idempotency_key, `open_state_token` в handlers не читается)
**Fix:** сверять token с текущим `ad_alert_state.open_state_token`, при несовпадении — отказ «алерт устарел».

### H-5. Гонка дедупа ingest постбэков: конкурентные дубли с разным received_at задваивают депозит
`core/adset_pro/ingest.py:71-115` + `apps/api/routers/postback.py` · confidence: high (механика) / med (частота) · **подтверждено ревьюером** (UNIQUE включает микросекундный received_at; пред-SELECT под READ COMMITTED не видит незакоммиченный конкурентный INSERT)
Задвоенный FTD завышает external_deposits → может ложно защитить убыточный ад от STOP.
**Fix:** `pg_advisory_xact_lock(hashtext(click_id||event_type))` перед SELECT/INSERT.

### H-6. Секреты в Settings — plain str; redis_url (с кредами) логируется на INFO
`core/config.py` (все секрет-поля), `apps/api/main.py:94` · confidence: high · **подтверждено ревьюером** (SecretStr в config.py — 0 вхождений)
Любой repr/debug-лог/Sentry-контекст сольёт пароли и ключи открытым текстом; sentry-маскировка покрывает 2 из ~8 ключей.
**Fix:** SecretStr для секретов; в main.py логировать host:port без auth; расширить _SENSITIVE_KEYS.

### H-7. MCP rate-limit fail-open вопреки инварианту проекта
`apps/mcp_server/main.py:117-118` · confidence: high · **подтверждено ревьюером** (комментарий «Fail-open» в коде)
При сбое Redis лимит пропускается; read-tools ходят в живую Vision-сессию без ограничений. CLAUDE.md прямо требует secondary in-memory cap (как в `_ratelimit`).
**Fix:** применить `_check_memory_fallback` и во внешней ветке.

### H-8. WS `task_changed` не инвалидирует `["ads"]` — оператор видит устаревший FSM-статус
`frontend/src/lib/websocket/useRealtimeInvalidation.ts:36-42` · confidence: high · подтверждено агентом с цитатами кода
После реального pause/activate через meta_api_worker открытая таблица /ads и AdDrawer не обновляются live.
**Fix:** + `qc.invalidateQueries({queryKey:["ads"]})`.

### H-9. Settings→Health не показывает `meta_api_channel` — канал авто-стопа невидим в UI
`frontend/src/components/settings/HealthTab.tsx` · confidence: high · подтверждено агентом (поле есть в API и generated.ts, в JSX не читается)
Ровно инцидент 01.07: канал мёртв, Health весь зелёный (heartbeat'ы живы).
**Fix:** карточка «Канал авто-стопа» ONLINE/DEGRADED/UNKNOWN из `data.meta_api_channel`.

---

## MID (26)

**B1:** (1) zero-scan нового кабинетного дня деэскалирует `stop_sent→normal` — теряется инцидент на границе суток (fix: детект reset-строки, не деэскалировать по нулевой строке); (2) snooze через API ставится на `normal`-ад и глушит будущий STOP включая create_disable_task до конца окна (fix: снуз только из активных состояний или сброс при старте инцидента); (3) один депозит в 24ч-окне глушит все no-dep guardrail'ы — окно широковато, задокументировать/сузить.

**B2:** (4) duplicate_campaign не бросает при провале — обходит защиту `_IRREVERSIBLE_KINDS` (привести к контракту create_campaign); (5) draft idempotency-salt = isoformat-timestamp — коллизия двойного клика (добавить uuid4).

**B3:** (6) DB-ошибка вне `_run_account_scan` роняет цикл observer молча, мимо degraded-детектора — тихая слепота (вести счётчик и в общем except); (7) TG 409 не распознаётся → ретрай-шторм без backoff при двух поллерах; (8) offset подтверждается даже для упавшего callback — потеря money-кнопки at-most-once; (9) `/spy` без rate-limit — любой recipient может задудосить живой Vision (Redis-cooldown + Semaphore(1)); (10) мутация >30 мин может быть украдена reconciler'ом — освежать updated_at долгих исполнителей; (11) digest_scheduler голый gather без `_supervised` (класс 246000c7); (12) digest sent-флаг ставится при 0 доставленных; (13) creator_recorder не переподписывается после обрыва Redis.

**B5:** (14) `date_trunc('day')` = UTC-день во всех многодневных агрегациях, а спенд сбрасывается по TZ кабинета → систематический недосчёт аналитики для не-UTC кабинетов (стоп-решения НЕ задеты; fix: `AT TIME ZONE` per-account или зафиксировать погрешность в runbook); (15) `tracker_aggregate.revenue Numeric(12,2)` ужимает источник (12,4).

**X:** (16) browser-agent не детектит разлогин/checkpoint — «login_required» неотличим от сетевого блипа (класс инцидента 01.07); (17) `current_day_spend` (headline-спенд) без единого семантического SQL-теста — прямой рецидив-риск CRIT-1; (18) rotate_encryption_key: InvalidToken пропускается → частично перешифрованное состояние, ключ в .env не меняется; (19) `allow_tools=False` без hard-guard в tool-use цикле; (20) performance/chart-data тесты проверяют shape, не значения из ответа endpoint'а.

**F2:** (21) форма правил mini без slider-модели и live-preview порогов (web имеет); (22) обратный отсчёт скана — локальный таймер, не observer:runtime; (23) тестовые дыры error-путей money-экранов.

**Логи/prod:** (24) `campaign_create` 8 failed vs 3 succeeded — причины в task_queue.last_error не разобраны; (25) единичный `probe_network_down` 02.07 02:44 — класс «Vision channel dies», наблюдать; (26) campaign_service_audit 22.06 (CRIT-1/2) частично устарел — залив из UI работал (3 succeeded), требуется ревизия актуальности его находок.

## LOW (31, сжато)

B1: cpa_threshold=0→дефолт 100; мёртвая ветка frequency_1h_ago; writers.py 703 стр / main.py 1222 стр. B2: set_ad_creative докстринг; autostop_alert спенд-снимок в тексте. B3: enable_reco алерт не ретраится после сбоя TG; тяжёлый I/O до guard; cabinet_scheduler/tracker_aggregator мелочи gather/heartbeat; cleanup orphan-media CWD-зависимость; spy fire-and-forget task; расхождение комментария campaign_creator; bulk.py без SQL LIMIT. B4: scope_key без max_length; spend-history без cap при fb_ad_id; str(exc) в HTTP detail; лишние bind-параметры COUNT. B5: молчаливый дроп невалидного country (нужен счётчик); tracker day UTC; CampaignCreative.run_id без FK/index. X: авто-стоп не переотправляется после 15 попыток при живом инциденте (runbook); probeCache 60с stale; промпт-инъекция через ad_name без регресс-теста; INCR/EXPIRE гонка rate-limit; except (InvalidToken, Exception). F1: dead-code cumulativeSpendTotal (соблазн повторить naive-SUM); дубль money1(); fallback alert_state:"normal" на deep-link. F2: Number(p.spend)||0 на графике; нет enable в AdDetail. Логи: docker builder prune 5.2GB.

## Состояние прода по логам (чисто)

После восстановления 01.07 20:12: 9+ часов без регрессий — 377 сканов, avg 9.9с, adaptive interval штатно; restart-счётчики 0 у всех 24 контейнеров; OOM нет; диск 21%; партиции июль+август у всех 6 таблиц созданы заранее; heartbeat 14/14; Postgres без deadlock/slow; Redis 1.3MB. `vision-token-refresh` 02.07 05:30 — success (team-token, 29.7д до exp) — фиксы 4d9714d5/e3c1cc6a работают.

## Гигиена репо

- **5 готовых money-фикс веток от 22.06 НЕ смержены** (`worktree-wf_b79b3d91-2c1-1..5`): закрывают R1 (=C-2), R3 (=H-1), R4 (NULL owner_tag), R2 (naive SUM enable-reco), R-money (автостарт мёртвых ads). Самый дешёвый выигрыш всего аудита.
- Секреты вне git, но в рабочем каталоге: `.env.bak.dedup` (34 ключа с паролями/токенами) + 7 файлов `data/secrets_backup_*.json` — перенести в защищённое хранилище.
- `core/domain.py` — брошенная незакоммиченная правка (удалён enum CampaignCreatorTaskStatus); `pyproject.toml` — убраны patchright/playwright. Решить: commit или revert.
- 3 непушенных коммита в main (4d9714d5, 246000c7, e3c1cc6a) — инцидент-фиксы 01.07, ждут push.
- Дока-дрейф CLAUDE.md: «12 воркеров» (реально 13), `resolve_owner_ad_ids_by_dates` не существует, «1055 тестов» (реально ~2050), «17 модулей API» (реально 25).

## Рекомендованный план

**Сейчас (money, максимум эффекта за минимум работы):**
1. Push 3 инцидент-коммитов + деплой (алертинг/watchdog/деплой-фикс вступят в силу).
2. **Merge 5 готовых веток 22.06** → закрывает C-2, H-1 и ещё 3 HIGH одним заходом (нужно свежее ревью после 10 дней дрейфа main).
3. H-3 (creator try/except + IRREVERSIBLE) и H-4 (валидация dis:-token) — по ~полдня.
4. Продуктовое решение по H-2 (пороги: реализовать или убрать из UI).

**Следом (неделя):** C-1 (bootstrap ключа), H-5 (advisory-lock ingest), H-6/H-7 (SecretStr, fail-closed MCP), H-8/H-9 (2 строки + карточка Health — мгновенно), MID-6 (тихая слепота observer), MID-17 (семантический тест cabinet_spend).

**Tech-debt (по мере):** остальные MID (digest/poller/UTC-день аналитики), LOW-пачка, актуализация CLAUDE.md, ревизия campaign_service_audit.
