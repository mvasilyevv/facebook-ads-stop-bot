# Улучшения: библиотеки-заменители (закрыть кастом)

> Тема Фазы 2. Цель — найти **зрелые** библиотеки, которыми можно убрать значимый объём
> кастомного кода (LOC/баги), и **честно** отметить, где кастом обоснован и переписывание не окупается.
> Привязка к Фазе 1: `arch/workers-money.md`, `arch/workers-aux.md`, `arch/telegram.md`,
> `99-risk-synthesis.md`. Read-only по коду.

---

## Краткий вывод

Костяк системы (race-safe `claim/mark/requeue`, канон `attempt_count`, partition-pruning, FSM-guards,
ACL owner в TG/draft, batch JSONPath-encode) Фаза 1 признала **качественным**. Поэтому большинство
«модных» замен здесь — это **skip или consider, а не do**: они трогают money-критичный outbox, который
уже работает race-safe, и переписывание несёт больше риска, чем экономии.

Единственный кандидат с реальным позитивным балансом — **procrastinate** (Postgres-native очередь
задач): он совпадает с уже принятыми архитектурными решениями (Postgres+outbox, транзакционный defer,
`FOR UPDATE`, периодические задачи с catch-up) почти один-в-один и закрывает три **подтверждённых
паттерна-боли** из риск-синтеза: ручной catch-up в планировщиках (#11 workers-money), дубль
heartbeat_loop в 13 местах (контракт writer↔reader, паттерн #3) и партиции next-month, висящие на одном
cleanup_worker (R8). Но и он — `consider`, а не `do`: миграция money-контура — это L-усилие с риском
регрессии необратимых действий, оправданное только если команда осознанно идёт на унификацию воркеров.

Telegram (aiogram/PTB), circuit breaker (purgatory) и «готовый transactional outbox» — **skip**: кастом
тоньше, отвязан по контракту (TG-клиент сознательно без ORM), либо использует API, которого в библиотеках
нет (streaming-aware breaker). Замена раздувает зависимости и ломает уже отлаженные инварианты без выигрыша.

---

## Таблица рекомендаций

| Рекомендация | Что меняем | Почему (боль) | Что даёт | Усилие | Риск | Вердикт |
|---|---|---|---|:--:|:--:|:--:|
| **procrastinate как движок очереди** | `core/tasks/queue.py` claim/mark/reconcile + `apps/*_worker` while-loop + heartbeat_loop ×13 | Ручной outbox + 13 копий boilerplate, контракт writer↔reader держится дисциплиной (паттерн #3) | −600…900 LOC boilerplate, транзакц. defer, LISTEN/NOTIFY вместо polling, ретраи/локи из коробки | **L** | **high** | **consider** |
| **procrastinate `@periodic` для планировщиков** | `apps/digest_scheduler`, `apps/cleanup_worker`, окно в `core/scheduler/cabinet_autostart.py` | Ручной catch-up (digest/cleanup/autostart) + Redis-дедуп вручную; cleanup без catch-up (R8) | DB-гарантия «1 раз за период» на N воркеров, `max_delay` = тот же catch-up, убирает Redis-дедуп | **M** | **med** | **consider** |
| **purgatory вместо кастомного circuit breaker** | `core/browser/circuit_breaker.py` (233 LOC) | — (кастом качественный) | Меньше своего кода; но purgatory не покрывает streaming-API (`check_open`+ручной record) | **M** | **med** | **skip** |
| **aiogram 3 / PTB вместо httpx TG-клиента** | `core/telegram/client.py` (392 LOC) + `bot_handler`/handlers | — (клиент сознательно без ORM, тонкий) | FSM/middleware/типы; но ломает отвязку от ORM, дублирует ACL/дедуп | **L** | **high** | **skip** |
| **Готовый transactional-outbox пакет** | весь `task_queue`-контур | — (уже race-safe, верифицирован) | Ничего сверх текущего; чужая схема под money-инварианты | **L** | **high** | **skip** |
| **APScheduler (3.x sync / 4.x alpha)** | планировщики окон | — (procrastinate покрывает лучше) | 3.x sync не вписан в async; 4.x alpha — нельзя в прод | **M** | **high** | **skip** |

---

## Детализация

### 1. procrastinate как движок очереди задач — `consider` (L, high)

**Что заменяет (конкретно):**
- `core/tasks/queue.py` (458 LOC): `create_task` (ON CONFLICT idempotency), `claim_next_task`
  (`UPDATE ... WHERE id=(SELECT ... FOR UPDATE SKIP LOCKED)`), `mark_succeeded/mark_failed/requeue_for_retry`
  (с `WHERE status='running'`-guard), `reconcile_stuck_running`, `fail_stuck_irreversible`,
  `cancel_stale_drafts`, backoff `min(30·2^n, 300)`.
- Цикл `task_loop` + `heartbeat_loop` + graceful shutdown в воркерах-потребителях очереди:
  `meta_api_worker`, `creator_worker` (и архитектурно `disable/enable` через `toggle_executor`).
- `core/meta_api/queue.py` (395) + `core/meta_api/reconciler.py` (100) — outbox-обёртки над тем же.

**Why (боль).** Из `99-risk-synthesis.md`, сквозной паттерн #3: «контракт writer↔reader держится
дисциплиной, не типами» — `heartbeat_loop` физически скопирован в **13 файлов** (подтверждено grep'ом),
имена heartbeat жёстко связаны с `health_watchdog.EXPECTED_WORKERS`, любой рассинхрон тихий (история
Round 11). Ручной retry/reconcile/backoff — это код, который надо тестировать и держать в голове на каждом
ревью money-пути.

**Benefit (что даёт конкретно).**
- **−600…900 LOC** boilerplate (13× heartbeat_loop ≈ 200–300 LOC + ручной claim/mark/backoff/reconcile
  ≈ 400–600 LOC), которые перестанут быть поверхностью для багов.
- **Транзакционный defer** (`procrastinate` умеет `defer` на пользовательской транзакции через
  `SQLAlchemyPsycopg2Connector`/`PsycopgConnector`) — это **ровно тот инвариант**, который проект уже
  держит вручную: «enqueue в той же транзакции, что и бизнес-логика». Закрывает класс orphan-задач (грань
  R1: задача и изменение каталога коммитятся атомарно).
- **LISTEN/NOTIFY** вместо `while True: claim; sleep` — воркер просыпается на NOTIFY, а не поллит; ниже
  latency исполнения pause_ad и меньше холостых запросов к БД.
- Ретраи, exponential backoff, локи (`lock`/`queueing_lock`), периодика — из коробки, протестировано
  мейнтейнерами.

**Зрелость/maintenance (сверено).** PyPI **3.9.0** (июнь 2026), Production/Stable, MIT, ~1.3k★, релизы
каждые недели-месяцы весь 2025–2026 (3.0→3.9), Postgres 13+, Python 3.10+, нативный async + sync,
LISTEN/NOTIFY, транзакц. defer, периодика на cron-синтаксисе. Активно поддерживается.
Источник: [procrastinate GitHub](https://github.com/procrastinate-org/procrastinate),
[PyPI](https://pypi.org/project/procrastinate/),
[docs](https://procrastinate.readthedocs.io/).

**Почему всё-таки `consider`, а не `do` (честно).**
- **Это money-контур.** Текущий outbox **верифицирован** Фазой 1 как качественный (race-safe). Заменять
  работающий, протестированный 1055 тестами слой — это риск регрессии в **необратимых** действиях
  (pause/activate чужой рекламы). Выгода реальна, но не «бесплатна».
- **procrastinate-специфичные инварианты придётся восстановить руками.** Проект держит
  *особые* money-правила, которых у procrastinate нет «из коробки»: `fail_stuck_irreversible`
  (необратимые `create_campaign/duplicate_campaign` НЕ ретраятся — иначе дубль кампании),
  асимметричный стоп-гейт (на паузе исполняются только выключающие мутации), owner-scoping
  last-line-of-defense, FSM-sync после мутации. Это бизнес-логика поверх очереди — она остаётся в любом
  случае, procrastinate убирает только механику claim/retry/heartbeat, не доменные guard'ы.
- **Известная ловушка procrastinate-локов:** если воркер убит во время job с `lock=X`, следующие job с
  тем же lock не запустятся, пока зависший не помечен failed/succeeded вручную — то есть `reconciler`
  по сути **остаётся** (нужен periodic-таск, сбрасывающий зависшие). Это снижает экономию.
- **CHECK-constraints/статусы** (`draft/pending/running/...`, `task_type`) и схема `task_queue` —
  кастомные; миграция на схему procrastinate (`procrastinate_jobs`) или адаптер — отдельная работа.

**План (если идти):**
1. Пилот на **одном** не-money воркере (`tracker_aggregator` или `cleanup`) — обкатать connector,
   periodic, heartbeat-замену без риска для денег.
2. Перевести `creator_worker`/`plan_run` (latency-tolerant, не критично).
3. Только после стабильного пилота — `meta_api_worker`, с сохранением всех доменных guard'ов как
   pre/post-хуков задачи и periodic-reconciler под procrastinate-локи.
4. observer_worker **НЕ трогать** — это не outbox-consumer, а бесконечный scan-loop со своей семантикой
   (gate-factory, pubsub, адаптивный sleep); procrastinate там не к месту.

---

### 2. procrastinate `@periodic` для планировщиков окон — `consider` (M, med)

**Что заменяет:** ручные «окно HH:MM + Redis-дедуп + catch-up до конца суток» в:
- `apps/digest_scheduler/main.py` (317) — `is_in_send_window` + `digest:sent:YYYY-MM-DD` TTL 26h.
- `apps/cleanup_worker/main.py` (146) — `sleep until 04:00`, **без catch-up** (пропустил → +24h).
- `core/scheduler/cabinet_autostart.py` (142) + `apps/cabinet_scheduler` — окно + `cabinet:autostart:*`.

**Why (боль).** `99-risk` / `workers-money.md` инвариант #11: «catch-up окна реализованы вручную, повтор
блокирует Redis-ключ, не само окно» с хрупкостями (autostart `no_owner_ads`-путь не ставит done-ключ →
ретрай каждый тик; cleanup вообще без catch-up; R8 — партиции next-month висят на cleanup, его простой на
стыке месяца тихо останавливает INSERT во ВСЕ partitioned-таблицы).

**Benefit.** procrastinate `App.periodic(cron=...)` даёт **DB-гарантию «ровно один defer за период»** даже
при N воркерах, а `max_delay` — это **встроенный catch-up** (задача, просроченная меньше чем на `max_delay`,
до-деферится при старте; дольше — пропускается; ровно нужная семантика «нагнать digest при рестарте, но не
лить ночной cleanup днём»). Убирает ручной Redis-дедуп и его гонки (`worker_notify` dedup GET+SET NX не
атомарен — инвариант #8 telegram). 6-я колонка cron = секунды.
Источник: [procrastinate cron howto](https://procrastinate.readthedocs.io/en/stable/howto/advanced/cron.html).

**Почему `consider`.** Money-критичность `cabinet_scheduler` (автостарт кабинета = реальные деньги): его
дедуп (`idempotency_key=autostart:{day}:activate` + Redis) — двойная защита, которую нельзя ослабить при
переезде. Безопаснее всего — внедрять `@periodic` вместе с п.1 (один и тот же движок), а не точечно тащить
procrastinate только ради планировщика. Самостоятельный вывод: **cleanup_worker — первый кандидат**
(catch-up для партиций закрывает R8 при минимальном money-риске).

---

### 3. purgatory вместо кастомного circuit breaker — `skip` (M, med)

**Кастом:** `core/browser/circuit_breaker.py` (233 LOC), используется в `clients/python_grpc/client.py` и
`core/meta_api/client.py`.

**Почему skip (честно).** Кастомный breaker имеет **streaming-aware API**, которого у библиотек нет:
помимо обычного `call(func)` он отдаёт `check_open()` + раздельные `record_failure()/record_success()` —
это нужно для **gRPC server-streaming** (`RunScanCycle`), где нельзя обернуть весь стрим в один `await`:
проверяем перед стримом, фиксируем исход по факту дочитки. `purgatory`/`pybreaker` дают декоратор/контекст
вокруг одного awaitable — под стриминг их пришлось бы **обходить вручную**, воспроизводя ту же логику.
Плюс кастом уже интегрирован с `record_vision_failure()` (Prometheus-метрика). Зрелость purgatory
подтверждена (`purgatory` 3.0.1, активен, asyncio+Redis backend —
[purgatory GitHub](https://github.com/mardiros/purgatory)), но **выгоды нет**: 233 LOC простого, покрытого
тестами кода против новой зависимости, которая не закрывает главный use-case. Фаза 1 не нашла здесь ни
одного бага. Замена ради «не своё» — антипаттерн из требований заказчика.

---

### 4. aiogram 3 / python-telegram-bot вместо httpx-клиента — `skip` (L, high)

**Кастом:** `core/telegram/client.py` (392 LOC, send/edit/poll/forum + retry 429/5xx + HTML-балансировка)
и `bot_handler`/`handlers/*` (router, ACL-гейт, dispatch).

**Почему skip (честно).**
- **Сознательное архитектурное решение, подтверждённое Фазой 1:** клиент **намеренно отвязан от ORM**
  (`client.py` не зависит от SQLAlchemy — `telegram.md`), что даёт чистую тестируемость pure-рендереров
  (`format.py`, `renderer.py`) и переиспользование в `worker_notify`. aiogram/PTB тянут собственный Bot/
  Dispatcher/FSM/middleware-стек, который придётся **переплести** с уже существующими доменными
  инвариантами: pre-claim дедуп `telegram_message_refs` (sentinel `message_id=0`, UNIQUE на
  chat×ad×incident×stream), ACL-слои owner-only callbacks, hot-reload токена, dedup-after-send в
  `worker_notify`. Эти инварианты — **money/доставка алертов**, переписывать их под чужой роутер = high
  risk без функционального выигрыша.
- Объём кастома **умеренный** (392 LOC клиента — это тонкий httpx-wrapper, а не «фреймворк»), Фаза 1
  багов в нём не нашла (telegram: 0 CRIT/HIGH).
- **MEMORY-контекст:** идёт редизайн TG (волны DM+MiniApp, супергруппу убрать) — менять транспортный слой
  посреди продуктового редизайна повышает риск без причины.

aiogram 3 зрело и активно ([aiogram 3.29](https://github.com/aiogram/aiogram), async-only, 3.10+), PTB 21.x
тоже async и поддерживается — но зрелость библиотеки не аргумент за миграцию, когда свой слой тоньше и
сознательно спроектирован. **Skip обоснован спецификой**, а не инерцией.

---

### 5. Готовый «transactional outbox» пакет — `skip` (L, high)

**Почему skip.** Текущий `task_queue` **И ЕСТЬ** transactional outbox, причём верифицированный Фазой 1 как
race-safe (claim под `FOR UPDATE SKIP LOCKED`, `WHERE status='running'`-guard от двойного исполнения,
канон `attempt_count` в одной точке, idempotency UNIQUE). Готовых Python-пакетов «generic transactional
outbox» уровня зрелости procrastinate/celery нет — это паттерн, а не библиотека; то, что обычно тащат
(Debezium/Kafka-connect CDC) — для другого масштаба и стека. Единственный осмысленный «готовый outbox»
здесь — это **п.1 procrastinate** (его `defer` на пользовательской транзакции — и есть outbox из коробки).
Отдельной рекомендации сверх п.1 нет. Самостоятельный вывод заказчику из брифа подтверждён: **кастом
race-safe outbox дешевле миграции** — оставить своё.

---

### 6. APScheduler — `skip` (M, high)

**Почему skip.** APScheduler **4.x** (нативный async + asyncpg-jobstore + NOTIFY + мульти-шедулер HA) —
**всё ещё alpha** (4.0.0a6, апр 2025), мейнтейнер прямо пишет «do NOT use in production». APScheduler
**3.x** стабилен (3.11.2, дек 2025), но его jobstore **синхронный** (asyncpg невозможен) — в async-проекте
это чужеродный поток/тред-пул. Для периодики у нас уже есть лучший вписанный вариант — **procrastinate
`@periodic`** (п.2): Postgres-native, async, та же БД, тот же движок, что и очередь. Тащить второй
планировщик-фреймворк ради того же — лишняя зависимость.
Источник: [APScheduler PyPI](https://pypi.org/project/APScheduler/),
[4.0.0a6 migration notes](https://apscheduler.readthedocs.io/en/master/migration.html).

---

## Итог по теме

- **Реальный выигрыш только у procrastinate** и только если идти на унификацию воркеров целиком
  (очередь + периодика одним движком). Это `consider`/L — стратегическое решение под аппетит к риску, не
  «быстрая победа». Начинать — с не-money пилота (cleanup закрывает R8 малой кровью).
- **Всё остальное — `skip`**, и это честный skip: circuit breaker и TG-клиент тоньше своих библиотечных
  аналогов и закрывают use-case'ы (gRPC-streaming breaker; ORM-free TG), которых у готовых решений нет;
  outbox уже race-safe; APScheduler перекрыт procrastinate.
- Главный нелибрарный вывод: боли из риск-синтеза (дубль heartbeat_loop, ручной catch-up, R8-партиции) —
  **реальны**, но их можно закрыть и **малыми кастомными правками** (общий `worker_runtime`-хелпер,
  ленивый CREATE партиций) без миграции на фреймворк. Библиотека оправдана, только если ценность —
  системная унификация, а не латание трёх точек.
