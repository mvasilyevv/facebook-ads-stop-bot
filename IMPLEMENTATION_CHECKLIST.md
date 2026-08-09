# FB Agent — чек-лист пересборки

Актуально на 2026-08-09. `[x]` означает: реализовано и локально проверено в integration-worktree. Это не означает production-release. Некритичные улучшения и внешние acceptance-gates вынесены в `BACKLOG.md`.

## PR-01 — продукт и дизайн

- [x] Зафиксировать PRODUCT.md и safety-инварианты.
- [x] Выбрать направление «Точный журнал / Шкала ведёт».
- [x] Зафиксировать DESIGN.md и UI-токены.

## PR-02 — запрещённая автоматизация

- [x] Удалить auto-activate workers, flags, API и UI.
- [x] Оставить activate только после owner-confirm.
- [x] Закрыть AI-доступ к mutations.
- [x] Проверить safety-critical lane/capability routing после удаления auto-activate.
- [x] Выполнить финальную общую проверку после сборки всех веток.

## PR-03 — чистая БД и перенос конфигурации

- [x] Оставить одну baseline-миграцию без legacy auto-enable.
- [x] Выделить кабинеты в отдельную сущность и нормализовать связь offer↔cabinet.
- [x] Реализовать adoption-bundle export/validate/dry-run/import.
- [x] Подготовить runbook и checklist повторного ввода секретов.
- [x] Проверить fail-closed validation и атомарный rollback импорта.
- [x] Прогнать adoption integration на отдельной чистой PostgreSQL.

## PR-04 — safety/control plane

- [x] Queue lanes, deadlines, leases, fencing и UNKNOWN reconciliation.
- [x] Разделить scan/control/interactive browser pages.
- [x] Находить кабинет во всех вкладках или безопасно открывать его автоматически.
- [x] Записывать реальный `cabinet_runtime.next_scan_at`.
- [x] Прогнать crash/concurrency/DB-restart и lost-NOTIFY acceptance.

## PR-05 — incidents и Telegram

- [x] Durable inbox/outbox, webhook и callback tokens.
- [x] Editable incident cards и suppression rules.
- [x] Удалить direct send, Rich, polling и Redis alert queue.
- [x] Прогнать полный Telegram failure/burst suite.

## PR-06 — Operator API и frontend foundation

- [x] Typed OpenAPI client и общие operator contracts.
- [x] Global/cabinet snapshots и realtime reconciliation barrier.
- [x] USD-only fail-closed money semantics.
- [x] Регенерировать OpenAPI/TS после текущих backend-контрактов.
- [x] Синхронизировать integration `uv.lock` с `pyproject.toml`; исходный dirty worktree не затронут.

## PR-07 — «Сейчас» и кабинеты

- [x] Пересобрать portfolio overview.
- [x] Добавить cabinet drill-down.
- [x] Убрать false-green для partial/stale/unavailable.
- [x] Сделать web/TMA responsive parity.

## PR-08 — Ads, Actions и Incidents

- [x] Ads list/detail и pause/activate preview + confirm.
- [x] Actions list/detail, filters, recovery links и cancelled state.
- [x] URL-state, cabinet filters и mobile sheets.
- [x] Добавить полный incidents list/API для web и TMA.
- [x] Выполнить общий PR-08 review после объединения.

## PR-09 — аналитика

- [x] Завершить Spend, Funnel и Daypart по chart model.
- [x] Завершить AccessibleChartFrame и таблицы данных.
- [x] Проверить mobile/TMA SVG renderer и USD fail-closed.
- [x] Проверить семь колонок и analytics presets.

## PR-10 — Campaigns, Offers, Settings и AI

- [x] Дать mobile web и TMA полный campaign creation flow.
- [x] Сохранять campaign draft на сервере с CAS и atomic clear.
- [x] Дать web/TMA одинаковые offers и settings capabilities.
- [x] Оставить AI без mutation capability.
- [x] Завершить финальный PR-10 lint/build/a11y review.

## PR-11 — KasmVNC 1.5

- [x] Закрепить KasmVNC 1.5.0 source commit, deb checksum и notices.
- [x] Собрать first-party web client из pinned source локально.
- [ ] Получить immutable image digest из CI и прогнать runtime regression.

## PR-12 — единый remote desktop runtime

- [x] Пересобрать единый `vision-desktop` на `DISPLAY=:1`.
- [x] Удалить kasmxproxy, Selkies, sidecar и IPC/X-socket coupling.
- [x] Зафиксировать 1366×768, `/config`, Vision API и clipboard 256 KiB.
- [ ] Проверить новый runtime в disposable container fixture.

## PR-13 — remote desktop UX

- [x] Собрать pinned first-party Kasm web client.
- [x] Добавить server-bound desktop/mobile presentation profiles.
- [x] Реализовать Fit/100%, cursor/navigation, pinch, keyboard, clipboard и reconnect.
- [ ] Пройти physical-device matrix.

## PR-14 — platform evidence

- [x] Реализовать immutable CI images и digest-only deploy без mutable `:latest`.
- [x] Подготовить off-host monitoring, traces и blackbox-конфигурацию.
- [x] Настроить pgBackRest continuous WAL, full/differential backup и drill machinery.
- [x] Добавить обязательные Storybook/a11y и Playwright CI jobs.
- [x] Получить живые source CI, a11y и локальные load/chaos artifacts.
- [ ] Получить release-image CI, restore/PITR и field Web Vitals artifacts.

## PR-15 — legacy eradication и release packet

- [x] Запустить архитектурные legacy guards по всему репозиторию.
- [x] Провести независимые backend/Telegram/UI/platform reviews.
- [x] Исправить все P0/P1 и решить подтверждённые P2.
- [x] Подготовить rollback и двухчасовой cutover runbook.
- [ ] Сформировать финальные production manifests и adoption bundle.

## Текущие integration gates

- [x] Вернуть TMA initial JS в бюджет ≤160 KB (`149 665 B gzip`, запас `10 335 B`).
- [x] Завершить server-backed display timezone preference и подключить analytics.
- [x] Прогнать локальные unit/type/lint/build gates: backend `2463 passed`, web `418`, TMA `151`, browser-agent `224`.
- [x] Прогнать DB integration tests на изолированной PostgreSQL (`779 passed`).
- [ ] Выполнить browser/device QA для settings, campaigns и desktop.

## До локально рабочего release candidate

- [x] Закрыть safety-critical lane/capability fix.
- [x] Завершить display-timezone slice и повторно сгенерировать typed contracts.
- [x] Прогнать общий backend/frontend/browser-agent/static regression.
- [x] Проверить точное совпадение runtime OpenAPI и generated client.
- [x] Провести финальный P0/P1 review: открытых P0/P1 нет; остаток зафиксирован в `BACKLOG.md`.

## Production gate

- [ ] Все CI, restore, load, chaos, accessibility и device gates зелёные.
- [x] Подготовлен двухчасовой cutover packet; запуск всё ещё заблокирован gates ниже.
- [ ] Получена отдельная команда owner: `запускай`.
- [ ] Production-switch выполнен.
- [ ] Старые production data удалены только после отдельного подтверждения.
