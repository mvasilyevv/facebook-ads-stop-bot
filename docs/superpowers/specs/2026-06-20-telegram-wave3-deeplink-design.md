# Дизайн: Telegram — Волна 3 (web_app deep-link кнопки под алертами + live-аудит туннеля)

Дата: 2026-06-20. Статус: апрувнут пользователем. Завершает пересмотр формата TG
(волны 1-2-4 в проде). Часть пересмотра формата TG.

## Контекст

Исходно Волна 3 оценивалась как крупная инфра-фича (туннель, хостинг Mini App,
MenuButton, web_app-кнопки). Ревизия кодовой базы показала, что **инфра уже
построена и закоммичена**:

- Туннель: `run.sh` поднимает cloudflared quick-tunnel и детектит **Tailscale
  Funnel** (стабильный публичный HTTPS-URL) — `detect_tailscale_funnel_url` +
  `scripts/setup_tailscale_funnel.sh` + `docs/mini_app_tunnel.md`.
- `web_app_url` в `system_config` (`core/telegram/web_app_url.py`), меняется без
  рестарта; `PUT /settings/telegram/web-app-url` (`apps/api/routers/v1/settings_telegram.py`)
  + авто `setChatMenuButton` через `_sync_bot_menu_button`.
- MenuButtonWebApp → дашборд Mini App: `core/telegram/client.py::set_chat_menu_button`.
- TMA-auth (`core/auth/tma.py`) + богатый `/tma/*` API (`apps/api/routers/v1/tma.py`):
  disable/snooze/claim/autostart.
- Mini App (`frontend-mini`): 8 экранов, base `/tma/`, роут `ads/$fbAdId` (детали
  объявления) уже существует.

**Единственная реальная дыра:** под алертами в личке сейчас только callback-кнопка
`🛑 Отключить` (`core/telegram/renderer.py::render_inline_keyboard`) и `▶️ Включить`
в enable_reco (`core/enable_reco/alert.py`). **Нет web_app-кнопки**, открывающей
Mini App прямо на объявлении алерта (`{base}/ads/{fb_ad_id}`).

**Охват (решение владельца): «Дыра + live-аудит туннеля E2E».**

## Решение

### A. web_app deep-link кнопки под алертами (код)

#### A1. `core/telegram/renderer.py`
- `AlertRenderInput`: добавить поле `web_app_base: str | None = None`. Это полная
  база Mini App, включающая префикс `/tma`, без хвостового слэша
  (напр. `https://host.ts.net/tma`).
- `render_inline_keyboard`: если `web_app_base` задан **и** начинается с `https://` —
  добавить **первой строкой** клавиатуры:
  ```python
  [{"text": "🔎 Открыть в Mini App",
    "web_app": {"url": f"{web_app_base}/ads/{fb_ad_id}"}}]
  ```
  **над** строкой `🛑 Отключить`. Две отдельные строки: безопасная навигация
  сверху, деструктивное «Отключить» — отдельной строкой (анти-fat-finger).
- Если `web_app_base` None/пусто/не-`https://` → web_app-кнопки нет (текущее
  поведение сохраняется, graceful — туннель может быть не поднят).
- web_app-кнопка добавляется при `stage in ('warning', 'stop')` (там же, где
  сейчас «Отключить»).

#### A2. `core/telegram/alert_dispatcher.py`
- В `dispatch_pending_alerts` и `sweep_orphan_alerts`: загрузить `web_app_url`
  **один раз на батч** через `load_web_app_url(engine)` (не per-alert), нормализовать
  в base: `strip()`, убрать хвостовой `/`, вернуть только если `startswith("https://")`,
  иначе `None`. Вынести нормализацию в маленький helper `_resolve_web_app_base(engine)`.
- Прокинуть результат параметром `web_app_base: str | None` через `_deliver_one_alert`
  в `AlertRenderInput(web_app_base=...)`.
- `None` (туннеля нет) → кнопка опущена, доставка алерта не ломается.

#### A3. `core/enable_reco/alert.py` + `apps/enable_recommendation_worker/main.py`
- `EnableRecoRenderInput`: добавить `web_app_base: str | None = None`.
- `render_enable_reco_alert`: добавить web_app-строку
  `[{"text": "🔎 Открыть в Mini App", "web_app": {"url": f"{base}/ads/{fb_ad_id}"}}]`
  **над** строкой `▶️ Включить` (тот же https-guard).
