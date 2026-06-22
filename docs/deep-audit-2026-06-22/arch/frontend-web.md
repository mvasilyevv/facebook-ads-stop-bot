# Архитектура: Frontend Web (`frontend/src/`)

## Стек

| Слой | Технология |
|------|-----------|
| Фреймворк | React 19 + Vite 6 |
| Типизация | TypeScript strict |
| CSS | Tailwind 4 (`@theme`) |
| Роутинг | TanStack Router v1 (file-based, `createFileRoute`) |
| Сервер-стейт | TanStack Query v5 |
| Клиент-стейт | Zustand v5 (`persist`) |
| Виртуализация | `@tanstack/react-virtual` (overscan=8) |
| WebSocket | ручная реализация + polling fallback |
| Иконки | Lucide |
| UI-примитивы | Radix Primitives |
| Тесты | vitest (~331) |

---

## Структура директорий

```
frontend/src/
├── main.tsx              — QueryClient (MutationCache), AuthStore bootstrap
├── routes/
│   ├── __root.tsx        — Root layout (Shell: Sidebar + TopBar + Outlet)
│   ├── index.tsx         — Dashboard (531L → см. ниже)
│   ├── ads/
│   │   ├── index.tsx     — AdsPage (531L, god-component)
│   │   └── $fbAdId.tsx   — Ad deep-link (drawer route)
│   ├── campaigns/
│   │   └── index.tsx     — CampaignsPage
│   ├── offers/
│   │   └── index.tsx     — OffersPage (содержит баг удаления)
│   ├── history/
│   │   └── index.tsx     — HistoryPage
│   └── settings/
│       └── index.tsx     — SettingsPage (табы Observer/Telegram/Vision/Health)
├── components/
│   ├── domain/
│   │   └── ads/
│   │       ├── AdDrawer.tsx    (638L — god-component)
│   │       ├── AdsTable.tsx    (414L — god-component)
│   │       ├── FilterBar.tsx   (410L — god-component)
│   │       └── adHelpers.ts    — утилиты (isCplBad, isFreqBad, adAccountId)
│   ├── data/
│   │   ├── SpendChart.tsx
│   │   └── SparklineKpiRow.tsx
│   └── ui/
│       └── ConfirmDialog.tsx   — требует ввода confirmWord (напр. ad code)
├── lib/
│   ├── api/
│   │   ├── client.ts          — apiGet/apiSend/apiGetWithCount (X-API-Key)
│   │   └── hooks/             — useAds, useDashboardStats, useBulkDisable, …
│   ├── websocket/
│   │   ├── useDashboardSocket.ts   — WS + exponential backoff + polling fallback
│   │   └── useRealtimeInvalidation.ts — event→invalidateQueries маппинг
│   └── utils/
│       └── spendTotal.ts           — cumulativeSpendTotal (правильная логика)
└── stores/
    ├── auth.ts    — Zustand persist, apiKey → localStorage
    └── ui.ts      — density (comfortable/compact/dense) → data-density на <html>
```

---

## Ключевые цепочки вызовов

### Авто-стоп / bulk disable
```
AdsPage (useAds → GET /api/v1/dashboard/ads?limit=1000)
  → пользователь выбирает X строк (keyboard: X)
  → BulkActionBar → кнопка "Disable"
  → ConfirmDialog (confirmWord="DISABLE")
  → useBulkDisable.mutateAsync({ fb_ad_ids, idempotency_token: crypto.randomUUID() })
  → POST /api/v1/dashboard/ads/bulk-disable
  → task_queue[meta_api_mutation pause_ad] → meta_api_worker
```

### WS live-invalidation
```
useRealtimeInvalidation()
  → useDashboardSocket({ onMessage })
  → new WebSocket(ws://…/ws?api_key=TOKEN)
  → exponential backoff: [1s,2s,4s,8s,16s,30s]
  → после 3 сбоев: pollingFallback=true → setInterval(15s) invalidateAll
```

### Открытие объявления из deep-link
```
/ads/$fbAdId (холодная загрузка)
  → TanStack Router: createFileRoute('/ads/$fbAdId')
  → useQueryClient().getQueryData(["ads"]) — поиск в кэше
  → если нет: useAdTimeline(fbAdId) + синтез AdSnapshot (alert_state: "normal" hardcoded)
  → AdDrawer рендерится
```

### Dashboard spend headline vs chart
```
stats.current_day_spend  → parseFloat() → SpendKpiRow headline  ← ПРАВИЛЬНО
chartQ.data[].spend      → Number()     → SpendChart + Sparkline ← RAW кумулятив (завышен внутри дня)
cumulativeSpendTotal()   — существует в lib/utils/spendTotal.ts, но НЕ используется на Dashboard
```

---

## Auth-модель

- **HTTP**: `X-API-Key: <key>` header. `apiKey` хранится в Zustand `persist` (localStorage key `fb-auth`). При старте `main.tsx` пишет из `VITE_API_KEY` если store пуст.
- **WebSocket**: `?api_key=<key>` query-param (браузер не поддерживает custom headers на WS). Ключ читается из `useAuthStore.getState().apiKey` в момент коннекта (не реактивно).
- **Backend**: `ApiKeyAuthMiddleware` — timing-safe compare через `secrets.compare_digest`, fail-closed (503 если ключ не настроен).

---

## Виртуализированная таблица объявлений

`AdsTable.tsx` использует `useVirtualizer` (`@tanstack/react-virtual`):
- `overscan: 8` строк
- CSS grid: `40px minmax(0,1fr) 64px 56px 130px 96px 74px 62px 62px 62px 66px`
- `estimateSize: () => density === "dense" ? 28 : density === "compact" ? 34 : 44`
- Keyboard nav (J/K/X/Enter/D) реализован в `AdsPage` через `window.addEventListener("keydown")`; cleanup через `removeEventListener` (утечки нет)

---

## Типы и контракты

- `@fb/shared/api/generated` — OpenAPI-generated TS types (`AdSnapshot`, `Offer`, `TaskOut`, …)
- `@fb/shared` — FSM-константы (`ALERT_STATE_LABELS`, `normalizeAlertState`), форматтеры (`formatRelativeTime`, `formatSpend`), `deriveGeoFromNames`
- Поля `creative_thumb_url`, `ad_account_id`, `adset_daily_budget`, `learning_stage` существуют на бэкенде, но **не входят** в `AdSnapshot` TS-тип → повсеместные `as AdSnapshot & { field? }` unsafe casts

---

## Связанные бэкенд-модули

| Frontend | Backend |
|----------|---------|
| `useAds` | `GET /api/v1/dashboard/ads` (`apps/api/routers/v1/dashboard.py`) |
| `useBulkDisable` | `POST /api/v1/dashboard/ads/bulk-disable` → `task_queue` → `meta_api_worker` |
| `useDashboardStats` | `GET /api/v1/dashboard/stats` (Redis observer:runtime + DB) |
| `useChartData` | `GET /api/v1/dashboard/chart-data` (`ad_metrics` SUM per date_trunc) |
| `useRealtimeInvalidation` | WS `/ws` (FastAPI → Redis pubsub forward) |
| `useDeleteOffer` | `DELETE /api/v1/offers/{id}` (soft delete `is_active=false`) |
