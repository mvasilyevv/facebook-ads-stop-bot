# Feature Design Document: страница «История заливов»

> Дата: 2026-04-03
> Команда: UX-дизайнер, Frontend-разработчик, Backend-аналитик, Медиабаер

---

## 1. Use Cases

### P0 — Must have для MVP

| # | Сценарий | Вопрос баера | Решение | Данные |
|---|----------|-------------|---------|--------|
| UC-1 | **Утренняя сводка за вчера.** Баер открывает дашборд утром, вчерашний залив обнулился. | «Сколько потратил вчера, сколько депов, реальный CPA?» | Продолжать с текущими бюджетами или корректировать | spend, deposits, regs, leads, spend_per_dep, ROAS. Группировка по офферам. Дельта к позавчера |
| UC-2 | **Сравнение офферов за период.** 3 оффера параллельно — какой приносит деньги? | «Какой оффер самый прибыльный за неделю? Где CPA выходит за payout?» | Перераспределить бюджет: увеличить прибыльный, урезать убыточный | Группировка по offer_code: spend, deposits, spend_per_dep, ROAS, profit = deps × payout − spend |
| UC-3 | **Анализ крео.** 10-15 объявлений в оффере, часть отключены. Какие давали результат? | «Какие объявления дали лучший CPA? Что масштабировать?» | Дублировать рабочие крео, не повторять неудачные | Список объявлений: spend, clicks, leads, regs, deps, CPC, CPL, CPR, CTR, alert_state |

### P1 — Важно

| # | Сценарий | Вопрос баера | Решение | Данные |
|---|----------|-------------|---------|--------|
| UC-4 | **Аудит бота — «не отключил ли зря?»** Мало депов, подозрение на ложные стопы | «Какие объявления бот отключил, по какому правилу, при каком расходе?» | Скорректировать пороги стоп-правил или пересоздать объявление | DisableTask (SUCCEEDED) + metrics на момент отключения + rule_codes + chain EARLY→WARNING→STOP→DISABLED |
| UC-5 | **Недельный отчёт для тимлида.** Сколько потрачено, заработано, тренд | «Какая динамика расхода и депов за неделю? CPA растёт или падает?» | Обосновать увеличение бюджета или объяснить просадку | Таблица по дням + totals + линейный график. Источник: CabinetDayArchive |
| UC-6 | **Оценка перед масштабированием.** Оффер 3 дня хорошо — увеличить x2? | «Стабилен ли CPA или скачки? Это не случайный всплеск?» | Увеличивать плавно или подождать | CPA по дням, конверсия reg→dep, min/max/avg, коэффициент вариации |

### P2 — Nice to have

| # | Сценарий | Вопрос баера |
|---|----------|-------------|
| UC-7 | **Анализ ранних сигналов.** Процент early_signal → STOP vs early_signal → NORMAL | «Ранние сигналы полезны или создают шум?» |

---

## 2. Page Layout

