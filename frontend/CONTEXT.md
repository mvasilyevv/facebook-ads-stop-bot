# Operator product context

## Назначение

Operator web и Telegram Mini App дают человеку одну action-first модель
состояния системы. Surface mode — **Operate**: скорость распознавания риска,
причины и следующего действия важнее декоративной выразительности.

## Владеет

- responsive web shell в `frontend/`;
- always-dark TMA shell в `frontend-mini/`;
- shared domain/view-models в `packages/shared/` и `packages/features/`;
- typed query layer в `packages/operator-api/`;
- UI primitives и tokens в `packages/operator-ui/`;
- доступными charts, mobile cards, action receipts и realtime reconciliation.

## Инварианты

- `ready | empty | partial | stale | unavailable` отображаются различимо.
- Green используется только для confirmed/ready; unknown не становится zero.
- Status всегда содержит icon, label и color; color не является единственным
  каналом смысла.
- Mobile использует cards из общих row view-models, а не сжатую desktop-table.
- График показывает timezone, source, `as_of`, completeness, summary и таблицу
  данных; пропуск точки остаётся разрывом.
- HTTP `202` отображается как queued с переходом к action lifecycle.
- TMA доверяет backend-валидации `initData`, учитывает safe areas и BackButton.

## Glossary

- **Snapshot** — revisioned server projection operator-состояния.
- **Attention item** — ranked проблема с причиной и доступным действием.
- **Data state** — качество и доступность секции, отдельно от business severity.
- **Action state** — `queued | running | confirmed | failed | cancelled | unknown`.
- **Reconciliation** — один snapshot fetch после gap в WS sequence.
