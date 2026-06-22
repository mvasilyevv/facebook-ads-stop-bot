# Архитектурные паттерны и рефакторинг — стратегия улучшений

> Фаза 2 deep-audit 2026-06-22. Тема: **структурные улучшения**.
> Привязка к Фазе 1: `00-system-map.md` (§3 контракты, §4 циклы/нарушения слоёв), `99-risk-synthesis.md`
> (паттерн #3 «контракт writer↔reader держится дисциплиной, не типами»; R3 result['success']; CRIT-2/Round 11),
> `arch/observer-core.md`, `arch/workers-money.md`, `arch/workers-aux.md`, `arch/data-layer.md`.
> Код read-only. Ни один файл кода не менялся.

---

## Краткий вывод

Костяк системы **архитектурно здоров**: stateless-воркеры вокруг трёх шин (Postgres/Redis/outbox),
чистый FSM, race-safe claim/mark, partition-pruning, owner-ACL. Это не надо переписывать.

Где реальная боль — **не «большие файлы сами по себе», а отсутствие типов на границах**. Два из трёх
исторических CRIT (CRIT-2 observer:runtime, Round 11 heartbeat-имена) и текущий R3 (result['success'])
— это один класс: **контракт между writer и reader живёт в docstring и code-review, а не в коде**.
Любой рассинхрон — тихий, money-критичный, проходит сквозь 1000+ тестов (потому что тесты проверяют
shape, не семантику границы). Это самая окупаемая зона: типизировать 4-5 Redis/result-контрактов
дешевле, чем декомпозировать любой god-component, а закрывает целый рецидивирующий класс багов.

Второй по ценности — **дедупликация воркер-каркаса**: 12 идентичных копий `heartbeat_loop` + повторённый
graceful-shutdown/`main_loop`-boilerplate. Это не «красота» — это поверхность для расхождения имён
(уже стреляло в Round 11) и место, куда забывают добавить новый воркер в мониторинг.

God-components (`observer_worker/main.py` 1226, `health_watchdog/main.py` 812, `create_campaign.py` 650,
`offers.py` 592, `tma.py` 522) — **реальный, но менее острый** долг: тестируемость и навигация, не money.
Декомпозиция здесь — `consider`, и только по чётким линиям разреза, без «дробления ради <500 строк».

Циклы слоёв (meta_api↔observer через `fsm_sync`, FastAPI↔Vision) — **структурно неприятны, но 1-2 точки**;
их дешевле обозначить интерфейсом, чем «разрывать» большим рефакторингом.

**Честные skip:** вводить msgspec/новый DI-фреймворк/event-bus поверх pydantic — не окупается, в стеке уже
есть всё нужное (pydantic v2, dataclasses, asyncio.gather-паттерн). Переписывать `create_campaign`
полностью — спецификой Batch API оправдан текущий вид.

---

## Таблица рекомендаций

| # | Рекомендация | Что меняем | Почему (класс багов/боль) | Что даёт | Усилие | Риск | Вердикт |
|---|---|---|---|---|---|---|---|
| A1 | **Типизированные Redis-контракты** (observer:runtime, heartbeat, pubsub) | dataclass/pydantic-модель + один encode/decode на ключ вместо инлайн-dict | Тихий writer↔reader drift (CRIT-2, паттерн #3) | Drift ловится тестом/типами, не прод-инцидентом | M | low | **do** |
| A2 | **Типизированный результат мутации** (`MutationResult` вместо `dict['success']`) | frozen dataclass + worker читает `.success` | R3: bulk-стоп с полным отказом метится succeeded без money-DM | Закрывает money-дыру по типу, а не по дисциплине | S | low | **do** |
| A3 | **Единый `heartbeat_loop` + worker-runner** | вынести 12 копий в `core/workers/runtime.py`; имя воркера — из `Enum`/реестра | Round 11: рассинхрон имён writer↔EXPECTED_WORKERS; забыли воркер в мониторинге | −~250 LOC дубля; новый воркер автоматически в health-контракте | M | low | **do** |
| A4 | **Реестр воркеров = источник истины EXPECTED_WORKERS** | `WORKERS` Enum/таблица; heartbeat-key и watchdog читают из неё | Имена heartbeat в 12 местах ↔ CSV env в watchdog — ручное зеркало | Контрактный тест писатель↔читатель из одной таблицы | S | low | **do** |
| A5 | Декомпозиция `observer_worker/main.py` (1226) | вынести scan_run-CTE, runtime-publish, gate/redis/tg-фабрики, degraded-алерт | Навигация/тестируемость money-воркера; смешаны 5 ответственностей | Меньше merge-конфликтов, изолированные unit-тесты фаз | M | med | **consider** |
| A6 | Декомпозиция `health_watchdog/main.py` (812) | разнести 3 детектора (heartbeat / runtime / autostop-channel) + probe в модули | 4 разнородных детектора в одном файле; шум при добавлении 5-го | Изоляция дедупа алертов (R6); проще править один детектор | M | low | **consider** |
| A7 | Декомпозиция `create_campaign.py` (650) | вынести `_validate_*` (15 шт) в `mutations/_validators.py`, body-builders отдельно | Один класс — валидация+Batch-сборка+exec | Переиспользование валидаторов в др. мутациях; тест валидаторов без Batch | S | low | **consider** |
| A8 | Декомпозиция `offers.py` (592) / `tma.py` (522) / `history.py` (419) | вынести `compare_offers`/preview, `_load_ad_extras`/disable-helpers, оставить роутер тонким | God-router смешивает HTTP и доменную агрегацию | Роутер = только HTTP; логику в `core/` тестируют без TestClient | M | low | **consider** |
| A9 | Интерфейс на границе `meta_api → observer` (fsm_sync) | reset-функции за `Protocol`/`FsmResetPort` в `core/observer` | Слой исполнения зависит от слоя детекта; best-effort except глушит drift сигнатуры | Изменение reset-сигнатуры ломает типы, не молча FSM-sync | M | med | **consider** |
| A10 | Вынести `dict`-payload pubsub-событий в типы (scan:finished, task:changed) | те же модели A1; publisher/subscriber делят тип | fire-and-forget dict без схемы | Консистентность фронт-инвалидации | S | low | **consider** |
| — | Заменить pydantic→msgspec для скорости сериализации | — | — | Микро-выигрыш, +зависимость, дубль с pydantic v2 | — | — | **skip** |
| — | DI-фреймворк (dependency-injector и т.п.) поверх FastAPI Depends | — | — | FastAPI Depends + фабрики уже покрывают; ввод фреймворка = чистый оверхед | — | — | **skip** |
| — | «Разбить все файлы <500 строк» как самоцель | — | — | Декомпозиция без линии разреза плодит шов и косвенность | — | — | **skip** |
| — | Event-bus / message broker (Kafka/NATS) вместо outbox+pubsub | — | — | Postgres-outbox+Redis-pubsub адекватны масштабу 1-кабинет; broker = операционный налог | — | — | **skip** |

---

## Детализация (do / consider)

### A1 — Типизированные Redis-контракты (**do**, M, low)

**Боль.** `observer:runtime` — это inline-`dict` с **двумя** статус-полями (`worker_status` детальный +
`status` нормализованный), которые writer (`apps/observer_worker/main.py::_publish_runtime_status`) и reader
(`core/observer/runtime.py::read_observer_runtime`) синхронизируют через **совпадающие docstring'и**. Ровно
это рассинхронизировалось в **CRIT-2 Round 10** (writer писал `worker_status∈{scanning,idle,paused}`, reader
ждал `status∈{running,paused}` → `observer_status` всегда `unknown`). Тот же паттерн — `heartbeat:{name}`
(значение `"alive"`, ключ собирается строкой в 12 местах) и pubsub-payload (`fb_agent:scan:finished` —
`json.dumps(event)` без схемы). 99-risk-synthesis паттерн #3 прямо называет это рецидивирующим источником
тихих багов.

**Что меняем.** Один модуль `core/contracts/redis_payloads.py` (или `core/observer/runtime.py` расширить):
- `@dataclass(frozen=True) ObserverRuntime` с полями контракта + методами `to_redis()->str` / `from_redis(raw)->ObserverRuntime` (нормализация `scanning/idle/dispatch/preparing→running` **внутри типа**, в одном месте).
- writer строит `ObserverRuntime(...)` и пишет `.to_redis()`; reader зовёт `.from_redis()`. Inline-`dict` исчезает с обеих сторон.
- аналогично — `WorkerHeartbeat` (тривиальна, но даёт типизированный ключ-билдер) и `ScanFinishedEvent`.

**Почему именно dataclass/pydantic, не msgspec.** Pydantic v2 **уже в стеке** (config), dataclasses —
родной паттерн проекта (`ScannedAdRow`, `FsmInput`, `RuleHit` — все frozen dataclasses, см.
`arch/observer-core.md`). msgspec (проверено через context7, `/websites/jcristharif_msgspec`: зрелый,
валидация на decode, High reputation) дал бы валидацию-на-decode и скорость, **но**: (1) вводит новую
зависимость ради сериализации, которой pydantic v2 уже умеет с запасом на этих объёмах (десятки ключей/мин,
не hot-path сериализации мегабайтами); (2) ломает однородность с существующими dataclass-контрактами.
Вывод: **dataclass с явными `to_redis/from_redis`** + (опционально) pydantic-модель там, где нужна
валидация входа. msgspec — `skip`.

**Что даёт.** Контрактный тест `test_observer_runtime_roundtrip` (writer-тип → Redis → reader-тип ==
исходное) ловит drift в CI, а не в проде. Нормализация статуса — в одной функции, не дублируется в writer и
reader. Класс CRIT-2 закрыт структурно.

**Усилие M:** 1 модуль + правка 2 сайтов observer:runtime + 12 сайтов heartbeat-ключа (механически) +
контрактные тесты. **Риск low:** чистый рефакторинг с тестом на эквивалентность; ключи/значения не меняются.

---

### A2 — Типизированный `MutationResult` вместо `dict['success']` (**do**, S, low)

**Боль (R3, money).** `core/meta_api/mutations/base.py::success_result` возвращает
`{"success": True, "graph_response": ..., "modified_ids": [...]}` — **нетипизированный dict**.
`apps/meta_api_worker/main.py:419` после `execute_mutation` **безусловно** зовёт
`mark_task_succeeded(result=result)` и **не читает `result['success']`**. Для `bulk_status_change` Batch API
отдаёт HTTP 200, а пер-саб ошибки живут в теле: при отклонении Meta **всех** sub-requests bulk-стоп метится
succeeded, money-fail DM (только в `except`-ветках) не уходит — оператор видит «успех», реклама тратит
бюджет (подтверждённый HIGH).

**Что меняем.**
- `@dataclass(frozen=True) MutationResult { success: bool; graph_response: dict; modified_ids: list[str]; failed_ids: list[str]=() }`. `success_result()`/`bulk`-handler возвращают его.
- В worker: `if not result.success: → mark_failed + _alert_money_fail` (вместо безусловного succeeded). Либо bulk raise при `succeeded==0 and failed>0` — но **типизированный путь предпочтительнее**, т.к. покрывает частичный провал тоже.

**Что даёт.** Money-дыра закрыта **по типу**: невозможно «забыть прочитать success» — поле обязательно в
сигнатуре. Тест `test_bulk_all_failed_marks_failed` фиксирует семантику границы (которой не было). Заодно
`modified_ids`/`failed_ids` типизированы для FSM-sync (H2 — метить FSM только по реально применённым id).

**Усилие S:** один dataclass, ~6 handler'ов возвращают его (механически), 1 ветка в worker.
**Риск low:** аддитивно; существующие читатели dict-ключей продолжат работать, если оставить `__getitem__`
shim на переходный период (или поправить все — их немного).

---

### A3 — Единый `heartbeat_loop` + worker-runner (**do**, M, low)

**Боль.** **12 идентичных копий** `async def heartbeat_loop` (подтверждено grep: все 12 воркеров в `apps/`).
Тело побайтово одно: `redis.set(KEY, "alive", ex=TTL)` + `asyncio.wait_for(stop.wait(), interval)`. Плюс
повторённый `main_loop`-boilerplate: создание engine/redis, `add_signal_handler(SIGTERM/SIGINT)`,
`asyncio.gather(heartbeat_loop, <work_loop>)`, `finally: aclose/dispose`. Round 11 показал реальную цену:
из-за копипасты heartbeat имена разъехались с `EXPECTED_WORKERS`, мониторился только `meta_api`, 6 воркеров
давали ложное «мёртв».

**Что меняем.** `core/workers/runtime.py`:
```python
async def heartbeat_loop(redis, key: str, stop, *, ttl=HEARTBEAT_TTL): ...  # одна копия
async def run_worker(name: str, *, work_loop, ...):                          # каркас
    # engine/redis init, signal handlers, gather(heartbeat_loop(key_for(name)), work_loop), finally cleanup
```
Каждый `apps/<worker>/main.py::main_loop` ужимается до резолва конфигов + передачи своего `work_loop` в
`run_worker(WORKERS.observer, work_loop=tick_loop)`. Heartbeat-ключ строится **только** через
`heartbeat_key(name)` из реестра (см. A4).

**Что даёт.** −~250 LOC дубля; новый воркер физически не может «забыть» heartbeat или разойтись в имени —
он берёт его из реестра. Graceful-shutdown и cleanup-логика правятся в одном месте (сейчас 12). Снижает
порог добавления воркера.

**Усилие M:** 1 модуль + механическая правка 12 main_loop. **Риск low:** поведение идентично; контрактный
тест `test_heartbeat_contract.py` (уже существует, 22 кейса) защищает от регресса имён при миграции.

---

### A4 — Реестр воркеров как источник истины (**do**, S, low) — парная к A3

**Боль.** Имена heartbeat (`worker:heartbeat:observer` и т.д.) задаются строкой в каждом воркере, а
`health_watchdog` читает `EXPECTED_WORKERS` из **env CSV** (`parse_expected_workers`). Это **ручное зеркало**
двух списков (00-system-map §3, контракт `heartbeat:{name}`). Расхождение → ложный «мёртв»/пропущенный
мониторинг.

**Что меняем.** `core/workers/registry.py`: `class Worker(StrEnum)` со всеми 12 именами + `heartbeat_key()`.
И writer (A3), и `health_watchdog.DEFAULT_EXPECTED_WORKERS` берут множество из `Worker`. Env-override
оставить как фильтр («мониторить подмножество»), но **дефолт** — из реестра.

**Что даёт.** Невозможно завести воркер, не появившись в мониторинге; невозможно опечататься в имени —
оно одно. Контрактный тест: `set(writers) == set(Worker) == set(watchdog default)`.

**Усилие S, риск low.** Чисто организационная правка; делается вместе с A3.

---

### A5 — Декомпозиция `observer_worker/main.py` (1226) (**consider**, M, med)

**Линия разреза.** Файл смешивает 5 ответственностей (видно по списку def'ов):
1. **scan_run lifecycle** — `_begin_scan_run` (CTE), `_finish_scan_run`, `_publish_scan_finished` → `apps/observer_worker/scan_run.py`.
2. **runtime/heartbeat-publish** — `_publish_runtime_status`, `heartbeat_loop` → уезжает в A1/A3.
3. **TG-нотификации** — `_notify_synced_disabled`, `_notify_tg_simple`, `_prepare_tg_allowed` → `apps/observer_worker/notify.py`.
4. **degraded-надзор** — `_ObserverState`, `_maybe_alert_degraded`, `_clear_degraded_dedup` → `apps/observer_worker/degraded.py`.
5. **фабрики** — `_default_gate_factory`/`_redis`/`_tg_client` → `apps/observer_worker/factories.py`.
Ядро цикла (`run_one_cycle`, `_run_account_scan`, `_prepare_workspace`, `main_loop`, `_sleep_with_runtime_refresh`) остаётся в `main.py` (~500 строк).

**Почему consider, не do.** Это money-воркер; декомпозиция нужна для **тестируемости** (сейчас фазы
переплетены) и навигации, но **не закрывает баг** — риск чисто в стоимости изменений/ревью. Делать только
**после** A1/A3 (они и так вынесут runtime+heartbeat, файл похудеет «бесплатно»). Риск med — легко занести
регресс в money-цикл при механическом переносе; обязателен прогон полного integration-набора.

**Что даёт.** Изолированные unit-тесты scan_run-CTE/degraded/notify без подъёма всего цикла; меньше
merge-конфликтов на горячем файле.

---

### A6 — Декомпозиция `health_watchdog/main.py` (812) (**consider**, M, low)

**Линия разреза.** Три **независимых** детектора + probe в одном файле:
- `apps/health_watchdog/detectors/heartbeats.py` — `check_worker_heartbeats`, `should_alert`, `parse_expected_workers` (последнее → реестр A4).
- `detectors/runtime.py` — `check_observer_runtime[_freshness]`.
- `detectors/autostop_channel.py` — `query_stuck_pause_tasks`, `query_desynced_stop_ads`, `build_autostop_channel_alert`, `check_autostop_channel`.
- `probe.py` — `check_meta_api_channel`, `classify_meta_probe`, `build_meta_channel_alert`, `meta_probe_loop`.
- `main.py` — `run_one_check`, `check_loop`, `main_loop` (оркестрация).

**Почему.** 99-risk-synthesis R6: «тройной/четверной детект отказа канала без общего дедупа → шквал
алертов». Разнеся детекторы, проще **ввести единый дедуп-слой** (общий `_maybe_alert_with_dedup` уже есть —
станет видно, что он применяется неравномерно). Риск low — детекторы уже почти не связаны между собой.

**Что даёт.** Каждый детектор тестируется/правится изолированно; явная точка для консолидации дедупа (R6);
probe-логика (единственный сетевой `GET /me`) отделена от БД-детекторов.

---

### A7 — Вынести валидаторы из `create_campaign.py` (650) (**consider**, S, low)

**Линия разреза.** Класс `CreateCampaignHandler` несёт **15 статических `_validate_*`**
(`_validate_name/objective/status/special_categories/cents/billing_event/optimization_goal/targeting/...`)
+ 4 `_build_*_body` + `execute` (Batch). Валидаторы — чистые функции без состояния handler'а →
`core/meta_api/mutations/_validators.py`. Body-builders (`_build_campaign/adset/creative/ad_body`) →
`_campaign_builders.py`. В классе остаётся `execute` + оркестрация Batch (~250 строк).

**Почему consider (и почему НЕ переписывать целиком — частичный skip).** Сам Batch-флоу
(JSONPath-refs, `CreateCampaignPartialError`, atomic rename) **оправдан спецификой Meta Batch API** — это
не «god ради god», переписывать его не надо. Боль только в том, что 15 валидаторов раздувают файл и
**не переиспользуются** другими мутациями (`set_adset_budget`, `set_ad_creative` валидируют те же cents/targeting заново).

**Что даёт.** Валидаторы тестируются без Batch-моков; переиспользование в соседних мутациях (DRY на
доменной валидации, где сейчас копипаста порогов/cents). −~200 строк из handler'а. Риск low — чистые
функции, перенос механический.

---

### A8 — Тонкие роутеры: `offers.py`/`tma.py`/`history.py` (**consider**, M, low)

**Линия разреза.** Роутеры смешивают HTTP-слой с доменной агрегацией:
- `offers.py` — `compare_offers` (~136 строк агрегации Offer+AdMetrics+AlertEvent) и `preview_rule_thresholds` (`_spend`-хелпер) → в `core/dashboard/` или `core/offers/`. Останутся тонкие CRUD-эндпоинты.
- `tma.py` — `_load_ad_extras`, `_resolve_ad_token`, `_create_disable_action` — это **доменная логика disable/snooze/claim**, продублированная с web-путём. Вынести в `core/tma/` или переиспользовать общий `core/tasks`-хелпер. (Заодно тут живёт R-tma и TMA-secret — трогать аккуратно.)
- `history.py` — уже частично вынес в `core/dashboard/history_queries.py` (8 SQL-функций, см. data-layer). Осталось дотащить парсинг окна/форматтеры; роутер сократится до тонких обёрток.

**Почему.** `arch/api-surface.md`: роутеры — «только чтение состояния + постановка в outbox», бизнес-логики
быть не должно. Сейчас compare/preview/tma-disable нарушают это. Вынос делает логику тестируемой **без
TestClient** (быстрее, без подъёма app).

**Что даёт.** Роутер = HTTP+валидация запроса; домен — в `core/`, переиспользуем между web и TMA (сейчас
disable-логика задвоена). Меньше дубля между `tma.py` и web-роутерами.

**Почему M, не S.** `tma.py` пересекается с auth/secret и активными findings (R-tma) — нужен аккуратный
перенос с тестами, не механический.

---

### A9 — Интерфейс на границе `meta_api → observer` (fsm_sync) (**consider**, M, med)

**Боль (00-system-map §4.1, цикл слоёв).** `core/meta_api/fsm_sync.py` импортирует
`core/observer/writers.reset_alert_state_after_*`. Слой **исполнения мутаций** зависит от слоя **детекта**.
Изменение сигнатуры reset-функций ломает FSM-sync **молча** — best-effort `except` глушит (99-risk-synthesis
паттерн #7). Это двусторонняя связь meta_api↔observer.

**Что меняем.** Определить `Protocol FsmResetPort` (методы `reset_after_disable/enable/claim`) в
**нейтральном** месте (`core/observer/ports.py` или `core/contracts/`). `writers` реализует его, `fsm_sync`
зависит от Protocol, не от конкретного модуля. Убрать «best-effort глушение» хотя бы для drift-ошибок
(`TypeError` сигнатуры — это баг, не транзиентный сбой; логировать как error).

**Почему consider, med.** Это 1-2 точки связи, не системный цикл — выгода умеренная, а риск трогать
money-FSM-sync реальный. Делать, только если A1/A2 уже показали ценность типизации границ. Альтернатива
дешевле: оставить импорт, но **снять `except`-глушилку** с сигнатурных ошибок (тогда drift хотя бы громкий).

**Что даёт.** Изменение reset-сигнатуры ломает типы/тест, а не молча оставляет FSM в `stop_sent` (money:
без sync FSM застревал — это уже была причина пробела).

---

### A10 — Типы для pubsub-событий (**consider**, S, low)

Те же модели, что A1, применить к `fb_agent:scan:finished`/`task:changed`/`health:updated` (сейчас
`json.dumps(event)` с inline-dict). Publisher и subscriber (фронт-инвалидация, observer-trigger) делят один
тип. Низкая срочность (события некритичны, fire-and-forget), но дёшево и закрывает остаток паттерна #3.
Делать «прицепом» к A1.

---

## Что НЕ делать (обоснование skip)

- **msgspec вместо pydantic/dataclass** — в стеке pydantic v2 + родной паттерн frozen-dataclass
  (`ScannedAdRow`/`FsmInput`). Объёмы сериализации (десятки Redis-ключей/мин) не оправдывают новую
  зависимость. Скорость msgspec тут не нужна, его валидация-на-decode достижима pydantic'ом.
- **DI-фреймворк поверх FastAPI Depends** — фабрики (`_default_*_factory`) + `Depends` уже покрывают
  инъекцию. Фреймворк = чистый оверхед без закрытого класса багов.
- **Дробить файлы до <500 строк как самоцель** — без чёткой линии разреза вынос плодит шов/косвенность и
  усложняет money-путь. Рекомендованы только разрезы по **ответственностям**, не по счётчику строк.
- **Event-bus / брокер (Kafka/NATS) вместо outbox+pubsub** — Postgres-outbox (race-safe claim/mark,
  идемпотентность, reconciler) + Redis-pubsub адекватны масштабу (один кабинет-профиль, single-writer на
  Vision-сессию). Брокер добавит операционный налог без выигрыша.
- **Переписывать `create_campaign` Batch-флоу** — оправдан спецификой Meta Batch API (JSONPath-refs,
  partial-error, atomic rename). Выносим только валидаторы (A7), ядро не трогаем.

---

## Рекомендованный порядок

1. **A2** (S) — money-дыра R3 закрывается типом, минимальный объём. Первым.
2. **A4 + A3** (S+M) — реестр воркеров → единый heartbeat/runner. Закрывает класс Round 11, −250 LOC.
3. **A1 + A10** (M+S) — типизация observer:runtime/heartbeat/pubsub. Закрывает класс CRIT-2.
4. **A7** (S) — валидаторы create_campaign (дёшево, разблокирует переиспользование).
5. **A6** (M) — health_watchdog по детекторам (готовит почву под единый дедуп R6).
6. **A5 / A8** (M) — observer_worker и роутеры; **после** A1/A3 (часть уедет бесплатно).
7. **A9** (M, опц.) — Protocol на границе meta_api↔observer, либо минимально снять except-глушилку drift.

Пункты 1-3 — основная окупаемость (закрывают рецидивирующий класс «контракт держится дисциплиной» по типам).
Пункты 4-7 — долг тестируемости/навигации, не money; брать по мере касания файлов.
