# FB Agent — Frontend (web)

Production-grade web-интерфейс FB Agent.

Calm-industrial operator console с responsive web и mobile action flows.
Токены и обязательные data states задаются общими packages; документ
`docs/frontend_design.md` остаётся design reference, а не API contract.

Часть монорепо на **pnpm workspaces**. Установка зависимостей — `pnpm install` из корня репозитория.

## Стек

- **React 19** + **TypeScript strict**
- **Vite 6** (dev/build)
- **Tailwind CSS 4** через `@tailwindcss/vite`
- **TanStack Router** — file-based routing (`src/routes/`)
- **TanStack Query 5** — server state
- **TanStack Table v8 + Virtual** — таблицы с виртуализацией
- **Zustand 5** — cross-page UI state (sidebar / display timezone)
- **Radix UI** primitives (Dialog, Tooltip, Tabs, Toast)
- **Lucide Icons** — иконки
- **Recharts 3** — доступные desktop/mobile-web графики
- **Storybook 10** + a11y/Vitest — изоляция и browser component tests
- **Vitest** + Testing Library + self-hosted Playwright — unit/component/responsive tests

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
pnpm --filter fb-stop-bot-frontend test:storybook  # Chromium + a11y
pnpm --filter fb-stop-bot-frontend test:e2e        # viewports 360…1920

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
│   ├── styles/                    # globals, fonts, reset
│   ├── lib/
│   │   ├── api/                  # domain adapters поверх typed client
│   │   ├── websocket/            # WS lifecycle и snapshot reconciliation
│   │   ├── utils/                # cn, formatters
│   │   ├── constants/            # FSM states, task statuses
│   │   └── hooks/                # custom hooks
│   ├── stores/                   # Zustand (ui, auth)
│   ├── components/
│   │   ├── ui/                   # base (Button, Input, Card, Badge, ...)
│   │   ├── layout/               # Shell, Sidebar, TopBar, PageHeader
│   │   ├── data/                 # Table, AccessibleChartFrame, timeline
│   │   └── domain/               # operator-facing domain components
│   ├── features/                 # typed operator vertical slices
│   ├── routes/                   # TanStack Router file-based
│   │   ├── __root.tsx
│   │   ├── index.tsx             # Operator snapshot: «Сейчас»
│   │   ├── actions/              # Lifecycle действий
│   │   ├── ads/
│   │   ├── analytics/
│   │   ├── campaigns/
│   │   ├── incidents/
│   │   ├── offers/
│   │   ├── settings/
│   │   └── system/
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
- Статус всегда выражается icon + label + color; цвет не является единственным
  носителем смысла.
- Основной текст не меньше 16 px, вторичный — 14 px, служебный — 12 px;
  interactive target не меньше 44×44 px.
- `partial`, `stale`, `unavailable` и `unknown` никогда не выглядят зелёными.

## Контрактные типы (OpenAPI codegen)

TypeScript-типы генерируются из OpenAPI-схемы FastAPI. Typed transport и query
layer находятся в `packages/operator-api`; общие states, formatters и
view-models — в `packages/shared`. Строковые URL и ручные response interfaces в
feature-код не добавляются.

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
- `packages/shared/src/api/generated.ts` — generated schema types.
- `packages/operator-api/` — `openapi-fetch`/query client и hooks.
- `packages/shared/src/operator/` — общие view-models для web и TMA.

CI должен проваливаться, если backend contract изменился, а OpenAPI/codegen не
обновлены.

## Backend

Префикс API — `/api`. В production browser использует same-origin Telegram session; Caddy
добавляет server-only `X-API-Key` только в upstream-запрос. Ключ не компилируется
во frontend и не хранится в browser storage.

Vite proxy уже настроен: запросы к `/api` идут на `http://localhost:8100`.

Operator pages читают типизированные `/api/operator/*`. `/ws/operator` несёт
sequence и snapshot revision; gap вызывает одно snapshot reconciliation и не
запускает broad invalidation sweep.

## Реализованные страницы

Реализованы responsive «Сейчас», lifecycle «Действия», «Реклама», analytics,
campaign runs, incidents, offers, settings, sources и remote desktop.
Mobile использует cards из тех же row view-models, а не сжатые desktop tables.
Campaign creation полностью доступен в responsive web и TMA через platform-native layouts.

Storybook 10 запускает stories и a11y в headless Chromium. Playwright проверяет 360, 390, 430, 768, 1280, 1440 и 1920 px без horizontal page scroll.
