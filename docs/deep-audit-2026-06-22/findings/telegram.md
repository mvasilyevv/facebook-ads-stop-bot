# Аудит: Telegram-подсистема

Дата: 2026-06-22. Read-only, adversarial review исходного кода.
Не дублирует закрытые находки из AUDIT_2026-06-17.md и секции «Известные технические долги» CLAUDE.md.

---

## Резюме

| Severity | Кол-во |
|----------|--------|
| HIGH     | 2      |
| MID      | 3      |
| LOW      | 3      |
| **Всего**| **8**  |

---

## HIGH

### H-1: `worker_notify` dedup — не атомарный GET+SET, окно дублирования уведомлений

**Файл:** `core/telegram/worker_notify.py:61-67, 89-91`

**Проблема.** Функции `notify_owners` и `notify_recipients` проверяют дедуп-ключ двумя раздельными Redis-операциями: `GET dedup_key` → (если нет) `send_message` → `SET NX dedup_key`. Если два воркера вызовут `notify_owners` с одним `dedup_key` одновременно (например, `health_watchdog` и `meta_api_worker` оба обнаружили проблему), оба пройдут GET-check и оба отправят TG-сообщение. SET NX ставится только после доставки, поэтому «гонка-до-SET» реальна в промежутке между GET и SET.

**Impact.** Дублирование money/ops алертов в DM owner'а. Не money-потеря напрямую, но при frequent-events (health_watchdog сработал несколько раз за TTL) owner получит флуд идентичных критических сообщений — может проигнорировать как дубль, пропустив реальный инцидент.

**Fix.** Заменить двухшаговый GET+SET на атомарный `SET NX EX` ПЕРЕД send:
```python
if dedup_key:
    set_ok = await redis.set(dedup_key, "1", nx=True, ex=dedup_ttl_seconds)
    if not set_ok:
        return False  # уже отправлено
# send ...
```
Если send упал — ключ всё равно стоит, следующий TTL-цикл пошлёт снова (dedup "на период TTL"). Это нормальное поведение для best-effort нотификаций.

**Confidence:** high.

---

### H-2: `/setup_topics` и `topics.py` задокументированы в CLAUDE.md, но файлы удалены — команды мёртвые заглушки в prod

**Файл:** `core/telegram/handlers/router.py` (нет обработчика), `CLAUDE.md:162`

**Проблема.** CLAUDE.md описывает `topics.py` (идемпотентный провижн форум-топиков) и `handlers/topics.py` (`/setup_topics`, `/topics`). Оба файла **отсутствуют** в кодовой базе. В `router.py` нет ни `cmd == "setup_topics"`, ни `cmd == "topics"`. Пользователь, который отправит `/setup_topics` или `/topics`, получит «Неизвестная команда» (отваливается в unknown-branch).

**Impact.** Форум-топики супергруппы нельзя создать через бот. Маршрутизация алертов по топикам (`thread_id_by_stage`) в `dispatch_pending_alerts` и `sweep_orphan_alerts` возвращает пустой dict — все алерты летят в General (это корректный fallback). Главный риск: документация обещает функционал, которого нет; оператор теряет время при настройке супергруппы.

**Fix.** Либо восстановить `core/telegram/topics.py` + `handlers/topics.py` и добавить маршрут в router, либо явно убрать из CLAUDE.md упоминание `/setup_topics`, `/topics`, `PgTopicStore` и `thread_id_by_stage`.

**Confidence:** high (файлы отсутствуют, поведение однозначно).

---

## MID

### M-1: `sweep_orphan_alerts` — N SQL-запросов per scan-цикл по числу recipients (N+1 pattern)

**Файл:** `core/telegram/alert_dispatcher.py:425-463`

**Проблема.** `sweep_orphan_alerts` итерирует по `recipients` и для каждого выполняет отдельный `SELECT alert_events ... WHERE ... AND r.chat_id = :cid`. При M recipients = M round-trips к PostgreSQL в конце каждого scan-цикла. При нормальной нагрузке (2-5 recipients) это несущественно, но архитектурно это N+1: один запрос с `UNNEST` по chat_id'ам и `GROUP BY / PARTITION` решил бы задачу за один round-trip.

**Impact.** При 10+ recipients и высокой частоте scan-циклов — излишняя нагрузка на Postgres. Реальный scan-цикл может занять значимое дополнительное время.

**Fix.** Переписать запрос с `UNNEST(:chat_ids::bigint[])` как inline-таблицей recipients, JOIN с alert_events по `chat_id`, GROUP/FILTER по NOT EXISTS per-chat. Один запрос вместо N.

**Confidence:** high.

---

### M-2: Stale docstring в `creator.py` заявляет «любой recipient» для `plan` callback, а router ограничивает только owner

**Файл:** `core/telegram/handlers/creator.py:247-249`, `core/telegram/handlers/router.py:66`

**Проблема.** Комментарий в `handle_plan_run_callback`:
```
# ACL сейчас не ограничен (любой active recipient может запустить любой план — 
# это намеренно мягкий уровень доступа)
```
Но в `router.py` action `plan` входит в `_OWNER_ONLY_CALLBACKS = frozenset({"dis", "ereco", "plan", "dr_ok"})`, значит нажать кнопку «Запустить» может только owner. Аналогично `/record_plan` и `/stop_record` — в `_OWNER_ONLY_COMMANDS`. Docstring врёт о реальном ACL.

