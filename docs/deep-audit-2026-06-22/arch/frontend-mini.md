# Архитектура: Frontend Mini + Shared Package

Дата аудита: 2026-06-22  
Подсистема: `frontend-mini/` + `packages/shared/`

---

## Обзор

Telegram Mini App (TMA) — мобильный клиент FB Stop Bot, встроенный во Telegram WebApp. Работает по адресу `/tma/` (basepath роутера). Технологический стек: React 19 + StrictMode, TanStack Router (file-based), TanStack Query v5, Tailwind 4, TypeScript strict.

Shared-пакет (`@fb/shared`) — монорепо-пакет, который объединяет типы API, FSM-константы, форматтеры и CSS-токены для обоих фронтендов (`frontend/` и `frontend-mini/`).

---

## Ключевые компоненты

### `frontend-mini/src/`

| Файл / директория | Роль |
|---|---|
| `main.tsx` | Точка входа. StrictMode + QueryClient (staleTime 10s) + router с basepath `/tma` |
| `lib/auth.ts` | TMA-аутентификация: `loginToBackend()` → `POST /api/tma/auth` → Bearer JWT в localStorage |
| `lib/api.ts` (567 строк) | Все API-хуки через `fetchJson<T>` + 401-retry. Хуки: `useDashboardBatch`, `useDashboardAds`, `useTmaAd`, `useTmaDisable`, `useTmaClaim`, `useOffers`, `useHealthDetails` и другие |
| `lib/tg.ts` | Обёртка Telegram WebApp API: `getInitData`, `initTheme`, `haptic.*`, `tgConfirm`, `tgAlert`, `openLink`, `registerBackButton` |
| `routes/__root.tsx` | Root layout: `AuthGuard → TelegramBackButton → <main><Outlet/></main> → TabBar` |
| `routes/index.tsx` (387 строк) | Dashboard: `useDashboardBatch` + `useObserverSettings` + `useSpendSeries`. Хук `useScanCountdown`, кнопка «Сканировать сейчас» |
| `routes/ads/index.tsx` | AdsPage: клиентская фильтрация 300 объявлений, multi-select state filter, сортировка по spend, показ 120 |
| `routes/ads/$fbAdId.tsx` | AdDetail: disable + claim + Ads Manager link |
| `routes/offers/index.tsx` (644 строк) | God-компонент: CRUD офферов + `ThresholdsForm` + `parseAccountIds` |
| `routes/settings/index.tsx` (520 строк) | God-компонент: Observer / Telegram / Vision / CabinetAutostart |
| `components/layout/AuthGuard.tsx` | Монтаж: `initTheme()` → `ensureAuthenticated()` → spinner / error / children |

### `packages/shared/src/`

| Файл | Роль |
|---|---|
| `api/generated.ts` (6563 строк) | OpenAPI-сгенерированные типы (`pnpm gen:api` из `frontend/openapi.json`) |
| `api/types.ts` | Эргономичные алиасы над generated: `Offer`, `OfferRule`, `DashboardBatchOut` и другие |
| `constants/states.ts` | `ALERT_STATES`, `normalizeAlertState`, **`alertStateCssVar`** (правильный маппинг `warning_sent → --fsm-warning`), `TASK_STATUSES`, `normalizeTaskStatus` |
| `domain/geo.ts` | `deriveGeoFromNames()` — единый экстрактор гео из названий кампании/адсета |
| `formatters/` | `formatSpend`, `formatPercent`, `formatInt` (Intl/UTC) |
| `tokens.ts` + `tokens.css` | Дизайн-токены — единый источник для обоих фронтендов |

---

## Поток аутентификации

```
Telegram WebApp
    ↓  window.Telegram.WebApp.initData (подписанная строка)
AuthGuard.onMount
    → initTheme() (tg.ready + tg.expand)
    → ensureAuthenticated()
        ├─ токен уже есть в localStorage → "idle" (done)
        └─ нет токена → loginToBackend()
            → POST /api/tma/auth { init_data }
            → backend: HMAC-валидация initData (secret = HMAC-SHA256("WebAppData", BOT_TOKEN))
            → backend: get_tma_principal — проверяет recipient в БД на каждом запросе (немедленный отзыв)
            → ответ: { token, role }
            → localStorage: token + role
            ↓ AbortSignal.timeout(15_000) — защита от вечного splash
spinner → children (app)
```