### Главная страница History

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ИСТОРИЯ ЗАЛИВОВ                                                        │
├─────────────────────────────────────────────────────────────────────────┤
│ ┌─ФИЛЬТРЫ (sticky top) ────────────────────────────────────────────┐   │
│ │ [Сегодня|7д|30д|Custom ▾]  [Оффер ▾]  [Кампания ▾]  [Сбросить] │   │
│ └───────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│ ┌─KPI──────┐ ┌─KPI──────┐ ┌─KPI──────┐ ┌─KPI──────┐ ┌─KPI──────┐    │
│ │ РАСХОД    │ │ ЛИДЫ     │ │ РЕГИ     │ │ ДЕПОЗИТЫ │ │ ROAS     │    │
│ │ $4,521    │ │ 312      │ │ 45       │ │ 12       │ │ 2.1x     │    │
│ │ ↑12%      │ │ ↑8%      │ │ ↓3%      │ │ ↑25%     │ │ ↑18%     │    │
│ └───────────┘ └───────────┘ └───────────┘ └───────────┘ └───────────┘    │
│                                                                         │
│ ┌─ТРЕНДЫ ───────────────────────────────────────────────────────────┐  │
│ │  Табы: [Spend] [Leads] [Deps] [CPL] [CPR] [ROAS]                 │  │
│ │  ┌─────────────────────────────────────────────────────────┐      │  │
│ │  │ $                                              Leads    │      │  │
│ │  │ ╱╲    ╱╲                                ●──●           │      │  │
│ │  │╱  ╲──╱  ╲──╱╲___                  ●──●      ●         │      │  │
│ │  │ 25.03  26.03  27.03  28.03  29.03  30.03  31.03       │      │  │
│ │  └─────────────────────────────────────────────────────────┘      │  │
│ │  Hover → тултип с полными метриками дня                           │  │
│ └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│ ┌─ТАБЛИЦА КАМПАНИЙ ──────────────────────────────────────────────────┐ │
│ │ Кампания▾        Расход▾  Лиды  Реги  Деп▾  CPL    ROAS▾        │ │
│ │ ────────────────────────────────────────────────────────────────── │ │
│ │ ► CR2|DRC|MV     $1,200   89    12    4     $13.48  3.2x        │ │
│ │ ► BR1|MXN|KD     $980     67    8     2     $14.63  1.8x        │ │
│ │ ► TZ3|NGN|AB     $850     55    6     1     $15.45  0.9x  ■RED  │ │
│ │ ► = клик → drill-down кампании                                    │ │
│ └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│ ┌─TIMELINE СОБЫТИЙ ─────────────────────────────────────────────────┐ │
│ │ Фильтр: [Все] [Алерты] [Действия]                                │ │
│ │                                                                    │ │
│ │ ● 14:23  STOP      ad#123 Tyver1    spend_no_leads               │ │
│ │ ✕ 13:45  DISABLED  ad#456 Tyver2    по запросу @mark             │ │
│ │ ▲ 12:10  WARNING   ad#321 Promo3    high_cpl                     │ │
│ │ ✓ 11:00  ENABLED   ad#789 BestAd    рекомендация бота            │ │
│ │                                        [Показать ещё]             │ │
│ └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

### Drill-down: кампания

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ← История    CR2 | DRC | MV | Tyver | 25.03    Оффер: DRC_CR2        │
├─────────────────────────────────────────────────────────────────────────┤
│ ┌─KPI─────┐ ┌─KPI─────┐ ┌─KPI─────┐ ┌─KPI─────┐ ┌─KPI─────┐        │
│ │ $1,200   │ │ 89 лидов│ │ 4 деп   │ │ $13.48  │ │ 3.2x    │        │
│ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘        │
│                                                                         │
│ ┌─ТРЕНД КАМПАНИИ (spend + deps по дням) ─────────────────────────┐    │
│ └─────────────────────────────────────────────────────────────────┘    │
│                                                                         │
│ ┌─ОБЪЯВЛЕНИЯ ────────────────────────────────────────────────────────┐ │
│ │ Объявление▾      Статус    Расход▾  Деп  CPL    Алерты           │ │
│ │ ► ad#123 Tyver1  ●STOP     $450    2    $15.00  3                │ │
│ │ ► ad#456 Tyver2  ✕ОТКЛ     $380    1    $13.57  2                │ │
│ │ ► ad#789 Tyver3  ●НОРМА    $370    1    $11.94  0                │ │
│ └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│ ┌─TIMELINE КАМПАНИИ ─────────────────────────────────────────────────┐ │
│ │ 01.04 14:23  ● STOP     ad#123  spend_no_leads                   │ │
│ │ 01.04 13:45  ✕ DISABLED ad#456  по запросу                       │ │
│ │ 31.03 10:12  ▲ WARNING  ad#123  high_cpl                         │ │
│ └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

### Drill-down: объявление

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ← Кампания    ad#123 Tyver1                                           │
│                Кампания: CR2|DRC|MV    Оффер: DRC_CR2    ●STOP_SENT    │
├─────────────────────────────────────────────────────────────────────────┤
│ ┌─МЕТРИКИ ПО ДНЯМ ──────────────────────────────────────────────────┐  │
│ │ Дата     Расход  Лиды  Реги  Деп  CPL     CPR    Статус         │  │
│ │ 01.04   $120    8     1     0    $15.00  —      ●STOP           │  │
│ │ 31.03   $110    10    2     1    $11.00  $110   ▲WARNING        │  │
│ │ 30.03   $95     7     1     0    $13.57  —      ●НОРМА          │  │
│ └────────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│ ┌─ТРЕНД ОБЪЯВЛЕНИЯ (spend + CPL, dual-axis Recharts) ─────────────┐   │
│ └──────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│ ┌─ПОЛНАЯ ИСТОРИЯ СОБЫТИЙ ────────────────────────────────────────────┐ │
│ │ 01.04 14:23  ● STOP_SENT      spend_no_leads   $120, 0 деп      │ │
│ │ 01.04 14:20  ▲ WARNING_SENT   high_cpl         CPL=$15.00       │ │
│ │ 31.03 16:00  ● STOP_SENT      high_cpr         CPR=$110         │ │
│ │ 31.03 10:12  ▲ WARNING_SENT   high_cpl         CPL=$11.00       │ │
│ └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│ ┌─ПОРОГИ ОФФЕРА (текущие) ──────────────────────────────────────────┐ │
│ │ spend_no_leads: $50 (W: $40)   high_cpl: $20 (W: $16)           │ │
│ │ high_cpr: $150 (W: $120)       regs_no_dep: 5 (W: 4)            │ │
│ └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Компоненты React

