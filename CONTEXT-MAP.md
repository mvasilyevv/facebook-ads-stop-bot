# FB_Agent context map

Карта направляет инженерные навыки к минимальному набору доменной информации.
Сначала применяются глобальные правила `CLAUDE.md`, затем релевантный контекст.

| Контекст | Документ | Владеет |
|---|---|---|
| Safety and control | `core/CONTEXT.md` | очереди, команды, observer, Meta lifecycle, fencing и PostgreSQL authority |
| Telegram notifications | `core/telegram/CONTEXT.md` | incidents, outbox, inbox, delivery, callbacks и Telegram UX |
| Campaign operations | `core/campaign_builder/CONTEXT.md` | campaign creation, offers/catalog, duplicate plans и launch lifecycle |
| Analytics and attribution | `core/analytics/CONTEXT.md` | metrics, funnels, tracker/postback ingestion, reconciliation и reporting semantics |
| Identity and access | `core/auth/CONTEXT.md` | web, TMA, desktop и service authentication/authorization boundaries |
| AI assistant | `core/ai_assistant/CONTEXT.md` | operator-assistant tools, evidence boundaries, prompts и approval policy |
| Operator product | `frontend/CONTEXT.md` | web, TMA, typed API, view-models, data states и operator workflows |
| Browser and Vision | `services/browser-agent/CONTEXT.md` | browser session, Graph tunnel, scan/control pages, deadlines и cancellation |
| Platform | `deploy/CONTEXT.md` | release topology, migrations, observability, backup, restore и HA gates |

## Cross-context changes

- Изменение money-команды требует чтения safety/control и browser/Vision;
  при наличии operator или Telegram action также соответствующего UI-контекста.
- Изменение notification lifecycle требует safety/control и Telegram.
- Изменение создания или дублирования кампаний требует campaign operations,
  safety/control и browser/Vision; при изменении surface также operator product.
- Изменение tracker/postback или вычисления метрики требует analytics and
  attribution; изменение operator-представления также требует operator product.
- Изменение login, invite, role или service credential требует identity and
  access и контекста защищаемого surface.
- Изменение AI tool, который читает operator data или создаёт команду, требует
  AI assistant и контекста-владельца соответствующих данных/команды.
- Изменение API-contract требует safety/control и operator product.
- Изменение release/migration/backup требует platform и контекста владельца
  изменяемого runtime.

Общесистемные архитектурные решения размещаются в `docs/adr/`. Решения,
затрагивающие только один контекст, размещаются в его локальном `docs/adr/`.
