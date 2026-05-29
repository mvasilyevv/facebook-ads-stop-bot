# Runbooks — FB Stop Bot

Сценарии реагирования на инциденты, восстановления и опасных операций.

Перед действиями убедитесь, что у вас есть доступ к: серверу с проектом
(SSH), Telegram-каналу для алертов, Vision UI, Postgres и Redis (`docker
compose exec`). Полная архитектура и список воркеров — в [CLAUDE.md](CLAUDE.md).

## Содержание

- [Vision-сессия упала](#vision)
- [Воркер не дышит (health_watchdog алерт)](#worker-dead)
- [Telegram-бот не отвечает](#tg-down)
- [Meta API: токен сессии invalidated (190)](#token-invalid)
- [Disable не срабатывает (toggle gate down)](#toggle-down)
- [Партиции на следующий месяц не созданы](#partition-stuck)
- [Очередь забилась (>1000 pending tasks)](#queue-full)
- [Ротация ENCRYPTION_KEY](#rotate-key)
- [Восстановление БД из бэкапа](#restore-db)
- [Полный wipe и пересоздание схемы](#full-wipe)
- [Postback от AdSet.pro возвращает 401/503](#postback-fail)
- [Frontend выдаёт CORS-ошибки](#cors)

---

<a id="vision"></a>
## Vision-сессия упала

**Симптомы:**
- В `.logs/observer.log` — `ScanDataUnavailableError`, `SessionUnavailableError` или `gRPC StatusCode.UNAVAILABLE`.
- Любой toggle-таск падает в `task_queue.status='retrying'`.
- Health watchdog шлёт алерт `observer worker stale` (если `observer:runtime` устарел >5 мин).

**Диагностика:**

```bash
# Vision API alive?
curl -sf "$VISION_API_URL/api/sessions/$VISION_PROFILE_ID"

# CDP порт виден?
curl -sf "http://localhost:8000/api/vision/ensure-cdp" -X POST   # или порт 8100 если без supervisord

# gRPC браузер-агент слышит?
nc -z localhost 50051

# Лог browser-agent
tail -50 .logs/browser_agent.log
```

**Действия:**

1. Открыть Vision UI, проверить что профиль `VISION_PROFILE_ID` запущен и не отключён.
2. Зайти в Ads Manager вручную через окно профиля — может быть expired session (Facebook требует повторного login). Если так — залогиниться заново и оставить вкладку Ads Manager открытой.
3. Если CDP порт пропал — вызвать `POST /api/vision/ensure-cdp` (с `X-API-Key` если задан). При `VISION_AUTO_RESTART_ON_MISSING_CDP=true` это закроет окно профиля и переоткроет с CDP.
4. Если browser-agent в `FATAL`/`BACKOFF` — `supervisorctl -c supervisord.conf restart browser_agent`.
5. После восстановления Vision — `supervisorctl restart all` (observer переподключится к browser-agent).

---

<a id="worker-dead"></a>
## Воркер не дышит

**Симптомы:**
- В TG приходит `Воркер <name> не дышит более N минут (heartbeat истёк)` от health_watchdog.
- В Redis отсутствует `worker:heartbeat:<name>` (TTL 60s истёк).

**Диагностика:**

```bash
# Что говорит supervisord
supervisorctl -c supervisord.conf status

# Лог воркера
tail -100 .logs/<worker>.log

# Heartbeat ключи в Redis
redis-cli -p 6380 keys 'worker:heartbeat:*'
redis-cli -p 6380 ttl worker:heartbeat:observer

# Стек упавшего процесса
grep -E 'Traceback|ERROR|CRITICAL' .logs/<worker>.log | tail -20
```

**Действия:**

1. Если воркер в `BACKOFF`/`FATAL` — `supervisorctl restart <worker>`.
2. Если воркер в `RUNNING`, но heartbeat не пишется — лог скажет почему (вероятно блок на gRPC или Redis). Проверить `nc -z localhost 50051` и `redis-cli -p 6380 ping`.
3. Если `EXPECTED_WORKERS` в env не совпадает с реально нужным набором — поправить и рестартовать `health_watchdog` (`supervisorctl restart health_watchdog`). Алерт дедуплицируется на 1 час через `health:alerted:<worker>` в Redis.
4. Если воркер постоянно падает — посмотреть `core/<домен>/` соответствующего воркера, проверить наличие нужных таблиц в БД (`apply_schema.py` мог не пройти).

---

<a id="tg-down"></a>
## Telegram-бот не отвечает

**Симптомы:**
- `/start`, `/help`, `/spy` молчат.
- Алерты от observer/health_watchdog не приходят.

**Диагностика:**

```bash
# Токен живой?
TG_TOKEN=$(.venv/bin/python -c "from core.crypto import decrypt_token; from core.telegram.service import load_telegram_config; import asyncio; from core.db import get_engine; ...")
# проще — через прямой запрос если токен известен:
curl -sf "https://api.telegram.org/bot<TOKEN>/getMe"

# Логи poller'а
tail -50 .logs/telegram.log

# Что в telegram_config
docker compose exec -T postgres psql -U fb_stop_bot -d fb_stop_bot \
    -c "SELECT chat_id, forum_warning_thread_id, forum_stop_thread_id, web_app_url, updated_at FROM telegram_config;"
```

**Действия:**

1. Проверить что `telegram_config.bot_token_encrypted` не пустой. Если пустой — записать через UI/API (или прямой INSERT с зашифрованным значением).
2. Проверить что `ENCRYPTION_KEY` в `.env` тот же, что использовался при сохранении токена (иначе `decrypt_token` упадёт с `InvalidToken`).
3. Если Telegram API отвечает 401 — токен отозван у BotFather, нужен новый.
4. `supervisorctl restart telegram_poller`.
5. Если бот шлёт сообщения, но не получает обновления — проверить, что нет конкурирующего polling (нельзя одновременно polling + webhook).

---

<a id="token-invalid"></a>
## Meta API: токен сессии invalidated (190)

**Симптомы:**
- В `.logs/meta_api_worker.log` повторяющиеся `TokenInvalidError` (Graph error code 190).
- Все таски `meta_api_mutation` уходят в `failed` без retry.
- AI-tools падают с `TokenInvalidError`.

**Действия:**

1. Зайти в Ads Manager через Vision-профиль вручную, проверить что сессия Facebook жива.
2. Если сессия живая, но Marketing API всё равно отдаёт 190 — токен сессии (LSD/access_token, который Marketing API получает через `page.evaluate(fetch)`) был invalidated на стороне Meta. Перезагрузить страницу Ads Manager во вкладке профиля.
3. Если не помогло — `supervisorctl restart browser_agent` (полный перезапуск gRPC сервиса вместе с Vision-сессией).
4. Если повторяется регулярно — возможно, аккаунт под подозрением Meta. Эскалировать вручную.

> Marketing API доступен только из Vision-сессии (`page.evaluate(fetch)`).
> Полный отрыв в standalone-Marketing-API не пройдёт `Identity Confirmation` —
> см. `META_INTEGRATION_PLAN.md` § 11 и `CLAUDE.md` "Этап 4 Ad Library — закрыт".

---

<a id="toggle-down"></a>
## Disable не срабатывает

**Симптомы:**
- В `task_queue` есть записи `task_type='disable'`, `status='retrying'`, `attempts > 3`.
- В TG приходит сообщение, что disable не сработал.

**Диагностика:**

```bash
# Что в очереди
docker compose exec -T postgres psql -U fb_stop_bot -d fb_stop_bot -c "
  SELECT task_type, status, attempts, last_error, fb_ad_id, created_at, next_attempt_at
  FROM task_queue
  WHERE task_type IN ('disable','enable')
    AND status IN ('retrying','failed')
  ORDER BY created_at DESC LIMIT 20;
"

# Что говорит disable_worker
tail -100 .logs/disable_worker.log | grep -E 'ERROR|toggle_ad|gRPC'
```

**Действия:**

1. Vision-сессия жива? → см. [Vision-сессия упала](#vision).
2. Если ошибки про "элемент не найден" / "колонка скрыта" — Ads Manager поменял DOM. Нужно обновить парсер: `services/browser-agent/src/parser.ts` и `ads-columns.ts`. Это код-change, не runbook.
3. Если задача в `failed` навсегда — отменить вручную:
   ```sql
   UPDATE task_queue SET status='cancelled' WHERE id = '<task_id>';
   ```
   и при необходимости создать новую через UI / TG inline.
4. Reconciler автоматически переводит `running` старше 30 мин → `retrying` (см. `apps/reconciler_worker/`). Если этого не происходит — проверьте, что reconciler_worker жив.

---

<a id="partition-stuck"></a>
## Партиции на следующий месяц не созданы

**Симптомы:**
- INSERT в `ad_metrics`, `alert_events`, `scan_runs`, `meta_api_audit_log`, `meta_api_webhook_event`, `ad_library_snapshot`, `tracker_postback` падают с `no partition of relation ... found for row`.
- Воркеры падают в начале нового месяца.

**Диагностика:**

```bash
docker compose exec -T postgres psql -U fb_stop_bot -d fb_stop_bot -c "
  SELECT parent.relname AS parent, child.relname AS partition
  FROM pg_inherits
  JOIN pg_class parent ON parent.oid = pg_inherits.inhparent
  JOIN pg_class child  ON child.oid  = pg_inherits.inhrelid
  WHERE parent.relname IN (
    'ad_metrics','alert_events','scan_runs',
    'meta_api_audit_log','meta_api_webhook_event',
    'ad_library_snapshot','tracker_postback'
  )
  ORDER BY 1,2;
"
```

**Действия:**

1. `cleanup_worker` создаёт партиции на следующий месяц раз в сутки в 04:00 UTC. Если воркер был мёртв — `supervisorctl restart cleanup_worker` и подождать одного прохода (либо запустить `python run_cleanup_worker.py` вручную).
2. Аварийно создать партицию руками:
   ```sql
   CREATE TABLE ad_metrics_2026_06
     PARTITION OF ad_metrics
     FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
   -- повторить для всех 7 партиционированных таблиц
   ```

---

<a id="queue-full"></a>
## Очередь забилась (>1000 pending tasks)

**Симптомы:**
- `SELECT count(*) FROM task_queue WHERE status IN ('pending','retrying')` > 1000.
- Disable срабатывает с задержкой в десятки минут.

**Диагностика:**

```sql
SELECT task_type, status, count(*), min(created_at), max(created_at)
FROM task_queue
WHERE status IN ('pending','retrying','running')
GROUP BY 1,2
ORDER BY 3 DESC;
```

**Действия:**

1. Узкое место — воркер. Если `disable_worker` отстаёт — он работает sequentially (`FOR UPDATE SKIP LOCKED` + один Vision поток). Параллелить нельзя — Vision-сессия одна. Только ускорить отдельные toggle-операции.
2. Если очередь забита `meta_api_mutation` — `meta_api_worker` обрабатывает по одному и retry'ит при `RateLimited`. Подождать.
3. Если очередь забита retry'ями — почистить:
   ```sql
   -- Отменить таски старше 24h
   UPDATE task_queue SET status='cancelled'
   WHERE status='retrying' AND created_at < now() - interval '24 hours';
   ```
4. Reconciler уже отменяет `draft` старше 24 часов автоматически (`cancel_stale_drafts`).

---

<a id="rotate-key"></a>
## Ротация ENCRYPTION_KEY

**Когда нужно:** периодически (раз в 6–12 мес) или при подозрении на утечку
`.env` / secret manager.

**Опасность:** если перешифровать только часть строк или потерять старый
ключ — расшифровать `vision_config.x_token_encrypted` /
`telegram_config.bot_token_encrypted` уже не получится. Бэкап обязателен.

**Шаги:**

1. Сделать бэкап:
   ```bash
   python scripts/backup_secrets.py   # data/secrets_backup_*.json
   pg_dump … > backup.sql              # на всякий случай полный dump
   ```
2. Сгенерировать новый ключ:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
3. В `.env` поставить:
   ```
   ENCRYPTION_KEY=<новый>
   ENCRYPTION_KEY_VERIFY=<старый>     # см. Settings в core/config.py
   ```
4. Прогнать перешифровку через утилиту `core/crypto.py::rotate_encryption_key` (
   она использует raw SQL по `telegram_config` и `vision_config`, не зависит
   от ORM). Готового CLI на момент написания нет — запуск через REPL:
   ```python
   import asyncio
   from core.crypto import rotate_encryption_key
   asyncio.run(rotate_encryption_key(old_key="<старый>", new_key="<новый>"))
   ```
5. Проверить, что Vision/TG продолжают работать:
   - `curl http://localhost:8000/readyz`
   - В TG: `/start` должен ответить
   - `supervisorctl restart all`
6. После проверки убрать `ENCRYPTION_KEY_VERIFY` из `.env`.

Если что-то пошло не так — `python scripts/restore_secrets.py <backup>`
после возврата старого `ENCRYPTION_KEY`.

---

<a id="restore-db"></a>
## Восстановление БД из бэкапа

**Сценарий:** Postgres-том повреждён / пересоздаётся инстанс / пришла катастрофа.

**Шаги:**

1. Если есть полный `pg_dump`:
   ```bash
   docker compose down postgres
   docker volume rm fb_agent_pgdata
   docker compose up -d postgres
   make db-wait
   docker compose exec -T postgres psql -U fb_stop_bot -d fb_stop_bot < backup.sql
   ```
2. Если бэкапа схемы нет, но есть `data/secrets_backup_*.json` от старой
   установки:
   ```bash
   python scripts/apply_schema.py --confirm-drop
   python scripts/restore_secrets.py <path/to/backup>
   ```
   Остальные настройки (offer, observer interval, install cost) задаются
   заново через UI или прямой INSERT.
3. После восстановления — `supervisorctl restart all`, проверки из § 4
   [DEPLOYMENT.md](DEPLOYMENT.md).

---

<a id="full-wipe"></a>
## Полный wipe и пересоздание схемы

**Когда:** разработческий стенд, повреждённая схема, миграция на новую
структуру.

**Опасность:** `apply_schema.py --confirm-drop` делает
`DROP SCHEMA public CASCADE` — необратимо удаляет все данные.

```bash
python scripts/backup_secrets.py          # обязательно
docker compose ps                          # убедиться что Postgres alive
python scripts/apply_schema.py --confirm-drop
python scripts/restore_secrets.py
supervisorctl -c supervisord.conf restart all
```

`apply_schema.py` создаёт партиции только на текущий + следующий месяц.
Для следующих месяцев работает `cleanup_worker`.

---

<a id="postback-fail"></a>
## Postback от AdSet.pro возвращает 401/503

**Симптомы:**
- `.logs/api.log` показывает `POST /api/v1/postback/adsetpro 401/503`.
- AdSet.pro в своей админке видит ошибки доставки.

**Действия:**

1. **503 "not configured"** — `ADSETPRO_POSTBACK_SECRET` пуст в `.env`.
   Задать секрет, прописать тот же в AdSet.pro, `supervisorctl restart api`.
2. **401 "invalid secret"** — секрет в `.env` и в AdSet.pro расходятся.
   Сверить, обновить, рестартовать.
3. Если AdSet.pro шлёт по HTTP, а FastAPI ждёт HTTPS — поставить
   reverse-proxy (nginx / cloudflared) и зарегистрировать новый URL в
   админке AdSet.pro.
4. Сейчас postback'и только логируются (`logger.info`). Запись в БД с
   дедупом по `click_id` — не реализована (Волна 3 миграций, см.
   `CLAUDE.md`).

---

<a id="cors"></a>
## Frontend выдаёт CORS-ошибки

**Симптомы:**
- В консоли браузера: `CORS policy: No 'Access-Control-Allow-Origin'`.

**Действия:**

1. Задать `FRONTEND_ORIGIN` в `.env` (например, `http://localhost:5173`).
2. `supervisorctl restart api`.
3. Без `FRONTEND_ORIGIN` CORS-middleware не подключается — это
   намеренно (см. `apps/api/main.py`).
4. На проде передавайте полный origin с протоколом и портом, без
   trailing slash.

---

## Общие команды диагностики

```bash
# Всё ли крутится
supervisorctl -c supervisord.conf status

# Логи отдельных воркеров
tail -50 .logs/observer.log
tail -50 .logs/disable_worker.log
tail -50 .logs/meta_api_worker.log
./run.sh --logs                              # tail -20 каждого *.log

# Метрики FastAPI (Prometheus)
curl -s http://localhost:8000/metrics | grep app_requests_total

# Очередь задач
docker compose exec -T postgres psql -U fb_stop_bot -d fb_stop_bot -c "
  SELECT task_type, status, count(*) FROM task_queue GROUP BY 1,2 ORDER BY 1,2;
"

# FSM ad_alert_state
docker compose exec -T postgres psql -U fb_stop_bot -d fb_stop_bot -c "
  SELECT alert_state, count(*) FROM ad_alert_state GROUP BY 1;
"

# Recent alert events
docker compose exec -T postgres psql -U fb_stop_bot -d fb_stop_bot -c "
  SELECT created_at, alert_stage, fb_ad_id, rules_triggered
  FROM alert_events
  WHERE created_at > now() - interval '1 hour'
  ORDER BY created_at DESC LIMIT 20;
"
```