### Фильтры

| Компонент | Новый | Props | Источник данных |
|-----------|-------|-------|-----------------|
| `HistoryFilters` | Да | `filters, onChange, offers, campaigns` | — (UI-контейнер) |
| `DateRangePicker` | Да | `from, to, onChange` | — (нативный input[type=date] + пресеты) |
| `OfferSelector` | Да | `offers, selected, onChange` | GET /offers (существующий `getOffers`) |
| `CampaignSelector` | Да | `campaigns, selected, onChange` | Из данных HistoryPage |

### KPI и графики

| Компонент | Новый | Props | Источник данных |
|-----------|-------|-------|-----------------|
| `HistoryKPIStrip` | Да | `summary` | GET /history/summary |
| `SpendTrendChart` | Да | `data` | GET /history/timeline |
| `MetricsTrendChart` | Да | `data` | GET /history/timeline |
| `FunnelComparisonChart` | Да | `data` | GET /history/summary |

### Таблицы и timeline

| Компонент | Новый | Props | Источник данных |
|-----------|-------|-------|-----------------|
| `HistoryCampaignTable` | Да | `data, sortKey, sortDir, onSort, onSelect` | GET /history/campaigns |
| `HistoryAdTable` | Да | `data, sort, onSort, page, onPage, total` | GET /history/ads |
| `EventTimeline` | Да | `events` | GET /history/events |

### Drill-down

| Компонент | Новый | Props | Источник данных |
|-----------|-------|-------|-----------------|
| `CampaignDetailPanel` | Да | `campaignName, dateRange, onClose` | GET /history/campaigns?campaign=X |
| `AdDetailPanel` | Да | `fbAdId, onClose` | GET /ads/{fbAdId}/timeline (существующий) |

**Переиспользуемое:** StateIcon (иконки состояний), ALERT_STATE_LABELS, COLORS из SpendAlertsChart, паттерн сортировки из CampaignBreakdownTable, useRefreshOnResume.

### State Management

`useState` в `HistoryPage` — достаточно (нет глубокого prop drilling, макс 2 уровня).

```
filters: { dateFrom, dateTo, offerCodes[], campaignNames[] }
summary, timeline, campaigns, events — данные от API
selectedCampaign, selectedAd — drill-down state
```

Кеширование: `useRef` с Map (ключ = сериализованные фильтры, TTL 5 мин, макс 10 записей).

### Файловая структура

```
frontend/src/
  pages/HistoryPage.jsx
  components/history/
    HistoryFilters.jsx
    DateRangePicker.jsx
    OfferSelector.jsx
    CampaignSelector.jsx
    HistoryKPIStrip.jsx
    SpendTrendChart.jsx
    MetricsTrendChart.jsx
    FunnelComparisonChart.jsx
    HistoryCampaignTable.jsx
    HistoryAdTable.jsx
    EventTimeline.jsx
    CampaignDetailPanel.jsx
    AdDetailPanel.jsx
  hooks/
    useDebouncedValue.js
    useFilterCache.js
```

---

## 4. API-спецификация

### Новый роутер: `apps/api/routers/history.py`

