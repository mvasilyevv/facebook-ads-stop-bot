# FB Stop Bot — Mini App (Telegram)

Telegram Mini App для FB Stop Bot. Мобильная адаптация основного интерфейса.

Часть монорепо на **pnpm workspaces**. Установка зависимостей — `pnpm install` из корня репозитория.

## Стек

- **React 19** + **TypeScript strict**
- **Vite 6** (dev/build)
- **Tailwind CSS 4** через `@tailwindcss/vite`
- **TanStack Router** — file-based routing
- **TanStack Query 5** — server state
- **`@fb/shared`** — общие типы, форматтеры, FSM-константы, дизайн-токены
- **TMA auth** — Telegram initData → Bearer token
- **Vitest** + Testing Library — тесты

## Дизайн

Тот же editorial-monochrome dark канон, что и web-фронт. Мобильная адаптация: нижний tab-bar, safe-area, touch-targets ≥44px. Порт dev **5175**, base `/tma/`.

## Экраны (9)

Dashboard, Ads, Ad Detail, Drafts, Health, History, Offers, Scripts, Settings.

## Команды

```bash
# Установка зависимостей (из корня монорепо)
pnpm install

# Dev-сервер на порту 5175
pnpm --filter fb-agent-mini dev
# или из корня: pnpm dev:mini

# Production build
pnpm --filter fb-agent-mini build

# Превью production build
pnpm --filter fb-agent-mini preview

# Тесты (~89 тестов)
pnpm --filter fb-agent-mini test        # один прогон
pnpm --filter fb-agent-mini test:watch  # watch mode

# Линт / typecheck
pnpm --filter fb-agent-mini lint
pnpm --filter fb-agent-mini typecheck
```

## Структура

```
frontend-mini/
├── src/
│   ├── main.tsx            # точка входа
│   ├── routes/             # TanStack Router file-based (9 экранов)
│   ├── components/         # UI-компоненты (переиспользуют дизайн-токены из @fb/shared)
│   ├── lib/
│   │   ├── api/            # TanStack Query клиенты per-domain
│   │   └── tma.ts          # TMA auth helpers
│   └── styles/             # globals.css + import tokens из @fb/shared
├── index.html
├── package.json
├── tsconfig.json
└── vite.config.ts
```

## Backend

Proxy `/api` → `http://localhost:8100`. Auth — подписанный TMA Bearer, полученный
после серверной проверки Telegram initData. Общие write-endpoint'ы разрешены
middleware только TMA-пользователю с актуальной ролью `owner`.
