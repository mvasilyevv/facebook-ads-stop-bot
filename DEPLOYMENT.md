# Deployment Guide — FB Stop Bot

Документ описывает развёртывание FB Stop Bot с нуля: системные требования,
обязательные секреты, инициализация БД, запуск воркеров, проверки и
production checklist.

Технические детали по архитектуре, воркерам и доменной логике — в
[CLAUDE.md](CLAUDE.md). Инциденты и восстановление — в [RUNBOOKS.md](RUNBOOKS.md).

---

## 1. Системные требования

| Компонент | Версия | Назначение |
|-----------|--------|------------|
| Linux / macOS | — | Тестировано на Darwin 25 (macOS) и Linux x86_64 |
| Python | 3.12+ | Все воркеры и FastAPI |
| Node.js | 20+ | `services/browser-agent` (gRPC) и frontend |
| Docker + Docker Compose v2 | актуальный | Postgres 16 + Redis 7 |
| Vision anti-detect browser | внешний | Хост с CDP на `127.0.0.1:3030` |
| `supervisord` (опционально) | 4.2+ | Автоперезапуск воркеров (`pip install supervisor`) |
| `cloudflared` (опционально) | актуальный | Quick-tunnel для приёма TG webhook/postback |

Минимальные ресурсы: 2 CPU, 4 GB RAM, 10 GB диск (Postgres + логи + Vision).

---

## 2. Быстрый старт (5 минут)

Подходит для dev-машины с уже установленными Docker, Python 3.12, Node.js.

```bash
git clone <repo-url> fb-stop-bot
cd fb-stop-bot

cp .env.example .env
# Открыть .env и заполнить минимум 4 ключа (см. § 3.1):
#   POSTGRES_PASSWORD, ENCRYPTION_KEY, VISION_X_TOKEN, VISION_PROFILE_ID
# Опционально: TELEGRAM_BOT_TOKEN (без него TG-бот работать не будет).

make bootstrap    # docker compose up + venv + install + apply schema
./run.sh          # старт всех воркеров через supervisord (если установлен)
```

После старта проверки:

```bash
curl -sf http://localhost:8000/healthz       # должен вернуть {"status":"ok"}
curl -sf http://localhost:8000/readyz        # {"ready":true,...}
supervisorctl -c supervisord.conf status     # все программы в RUNNING
```

---

## 3. Полная инструкция

### 3.1. Обязательные секреты

Минимальный набор переменных для запуска без UI-функционала:

