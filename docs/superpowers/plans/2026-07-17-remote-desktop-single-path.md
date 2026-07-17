# Удалённый рабочий стол: единая прод-реализация (итог 17.07.2026)

**Цель:** один продовый путь доступа к Vision-десктопу из браузера и с мобильного,
без канареек, дуал-пасов, обратной совместимости и костылей.

**Статус: реализация уже в проде** (origin/main `fc87138c`, PR #52–#61 + фиксы).
Этот документ — фиксация архитектуры, проведённой чистки и чек-листа приёмки.

## Архитектура (единственный путь)

```
Браузер / Mini App
   └─ https://app.adpulse.su/desktop/            (same-origin, отдельного домена НЕТ)
        └─ Caddy: handle /desktop/* → import desktop_session_auth → 127.0.0.1:8090
             └─ Guacamole 1.6 (header-trust, Remote-User=adpulse-desktop, canvas+WebSocket)
                  └─ guacd → TigerVNC X0tigervnc :5900 (loopback внутри webtop)
                       └─ webtop-контейнер: X-сервер 1366×768 + Vision (money-канал скана)
```

- **Вход веб:** панель (BasicAuth) → POST `/api/desktop/launch` → одноразовый тикет →
  `/desktop-auth/redeem` → cookie `__Secure-adpulse_desktop_session_v2`.
- **Вход Mini App:** экран «Рабочий стол» → POST `/api/desktop/launch` → `openLink(url)`
  (внешний браузер, не WebView-iframe). Во фронтенд-коде вызовы выглядят как
  относительный `/desktop/launch` — базу `/api` подставляет api-клиент.
- **Здоровье:** `/desktop-readyz` (guacamole HTTP + JDBC + полный guacd→VNC handshake).
- **Recovery:** только SSH. Break-glass basic-auth маршрутов нет и не добавлять.

## Что выпилено (17.07)

| Что | Где было | Судьба |
|---|---|---|
| Selkies как канал доставки + canary-роут `/guacamole/*` | локальное дерево (отвергнутый дизайн) | сброшено на origin/main |
| Отдельный домен `desktop.adpulse.su` + свой caddy-сайт | локальное дерево | удалён; осиротевший ACME-серт на хосте → `/root/cleanup-backup-20260717` |
| Telegram-OIDC панель (`panel_auth.py`, `panel_telegram.py`, `configure-panel-oidc.py`) | локальное дерево | сброшено; прод — BasicAuth-панель |
| Break-glass `/auth/recovery`, `/desktop-auth/recovery` | локальное дерево | не воскрешён (origin/main их не маршрутизирует) |
| `selkies-clipboard-bridge.js` (sed-инжект в вендорный HTML) | локальное дерево | удалён — у Guacamole нативный clipboard-канал |
| `remove-caddy-site-block.py` (одноразовый regex-обход) | локальное дерево | удалён |
| `deploy/panel-auth/` (мёртвый прототип, конфликт порта 8090) | origin/main | `git rm` — коммит `33020d44` |

Локальное дерево на момент чистки отставало от origin/main на 20 коммитов и целиком
содержало отвергнутый дизайн. Полный снимок WIP (145 файлов, включая
не-десктопные потоки meta_diagnostics/adset_duplicates/enable_reco/analytics)
сохранён в ветке **`backup/pre-desktop-cleanup-20260717`** (локальная, не пушить).

## Деплой / откат

- Автодеплой: push в main → CI (`CI_DEPLOY_ENABLED=true`) → `deploy-server.sh` →
  `install-server-units.sh` (caddy) — **не трогает** webtop/guacd/guacamole → даунтайм Vision = 0.
- Стек webtop ставится только вручную: `sudo ./scripts/install-vision-webtop.sh`
  (идемпотентен по manifest-hash, без нужды контейнеры не пересоздаёт).
- Откат: `git revert <merge-commit>` + push, либо симлинк `/opt/fb-agent/current`
  на предыдущий `releases/<ts>` + `install-server-units.sh` из него.
- Экстренный обрыв активной desktop-сессии: по SSH `docker restart vision-webtop-guacamole-1`
  (logout/revoke НЕ рвут уже открытый WS-туннель — см. фоллоу-апы).

## Чек-лист физической приёмки (владелец, руками)

Десктоп-браузер (уже проверено техникой: readyz ok, 303-гейт, header-auth в логах):
- [ ] Панель → «Открыть рабочий стол» → canvas рисует Vision; клавиатура/мышь/клипборд в обе стороны.

iPhone Safari — прямой заход `app.adpulse.su/desktop/`:
- [ ] Landscape, свайп от левого края → меню; режимы «Сенсорный экран» и «Тачпад».
- [ ] Right-click: long-press (сенсорный) и two-finger tap (тачпад); скролл; pinch-zoom.
- [ ] Кириллица с софт-клавиатуры в поле Ads Manager — посимвольная сверка.
- [ ] Клипборд: iOS = ручной textarea в свайп-меню (автосинк не обещан), оба направления.
- [ ] Свернуть Safari / заблокировать экран на 2–5 мин → вернуться: авто-реконнект или
      диалог Reconnect (замёрзший canvas без реакции = баг, фиксировать).

Telegram Mini App (отдельный обязательный сценарий, НЕ прямой URL):
- [ ] iOS Telegram: «Подключиться» → uходит в системный Safari, сессия живёт.
- [ ] Android Telegram: проверить, куда реально уводит `openLink` (in-app browser vs Chrome),
      переживает ли cookie сворачивание Telegram; тикет одноразовый — при затыке
      возвращаться в Mini App за новым.

Android Chrome — прямой заход:
- [ ] Тач-ввод, клавиатура, клипборд (ожидается автосинк).

Ожидаемое ограничение (не баг): 1366×768 рендерится в масштабе на телефоне,
плотный UI Ads Manager читается через pinch-zoom.

## Фоллоу-апы (вне этой чистки)

1. **MID** `/desktop-readyz` публичен и гоняет полный VNC-handshake на каждый хит —
   закрыть авторизацией/лупбеком + кэш результата.
2. **MID** Отзыв owner/logout не рвёт активный WS-туннель — таймер max-длительности
   сессии или периодический re-auth; в runbook: экстренный обрыв = SSH.
3. **LOW** Текст в Mini App «откроется во внешнем браузере» не гарантирован на Android —
   уточнить копирайт или передавать явную опцию openLink.
4. DNS: A-запись `desktop.adpulse.su` ещё существует — удалить у регистратора
   (или осознанно оставить 308-редирект; отдельный домен не воскрешать).
