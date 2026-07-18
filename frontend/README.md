# FB Stop Bot — Frontend (web)

Production-grade web-интерфейс FB Stop Bot.

Editorial-monochrome design, dark-only, desktop 1280+ minimum. Source of truth — `docs/frontend_design.md`.

Часть монорепо на **pnpm workspaces**. Установка зависимостей — `pnpm install` из корня репозитория.

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
# Установка зависимостей (из корня монорепо)
pnpm install

# Dev-сервер на порту 5174, proxy /api -> http://localhost:8100
pnpm --filter fb-stop-bot-frontend dev
# или из корня: pnpm dev:web

# Production build (tsc -b + vite build)
pnpm --filter fb-stop-bot-frontend build

# Превью production build
pnpm --filter fb-stop-bot-frontend preview

# Генерация TypeScript-типов из OpenAPI (из корня)
pnpm gen:api

# Storybook на порту 6006
pnpm --filter fb-stop-bot-frontend storybook
pnpm --filter fb-stop-bot-frontend build-storybook

# Тесты
pnpm --filter fb-stop-bot-frontend test        # один прогон
pnpm --filter fb-stop-bot-frontend test:watch  # watch mode
pnpm --filter fb-stop-bot-frontend test:ui     # UI

# Линт / форматирование
pnpm --filter fb-stop-bot-frontend lint
pnpm --filter fb-stop-bot-frontend lint:fix
pnpm --filter fb-stop-bot-frontend format
pnpm --filter fb-stop-bot-frontend typecheck
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
pnpm gen:api                # → packages/shared/src/api/generated.ts
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

Этот фронт интегрируется с 61 endpoint бэка (см. `apps/api/routers/v1/`). Префикс
`/api`. В production browser использует same-origin Telegram session; Caddy
добавляет server-only `X-API-Key` только в upstream-запрос. Ключ не компилируется
во frontend и не хранится в browser storage.

Vite proxy уже настроен: запросы к `/api` идут на `http://localhost:8100`.

WebSocket-хук `useDashboardSocket` подключается к `/ws/dashboard`, при 3 неудачных reconnect'ах автоматически отдаёт стейт `pollingFallback=true` — TanStack Query берёт на себя refetch. То есть бэкенду НЕ обязательно реализовывать WS на этом этапе.

## Реализованные страницы

6 полных страниц: Dashboard, Ads (+ drawer деталей), Drafts, Offers, History, Settings (табы Observer/Telegram/Vision/Workers/AI/Health).

~331 vitest-тест. Storybook 8. Виртуализованная таблица (@tanstack/react-virtual). WS live-invalidation с backoff + polling fallback.
