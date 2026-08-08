# Operator UI design contract

Актуальное направление: спокойный industrial control room. Интерфейс должен
быстро отвечать на три вопроса: что сломано, сколько это стоит и какое действие
доступно прямо сейчас.

Реализованный UI и общие tokens в `packages/shared/` являются источником
истины.

## Information architecture

Desktop navigation:

```text
Сейчас
Действия
Реклама
  Объявления
  Кампании
  Создание
  Офферы
Аналитика
Система
  Источники и воркеры
  Рабочий стол
  Настройки
```

Mobile web и TMA:

```text
Сейчас | Действия | Реклама | Ещё
```

Mobile использует те же query/view-models, но собственные cards и sticky
actions. Desktop table не сжимается в узкий экран. Campaign creation остаётся
desktop-first; mobile показывает run lifecycle и только реально доступные
resume/abort actions.

## Главная страница

В первом viewport:

- здоровье и freshness источников;
- единый ranked attention feed;
- spend, base и stop;
- короткая воронка;
- running/failed/unknown actions;
- причина каждого degraded state и доступный следующий шаг.

Не дублировать один KPI в нескольких декоративных карточках. Attention feed
ранжируется по severity, money risk, freshness и actionability, а не по времени
одному.

## State semantics

```text
DataState   = ready | empty | partial | stale | unavailable
Severity    = ok | warning | critical | unknown
ActionState = queued | running | confirmed | failed | cancelled | unknown
```

- `null` — unknown; `0` — подтверждённый ноль.
- `partial`, `stale`, `unavailable` и `unknown` никогда не выглядят healthy.
- Зелёный означает только confirmed/healthy.
- Красный используется только для active danger.
- Amber — pending/degraded; gray — unknown/stale.
- Статус всегда выражен icon + label + color.
- HTTP `202` отображается как queued, не success.
- Для каждой секции видимы `state`, `as_of`, freshness, sources и issues.

## Typography and interaction

- Основной текст: 16 px.
- Вторичный текст: 14 px.
- Служебный текст: минимум 12 px.
- Touch target: минимум 44×44 px.
- Focus indicator контрастный и не обрезается containers.
- Keyboard order следует visual order.
- Hover-only информация обязательно доступна по focus и touch.
- Reduced motion отключает декоративные transitions, но не feedback.
- Layout остаётся рабочим при 200% zoom и high contrast.

Плотность достигается компоновкой, а не микротекстом. Длинные имена, крупные
денежные значения и локализованные labels тестируются как штатные состояния.

## Charts

Web/mobile-web используют Recharts; TMA — лёгкий SVG renderer над общей chart
model. Каждый график находится внутри `AccessibleChartFrame` и содержит:

- title, timezone, source, `as_of`, completeness;
- короткую текстовую summary;
- keyboard/touch tooltip;
- HTML-таблицу «Данные»;
- явное состояние empty/partial/stale/unavailable.

Пропуск данных — разрыв, не ноль.

Spend chart:

- actual — solid;
- base — dashed;
- stop — danger dashed;
- current-time marker;
- единицы и timezone видимы без tooltip.

Funnel:

```text
Clicks → Registrations → FTD → Confirmed deposits
```

Для каждого шага показываются count, conversion и cost. Sankey не используется:
линейная воронка легче сравнивается на desktop и mobile.

Daypart: desktop 7×24, mobile — выбранный день ×24. Таблица данных обязательна.
Analytics по умолчанию показывает семь колонок; дополнительные группы доступны
через presets «Экономика», «Воронка» и «Доставка».

## Actions and incidents

- Денежное действие — максимум два осознанных нажатия.
- Confirmation показывает объект, масштаб, ожидаемый эффект и freshness данных.
- После submit UI показывает lifecycle, не optimistic success.
- `UNKNOWN` содержит reconciliation path и блокирует слепой повтор.
- Sticky mobile action не перекрывает safe area или контент.
- Incident detail сохраняет correlation ID для diagnostics, но не показывает
  raw traceback/secret.

## Telegram Mini App

TMA всегда тёмная. Обязательны:

- `safeAreaInset` и `contentSafeAreaInset`;
- `viewportStableHeight`;
- Telegram BackButton;
- activation/visibility events;
- server-side validation initData;
- opaque navigation token вместо raw entity/action IDs в URL.

`initDataUnsafe` разрешён только для display. Authorization всегда подтверждает
backend.

## Responsive breakpoints

Проверяемые viewports: 360, 390, 430, 768, 1280, 1440 и 1920 px.

- На 360 px нет horizontal page scroll.
- Navigation, filters, table/cards, dialog, chart и sticky actions доступны с
  keyboard/touch.
- Mobile cards получают те же значения и semantics, что desktop rows.
- Смена viewport не сбрасывает action lifecycle или query state.

## Performance budgets

- Web initial JS: не более 250 KB gzip.
- TMA initial JS: не более 160 KB gzip.
- Fonts: не более 100 KB.
- Operator snapshot: не более 100 KB gzip.
- LCP <2.5 s, INP <200 ms, CLS <0.1.

Используются variable WOFF2 Cyrillic/Latin. TMA не включает Recharts. Query
updates адресные; 15-секундный global sweep и broad invalidations запрещены.

## Acceptance

Storybook покрывает:

- ready, empty, partial, stale, unavailable;
- known zero и unknown;
- long names и large amounts;
- chart gaps;
- safe areas и reconnect.

Playwright проверяет все viewports, navigation, dialogs, keyboard и отсутствие
horizontal scroll. Дополнительно обязательны 200% zoom, reduced motion и high
contrast. VoiceOver/Safari, TalkBack/Telegram Android и Telegram iOS/Desktop
остаются ручными gates и не могут быть заменены Chromium snapshots.