| Переменная | Где взять / как сгенерировать |
|------------|-------------------------------|
| `POSTGRES_PASSWORD` | Любая надёжная строка. По умолчанию `.env.example` использует имя БД — это insecure default, `core/config.py` пишет warning. |
| `ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Должен оставаться неизменным между деплоями: им зашифрованы `vision_config.x_token_encrypted` и `telegram_config.bot_token_encrypted` в БД. |
| `VISION_X_TOKEN` | Из настроек Vision-браузера (раздел API). |
| `VISION_PROFILE_ID` | ID нужного профиля в Vision. |
| `TELEGRAM_BOT_TOKEN` | От [@BotFather](https://t.me/BotFather). Без него Telegram poller и health watchdog не смогут слать алерты. |

Опциональные, но рекомендуемые в production:

| Переменная | Назначение |
|------------|------------|
| `API_KEY` | Если пуст — сгенерируется автоматически и дописывается в `.env`. В проде задавайте явно. |
| `SENTRY_DSN` / `SENTRY_ENVIRONMENT` | Трейсинг ошибок. |
| `ADSETPRO_MCP_KEY`, `ADSETPRO_POSTBACK_SECRET` | Интеграция с трекером AdSet.pro (Этап 6). Без `ADSETPRO_POSTBACK_SECRET` endpoint `/api/v1/postback/adsetpro` возвращает 503. |
| `EXPECTED_WORKERS` | CSV имён воркеров, которые мониторит `health_watchdog`. Дефолт: `observer,disable,enable,telegram_poller,cleanup,reconciler,meta_api`. |
| `FRONTEND_ORIGIN` | Включает CORS только для указанного origin. Без него CORS не подключается. |
| `WEB_APP_URL` | Публичный URL Mini App для inline-кнопок в TG-алертах (cloudflared задаёт автоматически при `--tunnel`). |
| AI-провайдеры (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) | Опциональны. При пустых ключах AI-функции no-op, ошибок нет. |

Полный список — в [.env.example](.env.example) и `Settings` в [core/config.py](core/config.py).

### 3.2. Внешние зависимости

**Postgres 16 + Redis 7** поднимаются через `docker-compose.yml`:

```bash
docker compose up -d                    # порты 5433 (PG) и 6380 (Redis), bind 127.0.0.1
docker compose ps                       # healthcheck должен показывать healthy
```

Данные хранятся в именованных томах `pgdata` и `redisdata`. Бэкап перед DROP
обязателен (см. § 3.4).

**Vision anti-detect browser** — внешний сервис, не входит в этот репозиторий.
Должен слушать на `VISION_API_URL` (по умолчанию `http://127.0.0.1:3030`),
профиль `VISION_PROFILE_ID` должен быть запущен с CDP-портом. При первом
запросе `apps/api/routers/vision` (роут `/api/vision/ensure-cdp`, поднимается
`run.sh`) пробует автоматически восстановить CDP если выставлен флаг
`VISION_AUTO_RESTART_ON_MISSING_CDP=true`.

**Node.js gRPC browser-agent** собирается из `services/browser-agent/` и
запускается на `GRPC_PORT=50051`. `run.sh` собирает (`npm run build`) и
стартует автоматически; supervisord перезапускает при падении.

### 3.3. Установка зависимостей

```bash
make install            # venv + pip install -e '.[dev]' + npm ci в frontend/
# или вручную:
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cd frontend && npm ci && cd ..
cd services/browser-agent && npm install && npm run build && cd ../..
```

### 3.4. Инициализация БД

Свежий деплой:

```bash
docker compose up -d postgres redis
make db-wait                # ждёт healthcheck Postgres
python scripts/apply_schema.py --confirm-drop
```

`apply_schema.py` делает: `DROP SCHEMA public CASCADE` → `CREATE SCHEMA` →
`CREATE EXTENSION pgcrypto` → `Base.metadata.create_all()` (35 таблиц) →
партиции на текущий и следующий месяц для 7 partitioned-таблиц →
`system_config.retention_policy` со значениями по умолчанию.

**Сохранение секретов между миграциями.** Vision X-Token и Telegram bot token
зашифрованы Fernet и переживут DROP только через явный бэкап:

```bash
python scripts/backup_secrets.py     # data/secrets_backup_YYYYMMDD_HHMMSS.json (chmod 600)
python scripts/apply_schema.py --confirm-drop
python scripts/restore_secrets.py    # берёт последний бэкап
```

Если бэкапа нет, ввести токены придётся вручную через TG-команды или прямой
UPDATE по `vision_config` / `telegram_config`.

Alembic-миграции (`migrations/versions/`) применяются автоматически из
`run.sh` (`alembic upgrade head`). Если файлов миграций нет — `run.sh`
аварийно создаёт схему через `Base.metadata.create_all` (fallback для dev).

### 3.5. Запуск

Через `run.sh` (рекомендуется):

```bash
./run.sh                # supervisord + browser-agent + воркеры + frontend
./run.sh --dev          # uvicorn --reload
./run.sh --no-tunnel    # отключить cloudflared quick-tunnels
./run.sh --down         # остановка всех процессов и supervisord
./run.sh --logs         # tail -20 каждого *.log в .logs/
./run.sh --restart      # --down + повторный старт
```

Что делает `run.sh`:

1. Проверяет `.env`, Python 3.12+, Docker.
2. Останавливает предыдущие процессы (`PID_FILE` + matched `pgrep`).
3. `docker compose up -d` (Postgres + Redis), ждёт `pg_isready` до 45 с.
4. Создаёт `.venv`, ставит зависимости (с кэшем по MD5 `pyproject.toml`).
5. Применяет миграции (`alembic upgrade head`).
6. Поднимает FastAPI на `${API_PORT:-8100}` через uvicorn (foreground supervisor использует порт 8000 — см. ниже).
7. Дёргает `POST /api/vision/ensure-cdp` для проверки Vision CDP.
8. Собирает и запускает browser-agent.
9. Если найден `supervisord` — поднимает все воркеры через [supervisord.conf](supervisord.conf), иначе запускает их `python run_*.py` напрямую (без autorestart).
10. Поднимает frontend (`frontend/`) и Mini App (`frontend-mini/`).
11. Опционально — cloudflared quick-tunnels для API / Web UI / Mini App. Mini-app URL автоматически прописывается в `telegram_config.web_app_url` через API.
12. `caffeinate` блокирует сон macOS.

> **Замечание о портах API.** В `supervisord.conf` FastAPI запускается на
> `0.0.0.0:8000`. В `run.sh` (если supervisord не используется) — на
> `API_PORT` из `.env` (по умолчанию 8100). Тесты и health watchdog
> ориентируются на 8000. На проде унифицируйте: либо задайте `API_PORT=8000`
> в `.env`, либо используйте supervisord.

Через Makefile (раздельный запуск отдельных воркеров):

```bash
make api                # FastAPI с --reload
make observer
make disable-worker
make enable-worker
make meta-api-worker
make health-watchdog
make enable-reco-worker
make digest-scheduler
make telegram
make creator-worker
make creator-recorder
make cleanup-worker
make reconciler-worker
```

`make help` — список всех таргетов.

### 3.6. Healthcheck

После старта должно быть:

```bash
# supervisord (если запущен через run.sh с установленным supervisor)
supervisorctl -c supervisord.conf status
# все 14 программ в RUNNING:
#   browser_agent, api, observer_worker, disable_worker, enable_worker,
#   telegram_poller, meta_api_worker, creator_worker, creator_recorder,
#   health_watchdog, enable_recommendation_worker, digest_scheduler,
#   reconciler_worker, cleanup_worker

# FastAPI
curl -sf http://localhost:8000/healthz     # {"status":"ok"}
curl -sf http://localhost:8000/readyz      # {"ready":true,"postgres":true,"redis":true}
curl -sf http://localhost:8000/metrics     # Prometheus exposition

# Heartbeat воркеров (TTL 60s — должны быть свежие)
redis-cli -p 6380 keys 'worker:heartbeat:*'
# observer, disable, enable, telegram_poller, cleanup, reconciler,
# meta_api, health_watchdog, creator, creator_recorder, digest_scheduler,
# enable_recommendation_worker

# Observer runtime status (JSON с TTL 60s)
redis-cli -p 6380 get observer:runtime

# Telegram бот
# В Telegram: /start <invite-code>  → бот должен ответить
```

Логи всех процессов — в `.logs/`. Каждый воркер пишет в свой файл,
supervisord ротирует с `stdout_logfile_maxbytes=10MB`, 3 бэкапа.

---

## 4. Что должно быть после успешного запуска

Контрольный список:

- 14 программ под supervisord в состоянии `RUNNING` (см. § 3.6).
- 1 процесс `node services/browser-agent/dist/index.js` слушает gRPC на `:50051`.
- FastAPI на `:8000` отвечает 200 на `/healthz` и `/readyz`.
- В Redis есть ключи `worker:heartbeat:*` (12 штук), все с TTL ~30-60 секунд.
- В Redis есть ключ `observer:runtime` с актуальным JSON.
- В Telegram бот отвечает на `/start <invite-code>`. Сами инвайты создаются
  через `python scripts/create_telegram_invite.py` (см. `scripts/`).
