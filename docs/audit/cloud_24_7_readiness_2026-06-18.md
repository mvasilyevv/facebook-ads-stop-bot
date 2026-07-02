# Готовность FB Stop Bot к переезду на выделенный сервер (24/7)

Дата аудита: 2026-06-18. Аудит сквозной: Vision-интеграция, ops/хостинг, web UI, TG mini app + бот, backend-ручки и баги. Все находки подтверждены по коду (ссылки `file:line` внутри).

---

## 0. Краткий вердикт (TL;DR)

**Переезжать можно, но не «как есть».** Ядро (авто-стоп, FSM, outbox, mutations, ~1200 тестов) — зрелое и money-safe по логике. Но 24/7 на сервере упирается в три блока, которые сейчас не закрыты:

1. **Vision на сервере.** Технически Vision **обязан жить на том же хосте**, что browser-agent (CDP захардкожен на `127.0.0.1`). Сам по себе это не блокер — ставим Vision рядом. Реальная сложность — **headless-запуск GUI-браузера на Linux** (Xvfb + автостарт + залогиненная вкладка Ads Manager + повторный логин FB без человека у экрана). Это главный нерешённый кусок.
2. **Ops не готов к 24/7.** Нет автостарта при ребуте, эфемерный cloudflared-туннель **теряет postback'и AdSet.pro (деньги/депозиты)**, нет бэкапа Postgres, API без autorestart, k8s/helm-артефакты сломаны.
3. **UI/TG — 2 блокер-бага + дыра в auth mini app.** Удаление оффера тихо не работает; кнопка Vision «Переподключить» = 404; mini app не может делать write-действия в проде (шлёт только Bearer, а бэк требует `X-API-Key`).

