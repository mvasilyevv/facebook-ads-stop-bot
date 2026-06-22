# Observer ядро — находки аудита 2026-06-22

Подсистема: `core/observer/`, `core/rules/`, `core/scanner/`.
Зона относительно чистая: костяк (FSM-guards, partition-pruning, idempotency, owner-scoping,
H1-H4 из AUDIT_2026-06-17) подтверждён рабочим. Ниже — НОВОЕ, не пересекающееся с закрытым.

## CRIT

Нет находок уровня CRIT (нет прямого слива денег / порчи данных в самом ядре при штатной
конфигурации). Главный money-риск — HIGH-1 (snooze глушит auto-stop), но он контролируем
действием оператора, поэтому HIGH, не CRIT.

## HIGH

### HIGH-1 — Snooze подавляет авто-стоп, хотя по контракту «не отключает рекламу»
- **location**: `core/observer/pipeline.py:376-378,415-424` (+ контракт в `apps/api/routers/v1/ads_actions.py:8-10`)
- **problem**: при активном снузе `_suppress_emit` зануляет не только `emit_alert`, но и `create_disable_task`. Если ад в `warning_sent`/`normal` снузлен, а затем сработал STOP — auto-stop задача НЕ создаётся до истечения снуза (до 24ч). Документация снуза (`ads_actions.py`) прямо обещает «Это НЕ отключает рекламу — только заглушает уведомления».
- **impact (money)**: оператор снузит ад, чтобы убрать шум алертов, веря, что авто-стоп всё ещё защищает. Убыточный ад крутится без стопа до 24ч. Контракт UI и реальное поведение расходятся — оператор дезориентирован относительно money-защиты.
- **fix**: разделить «снуз уведомлений» и «снуз авто-стопа». Минимум — НЕ занулять `create_disable_task` при снузе (снуз глушит только TG-emit, авто-стоп остаётся). Если суппресс авто-стопа задуман — привести docstring `ads_actions.py`/UI-текст к реальности и показывать в UI явное предупреждение «снуз отключает и авто-стоп».
- **confidence**: high (поведение однозначно из кода; спорна лишь желаемая семантика — это и есть суть находки: контракт неоднозначен).

### HIGH-2 — Multi-cabinet без owner_tag → авто-стоп чужих объявлений в shared-кабинете
- **location**: `core/observer/queries.py:319-321` (`campaign_matches_owner` → True при пустом теге) + `apps/observer_worker/main.py:405` (owner_tag=config NULL не блокируется при >1 кабинета)
- **problem**: при мульти-кабинете (>1 ad_account_id) глобальный allowlist `campaign_ids` игнорируется (`_run_account_scan`), а скоупинг полностью полагается на `owner_tag`. Если `owner_campaign_tag` = NULL/пуст, `campaign_matches_owner` возвращает True для ВСЕХ кампаний → ядро оценивает правила и создаёт `pause_ad` по чужим объявлениям в общем кабинете.
- **impact (money/safety)**: бот может авто-стопнуть чужую (не свою) рекламу убыточного партнёра в shared-кабинете — необратимое действие над чужими деньгами/кампаниями. Нет guard'а, требующего owner_tag в мульти-кабе.
- **fix**: в `run_one_cycle` при `len(accounts) > 1` и пустом owner_tag — отказаться сканировать (или сканировать только кабинеты с явным per-offer скоупом), залогировать CRITICAL + TG-ops алерт «мульти-каб без owner_tag — скан остановлен ради безопасности». Зеркалит существующий guard «scanning ON + пустой allowlist» для single-cabinet.
- **confidence**: med (зависит от деплой-конфига; в текущем проде owner_tag, вероятно, задан — но защита отсутствует как инвариант).

## MID

### MID-1 — frequency_outlier_cap гасит frequency-правило на реальном тяжёлом burnout
- **location**: `core/rules/evaluator.py:224` + `core/rules/types.py:134` (хардкод `Decimal("10.0")`, не оверрайдится в `build_rule_context`)
- **problem**: `if current > ctx.frequency_outlier_cap: return None`. Cap 10.0 задуман отсекать стартовый шум (freq 50-100 при крошечном reach). Но долгоиграющий ад на узкой аудитории реально достигает frequency 10-15 — это и есть burnout, который правило должно ловить. При freq>10 правило молчит.
- **impact (money)**: самый выгоревший ад (freq>10) проскакивает frequency-стоп. Частично прикрыто funnel-правилами по цене, но именно frequency-anomaly создан ловить burnout РАНЬШЕ, чем цена взлетит — на крайних значениях он слепнет.
- **fix**: разделить «шум на старте» и «burnout» по reach/impressions, а не одним абсолютным cap'ом частоты. Напр. применять cap только при reach < N (малый охват = шум), а при достаточном охвате freq>cap трактовать как STOP. Либо поднять/сделать настраиваемым cap per-offer.
- **confidence**: med (другие правила обычно ловят дороговизну; но gap логически реален).