- В БД таблицы `vision_config` и `telegram_config` содержат singleton-строки
  с зашифрованными токенами (`x_token_encrypted IS NOT NULL`).
- `pubsub`-канал `fb_agent:scan:finished` получает сообщения после каждого scan-цикла.

---

## 5. Production checklist

Перед выкаткой в production:

- [ ] `ENCRYPTION_KEY` сгенерирован Fernet, хранится в secret manager (не в репо).
- [ ] `POSTGRES_PASSWORD ≠ POSTGRES_DB` (иначе `core/config.py` пишет warning).
- [ ] `TELEGRAM_BOT_TOKEN` — продакшен-бот, отличный от dev-бота.
- [ ] `API_KEY` задан явно (не сгенерирован автоматически в `.env`).
- [ ] Перед каждым `apply_schema.py --confirm-drop` — запуск `backup_secrets.py`.
- [ ] `SENTRY_DSN` и `SENTRY_ENVIRONMENT=production` заданы.
- [ ] HTTPS reverse-proxy (nginx / Caddy / cloudflared tunnel) перед FastAPI
      на `/api/v1/postback/adsetpro` — AdSet.pro отправляет postback'и без TLS verify только на HTTPS endpoint.
- [ ] `ADSETPRO_POSTBACK_SECRET` задан и совпадает с тем, что сконфигурирован в AdSet.pro (иначе endpoint вернёт 503 "not configured").
- [ ] `EXPECTED_WORKERS` соответствует реально запущенным воркерам (несоответствие → ложные алерты health_watchdog).
- [ ] Ротация логов в `.logs/` настроена (supervisord уже ротирует stdout, но при логировании в файлы — нужен logrotate).
- [ ] Vision-профиль не закрывается случайно (`VISION_AUTO_RESTART_ON_MISSING_CDP=true` помогает, но окно профиля закроется при restart).
- [ ] Docker volume `pgdata` бэкапится регулярно (минимум раз в сутки).
- [ ] `.env` исключён из git (есть в `.gitignore`).
- [ ] `data/secrets_backup_*.json` исключён из git и хранится в шифрованном виде.
- [ ] Файрвол: порты 5433 (Postgres), 6380 (Redis), 50051 (gRPC), 8000 (API), 5173/5174 (frontend) bound на 127.0.0.1 кроме API/frontend, проксируемых через nginx.
- [ ] Системные пакеты: установлены `cloudflared` (если используется), `caffeinate` (на macOS — для блока сна).

---

## 6. Helm / Kubernetes (production)

В репо есть артефакты для k8s:

```
helm/fb-stop-bot/                 # Helm chart
docker/Dockerfile.{api,workers,browser-agent,frontend,mini-app,python-base}
k8s/                              # raw manifests (вспомогательные)
```

Сборка и установка:

```bash
make docker-build                                  # все 5 образов
make k3s-import IMAGE_TAG=v1.0.0                   # импорт в локальный k3s
make helm-install                                  # helm upgrade --install
make k8s-logs                                      # kubectl logs -f
```

`helm/fb-stop-bot/secrets.yaml` нужно создать локально из `secrets.example.yaml`
(если есть) или собрать вручную с теми же значениями, что в `.env`.
В этом раунде документации Helm-флоу подробно не покрыт — отдельный заход.

---

## 7. Что НЕ покрыто в этом документе

- Подробная настройка Helm chart и production k8s (см. `helm/fb-stop-bot/` и `Makefile` targets `helm-*`).
- Бэкап Postgres (рекомендация — `pg_dump` в cron / managed Postgres со снапшотами).
- TLS и rate-limiting на reverse-proxy.
- Multi-tenant deployment (несколько Vision-аккаунтов в одном инстансе) — отложено, см. `META_INTEGRATION_PLAN.md` Этап 8.

Если нужен production-grade k8s — отдельный раунд документации.