**Незакрытый money-риск №1 (важнее всего):** полный цикл `scan → FSM → авто-disable` **на живом кабинете не прогонялся** (#36). Сами mutations pause/activate проверены вживую (24/24), но целостный авто-стоп — только в тестах.

**Оценка усилий:** это сфокусированный спринт на ~6 блокеров + headless-инфраструктура Vision, не «переписать всё». Детали — разделы 6–7.

---

## 1. Вопрос №1: можно ли использовать «облачный/серверный» Vision?

**Да — но Vision и browser-agent должны быть на одном хосте.** Удалённый Vision «из коробки» не поддерживается.

Что выяснено по коду:

- `VISION_API_URL` (HTTP control-API Vision, дефолт `http://127.0.0.1:3030`) **конфигурируется** через env/БД (`core/config.py:53-57`). Технически control-API можно увести на другой адрес.
- **НО** само CDP-подключение к браузеру **захардкожено на localhost**:
  - `services/browser-agent/src/session-manager.ts:519` → `http://127.0.0.1:${port}` → `chromium.connectOverCDP(...)`
  - `services/browser-agent/src/vision-client.ts:189` → `cdpUrl(port)` тоже `127.0.0.1`
  - Vision раздаёт каждому профилю **динамический CDP-порт** на localhost; Playwright цепляется к нему как к локальному.
- Понятий `CDP_HOST` / SSH-туннеля / remote-CDP в коде и доках **нет** (проверено grep'ом).

**Вывод:** самый простой и правильный путь — **поставить Vision на тот же Linux-сервер**, что и весь стек. Выносить Vision на отдельную машину = патчить 2-3 места + пробрасывать диапазон CDP-портов; нетривиально, не рекомендуется на первом этапе.

### Что нужно, чтобы Vision реально крутился headless на Linux 24/7

Это организационно самый тяжёлый кусок и в репо он **не покрыт** (DEPLOYMENT.md лишь «Vision — внешний сервис»):

| # | Требование | Почему |
|---|-----------|--------|
| 1 | **Виртуальный дисплей (Xvfb / `xvfb-run`)** | Vision — GUI-приложение (.deb/.AppImage). На сервере без X нужен виртуальный дисплей. Антидетект-браузеры обычно требуют именно Xvfb, а не `--headless`. |
| 2 | **systemd-юнит автозапуска Vision** | Поднять Xvfb → Vision → дождаться, пока профиль `VISION_PROFILE_ID` стартует с CDP-портом. Сейчас есть только `ensure-cdp` reconnect к **уже запущенному** профилю (`settings_vision.py`, `run.sh:1161`). |
| 3 | **Гарантия открытой залогиненной вкладки Ads Manager** | Код ждёт, что вкладка Ads Manager уже открыта и залогинена (`session-manager.ts:15,30-33,407`). Иначе первый скан падает «страница недоступна». |
| 4 | **Повторный логин FB без человека** | Facebook периодически требует re-login/2FA/checkpoint (`docs/playbooks/RUNBOOKS.md:53`). На headless-сервере **некому пройти вручную** → авто-стоп молча умолкает. Нужен SSH+VNC/noVNC доступ к Xvfb-дисплею. **Самый вероятный источник тихого простоя.** |
| 5 | **Резидентный прокси / правильный fingerprint** | Datacenter-IP сервера повышает шанс чекпоинтов FB. Vision-профиль должен ходить через тот же резидентный прокси, что и раньше. (Вне кода, но критично.) |
| 6 | `VISION_AUTO_RESTART_ON_MISSING_CDP=true` | Помогает авто-восстановлению, но **закрывает окно профиля** при рестарте (`DEPLOYMENT.md:271`) — после чего снова нужна залогиненная вкладка (п.3). |

---

## 2. Vision как single point of failure (money-critical)

**Vision — жёсткий SPOF для авто-стопа.** Цепочка money-critical полностью проходит через живой Vision:

> STOP-правило → observer создаёт `task_queue meta_api_mutation pause_ad` → `meta_api_worker` → gRPC `ExecuteGraphCall` → **`page.evaluate(fetch)` изнутри Vision-вкладки Ads Manager** (токен EAA из DOM, cookies сессии). Standalone httpx невозможен по дизайну (anti-fraud Meta) — `services/browser-agent/src/meta-api/client.ts:93-109`.

**Что будет, если Vision упадёт/зависнет/разлогинится:**

- `meta_api_worker` ловит `SessionUnavailableError`/`TemporaryError` → **requeue с backoff** (`apps/meta_api_worker/main.py:153-157,411`). STOP-задача **бесконечно ретраится, но не исполняется** → **объявление продолжает жечь бюджет**.

**Дыры в наблюдаемости (это и есть money-риск):**

- **Нет heartbeat у browser-agent/Vision.** Ключ `worker:heartbeat:browser-agent` **читается** (`settings_vision.py:40`), но **никто его не пишет** (grep по `services/browser-agent/src/` = 0). В `EXPECTED_WORKERS` browser-agent тоже нет (`apps/health_watchdog/main.py:52`). → Падение Vision ловится только **косвенно** (staleness observer'а >5 мин).
- **Нет алерта «mutation-канал застрял».** Если сканы идут, а ломается только канал мутаций (например, токен на mutation-вкладке протух) — STOP-задачи молча копят `retrying`, явного «авто-стоп не исполняется» алерта **нет**. Health_watchdog видит, что воркер **дышит**, но не то, что его задачи **проходят**.

**Что авто-recovery уже умеет (3 слоя, надёжность средняя):**
- Layer 1 (browser-agent): закрыли вкладку, но CDP жив → переоткрывает на last-known URL (`session-manager.ts:409-496`).
- Layer 2 (Python): «страница недоступна» → `reconnect_browser` + повтор (`clients/python_grpc/client.py:429-445`); протухший `session_id` → авто-`StartBrowser` (`client.py:550-576`).
- Layer 3 (observer): N циклов подряд падают → TG-алерт «Observer — деградация» (`apps/observer_worker/main.py:566-624`).
- **Не чинит:** перезапуск Vision после reboot, разлогин FB, first-cycle blindness (вкладку закрыли до кэширования URL кабинета).

---

## 3. Готовность по областям (светофор)

| Область | Статус | Главное |
|--------|--------|---------|
| **Backend-логика / mutations** | 🟢 Готово | Все money-действия имеют рабочий канал; 10/10 mutation-handlers; 14 entrypoint'ов на месте; ~1200 тестов (нужен локальный прогон). |
| **Авто-стоп end-to-end (живой кабинет)** | 🟡 Не проверено | #36: целостный `scan→FSM→pause` вживую не прогонялся. Mutations live-validated 24/24. |
| **Vision на сервере** | 🔴 Не готово | Co-location ок, но headless-инфра (Xvfb/autostart/re-login) не реализована. |
| **Наблюдаемость Vision** | 🔴 Дыра | Нет heartbeat browser-agent, нет алерта на застрявший mutation-канал. |
| **Ops / автостарт / туннели** | 🔴 Не готово | Нет systemd, эфемерный туннель теряет postback, нет бэкапа PG, API без autorestart. |
| **Web UI** | 🟡 Почти | Ядро работает; 2 блокер-бага; нет enable/бюджета/создания кампании в web. |
| **TG-бот** | 🟢 Хорошо | Алерты, inline-кнопки, draft-ACL, /pause /resume /autostart — надёжно. |
| **TG mini app** | 🔴 Блокер | Write-действия вне `/tma/*` падают 401 (шлёт только Bearer, бэк ждёт X-API-Key). |
| **Графейсфул-shutdown / очередь задач** | 🟢 Готово | SIGTERM во всех воркерах, `FOR UPDATE SKIP LOCKED` + reconciler + idempotency → не теряет/не задваивает. |
| **Schedulers (autostart/digest)** | 🟢 В целом | UTC-safe, двойная защита от двойного включения. Один конфиг-риск (пустой `dates`). |
| **docker/helm/k8s** | 🔴 Сломано | Ссылаются на удалённые воркеры, `/health`, `host.docker.internal`. Путь к проду — `run.sh`+systemd, не k8s. |

---

## 4. Риски (приоритизировано; 💰 = money-critical)

| # | Риск | 💰 | Где |
|---|------|----|-----|
| R1 | **Авто-стоп end-to-end не проверен на живом кабинете** (#36) | 💰 | observer + Vision column-preset |
| R2 | **Vision headless на Linux не реализован** (Xvfb/autostart/re-login FB) | 💰 | вне репо, см. §1 |
| R3 | **AdSet.pro postback на эфемерном cloudflared-URL** → теряются депозиты после каждого рестарта | 💰 | `run.sh:1106,1217`; postback идёт через API |
| R4 | **Нет автостарта при ребуте** (ни systemd, ни docker restart-policy) | 💰 косв. | `docker-compose.yml`, нет `*.service` |
| R5 | **Vision — SPOF без прямого heartbeat и без алерта на застрявший mutation-канал** | 💰 | `health_watchdog/main.py:52`, `meta_api_worker/main.py:411` |
| R6 | **Postgres-volume без автобэкапа** — потеря истории/FSM при сбое диска | 💰 | нет `pg_dump` cron |
| R7 | **cabinet_scheduler: пустой `dates` → молчаливый пропуск дня** (done-маркер ставится) | 💰 | `apps/cabinet_scheduler/main.py:126-131` |
| R8 | **API (uvicorn) без autorestart** — падение ломает postback-приём/фронт/Vision ensure-cdp | 💰 косв. | `supervisord.conf:134`, `run.sh:786` |
| R9 | **Mini app не делает write вне `/tma/*`** (401 в проде): scan-now, тумблер скана, CRUD офферов/правил, настройки | 🟡 | `middleware/api_key_auth.py:37`, `frontend-mini/src/lib/api.ts:34` |
| R10 | **Удаление оффера в web тихо не работает** (пустая заглушка `deleteOfferFn`) | 🟡 | `frontend/src/routes/offers/index.tsx:255-269` |
| R11 | **Web «Vision → Переподключить» = 404** (путь `/settings/vision/reconnect` vs реальный `/api/vision/reconnect`) | 🟡 | `frontend/src/lib/api/settings.ts:264` |
| R12 | **`run.sh` — интерактивный foreground с `trap EXIT`**: закрытие SSH гасит всё | 💰 косв. | `run.sh:541,1308` |
| R13 | **Логи `.logs/*.log` из run.sh без ротации** → переполнение диска за недели | 🟡 диск | нет logrotate |
| R14 | **Нет enable объявления из web** (только через TG/reco-флоу) — ключевой money-сценарий восстановления | 🟡 | `frontend` + `enable_recommendations.py` |
| R15 | **Секреты в plaintext `.env`; `ENCRYPTION_KEY` должен переживать деплои** | 🟡 | `.env` (gitignored ок) |
| R16 | **docker/helm/k8s артефакты сломаны** (удалённые воркеры, `/health`, `host.docker.internal`) | 🟢 | `docker/`, `helm/`, `k8s/` |
| R17 | **BSD-синтаксис `nc -z`/`lsof`/`md5`** в run.sh — возможны сбои health-проверок на чистом Linux | 🟡 | `run.sh` |
| R18 | **`syntx_auth_token` (30 дней) без авто-рефреша** → image-генерация отвалится молча | 🟢 | `core/syntx/auth.py:12` |
| R19 | **TMA Bearer TTL 1ч + re-auth зависит от свежести initData** (>24ч открытая вкладка → «Нет доступа») | 🟡 | `core/auth/tma.py`, `config.py:68` |
| R20 | **Authz-bypass read-команд в group-чатах** (`/ask`/`/spy` доступны не-recipient'у) — деньги не задеты, но AI-квота/спам | 🟢 | `core/telegram/handlers/router.py:258` |

**Хорошо (риска нет):** TG long-polling (эфемерный URL не вредит приёму команд); graceful shutdown + dispose во всех воркерах; задачи не теряются/не задваиваются (SKIP LOCKED + reconciler + idempotency_key); UTC-таймзоны планировщиков; двойная защита от двойного включения кабинета (Redis NX + БД UNIQUE); supervisord autorestart 13 воркеров + crashmail; TMA-auth алгоритмически корректен (HMAC + per-request проверка recipient → мгновенный отзыв).

---

## 5. Что можно и нельзя делать через UI / TG mini app

**Web UI — можно:** стоп (одиночный/bulk) + снуз + hard-delete объявлений; подтвердить/отклонить draft-мутацию; офферы (создать/редактировать/правила, live-preview порогов, мульти-кабинет); scan-now, пауза/возобновление скана, рестарт observer, «новый день кабинета», allowlist кампаний; настройки Telegram/Vision (сохранение); просмотр Dashboard/History/очередей/Health.

**Web UI — нельзя (нет в интерфейсе):** включить объявление обратно (enable); enable-рекомендации; изменить бюджет; создать/клонировать кампанию; /spy; корректировка фейк-депозитов; retry/cancel задач; управление TG-получателями; история сканов. Часть из этого есть на бэке, но не выведена в web; часть — только через AI-tools/Telegram (draft-first).

**TG mini app — работает:** все read-экраны; над конкретным объявлением — disable/snooze/claim/open; drafts list/confirm/reject (через `/api/tma/*`, Bearer).
**TG mini app — НЕ работает в проде (R9):** scan-now, тумблер скана, CRUD офферов/правил, настройки Vision/Telegram, план в Scripts → 401.

**TG-бот — работает надёжно:** алерты + inline `dis:`/`snz:`/`ereco:`; draft-подтверждение `dr_ok`/`dr_cancel` (owner-ACL); `/pause`, `/resume`, `/autostart`, `/ask`, `/spy`, `/setup_topics`, `/topics`.

---

## 6. Чеклист перед переездом

### Блокеры (без них 24/7 ненадёжен)

- [ ] **Headless-инфра Vision на Linux:** Xvfb + systemd-автозапуск Vision + гарантия открытой залогиненной вкладки Ads Manager (R2).
- [ ] **Процедура re-login FB без человека:** SSH+VNC/noVNC к Xvfb-дисплею; runbook на checkpoint/2FA (R2).
- [ ] **Постоянный домен для API/postback** (именованный Cloudflare Tunnel / nginx+Caddy+LE / Tailscale Funnel на API-порт); прописать стабильный postback-URL в кабинете AdSet.pro (R3).
- [ ] **systemd-юнит** на `run.sh`/`supervisord` с `Restart=always`, не-интерактивный запуск (R4, R12).
- [ ] **`restart: unless-stopped`** для postgres/redis в `docker-compose.yml` (R4).
- [ ] **Автобэкап Postgres** (`pg_dump` в cron, минимум ежедневно) (R6).
- [ ] **Прогон авто-стопа end-to-end на живом кабинете** (#36): column-preset + поднятый Vision-профиль → проверить `scan→FSM→pause` целиком (R1).
- [ ] **Фикс mini app write-auth (R9):** перевести write-действия на `/tma/*` под Bearer+owner-ACL. **НЕ** прокидывать `VITE_API_KEY` в публичный бандл.
- [ ] **Фикс 2 web-багов:** смонтировать `OfferDeleteManager` (R10); путь reconnect → `/api/vision/reconnect` (R11).

### Сильно желательно

- [ ] **Heartbeat для browser-agent** + добавить в `EXPECTED_WORKERS`, чтобы health_watchdog ловил смерть Vision напрямую (R5).
- [ ] **Алерт «mutation-канал застрял»:** мониторить `task_queue meta_api_mutation` в `retrying` с возрастом/`attempt_count` выше порога → TG (R5).
- [ ] **Вернуть API под autorestart** (отдельная `[program:api]` на едином `API_PORT=8100`) (R8).
- [ ] **logrotate** для `.logs/*.log` API/frontend/cloudflared (R13).
- [ ] **Добавить enable объявления в web UI** (R14).
- [ ] **cabinet_scheduler:** алерт при пустом `dates` в окне вместо тихого пропуска (R7).
- [ ] **Зафиксировать `tma_session_secret`** в проде (не полагаться на фолбэк `encryption_key`) (R19).
- [ ] **Стабильный `ENCRYPTION_KEY`** в secret-хранилище, бэкап отдельно от сервера, `.env` → `chmod 600` (R15).

### Привести в порядок / явно отказаться

- [ ] **Путь деплоя:** либо чинить docker/helm/k8s под нативный Linux, либо официально объявить их deprecated и идти `run.sh`+systemd (R16).
- [ ] Раздавать фронт через nginx (образы есть) вместо `vite preview`.
- [ ] Проверить `nc`/`lsof`/`md5` на целевом дистрибутиве; при необходимости `apt install netcat-openbsd` (R17).
- [ ] Резидентный прокси/fingerprint для серверного IP (§1, п.5).
- [ ] Внешний uptime-мониторинг (`/healthz` + postback-URL) — локальный health_watchdog не увидит падение хоста.
- [ ] Синхронизировать порт API в `.env.example`/`DEPLOYMENT.md` (:8000 → :8100); поправить `ADSETPRO_BASE_URL` → `https://adset.pro`; развести `MINI_PORT`.
- [ ] Прогнать локально `pytest tests/ -x --timeout=30` и `ruff check .` (в sandbox-аудите не выполнялось — macOS-venv несовместим с Linux).
- [ ] Authz read-команд в группах (R20); `syntx_auth_token` авто-рефреш (R18).

---

## 7. Пошаговый план миграции на выделенный Linux-сервер

**Фаза 0 — подготовка (до сервера).** Закрыть 2 web-бага (R10, R11) и mini-app auth (R9) — это локальные правки. Прогнать `pytest`/`ruff` на машине. Решить путь деплоя (рекомендуется `run.sh`+supervisord под systemd; k8s отложить).

**Фаза 1 — сервер и базовая инфра.** Арендовать Linux-сервер (Ubuntu 22.04+, 2+ CPU / 4+ GB / 20+ GB). Docker + Compose, Python 3.12, Node 20. `restart: unless-stopped` для postgres/redis. systemd-юнит на запуск стека. Перенести `.env` + `ENCRYPTION_KEY` (`chmod 600`).

**Фаза 2 — Vision headless.** Установить Vision (.deb/.AppImage). Поднять Xvfb. systemd-юнит: Xvfb → Vision → профиль с CDP. Залогинить FB-профиль вручную через VNC, открыть вкладку Ads Manager, прописать резидентный прокси. Проверить `POST /api/vision/ensure-cdp` → `ok=true`.

**Фаза 3 — постоянный домен.** Поднять именованный туннель/nginx с фикс-доменом для API. Прописать стабильный postback-URL в AdSet.pro. Проверить, что `web_app_url` для mini app стабилен.

**Фаза 4 — наблюдаемость.** Heartbeat browser-agent + в `EXPECTED_WORKERS`. Алерт на застрявший mutation-канал. API под autorestart. logrotate. Внешний uptime-мониторинг. Автобэкап `pg_dump`.

**Фаза 5 — боевая проверка (money).** На живом кабинете прогнать авто-стоп end-to-end (#36): дождаться STOP-правила/симулировать → убедиться, что `pause_ad` реально исполнился через Vision. Проверить enable/reco, autostart кабинета по расписанию, приём postback'ов (депозиты появляются в трекере). Прогнать сценарий «убили Vision → починили» и убедиться, что алерты пришли, а очередь добила задачи.

**Фаза 6 — переключение.** Параллельный прогон со старой машиной 1-2 дня, сверка метрик/алертов, затем полное переключение. Старую машину держать как горячий резерв до накопления статистики стабильности.

---

## Приложение: ключевые файлы

- Vision/браузер: `services/browser-agent/src/{session-manager,vision-client,meta-api/client,index}.ts`, `clients/python_grpc/client.py`, `core/config.py:53-57`, `apps/api/routers/v1/settings_vision.py`, `docs/playbooks/RUNBOOKS.md:26-56`.
- Авто-стоп/наблюдаемость: `apps/observer_worker/main.py:566`, `apps/meta_api_worker/main.py:153-157`, `apps/health_watchdog/main.py:52`.
- Ops: `run.sh` (`:497,541,786,1106,1217,1262,1308`), `supervisord.conf:134`, `docker-compose.yml`, `apps/cabinet_scheduler/main.py:126-131`, `scripts/setup_tailscale_funnel.sh`, `DEPLOYMENT.md:256-312`.
- UI/TG: `frontend/src/routes/offers/index.tsx:255`, `frontend/src/lib/api/settings.ts:264`, `apps/api/middleware/api_key_auth.py:37`, `frontend-mini/src/lib/api.ts:34`, `core/auth/tma.py`, `core/telegram/handlers/router.py:258`.
- Backend-ручки: `apps/api/routers/v1/{disable_tasks,enable_recommendations,draft_tasks,tma,settings_observer,observer}.py`, `core/meta_api/mutations/`.