### MID-2 — Per-ad запись разбита на 4 независимые транзакции (не атомарна)
- **location**: `core/observer/pipeline.py:303-407` (upsert_catalog TX#1 → insert_metrics TX#2 → apply_fsm_transition TX#3 → maybe_create_disable_task TX#4)
- **problem**: docstring `writers.py:347` обещает «Один atomic commit — FSM trans + event log не должны разойтись», и внутри `apply_fsm_transition` это так. Но между ним и `maybe_create_disable_task` — отдельная транзакция. Краш/откат между TX#3 и TX#4 оставляет FSM в `stop_sent` без созданной pause-задачи.
- **impact (money)**: окно, где FSM думает «застопано», но outbox-задачи нет → ад не паузится. Закрыто recovery-путём `stop_sent → stop_sent` (создаёт задачу на следующем скане), но recovery работает только если ад снова попадёт в скан со STOP-метриками и не будет снузлен (см. HIGH-1) — двойная зависимость.
- **fix**: объединить apply_fsm_transition + maybe_create_disable_task (и желательно insert_metrics) в одну транзакцию `engine.begin()`, либо явно задокументировать, что atomicity держит ТОЛЬКО recovery-путь, и покрыть его тестом «краш между FSM-commit и outbox».
- **confidence**: high (структурно очевидно; impact смягчён recovery).

### MID-3 — `select_scan_mode` замедляет скан, пока ад завис в stop_sent
- **location**: `core/observer/adaptive_interval.py:54-60` + `core/observer/pipeline.py:394-398` (alerts_stop инкрементируется только при `emit_alert`)
- **problem**: `result.alerts_stop` растёт только на НОВЫХ stop-emit'ах. Ад, уже сидящий в `stop_sent` (повтор → emit_alert=False), не даёт alerts_stop>0 → режим падает в CALM/IDLE, скан замедляется (×1.0..×1.5), хотя ад ещё горит и pause мог не примениться.
- **impact (money)**: медленнее ре-детект и медленнее recovery-создание pause-задачи (MID-2) для зависшего STOP. Прямой пейз идёт асинхронно через meta_api_worker, поэтому импакт ограничен скоростью повторной реакции.
- **fix**: учитывать в режиме не только новые emit'ы, но и наличие активных `stop_sent`/`claimed`-инцидентов в цикле (передавать счётчик из CycleResult/запросом), чтобы держать CRITICAL пока есть незакрытый STOP.
- **confidence**: med.

## LOW

### LOW-1 — Рассогласование регистра `stage` в `alert_events.metrics_json._hits` vs колонке `stage`
- **location**: `core/observer/pipeline.py:155` (`hit.stage.value` → "WARNING"/"STOP") vs `core/observer/writers.py:404-412` (колонка `stage` пишет lowercase "warning"/"stop" из `transition.alert_stage`)
- **problem**: `_hits[].stage` кладётся в UPPERCASE (из `AlertStage` enum), а колонка `alert_events.stage` — lowercase. Renderer, читающий `_hits`, получает иной регистр, чем колонка.
- **impact**: косметика/потенциальный баг рендера, если где-то сравнивают регистрозависимо. Денег не касается.
- **fix**: нормализовать в `_hits_payload` к lowercase (`.value.lower()` или единый источник лейблов).
- **confidence**: high.

### LOW-2 — `runtime.py` docstring расходится со списком running-статусов
- **location**: `core/observer/runtime.py:14-19` (docstring перечисляет scanning/idle/dispatch) vs `:46` (`_RUNNING_WORKER_STATUSES` включает ещё `preparing`)
- **problem**: docstring-контракт не упоминает `preparing`, хотя writer (`observer_worker/main.py:235`) и reader его поддерживают. Расхождение документации с кодом (сам код консистентен writer↔reader).
- **impact**: нет (код корректен), только риск будущей ошибки при чтении доки.
- **fix**: добавить `preparing → running` в docstring `runtime.py`.
- **confidence**: high.

### LOW-3 — `queries.py:177-202` `load_scanning_enabled` дублирует чтение из `load_observer_config`
- **location**: `core/observer/queries.py:142-202`
- **problem**: два отдельных SELECT'а к `observer_config` для пересекающихся полей; вызываются разными воркерами. Мелкая копипаста/лишний round-trip.
- **impact**: незначительный (singleton-таблица, дёшево). Тех-долг.
- **fix**: оставить как есть (осознанная гранулярность) или вынести общий резолвер. Низкий приоритет.
- **confidence**: high.

## Итог

Ядро в хорошем состоянии: money-инварианты (idempotency, open_token persistence,
FSM-guards, partition-pruning, депозит-как-щит) держатся, ранее найденные H1-H4 закрыты.
Новые риски сосредоточены на **семантической границе snooze↔auto-stop (HIGH-1)** и
**отсутствии owner_tag-guard в мульти-кабе (HIGH-2)** — оба про money/safety, оба требуют
явного решения о желаемом поведении, а не просто фикса. Остальное — корректность краёв и долг.
