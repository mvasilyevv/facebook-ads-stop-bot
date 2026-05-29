# FB Stop Bot — Frontend

Новый production-grade фронт FB Stop Bot. Живёт **рядом** со старым `frontend/` — старый не трогается, миграция страница-за-страницей.

Editorial-monochrome design, dark-only, desktop 1280+ minimum. Source of truth — `docs/frontend_design.md`.

## Стек

- **React 19** + **TypeScript strict**
- **Vite 6** (dev/build)
- **Tailwind CSS 4** через `@tailwindcss/vite`
- **TanStack Router** — file-based routing (`src/routes/`)
- **TanStack Query 5** — server state
- **TanStack Table v8 + Virtual** — таблицы с виртуализацией
- **Zustand 5** — cross-page UI state (sidebar / density)
- **Radix UI** primitives (Dialog, Tooltip, Tabs, Toast)
- **Lucide Icons** — иконки
- **Framer Motion** — анимации (используется минимально)
- **Storybook 8** — изоляция компонентов
- **Vitest** + Testing Library — тесты

## Команды

```bash
# Установка зависимостей
npm install

# Dev-сервер на порту 5174, proxy /api -> http://localhost:8100
npm run dev

# Production build (tsc -b + vite build)
npm run build

# Превью production build
npm run preview

# Storybook на порту 6006
npm run storybook
npm run build-storybook

# Тесты
npm test                # один прогон
npm run test:watch      # watch mode
npm run test:ui         # UI

# Линт / форматирование
npm run lint
npm run lint:fix
npm run format
npm run typecheck
```

## Структура

```
frontend/
├── public/                       # статика (favicon)
├── src/
│   ├── main.tsx                  # точка входа React
│   ├── styles/
│   │   ├── globals.css           # точка входа CSS
│   │   ├── tokens.css            # design tokens + @theme
│   │   ├── fonts.css             # JetBrains Mono + Inter Tight
│   │   └── reset.css             # минимальный reset
│   ├── lib/
│   │   ├── api/                  # TanStack Query клиенты per-domain
│   │   ├── types/                # TypeScript типы responses
│   │   ├── websocket/            # WS hook + reconnect/polling
│   │   ├── utils/                # cn, formatters
│   │   ├── constants/            # FSM states, task statuses
│   │   └── hooks/                # custom hooks
│   ├── stores/                   # Zustand (ui, auth)
│   ├── components/
│   │   ├── ui/                   # base (Button, Input, Card, Badge, ...)
│   │   ├── layout/               # Shell, Sidebar, TopBar, PageHeader
│   │   ├── data/                 # Table, KPICard, ChartWrapper
│   │   └── domain/               # AlertEventRow, DraftCard, TaskQueueRow
│   ├── routes/                   # TanStack Router file-based
│   │   ├── __root.tsx
│   │   ├── index.tsx             # Dashboard (placeholder)
│   │   ├── ads/
│   │   ├── offers/
│   │   ├── history/
│   │   ├── settings/
│   │   └── drafts/
│   ├── routeTree.gen.ts          # автогенерируется TanStack Router plugin
│   └── tests/                    # vitest setup + базовые тесты
├── stories/                      # Storybook stories
├── .storybook/                   # Storybook конфиг
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
├── eslint.config.js
└── postcss.config.js
```

## Архитектурные правила

- **Все комментарии и docstrings — на русском.**
- **TypeScript strict.** `any` запрещён ESLint'ом, `noUncheckedIndexedAccess` включён.
- **Tailwind 4 utility-first.** CSS vars экспонируются через `@theme` → классы `bg-bg-1`, `text-bg-11`, `border-bg-5`, `text-accent`.
- **Default `border-radius: 0`.** Если ставишь radius, обоснуй.
- **Никаких файлов >500 строк** в новом коде.
- **Server data → TanStack Query.** Form state → react-hook-form. Cross-page UI → Zustand. URL state → TanStack Router search params.
- **Density toggle** (`comfortable` 32px / `compact` 24px) — Zustand + CSS variable `--table-row-height`.
- **Dark-only.** Light mode — отдельная итерация.