Каждый запрос `fetchJson`: `Authorization: Bearer <token>`. При 401 — logout + повторный `loginToBackend()` + retry один раз.

---

## Главные цепочки вызовов

### Disable объявления (money-critical)

```
AdDetail.handleDisable
    → tgConfirm("Отключить объявление через API?")
    → useTmaDisable.mutateAsync({ fbAdId })
        → POST /api/tma/ads/{fbAdId}/disable
            → get_tma_principal (Bearer verify + DB re-check)
            → create task_queue:
                task_type = "meta_api_mutation"
                payload   = { mutation: "pause_ad", ad_id: fbAdId }
                idempotency_key = "tma:pause_ad:{fbAdId}:{open_state_token|uuid4}"
            → return { task_id }
        → meta_api_worker polls task_queue
        → ExecuteGraphCall → Meta Marketing API pause_ad
        → FSM sync: ad_alert_state → "disabled"
    → tgAlert("Задача отключения поставлена")
```

### Dashboard batch polling

```
routes/index.tsx (mount)
    → useDashboardBatch (refetchInterval: 20s)
        → GET /api/dashboard/batch
            → _safe_call секции: stats / incidents / alerts / disable / enable_recommendations
            → partial-failure: упавшая секция → empty default, остальные OK
    → useObserverSettings (stale 30s)
        → GET /api/settings/observer
    → useSpendSeries
        → GET /api/dashboard/spend-history
    → useScanCountdown (derived: observer.next_scan_at - now())
```

### Scan-now (Dashboard)

```
routes/index.tsx handleScanNow
    → useTriggerScan.mutateAsync()
        → POST /api/observer/scan-now          ← НЕВЕРНЫЙ путь (BUG)
        (правильный: /api/settings/observer/scan-now)
```

### Offer CRUD

```
routes/offers/index.tsx
    → useOffers (GET /api/offers)
    → handleSave → PUT /api/offers/{id}
    → handleSaveRules → PUT /api/offers/{id}/rules
    → ThresholdsForm: setValues(init) + setInitialized(true) в теле рендера  ← BUG
```

---

## Переиспользование `@fb/shared` vs копипаста

### Переиспользуется корректно

- `normalizeAlertState`, `alertStateCssVar` — экспортируются из shared, импортируются в mini
- `formatSpend`, `formatPercent`, `formatInt` — единая точка
- `deriveGeoFromNames` — заменила дублированный мини-специфичный `extractGeo`
- `ALERT_STATE_LABELS`, `TASK_STATUS_LABELS` — из shared
- CSS-токены `tokens.css` — подключаются оба фронтенда

### Остаточная копипаста / дрейф

- `API_BASE` объявлен и в `auth.ts:13`, и в `api.ts:21` — разные константы, синхронизируются вручную
- В `ads/index.tsx:349` используется `var(--fsm-${f.id})` вместо `alertStateCssVar(f.id)` из shared — задокументированный баг (комментарий в states.ts), но не исправлен

---

## Особенности TMA-модели

- **Роли**: `owner` / `recipient`. `is_owner` вычисляется из JWT. Cabinet autostart — только owner на бэкенде.
- **Disable/claim/snooze**: роль не проверяется на фронте — любой аутентифицированный recipient может отключить объявление (видимо, намеренно).
- **Partition pruning**: `_load_ad_extras` в `tma.py` запрашивает `alert_events` с `created_at >= NOW() - INTERVAL '30 days'` — правильно.
- **React StrictMode**: double-render — усиливает баг с `setState` в теле рендера `ThresholdsForm`.

---

## Инварианты

1. `generated.ts` — единственный источник типов API. Перегенерируется `pnpm gen:api` из `frontend/openapi.json`.
2. `alertStateCssVar()` — единственный правильный способ получить CSS-переменную для FSM-цвета. Прямая интерполяция `--fsm-${state}` некорректна.
3. Disable всегда создаёт `task_queue` запись (outbox), никогда не действует напрямую через API.
4. Bearer-токен хранится в localStorage (fallback sessionStorage), проверяется на бэкенде при каждом запросе через `get_tma_principal`.
5. `pnpm` — единственный пакетный менеджер (не npm); `packages/shared` — workspace-алиас `@fb/shared`.
