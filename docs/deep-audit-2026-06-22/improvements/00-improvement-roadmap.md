# FB Stop Bot — приоритизированная дорожная карта улучшений (deep audit 2026-06-22, Фаза 2)

Сводит рекомендации по 5 темам (библиотеки-заменители, архитектурные паттерны, переезд языка/рантайма,
перфоманс/масштабирование, DX/тесты/наблюдаемость) в ОДНУ приоритизированную карту. Каждый пункт привязан к
реальному коду и к корневым причинам багов Фазы 1 (`99-risk-synthesis.md`, `00-system-map.md`).

Рамка заказчика: для каждого пункта — **why** (какую боль/класс багов закрывает) + **benefit** (что конкретно
даёт: меньше LOC, меньше багов, latency, money-безопасность, скорость разработки). Где кастом обоснован или
переписывание не окупается — честный **skip** с обоснованием.

---

## 0. Резюме состояния — что улучшать и зачем

Костяк системы **здоров**: race-safe `claim/mark` (FOR UPDATE SKIP LOCKED + `WHERE status='running'`),
канонический `attempt_count`-bump, partition-pruning в 11/11 ad_metrics-сайтах, batch JSONPath-encode,
FSM-guards, ACL по owner в TG/draft, money-граница latest-per-day в data-layer — всё подтверждено Фазой 1 как
качественное. Переписывать его не надо.

Главная окупаемость — **не** декомпозиция god-файлов и **не** смена языка/рантайма, а закрытие **рецидивирующих
классов тихих money-багов**, которые проходят сквозь 1000+ тестов, потому что держатся дисциплиной и
комментариями, а не типами и CI:

| Корневая причина (паттерн риск-синтеза) | Класс багов | Чем закрывается |
|---|---|---|
| **Контракт держится дисциплиной, не типами** (#3) | observer:runtime `unknown` (CRIT-2), bulk `result['success']` (R3), heartbeat-имена (Round 11), ScannedAdRow ×3 | Типизированные контракты на границах (A1, A2, A4, кодоген мапперов), контрактные тесты в CI |
| **Naive SUM кумулятивных ad_metrics** (#1) | enable_reco analyzer (R2), Dashboard spend-chart, CRIT-1 Round 10 | latest-вместо-SUM фикс (P-enable) + CI grep-guard (DX-1) + хелпер metric_aggregation |
| **Состояние сессионное/глобальное там, где нужно per-entity** (#4) | browser-agent heal не той вкладки (R5), монотонный is_active (R-money) | per-cabinet heal-state, фильтр last_seen — это баг-фиксы (вне Фазы-2), но новый каркас воркеров (A3) убирает класс копипасты |
| **Идемпотентность/owner-проверка не на исполнении** (#5) | orphan-задачи bulk-delete (R1), NULL owner_tag (R4) | проверка на исполнении — баг-фиксы; типизированный MutationResult (A2) делает «забыл проверить» невозможным |
| **Партиции next-month на одном воркере** (R8) | тихая остановка INSERT во все partitioned-таблицы | `@periodic` с гарантией (procrastinate) ИЛИ partition health-check (T-6) + leader-lock |

**Вывод:** фокус Фазы 2 — **типизировать 4-5 контрактов на границах** и **автоматизировать 2 CI-guard'а**. Это
S/M-усилия с высокой отдачей по money-безопасности. Стратегический big-bet (procrastinate) рассматривать как
`consider/L` — он совпадает с уже принятыми решениями (Postgres+outbox), но трогать money-воркеры им — только
после стабилизации на не-money пилоте.

---

## 1. Quick wins (effort S, verdict do, высокий benefit/усилие)

Все — точечные правки без рефакторинга, низкий риск, закрывают подтверждённый класс багов или дают
money-наблюдаемость. **Порядок внутри секции = порядок выполнения** (money-безопасность вперёд).

| # | Улучшение | Why (боль/класс) | Benefit |
|---|---|---|---|
| **A2** | Типизированный `MutationResult` вместо `dict['success']` (`core/meta_api/mutations/base.py`, `meta_api_worker/main.py:419`) | R3 (HIGH money): worker безусловно метит `succeeded`, не читая `result['success']` → bulk-стоп при полном отказе Meta метится успехом, money-fail DM не уходит, реклама тратит бюджет | Money-дыра закрыта **по типу**: поле обязательно в сигнатуре, забыть прочитать нельзя. `modified_ids/failed_ids` типизированы для FSM-sync |
| **P-enable** | `enable_reco/analyzer._aggregate_spend` → `latest.spend` вместо SUM (`:77-84`) | R2 (HIGH money): SUM кумулятивных снимков → `total_spend` завышен в N раз → Rule 1 false-negative → валидные ads не получают рекомендацию к включению → упущенная выручка | Корректные enable-решения оператора. Закрывает HIGH из R2; одновременно правится фиксирующий-баг тест (T-2) |
| **T-1** | Тест bulk `result['success']=False → mark_failed` (`tests/integration/test_meta_api_outbox_e2e.py`) | Граница R3 не покрыта на уровне worker-pipeline; прошла сквозь 974 теста | Блокирует регресс money-gap в CI |
| **T-2** | Тест enable_reco на многоцикловых кумулятивах (assert `latest`, не `sum`) | Текущий тест `:197` фиксирует **баг** как ожидаемое (assert аддитивности) | Фиксирует инвариант `spend = latest snapshot`, сигналит при регрессе |
| **DX-1** | CI grep-guard: запрет `SUM(spend)` без `DISTINCT ON`/`latest_per_ad` (`.github/workflows`) | Паттерн #1: 3 рецидива naive-SUM (CRIT-1 Round 10, R2, Dashboard). Правило держится комментариями | Регресс money-bug №1 блокируется механически до merge. Шаг <2с, ложняки подавляются `# allow-naive-sum` |
| **P-perf2** | Inline `DISTINCT ON` → хелпер `latest_per_ad_per_day_cte` (`dashboard_performance.py`, `dashboard_timeseries.py`) | Дублирование inline `DISTINCT ON` создаёт визуальный соблазн написать `SUM` рядом — так появился CRIT-1 | −40-50 строк дубля; единое место правки guard'а/окна; минус один класс тихих регрессий |
| **P-scan** | `started_at` в `_finish_scan_run` WHERE (`observer_worker/main.py:165-188`) | M1: UPDATE scan_runs без partition-key → обход всех живых партиций на каждом скане (~90с) | Точечный pruning в 1 партицию; latency UPDATE и нагрузка на shared_buffers ↓ пропорц. ретеншну |
| **P-stats** | Окно 30д в `dashboard_stats` MAX(scan_runs) (`:69-71`) | HIGH-1: seq-scan по всем партициям на каждый `/dashboard/stats` и `/dashboard/batch` | Постоянная latency дашборда независимо от накопленных данных; COALESCE-фолбэк уже есть, семантика та же |
| **P-ai-timeout** | `asyncio.wait_for(timeout=60)` на `ChatSession.ask()` (`ai_analyze.py:160`) | MID-3: зависший AI-proxy занимает event-loop слоты → FastAPI не отвечает на `/healthz` → k8s рестартит pod | Предотвращает resource leak и каскадный сбой healthcheck. ~15 мин работы |
| **O-3** | `setup_sentry()` в 12 `run_*.py` + `apps/api/main.py` | `core/sentry.py` + config есть, но grep → 0 вызовов; Sentry не инициализирован даже при `SENTRY_DSN` | При `SENTRY_DSN` все необработанные исключения в проде → Sentry со stacktrace. 12 entry × 4 строки ≈ 1ч |
| **T-3** | Тест Dashboard `spendSeries` через `cumulativeSpendTotal`, не `raw.map` (`frontend`) | MID-2: спарклайн строится из кумулятивных бакетов → завышен 5-10×, конфликтует с корректным headline | Фиксирует инвариант, убирает введение оператора в заблуждение по дневному тренду расходов |
| **T-4** | Frontend Vitest + `tsc --noEmit` в CI (`.github/workflows/ci.yml`) | CI прогоняет только `pytest`; 331+12 vitest-тестов и TS-проверка не входят в gate | TS-ошибки и регрессии ловятся при push; все будущие фронт-тесты (T-3, T-5) автоматически в gate |
| **DX-2** | `pnpm gen:api` + `git diff --exit-code generated.ts` в CI | MID-4: `generated.ts` (6563 строки) генерится вручную, рассинхрон фронт↔бэк не проверяется | Shape-дрейф ловится в CI, не в runtime `undefined` |
| **T-5** | Тест `alertStateCssVar` в mini filter-chips (`frontend-mini/.../ads/index.tsx:349`) | Токенов `--fsm-warning_sent`/`--fsm-stop_sent` нет → точки фильтра прозрачны; баг задокументирован, но не исправлен | Визуальный дефект → регрессионный тест; `alertStateCssVar` уже в `@fb/shared`, фикс тривиален |
| **A4** | Реестр воркеров `Worker(StrEnum)` как источник `EXPECTED_WORKERS` | Имена heartbeat задаются строкой в каждом воркере, watchdog читает env CSV — ручное зеркало; расхождение = ложный «мёртв» (Round 11) | Невозможно завести воркер не появившись в мониторинге, опечататься в имени; контрактный тест `set(writers)==set(Worker)==set(watchdog)` |

> Примечание: A2/P-enable/T-1/T-2 формально money-баг-фиксы из Фазы 1, но включены сюда, т.к. их Фаза-2-форма —
> **структурное** закрытие класса (тип вместо проверки, тест-инвариант, CI-guard), а не разовая заплатка.

---

## 2. Big bets (effort M/L, стратегические — benefit и честная цена)

| # | Улучшение | Why | Benefit | Цена / риск | Verdict |
|---|---|---|---|---|---|
| **A1** | Типизированные Redis-контракты `ObserverRuntime`/`WorkerHeartbeat`/`ScanFinishedEvent` (`core/contracts` или `core/observer/runtime.py`) с `to_redis()/from_redis()` | Паттерн #3: `observer:runtime` — inline-dict с двумя статус-полями, синхронизируемыми docstring'ами writer↔reader; ровно это дало `observer_status=unknown` (CRIT-2 Round 10) | Drift ловится roundtrip-тестом в CI, а не money-инцидентом; нормализация статуса дедуплицируется. Закрывает класс CRIT-2 **по типу** | M / low. msgspec не нужен — pydantic v2 + frozen-dataclass уже в стеке | **do** |
| **A3** | Единый `heartbeat_loop` + `run_worker` каркас (`core/workers/runtime.py`): engine/redis init, signal handlers, `asyncio.gather(heartbeat, work_loop)`, cleanup. Каждый `apps/<worker>/main.py` → конфиги + свой `work_loop` | 12 побайтово идентичных `heartbeat_loop` + повторённый main_loop boilerplate; из-за копипасты имена разъехались с `EXPECTED_WORKERS` (Round 11) | −~250 LOC дубля; новый воркер не может забыть heartbeat/разойтись в имени (берёт из A4); graceful-shutdown в одном месте; ниже порог добавления воркера | M / low. `test_heartbeat_contract.py` (22 кейса) защитит миграцию. Делать парно с A4 | **do** |
| **O-1** | Wire `core/metrics.py` в воркеры: `scan_timer`/`OBSERVER_CYCLES` (observer), `WORKER_HEARTBEAT_AGE` (watchdog) | Из 7 объявленных Prometheus-метрик реально используются 2; `/metrics` отдаёт нули про money-контур | Grafana видит scan latency и heartbeat age. Сейчас `/metrics` знает про HTTP, но ничего про скорость сканов и состояние воркеров | M / low | **do** |
| **O-2** | Money-метрики: `AUTOSTOP_MUTATIONS` Counter + `TASK_QUEUE_DEPTH` Gauge в Prometheus | Нет time-series о числе авто-стопов и глубине outbox; деградация канала видна только в TG-алертах | Grafana alert «autostop failed >3/5мин» (деградация до channel-down DM) и «outbox depth >20» — оба критичны для money-контура | M / low. Зависит от O-1 (wire-up) | **do** |
| **P-lock** | Redis leader-lock (`core/workers/leader_lock.py`, SET NX + Lua CAS) для cleanup/digest/cabinet_scheduler | 3 шедулера защищены только от 2-го ТИКА того же экземпляра. При rolling deploy / k8s HPA ≥2 реплик: cleanup → DDL-конфликт DROP/CREATE партиций; digest → двойной TG; cabinet → двойные scan-trigger. Helm/k8s артефакты уже в репо | Предотвращает DDL-конфликт (потенциальная потеря данных), дубль дайджеста, шквал сканов. **Обязательно** перед горизонтальным деплоем ≥2 реплик | M / med | **do** |
| **A2-кодоген** | Кодоген ScannedAdRow-мапперов из proto-дескриптора (свести 3 рукописных места → 2 сгенерированных) ИЛИ контрактный тест-страж `dataclass==proto==маппер` | Паттерн #3 + MEMORY checklist: поле задаётся руками в 3 местах (`am-join.buildScannedRow`→`index.toProtoRow`→`client._proto_to_row`), пропуск = тихий NULL метрики в БД (потеря money-данных) | Структурно закрывает класс «новое поле не доехало до БД». Прецедент — `test_heartbeat_contract.py`. Это укрепление существующего proto-IDL, **не** смена языка | M / med. Дешёвый вариант (тест-страж) — начать с него | **consider** |
| **L-errors** | Единый источник Graph-error-классификации (TS коды ↔ Python `_CODE_MAP`) — proto enum/JSON + кодоген/контракт-тест | Две независимые таблицы кодов (`errors.py::_CODE_MAP` vs хардкод `if code===190` в `client.ts`); рассинхрон тихо ломает money-маршрутизацию авто-стопа (requeue vs mark_failed → pause_ad «навсегда failed») | Money: авто-стоп не застревает в failed при будущем рассинхроне трактовки кода. Самый дешёвый пункт темы языка (~25 кодов, чёткие границы) | S-M / low. Минимум — контрактный тест синхрона | **consider** |
| **A9** | `Protocol FsmResetPort` на границе meta_api→observer (`core/observer/ports.py`); снять best-effort except с сигнатурных ошибок | Нарушение слоёв #1/паттерн #7: `fsm_sync` импортирует `observer/writers.reset_*`; смена сигнатуры ломает FSM-sync молча (except глушит) → money: FSM застревал в `stop_sent` | Изменение reset-сигнатуры ломает типы/тест, а не молча оставляет FSM в `stop_sent`; drift становится громким | M / med. 1-2 точки связи, money-FSM-sync — осторожно. Дешёвая альтернатива: оставить импорт, только снять except | **consider** |
| **A10** | Типы для pubsub-событий (`scan:finished`/`task:changed`/`health:updated`) — применить модели A1 к payload | Остаток паттерна #3: pubsub-payload — fire-and-forget dict без схемы | Консистентность фронт-инвалидации по типу; дёшево закрывает хвост #3. Низкая срочность (события некритичны) | S / low. Прицепом к A1 | **consider** |
| **T-6** | Partition health-check в `health_watchdog` (`SELECT pg_tables WHERE tablename='ad_metrics_YYYY_MM'`) | R8: партиции создаёт ТОЛЬКО cleanup_worker; его простой на стыке месяца → нет партиции → INSERT падает → метрики/алерты не пишутся → авто-стоп слепнет | Предупреждение за 0-12ч до тихой остановки money-критичного потока записи. Без него оператор узнаёт из отсутствия алертов — поздно | M / low | **consider** |
| **P-config-cache** | TTL-кэш конфига (scanning_enabled, owner_tag) 2с в meta_api_worker | M4: 2 SELECT/task без кэша; на autostart-burst (50 задач/мин) = 100 лишних запросов | На burst 100 → ~2-3 запроса; money-настройки применяются с задержкой ≤2с (приемлемо для ручных тумблеров) | S / low | **consider** |
| **P-ws-fanout** | Singleton `WsBroadcaster` (1 Redis pubsub-коннект, `asyncio.Queue` per WS) | Каждое WS-соединение создаёт отдельный Redis-коннект (`ws.py:127`) | N соединений → 1 коннект. Актуально при росте числа операторов (командный доступ); при 1-3 — незначительно | M / med | **consider** |
| **A5-A8** | Декомпозиция god-файлов (observer 1226, health_watchdog 812, create_campaign 650, тонкие роутеры offers/tma/history) | Money-воркеры/роутеры смешивают 4-5 ответственностей; долг тестируемости, не баг | Изолированные unit-тесты без подъёма всего цикла; меньше merge-конфликтов; переиспользование валидаторов/disable. **НЕ закрывает баг** | M-L / med (observer/create_campaign), low (health/валидаторы). Делать после A1/A3 — часть уедет бесплатно | **consider** |
| **DX-3/DX-4/DX-5/DX-6** | Прочий DX: `core/workers/heartbeat.py` (поглощается A3), `AdSnapshotExtended` в `@fb/shared`, `thresholds.ts` shared, декомпозиция фронт-god-компонентов | DX-улучшения: дублирование heartbeat/порогов/типов; god-компоненты тяжело тестировать | Меньше дублей web↔mini, рост покрытия mini, TS-предупреждения при переименовании поля. Приоритет ниже T-серии | S-L / low-med | **consider** |
| **O-4** | Grafana dashboard JSON в репо (`monitoring/grafana/dashboards/`) | Grafana задеплоена, метрики объявлены, но нет декларативных dashboard-файлов; восстановление после пересборки — ручное | При инциденте causality-граф вместо grep по логам; повторный деплой не теряет мониторинг | M / low. После O-1+O-2 | **consider** |
| **LIB-procrastinate** | procrastinate как движок очереди (заменить `core/tasks/queue.py` 458 LOC + 13 heartbeat_loop + outbox-обёртки) | Ручной outbox + 13 копий heartbeat + ручной retry/backoff/reconcile — поверхность багов на каждом money-ревью; контракт writer↔reader держится дисциплиной | −600…900 LOC boilerplate; транзакц. defer (тот же инвариант вручную, закрывает orphan R1); LISTEN/NOTIFY → ниже latency pause_ad; ретраи/локи/периодика протестированы мейнтейнерами | **L / high**. Пилот на не-money (cleanup/tracker_aggregator) → creator → и только после стабилизации meta_api. observer НЕ трогать (scan-loop). 3.9.0, Production/Stable, MIT | **consider** (стратегический) |
| **LIB-periodic** | procrastinate `@periodic` для планировщиков окон (digest/cleanup/cabinet_autostart) | Catch-up окна вручную (инвариант #11): cleanup без catch-up, autostart no_owner_ads не ставит done-ключ, dedup GET+SET NX не атомарен. R8 — партиции на одном воркере | DB-гарантия «ровно один defer за период» на N воркеров; `max_delay` = встроенный catch-up. Закрывает R8 гарантированным CREATE партиций | M / med. Первый кандидат — cleanup (минимальный money-риск) | **consider** (часть LIB-procrastinate) |

---

## 3. Skip / оставить как есть (кастом обоснован или переписывание не окупается)

| Кандидат | Почему skip (honest verdict) |
|---|---|
| **purgatory/pybreaker вместо `core/browser/circuit_breaker.py`** | Боли нет — 0 багов в Фазе 1. Кастом имеет streaming-aware API (`check_open` + раздельные `record_failure/record_success`), нужный для gRPC server-streaming `RunScanCycle`, которого у purgatory/pybreaker нет; плюс интеграция с `record_vision_failure()` (Prometheus). 233 LOC покрытого кода против зависимости, не закрывающей главный use-case |
| **aiogram 3 / python-telegram-bot вместо httpx TG-клиента** | Боли нет — 0 CRIT/HIGH в telegram. Клиент **намеренно** отвязан от ORM (чистая тестируемость pure-рендереров, переиспользование в worker_notify). aiogram/PTB тянут Bot/Dispatcher/FSM/middleware, который придётся переплести с money-инвариантами (pre-claim sentinel `message_id=0`, ACL owner-only, hot-reload токена). High-risk переписывание + идёт TG-редизайн (DM+MiniApp) — не время менять транспорт |
| **Готовый transactional-outbox пакет** | `task_queue` И ЕСТЬ transactional outbox, верифицирован Фазой 1 как race-safe (`FOR UPDATE SKIP LOCKED` + `WHERE status='running'` + idempotency UNIQUE). Готовых Python-пакетов «generic outbox» уровня зрелости нет — это паттерн, а не библиотека (Debezium/Kafka — другой масштаб/стек). Единственный осмысленный «готовый outbox» = `procrastinate.defer` (уже учтён в big-bets) |
| **APScheduler для планировщиков** | 4.x (нативный async + asyncpg + NOTIFY) всё ещё alpha (`4.0.0a6`, мейнтейнер запрещает прод); 3.x стабилен, но jobstore синхронный (asyncpg невозможен) — чужероден async-проекту. procrastinate `@periodic` вписан лучше (Postgres-native, та же БД и движок, что очередь) |
| **browser-agent → Playwright-Python (консолидация на один язык)** | Почти нулевой выигрыш: Playwright-Python внутри всё равно запускает Node-драйвер — Node не уходит, добавляется новый IPC-мост. Anti-detect целиком во внешнем Vision (`connectOverCDP`), в Node-процессе нет stealth-либ — JS-экосистема не держит, но и Python ничего не выигрывает. Перф нейтрален (горячий путь — один `page.evaluate(fetch)`). Против: **L-риск регресса money-маппинга spend/leads/deposits** на самом критичном сервисе (оба канала — детект и авто-стоп). 8.5k LOC прод-TS |
| **Rust/Go/PyO3 для evaluator + агрегаций спенда** | Нулевой выигрыш: пути **I/O-bound**, не CPU-bound. Жизненный цикл объявления доминируется сетью (`page.evaluate(fetch)` к Meta) и Postgres (4 транзакции/строку). `evaluator.py` — десятки сравнений Decimal на строку, без numpy/циклов на 10^4+. Агрегации спенда уже в Postgres (`metric_aggregation.py` — построение SQL CTE). Нативный мост + кросс-компиляция = высокая стоимость ради экономии, теряющейся в шуме сетевого I/O |
| **buf/protovalidate вместо grpc_tools codegen** | Не закрывает реальную боль: protovalidate валидирует ЗНАЧЕНИЯ полей, а дрейф здесь в МАППИНГЕ (ScannedAdRow ×3) и СЕМАНТИКЕ ошибок (code→retry-класс) — валидация их не выражает. Текущий codegen отлажен и встроен в run.sh/Makefile/CI. Замена рабочего пайплайна без отдачи |
| **msgspec / DI-фреймворк / event-broker (Kafka/NATS) / дробление до <500 строк ради счётчика** | Добавляют зависимость/оверхед/операционный налог без закрытого класса багов. pydantic v2 + frozen-dataclass уже покрывают сериализацию; FastAPI Depends+фабрики — адекватный DI; outbox+pubsub адекватны масштабу (1 кабинет-профиль, single-writer Vision); Batch-флоу `create_campaign` оправдан спецификой Meta Batch API; дробить файлы без линии разреза — счётчик ради счётчика |
| **Горизонтальное масштабирование observer/meta_api_worker** | Второй observer требует второй Vision-сессии (профиль + browser-agent) — против сознательной архитектуры (один профиль = минимум anti-detect сигналов). meta_api_worker при ≥2 безопасен (SKIP LOCKED), но `ExecuteGraphCall` всё равно через одну Vision-сессию. Нет бизнес-требования параллельного многопрофильного скана; текущий инстанс справляется с 1-2 кабинетами |

---

## 4. Предлагаемый порядок выполнения (максимум money-безопасности / снижения багов на единицу усилия)

Очерёдность оптимизирована по **(money-безопасность × структурность) / усилие**. Сначала — дешёвые
структурные закрытия рецидивных классов, потом — наблюдаемость, потом — стратегические big-bets.

**Волна 1 — money-граница по типу + CI-guard'ы (S, максимальная отдача):**
1. **A2** + **T-1** — типизированный `MutationResult`, worker читает `success`, тест bulk-all-failed. Закрывает R3 структурно.
2. **P-enable** + **T-2** — `latest.spend` в enable_reco + тест-инвариант. Закрывает R2.
3. **DX-1** — CI grep-guard naive-SUM. Механически блокирует рецидив паттерна #1.
4. **P-perf2** + **P-scan** + **P-stats** — хелпер DISTINCT ON + partition-pruning на горячих путях.
5. **A4** — реестр воркеров (источник истины heartbeat-имён). Дёшево, готовит почву для A3.

**Волна 2 — типизация контрактов + наблюдаемость (S-M):**
6. **A1** (+**A10** прицепом) — типизированные Redis/pubsub-контракты. Закрывает класс CRIT-2.
7. **A3** — единый каркас воркеров (поглощает DX-3). −250 LOC, парно с A4.
8. **O-3** — Sentry в entry-points (1ч, весь error-tracking).
9. **O-1** + **O-2** — wire Prometheus-метрики + money-метрики (autostop rate, outbox depth).
10. **P-ai-timeout** — таймаут AI, защита healthcheck.

**Волна 3 — фронт-CI + наблюдаемость инцидентов (S-M):**
11. **T-4** + **DX-2** — Vitest/tsc + gen:api-drift в CI (включает T-3, T-5 в gate).
12. **T-3** / **T-5** — фронт-тесты дашборда/фильтров.
13. **T-6** — partition health-check (предупреждение до тихой остановки потока метрик).
14. **O-4** — Grafana dashboard JSON в репо.

**Волна 4 — стратегические/предеплойные (M-L, по бизнес-триггеру):**
15. **P-lock** — leader-lock. **Обязательно перед** горизонтальным деплоем ≥2 реплик.
16. **L-errors** + **A2-кодоген** — единый источник Graph-кодов и ScannedAdRow-мапперов (контракт-тест минимум).
17. **A9** — Protocol на границе meta_api→observer (осторожно, money-FSM-sync).
18. **A5-A8** — декомпозиция god-файлов (после A1/A3 часть уедет бесплатно; долг тестируемости, не баг).
19. **LIB-procrastinate** / **LIB-periodic** — пилот на не-money воркере → creator → meta_api **только после
    стабилизации**. Самый большой потенциал (−600…900 LOC, LISTEN/NOTIFY latency), но и самый высокий риск —
    не первым.

**Принцип:** Волны 1-2 (S/M, ~2-3 недели) снимают подтверждённые HIGH money-gap'ы **структурно** (типом/тестом/
guard'ом, а не заплаткой), поэтому регресс не вернётся. Big-bets (Волна 4) дают долгосрочную экономию LOC и
latency, но трогают money-исполнение — только после того, как Волны 1-3 поставили CI-сетку под них.