| Метод | Путь | Параметры | Response | Описание |
|-------|------|-----------|----------|----------|
| GET | `/history/summary` | `date_from, date_to, offer_code?` | `HistorySummarySchema` | Агрегаты за период: spend, leads, regs, deps, CPL, CPR, ROAS, дельта к предыдущему периоду |
| GET | `/history/timeline` | `date_from, date_to, offer_code?, metric?` | `list[TimelinePoint]` | Данные для графика: [{date, value, metric}] по дням |
| GET | `/history/campaigns` | `date_from, date_to, offer_code?, sort_by?, sort_dir?` | `list[CampaignRow]` | Таблица кампаний с агрегатами |
| GET | `/history/offers` | `date_from, date_to` | `list[OfferSummary]` | Сводка по офферам: spend, deps, ROAS, profit |
| GET | `/history/ads/{fb_ad_id}` | — | `AdDetailSchema` | Полная история объявления: snapshot + alerts + actions |
| GET | `/history/events` | `date_from, date_to, offer_code?, event_type?, limit?, offset?` | `EventsPage` | Лента событий (UNION alert + disable + enable) |

### Функции в api.js

```javascript
getHistorySummary({ date_from, date_to, offer_codes })
getHistoryTimeline({ date_from, date_to, offer_codes, metric })
getHistoryCampaigns({ date_from, date_to, offer_codes, sort_by, sort_dir })
getHistoryOffers({ date_from, date_to })
getHistoryAdDetail(fbAdId)
getHistoryEvents({ date_from, date_to, offer_codes, event_type, limit, offset })
```

---

## 5. Data Model Changes

### Решение: AdSnapshotHistory НЕ нужна

Существующих данных достаточно:
- **CabinetDayArchive** — daily granularity (spend, leads, deps, CPA по дням и кампаниям)
- **AlertEvent.metrics_json** — point-in-time метрики при каждом алерте
- **DisableTask/EnableTask** — полный lifecycle действий

2.9M строк/месяц при ~100 объявлениях — избыточная нагрузка без уникальной ценности.

### Изменения

1. **Новое поле** `offer_stats_json JSONB` в `cabinet_day_archives` — per-offer агрегаты

2. **Новые индексы:**
```sql
CREATE INDEX ix_cabinet_day_archive_range
  ON cabinet_day_archives(started_at, ended_at);
CREATE INDEX ix_alert_event_ad_timeline
  ON alert_events(fb_ad_id, created_at DESC);
CREATE INDEX ix_disable_task_ad_timeline
  ON disable_tasks(fb_ad_id, created_at DESC);
CREATE INDEX ix_enable_task_ad_timeline
  ON enable_tasks(fb_ad_id, created_at DESC);
```

3. **Retention:** 90 дней. **Кеширование:** `Cache-Control: max-age=300` для завершённых дней.

---

## 6. MVP Scope

### MVP (1 спринт)

- [ ] Страница HistoryPage + route /history в sidebar
- [ ] Фильтры: период (пресеты + custom), оффер
- [ ] KPI-полоса: расход, лиды, реги, депозиты, ROAS
- [ ] График трендов по дням (spend + deposits, Recharts)
- [ ] Таблица кампаний с сортировкой + клик на drill-down
- [ ] Drill-down кампании: KPI + таблица объявлений
- [ ] Timeline событий (алерты + disable/enable)
- [ ] Backend: роутер /history (summary, timeline, campaigns, events)
- [ ] Alembic миграция (offer_stats_json + индексы)

### Откладывается на v2

- Drill-down объявления (отдельный экран)
- Фильтр по кампании (каскадный селектор)
- Сравнение периодов (дельта)
- Эндпоинт /history/offers
- Тепловая карта, lazy loading, виртуальный скролл

---

## 7. Ценность для баера

1. **-20 мин каждое утро** — сводка за вчера в один клик
2. **Выявление убыточных офферов за 1 мин** — profit по офферам без ручных таблиц
3. **Предотвращение слепого масштабирования** — стабильность CPA по дням
4. **Обнаружение ложных стопов** — аудит отключений, каждый ложный стоп = $20-50 потери
5. **Готовый отчёт для тимлида** — таблица по дням в 2 клика
6. **Осмысленная настройка правил** — видно какие правила срабатывают и с каким исходом
7. **Быстрая ротация крео** — понимание какие объявления работали лучше

---

## 8. Антипаттерны

1. Не показывать все 15 метрик сразу — по умолчанию 4: spend, deps, spend_per_dep, ROAS
2. Не дублировать live-дашборд — история про завершённые дни
3. Не делать бесконечный скролл без фильтров
4. Не путать «залив» и «календарный день» — использовать CabinetDayArchive
5. Не прятать убытки — ROAS < 1x подсвечивать красным
6. Не добавлять сложную визуализацию ради красоты
7. Не показывать сырые данные (JSON, tokens, UUIDs)
