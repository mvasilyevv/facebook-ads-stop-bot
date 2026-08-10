# Analytics and attribution context

## Назначение

Контекст нормализует Meta и tracker/postback evidence в воспроизводимые метрики,
воронки и временные ряды без подмены отсутствующих данных нулями.

## Владеет

- tracker/postback ingestion, deduplication и reconciliation;
- spend, revenue, CPA/CPL, funnel и daypart semantics;
- cabinet-day, timezone и currency boundaries для аналитики;
- completeness, freshness, sources и issues аналитических секций;
- query models для operator API, reports и deterministic digests.

## Инварианты

- `null` означает unknown; подтверждённый `0` имеет отдельное evidence.
- Пропуск временного ряда остаётся gap, а не нулевой точкой.
- День кабинета и timezone задаёт сервер.
- Postback обрабатывается идемпотентно; reconciliation не создаёт двойную
  атрибуцию.
- Денежные значения и точные ratios пересекают API как decimal strings.

## Glossary

- **Attribution** — связь tracker-события с Meta entity и cabinet day.
- **Completeness** — доля ожидаемых источников, вошедших в projection.
- **Freshness** — возраст последнего подтверждённого source evidence.
- **Reconciliation** — повторная сверка durable source facts и projection.
