# Стабильный туннель для Telegram Mini App (Tailscale Funnel)

## Зачем

Раньше Mini App публиковался через cloudflared **quick-tunnel** — его URL
(`*.trycloudflare.com`) **менялся при каждом запуске** `run.sh`. Telegram Menu
Button в BotFather настраивается на фиксированный URL → после перезапуска кнопка
вела на мёртвый туннель, и Mini App «не работал».

**Tailscale Funnel** даёт **постоянный** публичный HTTPS-адрес
`https://<host>.<tailnet>.ts.net` — без своего домена. BotFather Menu Button
настраивается **один раз**, дальше URL не меняется.

## Разовая настройка

### 1. Логин в Tailscale (твой аккаунт, браузер)

```bash
tailscale up
```

Откроется браузер — авторизуйся. Проверь: `tailscale status` должен показать
машину как `online`.

### 2. Включить MagicDNS + HTTPS-сертификаты

Admin console → **DNS**: включи **MagicDNS** и **HTTPS Certificates**.
(Funnel работает только с валидными HTTPS-сертификатами tailnet.)

### 3. Разрешить Funnel для этой машины (ACL)

Admin console → **Access Controls**: добавь node-атрибут `funnel` для машины.
Минимальный пример в ACL:

```jsonc
"nodeAttrs": [
  { "target": ["autogroup:member"], "attr": ["funnel"] }
]
```

(Подробнее: https://tailscale.com/kb/1223/funnel)

### 4. Запустить скрипт настройки

```bash
./scripts/setup_tailscale_funnel.sh
```

Скрипт: проверит логин и MagicDNS, включит Funnel на порт mini-app (`5174`),
получит стабильный URL, зарегистрирует `web_app_url` в боте (это автоматически
обновит Menu Button — см. `PUT /settings/telegram/web-app-url`), и напечатает
итоговый URL.

### 5. BotFather — один раз

BotFather → твой бот → **Bot Settings → Menu Button** → вставь URL вида:

```
https://<host>.<tailnet>.ts.net/tma/
```

Готово. Этот URL постоянный.

## Как это работает дальше

`run.sh` при каждом запуске вызывает `detect_tailscale_funnel_url`:
- если Funnel активен на `5174` → берёт стабильный URL, регистрирует его
  (`web_app_url` + авто Menu Button) и **не поднимает** cloudflared quick-tunnel
  для mini;
- если Funnel не настроен → фолбэк на прежний quick-tunnel (эфемерный URL +
  авто-обновление кнопки).

Funnel живёт в демоне `tailscaled` и **переживает перезапуски** `run.sh` —
повторно настраивать не нужно.

## Проверка и управление

```bash
tailscale funnel status     # текущая конфигурация Funnel
tailscale funnel reset      # выключить Funnel (вернётся quick-tunnel)
tailscale status            # online/offline машины
```

## Важно

- Funnel публикует сервис **в публичный интернет** — поэтому доступ к действиям
  Mini App защищён TMA-авторизацией (initData → recipient, см. `core/auth/tma.py`
  и `apps/api/routers/v1/tma.py`). Без валидного recipient'а действия недоступны.
- Mini App слушает `5174`; запросы `/api/*` проксируются vite-сервером на API
  (`:8100`) — отдельный туннель для API не нужен, Funnel на `5174` покрывает всё.