## Контрактные типы (OpenAPI codegen)

TypeScript-типы генерируются из OpenAPI-схемы FastAPI — это **источник истины**, а не ручной `api.ts`.

### Почему ручные типы — риск

При ручном написании типов (старый подход) дважды случались runtime-краши: тип фронта расходился с реальным ответом backend (например, `ObserverSettings.scan_interval_seconds` vs `is_scanning_enabled`, `TelegramInviteResponse.invite_code` vs `code`, пороги `number` vs `string`). TypeScript это не ловил, потому что типы совпадали внутри фронта.

### Регенерация (после изменений в backend-схемах)

```bash
# Из корня проекта:
make gen-api-types          # export + codegen за один шаг

# Или по шагам:
make export-openapi         # → frontend/openapi.json
cd frontend && npm run gen:api   # → src/lib/types/api-generated.ts
```

### Файлы

- `frontend/openapi.json` — экспортированная схема (в git, фронт работает офлайн).
- `frontend/src/lib/types/api-generated.ts` — автогенерированные типы (в git).
- `frontend/src/lib/types/api.ts` — ручные alias-типы для удобства; при расхождении `api-generated.ts` — победитель.

### Endpoints без response_model (типов нет в generated)

| Endpoint | Причина |
|---|---|
| `DELETE /api/dashboard/auto-enable-disabled/{fb_ad_id}` | 204 No Content |
| `DELETE /api/dashboard/disable-tasks/{task_id}` | 204 No Content |
| `DELETE /api/fake-deposits/{fb_ad_id}` | 204 No Content |
| `DELETE /api/offers/{offer_id}` | 204 No Content |
| `POST /api/v1/postback/adsetpro` | JSONResponse без response_model |

### Plan миграции на generated типы

1. В новых компонентах: `components["schemas"]["XxxOut"]` из `api-generated.ts` напрямую.
2. Постепенно заменить ручные типы в `api.ts` на `export type X = components["schemas"]["XOut"]` алиасы.
3. Удалить поля с `@deprecated` после обновления всех компонентов.
4. CI: `make gen-api-types && git diff --exit-code frontend/openapi.json frontend/src/lib/types/api-generated.ts` — провалит PR если backend изменился, а codegen не перегнали.

## Backend

Этот фронт интегрируется с 61 endpoint бэка (см. `apps/api/routers/v1/`). Префикс `/api`. Auth — `X-API-Key` header.

Vite proxy уже настроен: запросы к `/api` идут на `http://localhost:8100`.

WebSocket-хук `useDashboardSocket` подключается к `/ws/dashboard`, при 3 неудачных reconnect'ах автоматически отдаёт стейт `pollingFallback=true` — TanStack Query берёт на себя refetch. То есть бэкенду НЕ обязательно реализовывать WS на этом этапе.

## Что готово сейчас

Foundation (Round 8.0):

- Design tokens + шрифты.
- Базовый UI-набор (Button, Input, Card, Badge, Pill, Tabs, Modal, Drawer, Tooltip, Toast, Skeleton, EmptyState, ErrorState, Select, Kbd, ConfirmDialog).
- Table компонент с TanStack Table + Virtual.
- Layout shell (Sidebar + TopBar + WorkerPulse).
- 6 placeholder-страниц (Dashboard, Ads, Offers, History, Settings, Drafts).
- TanStack Query клиенты per-domain.
- WebSocket hook с reconnect + polling fallback.
- Zustand stores: ui (sidebar, density) и auth (apiKey).
- Storybook setup + 4 stories.
- Vitest setup + 5 unit-тестов.

Полная имплементация страниц — отдельные раунды (Round 8.1+).
