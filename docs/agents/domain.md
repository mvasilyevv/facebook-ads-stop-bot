# Domain docs

Правила чтения доменной документации инженерными навыками этого репозитория.

## Перед исследованием кода

1. Прочитать корневой `CONTEXT-MAP.md`.
2. Открыть только те `CONTEXT.md`, которые относятся к задаче.
3. Проверить системные решения в `docs/adr/`.
4. Проверить доменные ADR в каталоге `docs/adr/` рядом с выбранным
   `CONTEXT.md`, если такой каталог существует.

Если документ или каталог ADR отсутствует, продолжить молча. Новые термины и
решения фиксируются через domain-modeling по мере появления, а не создаются
заранее пустыми файлами.

## Layout

```text
/
├── CONTEXT-MAP.md
├── docs/adr/                         # общесистемные решения
├── core/CONTEXT.md                   # safety/control и durable state
├── core/telegram/CONTEXT.md          # notification plane
├── core/campaign_builder/CONTEXT.md  # campaign, offers и launch operations
├── core/analytics/CONTEXT.md         # analytics, trackers и attribution
├── core/auth/CONTEXT.md              # identity и access boundaries
├── core/ai_assistant/CONTEXT.md      # operator AI assistant
├── frontend/CONTEXT.md               # web, TMA и shared operator packages
├── services/browser-agent/CONTEXT.md # browser/Vision boundary
└── deploy/CONTEXT.md                 # release, observability, backup и HA
```

`apps/` содержит entrypoints и workers, но их доменная принадлежность задаётся
картой, а не физическим каталогом. `frontend-mini/`, `packages/operator-*`,
`packages/shared/` и `packages/features/` относятся к operator-контексту.

## Терминология и ADR

- Использовать термины из glossary соответствующего `CONTEXT.md`; не вводить
  синонимы для уже определённых понятий.
- Если нужного понятия нет, сначала проверить, не является ли оно ошибочной
  абстракцией. Реальный пробел отметить для domain-modeling.
- Любое предложение, противоречащее ADR, должно явно назвать конфликт:
  `Contradicts ADR-XXXX (...)` и объяснить, почему решение стоит пересмотреть.
- Глобальные инварианты из `CLAUDE.md` имеют приоритет над контекстными
  документами.
