# FB Agent — чек-лист пересборки

Актуально на 2026-08-11. `[x]` означает только то, что реализовано и
подтверждено локальными проверками; это не означает GitHub CI или production.
Обязательные release-gates не переносятся в `BACKLOG.md`.

- [x] В repository `AGENTS.md` закреплён risk-based workflow:
  анализ → план → тест → реализация → проверки → review.

## Продукт, UI и safety

- [x] Зафиксированы `PRODUCT.md`, направление «Точный журнал / Шкала ведёт»,
  `DESIGN.md` и UI-токены.
- [x] Удалены автоматические activate-пути; автоматическая команда допускается
  только для deterministic `pause` при свежих полных USD-данных.
- [x] AI не имеет mutation capability; activate остаётся owner-confirmed.
- [x] Portfolio/cabinet UI, Ads, Actions, Incidents, Analytics, Campaigns,
  Offers и Settings реализованы для web и TMA с общими контрактами.
- [x] `partial`, `stale`, `unavailable` и unknown не отображаются как
  confirmed/green; non-USD данные fail-closed для auto-pause.
- [x] Durable Telegram inbox/outbox, webhook, callback tokens, editable
  incident cards и suppression rules покрыты локальными тестами.
- [x] Legacy direct send, Rich/polling и Redis alert queue удалены из runtime
  contracts.
- [ ] Пройти physical-device matrix и live usability Telegram/UI/TMA/desktop.

## Чистая БД и control plane

- [x] Baseline `0001_safety_first_baseline`, queue lanes/deadlines/leases,
  fencing и `UNKNOWN` reconciliation проверены локально.
- [x] Adoption bundle ограничен allowlist; import валидируется, dry-run
  поддержан и выполняется атомарно.
- [x] Receipt adoption хранится в PostgreSQL в той же SERIALIZABLE-транзакции,
  что import; host marker не является источником истины.
- [x] Миграции forward-only: `0001` замораживается, `0002+` линейны;
  unknown/multiple/foreign revision отклоняются до DDL под advisory lock.
- [x] Локальные PostgreSQL integration-тесты подтверждают rollback оборванной
  миграции, сохранение данных при `0001 → 0002` и владение миграцией одним
  lock holder: `789 passed, 2 skipped, 3 deselected`.
- [x] Observer разделяет scan/control страницы, умеет находить/открывать
  нужный кабинет и сохраняет фактический `next_scan_at`.

## Single-slot release и `fbctl`

- [x] Blue/green, release journal, rollback/handoff, pgBackRest gates и
  candidate-Alloy удалены из целевого runtime.
- [x] Compose разделён на стабильные `infra`, `jobs`, `app`, `desktop` и
  `monitoring` проекты; routine deploy допускает downtime.
- [x] Реализован тестируемый Python `fbctl`: `doctor`, `bootstrap`, `deploy`,
  `status`, `logs`, `restart`, `cleanup` и `db`.
- [x] `bootstrap` отдельно создаёт host/runtime, применяет baseline, импортирует
  adoption и seed Vision; обычный `deploy` требует DB receipt и не повторяет
  import/provisioning.
- [x] Routine deploy выполняет preflight/pull до остановки, forward migration,
  desktop → app → workers, worker DB-poll+heartbeat readiness, system-ready и
  Telegram webhook checks; ошибка оставляет money workers выключенными.
- [x] Canonical `runtime.env` генерируется из строгого единственного
  конфигурационного контракта; обычный deploy не передаёт source secrets.
- [x] Локально проверены parser/Compose contracts, migration/adoption path и
  full Python suite: `2417 passed`.
- [ ] GitHub CI построил и проверил immutable content-addressed image digests и
  release manifest.
- [ ] Production-like Docker rehearsal прошёл на опубликованных images:
  clean DB → bootstrap → deploy, failpoints и повторный deploy.
- [ ] Реальный `fbctl bootstrap`, затем `fbctl deploy` выполнены на production.
- [ ] Live smoke подтверждает Vision/browser-agent, кабинеты, web, TMA,
  Telegram webhook/card edit и system readiness.
- [ ] После live smoke удалены только явно одобренные legacy volumes/dumps/
  Compose resources.

## Локальные проверки

- [x] Локальные frontend gates: typecheck `6/6`, TMA `151`, browser-agent
  `224`, Storybook a11y `58`, Playwright `140`; web/TMA builds укладываются в
  установленные budgets.

## Обязательные release-gates

- [ ] GitHub verify, image publish и Docker rehearsal зелёные.
- [ ] Выполнены `fbctl doctor`, `fbctl db check`, clean bootstrap и повторный
  idempotent deploy на production-like окружении.
- [ ] Runtime OpenAPI и generated TypeScript совпадают; frontend/TMA/
  browser-agent build, lint, a11y и responsive smoke зелёные в CI.
- [ ] Выполнены live production smoke UI/TMA/Telegram/desktop/cabinets.

## Принятые эксплуатационные решения

- [x] Owner принял downtime, single slot, отсутствие automatic rollback и
  backup/restore automation.
- [x] Старые production data удаляются только после отдельной live проверки и
  в рамках уже данного owner-разрешения.
