# Remote Desktop: беспарольный вход + переезд на KasmVNC — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development или executing-plans. Шаги — чекбоксы (`- [ ]`).

**Goal:** (Фаза 0) убрать постоянный ввод логина/пароля на веб-панели, переведя её на беспарольный Telegram-вход. (Фаза 1) заменить канал рабочего стола Guacamole+guacd+TigerVNC на KasmVNC, чтобы на телефоне картинка масштабировалась под экран, а не показывалась «столбом» 1366×768.

**Честный вердикт (до старта прочитать владельцу):**
- KasmVNC **чинит**: fit-to-width/пинч-зум/панораму на телефоне (клиентский Local Scaling), радикально упрощает стек (−3 контейнера, −БД, −JAR, −200 строк readiness).
- KasmVNC **НЕ чинит** (врождённые ограничения iOS Safari/WebKit — одинаковы у Guacamole/KasmVNC/Selkies): системный copy/paste (2FA-коды — только ручным полем), fullscreen без установки PWA, обрыв WebSocket при блокировке экрана/сворачивании (нужен reconnect).
- **Это разворот вчерашнего решения** (`docs/superpowers/plans/2026-07-17-remote-desktop-single-path.md` фиксировал Guacamole как единственный канон). Делаем осознанно, по прямому запросу владельца — не как рутинный рефакторинг.
- **Money-риск:** KasmVNC = смена ПЕРВИЧНОГО X-сервера Vision (Xorg→Xvnc). Обязателен fingerprint-smoke FB Ads Manager (T10) ДО боевого cutover.

**Tech Stack:** Docker, s6-overlay, KasmVNC (.deb kasmtech/KasmVNC), Caddy v2.11 (forward_auth), FastAPI, Redis (тикеты/сессии), Telegram Bot.

## Global Constraints

- Money-критичность: Vision-канал сканирования (gRPC :50051) не падает дольше ~2 мин; cutover — в окно низкой активности рекламы. **CI_DEPLOY_ENABLED=true** — merge в main деплоит автоматически, встроенного «окна» нет; заливать в спокойное время вручную/через паузу деплоя.
- Vision — anti-detect: серверное разрешение фиксировано 1366×768×24 DPI96, НЕ меняется под клиента (fingerprint). Fit-to-width — только client-side.
- Один пользователь (владелец, telegram_user_id=911436108). Доступ: Mac Safari/Chrome + iPhone Safari, БЕЗ VPN, только браузер по HTTPS.
- Авторизация десктопа не зависит от канала: тикет→cookie→forward_auth→owner-recheck (`core/auth/desktop_access.py`) переиспользуется как есть.
- Перед работой: `git fetch` + сверка с origin/main (владелец параллельно работает через Codex-PR — [[project-remote-desktop-single-path]]).
- Комментарии/логи/тесты — по-русски (CLAUDE.md).

---

## ФАЗА 0 — Беспарольный вход по Telegram (независимо, делать первой)

**Мотив:** пароль болит только на веб-панели `app.adpulse.su` в браузере (Caddy `basic_auth`). Mini App уже беспарольный (TMA initData→Bearer), рабочий стол — тикет→cookie. Переводим панель на тот же тикет→cookie-контур с долгим TTL. BasicAuth не удаляем — прячем в break-glass.

**Архитектура:** владелец жмёт в Telegram-боте «Открыть панель» → бот шлёт одноразовую ссылку `https://app.adpulse.su/auth/panel/redeem?ticket=…` → ставится долгоживущая cookie `__Secure-adpulse_panel_session` (30 дней, sliding) → Caddy на панели и `/api/*`,`/ws/*` делает `forward_auth` на `/auth/panel/verify` вместо `import panel_auth`. Owner-recheck как у десктопа. BasicAuth остаётся только на скрытом `/auth/recovery`.

### Task P1: Panel-сессия в core/auth (по образцу desktop_access)