**Impact.** Если опираться на docstring при ревью или тестировании — ожидаемое поведение расходится с реальным. При анализе инцидентов оператор может ошибочно решить, что не-owner запустил план, хотя это невозможно.

**Fix.** Исправить комментарий: «ACL: только owner (router.py:_OWNER_ONLY_CALLBACKS)». Или если «любой recipient» — намеренный дизайн, убрать `plan` из `_OWNER_ONLY_CALLBACKS` и восстановить аудит-лог.

**Confidence:** high.

---

### M-3: `handlers/alerts.py` docstring заявляет «Access control — recipient'ы только», а фактически ereco — owner-only

**Файл:** `core/telegram/handlers/alerts.py:5`, `core/telegram/handlers/router.py:66`

**Проблема.** Docstring модуля `alerts.py`: «action ∈ {'dis', 'ereco'}. Access control — recipient'ы только.» Реально `ereco` в `_OWNER_ONLY_CALLBACKS` — только owner. `dis` — тоже owner-only. Модуль не реализует никакой своей ACL-проверки, полностью полагаясь на router.py; но его docstring вводит в заблуждение разработчика, который будет добавлять новый callback action.

**Impact.** Некорректная ментальная модель → потенциальная ACL-дыра при добавлении нового action в `alerts.py` с ожиданием, что достаточно быть recipient'ом.

**Fix.** Исправить docstring: «ACL для всех действий модуля определяется в router.py (_OWNER_ONLY_CALLBACKS): dis и ereco — только owner.»

**Confidence:** high.

---

## LOW

### L-1: `dispatch_pending_alerts` — жёстко заданное 1-часовое окно без учёта реального времени scan-цикла

**Файл:** `core/telegram/alert_dispatcher.py:305`

**Проблема.** `since_dt = datetime.now(timezone.utc) - timedelta(hours=1)` — константа, не параметр. Если scan-цикл теоретически затянулся > 1ч (тормозит сеть, Vision завис), алерты, вставленные в начале scan'а, могут не попасть в окно фильтра `created_at >= :since`. В реальности scan занимает секунды/минуты, но edge case не документирован.

**Impact.** Очень маловероятен, но при сбое Vision (долгий scan) → потерянные алерты не доставятся через dispatch (их подберёт sweep, но уже в следующем цикле).

**Fix.** Добавить параметр `since_hours: int = 1` или вычислять `since_dt` как `scan_started_at - buffer`, если scan_id позволяет получить время начала скана.

**Confidence:** med (очень редкий edge case).

---

### L-2: `send_text` в `handlers/_send.py` молча отбрасывает `reply_to_message_id`

**Файл:** `core/telegram/handlers/_send.py:23,33`

**Проблема.** Параметр `reply_to_message_id` принимается в сигнатуре и сразу присваивается `_` (no-op). Telegram Bot API поддерживает `reply_to_message_id`, просто `client.send_message()` не пробрасывает его в payload. Комментарий «клиент не поддерживает» некорректен — это сознательная заглушка в `client.send_message`. В результате все reply-ответы шлются как обычные сообщения без треда.

**Impact.** Пользователь в группе не видит связи ответа с командой. В DM это незаметно. При переходе на группы или топики — потеря контекста.

**Fix.** Добавить `reply_parameters` (Telegram API v7+) или `reply_to_message_id` в `client.send_message` payload.

**Confidence:** high (явный no-op в коде).

---

### L-3: Stale `_client_cache` в `worker_notify` не закрывает старый httpx-клиент при ротации токена

**Файл:** `core/telegram/worker_notify.py:32-38`

**Проблема.** При ротации токена `_client_cache.clear()` удаляет ссылку на старый `TelegramBotClient`, но `client.close()` не вызывается. Внутренний `httpx.AsyncClient` старого объекта не закрывается → connection pool утекает до GC. `TelegramBotClient.__init__` при `http_client=None` создаёт новый `httpx.AsyncClient` и устанавливает `_owns_http_client=True`, но `close()` в `worker_notify` никогда не вызывается.

**Impact.** Утечка connection pool при ротации токена (редкое событие). До GC — 1-2 лишних TCP connection. Незначительно в проде с редкой ротацией.

**Fix.** При замене клиента: `old_client = _client_cache.get(old_token); if old_client: asyncio.ensure_future(old_client.close())`.

**Confidence:** med (требует code-path с ротацией).

---

## Выводы

Telegram-подсистема архитектурно зрелая: pre-claim dедуп на уровне Postgres UNIQUE constraint надёжен, partition pruning присутствует в ключевых запросах, ACL-гейт централизован в router.py, hot-reload токена без рестарта реализован.

Главные проблемы — **несинхронизированные инварианты** (docstrings/CLAUDE.md расходятся с кодом, H-2/M-2/M-3) и **неатомарный dedup** в `worker_notify` (H-1). Money-критичных дыр (дублированный авто-стоп, ложный disable, ACL bypass на mutation) нет. Snooze удалён. Наибольший операционный риск — H-2 (мёртвые команды /setup_topics при попытке настройки форум-топиков).