- `apps/enable_recommendation_worker/main.py`: воркер уже имеет `engine` — загрузить
  `web_app_url` (нормализация тем же helper'ом — переиспользовать из renderer/общего
  места, **не дублировать** логику нормализации) и передать base в `EnableRecoRenderInput`.

#### A4. Нормализация base — единый источник
Чтобы не дублировать https-guard и strip-trailing-slash в трёх местах
(dispatcher, enable_reco worker), вынести в одну pure-функцию
`normalize_web_app_base(raw: str | None) -> str | None` в `core/telegram/web_app_url.py`
(рядом с `load_web_app_url`). Возвращает нормализованную base или `None`. Обе
точки чтения (`_resolve_web_app_base`, enable_reco worker) зовут её.

### B. Live-аудит E2E + runbook (раздел труда)

**Автоматизируется (выполняется в рамках задач):**
- Поднять **cloudflared quick-tunnel** против работающего mini-сервера
  (`cloudflared tunnel --url http://localhost:<MINI_PORT>`), получить URL.
- `curl -sS {tunnel}/tma/ads/<sample_fb_ad_id>` → ожидать 200 + SPA index.html
  (vite отдаёт index для произвольного пути — проверка, что deep-link доедет до
  роутера Mini App).
- Тестами зафиксировать JSON-формы: `setChatMenuButton` payload (уже есть
  `test_tma_menu_button.py`) и web_app-кнопки под алертами (новые тесты A4).
- `_sync_bot_menu_button` против живого бота — best-effort, лог результата.

**Разовый ops пользователя (точный runbook в выходе):**
1. `tailscale up` (browser-login в свой аккаунт).
2. `./scripts/setup_tailscale_funnel.sh` (стабильный URL + авто `web_app_url`
   в БД + авто MenuButton).
3. BotFather → Menu Button → вставить `https://<host>.<tailnet>.ts.net/tma/` (разово).
4. Тапнуть «🔎 Открыть в Mini App» под реальным алертом на телефоне → убедиться,
   что Mini App открывается на нужном объявлении.

**Выход аудита:** дополнить `docs/mini_app_tunnel.md` (или `docs/playbooks/RUNBOOKS.md`)
секцией «Волна 3 — чеклист web_app-кнопки под алертами» с шагами проверки.

## Границы (НЕ в Волне 3)

- Named Mini App / `startapp`-параметр (`t.me/bot/app?startapp=`) — НЕ используем.
  Прямой web_app-URL `{base}/ads/{id}` достаточен и не требует доп. конфига BotFather
  кроме уже существующего MenuButton.
- Новые экраны / переработка UX Mini App — отдельный трек.
- Переархитектура туннеля — инфра готова, не трогаем.
- UI scan-controls (прервать идущий скан) — отдельный roadmap-трек
  (`docs/roadmap/ui-scan-controls.md`).

## Telegram-ограничения (учтены)

- Inline-кнопки типа `web_app` работают **только в приватных чатах** между
  пользователем и ботом. После Волны 2 канал DM-only → ограничение выполнено
  автоматически.
- URL в `web_app.url` обязан быть `https://` — guard в renderer/enable_reco.
- `callback_data` лимит 64 байта — web_app-кнопка его не использует (URL в `web_app`),
  существующий `dis:` не трогаем.

## Тестирование

- Unit renderer: web_app-кнопка присутствует и URL = `{base}/ads/{id}` при заданном
  https-base; отсутствует при `None`; отсутствует при не-https base; «Отключить»
  остаётся отдельной строкой; порядок строк (web_app сверху).
- Unit `normalize_web_app_base`: https-passthrough со strip trailing slash; http → None;
  None/пусто → None.
- Unit enable_reco: web_app-строка над «Включить» при base; отсутствует при None.
- Integration dispatcher: `system_config.web_app_url` задан → клавиатура алерта
  содержит web_app-кнопку с верным URL; `null` → кнопка опущена, алерт доставлен.
- Integration enable_reco worker: тот же контракт.
- Регресс: существующие `test_tma_menu_button.py` и dispatcher-тесты остаются зелёными.
- Live-аудит (часть B): cloudflared quick-tunnel curl-проверка deep-link 200.

## Метрика готовности

Под алертами warning/stop и enable_reco в личке появляется «🔎 Открыть в Mini App»,
тап открывает Mini App на `{base}/ads/{fb_ad_id}`; при отсутствии туннеля кнопка
graceful-опущена и доставка не ломается; cloudflared curl-проверка deep-link = 200;
runbook стабильного Tailscale Funnel + BotFather зафиксирован; unit+integration
зелёные; ruff чисто; opus-review «Ready to merge».