**Files:**
- Create: `core/auth/panel_access.py` (копия паттерна `desktop_access.py`: `create_panel_ticket`/`consume_panel_ticket`/`create_panel_session`/`load_panel_session`/`delete_panel_session`/`mark_panel_owner_checked`; cookie `__Secure-adpulse_panel_session`, Path=/`)
- Modify: `core/config.py` — `panel_access_ticket_ttl_seconds=300`, `panel_access_session_ttl_seconds=30*24*3600`, `panel_access_owner_recheck_seconds=300`
- Test: `tests/unit/test_panel_access_auth.py`

- [ ] Шаг 1: тест — `create_panel_session` кладёт в Redis, `load_panel_session` читает, `delete` удаляет; owner-recheck переставляет `owner_checked_at`. (Русский комментарий над тестом.)
- [ ] Шаг 2: реализация по образцу `desktop_access.py` (тот же Redis-паттерн, sliding TTL при чтении).
- [ ] Шаг 3: `pytest tests/unit/test_panel_access_auth.py -q` → PASS.
- [ ] Шаг 4: commit.

### Task P2: FastAPI-эндпоинты panel redeem/verify/logout

**Files:**
- Create: `apps/api/routers/panel_auth.py` — `GET /auth/panel/redeem` (consume ticket→set cookie→303 на `/`), `GET /auth/panel/verify` (forward_auth target: 200+ok или 303 на страницу входа), `POST /auth/panel/logout`
- Modify: `apps/api/main.py` — регистрация роутера
- Test: `tests/integration/test_api_panel_auth.py`

- [ ] Шаг 1: тест — redeem с валидным тикетом owner → 303 + Set-Cookie; verify с валидной cookie → 200; без cookie → 303; чужой/разжалованный → 303 + clear.
- [ ] Шаг 2: реализация (переиспользовать `find_recipient_by_telegram_user_id`, owner-check).
- [ ] Шаг 3: тесты PASS.
- [ ] Шаг 4: commit.

### Task P3: Telegram-кнопка выдачи тикета

**Files:**
- Modify: `core/telegram/handlers/` (новый хендлер `/panel` или кнопка в `/start`) — owner-only: создаёт panel-тикет, шлёт inline-ссылку `https://app.adpulse.su/auth/panel/redeem?ticket=…`
- Test: `tests/unit/test_panel_ticket_handler.py`

- [ ] Шаг 1: тест — owner получает ссылку с тикетом; не-owner получает отказ.
- [ ] Шаг 2: реализация (по образцу выдачи desktop-тикета).
- [ ] Шаг 3: тесты PASS.
- [ ] Шаг 4: commit.

### Task P4: Caddy — панель на forward_auth вместо basic_auth

**Files:**
- Modify: `deploy/caddy/app.adpulse.su.caddy` — новый сниппет `(telegram_panel_auth)` (forward_auth 127.0.0.1:8100 uri `/auth/panel/verify`, strip identity/WS-заголовков как в `desktop_session_auth`); заменить `import panel_auth` на `import telegram_panel_auth` в `handle /api/*`, `/ws/*`, catch-all `handle {}`; добавить `handle /auth/panel/*` (redeem log_skip, verify→404, logout); оставить `(panel_auth)` только на скрытом `/auth/recovery`
- Test: `tests/unit/test_desktop_caddy_policy.py` (+ проверка, что панель за forward_auth, basic_auth только на recovery)

- [ ] Шаг 1: тест-политика — `import telegram_panel_auth` на панели/api/ws; `basic_auth` только в recovery-блоке.
- [ ] Шаг 2: правка Caddy.
- [ ] Шаг 3: `caddy validate` в полном контексте на проде (loopback, `. /etc/fb-agent/caddy.env`) → Valid.
- [ ] Шаг 4: тесты PASS; commit.

> ⚠️ Деплой Фазы 0: сначала выкатить P1–P3 (backend+бот), убедиться, что тикет→cookie работает (loopback-проверка verify), и ТОЛЬКО потом P4 (переключение Caddy) — иначе панель окажется без рабочего входа. Break-glass `/auth/recovery` (BasicAuth) держать доступным всё время.

**Acceptance Фазы 0:** тап «Открыть панель» в Telegram → браузер открывает панель без пароля; повторные заходы 30 дней без промптов; разжалование owner рвёт сессию ≤ recheck; `/auth/recovery` под BasicAuth ещё работает.

**Effort Фазы 0:** ~0.5–1 день (80% инфраструктуры готово).

---

## ФАЗА 1 — Переезд на KasmVNC

### Task T0: Спайк-верификация KasmVNC (БЛОКИРУЮЩИЙ, до любого кода)

На отдельной VM/контейнере (НЕ прод). Зафиксировать ФАКТАМИ (не выдумывать):
- [ ] Фактический дисплей-бэкенд запиненного `lscr.io/linuxserver/webtop:ubuntu-xfce@sha256:f10654…` — Selkies (linuxserver ребейзнул всё на Selkies 17.06.2025) или нет; живы ли `SELKIES_*` env. **От этого зависит выбор пути** (см. ниже).
- [ ] Установка `.deb` kasmtech/KasmVNC поверх образа: дата последнего релиза (не заморожен ли), ставится ли на ubuntu-noble базы, версия+sha256 для пина.
- [ ] Дефолтный порт **и схема** встроенного веб/WS KasmVNC: по докам reverse-proxy он отдаёт **HTTPS self-signed** (`proxy_pass https://127.0.0.1:8444`). Подтвердить порт и решить: отключить SSL в `kasmvnc.yaml` (`network.ssl.require_ssl:false`, отдавать http на loopback — приемлемо, bind только 127.0.0.1) ИЛИ Caddy `reverse_proxy https://` + `tls_insecure_skip_verify`.
- [ ] Флаг `-disableBasicAuth` реально снимает basic-auth на этой версии: **smoke именно через iOS Safari сквозь Caddy** (не только curl), т.к. это единственный рычаг, снимающий врождённый блок Safari (Safari не шлёт basic-auth в WS). Заложить fallback: «Caddy сам инжектит Authorization после cookie-валидации» (паттерн Kasm Workspaces), если флаг ненадёжен.
- [ ] Разделить: **fingerprint-lock** = серверный `allow_resize:false` + `Xvnc -AcceptSetDesktopSize=0`; **fit-to-width** = клиентский **Local Scaling** (сделать дефолтом через URL-параметры/kasm-настройки клиента). Проверить, что клиент по умолчанию масштабирует, а не показывает столбом, и что клиентский override remote-resize запрещён (`allow_client_to_override_kasm_server_settings:false`).

**Выход T0:** таблица «версия/sha256/порт/схема/флаги подтверждены» + решение development-path (см. Open Questions). Без зелёного T0 код не пишем.

### Task T1: Ветка + оба compose параллельно (rollback-артефакт)
- [ ] `git fetch` + сверка origin/main; ветка `feat/desktop-kasmvnc`.
- [ ] Сохранить текущий `compose.yaml`→`compose.guacamole.yaml`; создать `compose.kasmvnc.yaml`. Оба образа пинованы и НЕ удаляются до приёмки.
- [ ] commit.

### Task T2: Dockerfile — KasmVNC .deb + s6 svc-vision-display
- [ ] Убрать `tigervnc-scraping-server`/`tigervnc-tools`. Добавить установку KasmVNC `.deb` (`KASMVNC_DEB_URL`/`_SHA256`, паттерн `VISION_DEB_*`).
- [ ] Новая s6-longrun `svc-vision-display`: `Xvnc :1` фикс 1366×768×24 DPI96, `-disableBasicAuth`, `-AcceptSetDesktopSize=0`, bind 127.0.0.1:<port> + xfce-сессия. Отключить встроенные display-службы базового образа (по факту T0). Удалить `svc-vision-vnc`, перецепить `svc-vision`/`svc-vision-window-fit` на `svc-vision-display`.
- [ ] Тест: контейнер стартует, Xvnc жив, xfce на :1, Vision-окно видно.
- [ ] commit.

### Task T3: kasmvnc.yaml (fingerprint-safe) + доставка в контейнер
- [ ] Создать `deploy/vision-webtop/kasmvnc.yaml`: `desktop.resolution {width:1366,height:768}`, `allow_resize:false`; `runtime_configuration.allow_client_to_override_kasm_server_settings:false`; `network` (ssl по решению T0); Local Scaling — клиентский дефолт.
- [ ] **Прошить доставку:** `install-vision-webtop.sh` копирует `kasmvnc.yaml` в `$TARGET_DIR` (как `vision-*-run`); `compose.kasmvnc.yaml` монтирует его в путь, который KasmVNC реально читает (подтвердить путь из T0). Без этого конфиг не применится.
- [ ] Тест: изнутри Vision console — `screen.width==1366 && screen.height==768`, `devicePixelRatio` стабилен при одновременном подключении телефона И десктопа.
- [ ] commit.

### Task T4: compose.kasmvnc.yaml — один сервис
- [ ] Оставить только `webtop`; publish `127.0.0.1:<port>:<port>`; убрать guacd/guacamole/guacamole-postgres/database-bootstrap + том; выпилить `SELKIES_*`/`DESKTOP_GUACAMOLE_*` env; healthcheck на curl встроенного KasmVNC-веба (+ pgrep Xvnc); сохранить `shm_size`, `/config` том, порты 3030/50051, network namespace для browser-agent.
- [ ] Тест: `docker compose config` валиден, стек поднимается, healthcheck зелёный.
- [ ] commit.

### Task T5: config.py + prepare_production_env — KasmVNC readiness
- [ ] Добавить `desktop_kasmvnc_internal_url`+`desktop_kasmvnc_port`; удалить `desktop_guacamole_*`/`guacd`/(опц.)`vnc_password`.
- [ ] `scripts/prepare_production_env.py` валидирует `DESKTOP_GUACAMOLE_POSTGRES_PASSWORD` на КАЖДОМ деплое — **сначала** экспортировать текущие GUACAMOLE/VNC-секреты в защищённое хранилище (для отката, держать ≥ неделю), затем убрать из валидатора. Обновить `tests/unit/test_prepare_production_env.py` (фикстура `DEFAULT_VALUES` + assert текста ошибки).
- [ ] Тест: settings грузятся без старых ключей; prepare-env тесты PASS.
- [ ] commit.

### Task T6: desktop_auth.py — HTTP-probe вместо guacd/JDBC
- [ ] Заменить `NetworkDesktopReadinessProbe` на HTTP-probe (`GET internal_url`→200). Удалить `_guacamole_ready`/`_jdbc_ready`/`_guacd_vnc_ready`/`_encode`/`_decode_guacamole_instruction`. `desktop_readyz`→`{kasmvnc: bool}`. **Убрать `Remote-User` из ответа `verify_desktop_session`** (header-trust больше не нужен). Сохранить `DesktopReadyzCache`, redeem/verify/logout, owner-recheck.
- [ ] Тест: readyz 200 при живом KasmVNC, 503 при мёртвом; verify выдаёт cookie как раньше, без Remote-User.
- [ ] commit.

### Task T7: Caddy /desktop/* → KasmVNC
- [ ] `handle /desktop/*` → `reverse_proxy 127.0.0.1:<kasmvnc_port>` (схема по T0); убрать `header_up Remote-User`; сохранить `stream_timeout 30m`/`stream_close_delay`/strip identity+Authorization; обновить комментарии. Держать `app.adpulse.su.caddy` (KasmVNC) и `app.adpulse.su.guacamole.caddy` в git до приёмки для быстрого отката.
- [ ] Тест: `caddy validate` ок; `test_desktop_caddy_policy.py` обновлён (нет Remote-User set, upstream KasmVNC).
- [ ] commit.

### Task T8: install-vision-webtop.sh под KasmVNC
- [ ] `desktop_is_ready()` → webtop healthy + curl 127.0.0.1:<port>; убрать guac/JDBC/5900/4822/схему/JAR/bootstrap. **Исправить cutover-баг:** строка `install … compose.yaml` безусловно перезаписывает целевой compose ДО `compose rm`, поэтому `compose rm -sf guacamole guacd database-bootstrap` не найдёт старые сервисы → заменить на `compose rm -sf webtop` (форс пересоздания) либо убрать (up -d пересоздаст по изменённому compose). Пересчитать `manifest_hash` по новому набору (kasmvnc.yaml, vision-display-run; без bootstrap/extension). Сохранить rollback-контур, browser-agent restart, ensure_cdp, пин @sha256.
- [ ] Тест: idempotent повторный прогон без mutate; подмена на битый образ → rollback.
- [ ] commit.

### Task T9: Тесты — заменить guacamole-контрактные
- [ ] Удалить `test_vision_webtop_guacamole.py`, `test_desktop_readiness_probe.py`. Создать `test_vision_webtop_kasmvnc.py` (структура compose/Dockerfile/s6: один сервис, порт, `-disableBasicAuth` присутствует, `allow_resize:false`). Обновить `test_desktop_caddy_policy.py`, `test_api_desktop_auth.py`, `test_prepare_production_env.py`. Русские комментарии над тестами.
- [ ] `pytest tests/unit/test_desktop_* tests/integration/test_api_desktop_auth.py -q` + `ruff` → зелёные.
- [ ] commit.

### Task T10: FINGERPRINT SMOKE-TEST (money-gate, БЛОКИРУЮЩИЙ)
- [ ] На прод-подобном стенде с боевым Vision-профилем: FB Ads Manager, сравнить ДО (Xorg) vs ПОСЛЕ (Xvnc): `screen.*`, `devicePixelRatio`, **WebGL UNMASKED_VENDOR/RENDERER, список GL-расширений, canvas- и audio-хэши**. Убедиться: нет чекпоинта/2FA-триггера, кабинет открывается, `RunScanCycle` отдаёт строки.
- [ ] **Без зелёного — боевой cutover НЕ делаем.**

### Task T11: iPhone Safari приёмка на стенде
- [ ] Реальный iPhone Safari (без VPN) через Caddy-тикет: fit-to-width (НЕ столбом), **реальный** пинч-зум/панорама (а не только «кнопка клавиатуры видна»), **фактический ввод** — логин/пароль со спецсимволами + 2FA-цифры, кириллица; поворот portrait↔landscape; **блокировка экрана → возврат: авто-reconnect и повторный WS-upgrade проходит forward_auth в пределах cookie** (не пустой canvas/форма панели).
- [ ] Зафиксировать в runbook ЧЕСТНО: clipboard (ручное поле), fullscreen (только PWA).

### Task T12: Боевой cutover + мониторинг
- [ ] **Новая процедура экстренного обрыва** (взамен `docker restart vision-webtop-guacamole-1`): revoke сессии в Redis (`delete_panel_session`/`delete_desktop_session`) + естественное истечение `stream_timeout 30m`, либо `docker restart vision-webtop` (рвёт и money-канал — только крайний случай). Задокументировать в DEPLOYMENT.md ДО cutover.
- [ ] В окно низкой активности (или с паузой CI-деплоя): `install-vision-webtop.sh` с KasmVNC-манифестом (пересоздаёт vision-webtop, browser-agent рестарт, ensure-cdp). Проверить desktop-readyz, health_watchdog, возобновление скана (<2 мин downtime). Наблюдать сутки.
- [ ] Обновить `DEPLOYMENT.md` (архитектурная таблица, установка, экстренный обрыв) и `docs/superpowers/plans/2026-07-17-remote-desktop-single-path.md` (пометить как заменённый этим планом).

## Rollback (Фаза 1)
- Оба compose (`compose.guacamole.yaml`) + оба образа + оба Caddy-файла держим в git/реестре ≥ неделю после приёмки. Том `guacamole-postgres` не удаляем.
- Откат ~2–3 мин: вернуть `DESKTOP_WEBTOP_IMAGE`+`compose.guacamole.yaml`; Caddy `/desktop/*`→8090 (+`header_up Remote-User`) через `install-server-units.sh` (reversible Caddy-транзакция); `install-vision-webtop.sh` пересоздаёт стек. GUACAMOLE/VNC-секреты восстановить из защищённого хранилища (см. T5).

## Effort
- Фаза 0 (auth): ~0.5–1 день.
- Фаза 1 (KasmVNC): ~5–8 рабочих дней (T0 спайк 1д; Dockerfile/compose/kasmvnc.yaml 1.5–2д; backend/Caddy 1д; тесты 0.5–1д; install 0.5–1д; fingerprint+iPhone+буфер 1–1.5д; cutover 0.5д). Календарно ~1.5–2 недели с окном для money-cutover.

## Open Questions (решить до/в T0)
1. Development-path после факта о базовом образе: (а) KasmVNC .deb поверх (план); (б) если база уже Selkies и Selkies-клиент устраивает мобильно — пересмотреть, не проще ли остаться на Selkies базы + наш forward_auth; (в) откат базы на прежний non-Selkies тег. **Сравнить в T0 до кода.**
2. Порт **и схема** (http/https self-signed) встроенного KasmVNC-веба — из T0.
3. Надёжность `-disableBasicAuth` между версиями (CLI-only) — пин по sha256 + fallback «Caddy инжектит Authorization».
4. Судьба `DESKTOP_VNC_PASSWORD` при `-disableBasicAuth` — рекомендация: дропнуть (bind loopback, RFB legacy off), но сначала сохранить для отката.
5. Ожидания владельца по iPhone: если в «плохо» входят clipboard/fullscreen/обрывы при блокировке — переезд их НЕ лечит; решить, нужна ли PWA-обёртка отдельной задачей.
6. `SELKIES_*` в текущем compose — были ли заделом на осознанный Selkies (Codex-PR)? Если да — пересмотреть выбор бэкенда до старта.
