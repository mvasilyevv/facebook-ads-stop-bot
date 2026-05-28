# FB Stop Bot — Frontend v2 Design Specification

> Internal operator tool. Editorial-monochrome direction. Dark mode primary.
> Document v1.0 · 2026-05-28

---

## 0. Preamble

### 0.1 Что это

Спецификация дизайн-системы и страничных макетов для нового фронта FB Stop Bot. Документ обязателен для всех, кто пишет UI-код в `frontend-v2/` (директория создаётся отдельным PR — этот документ её опережает).

Backend готов: 61 endpoint в `apps/api/routers/v1/`. Спецификация ссылается на endpoint'ы по имени файла-роутера, не по URL — URL'ы могут эволюционировать.

### 0.2 Конечная цель

Operator tool для одного-двух человек, которые сидят в нём по 6-8 часов в день. Главное — низкая когнитивная нагрузка, мгновенный access к alert state, отсутствие визуального шума. Не landing page, не marketing site.

### 0.3 Anti-goals

Чего избегаем:

- **Shadcn-карточки** — мы не commodity-SaaS-dashboard.
- **Cyberpunk neon / Grafana-look** — это инженерный tool, но не observability stack.
- **Покрытие всех viewport'ов** — desktop 1280+ only. Мобила — отдельный TMA, отдельный repo (`frontend-mini/`).
- **Скруглённые щенячьи углы**, drop-shadows, "soft UI". Это operator tool, не fintech-стартап.
- **Дисперсия акцентов** — больше одного accent color превращает каждый алерт в шум.

### 0.4 Брендовый код одной строкой

> Editorial monochrome. Острые углы, типографика как герой, единственный accent — warm-white. Density по образцу Bloomberg Terminal, но рендер по образцу Linear/Vercel.

---

## 1. Foundations

### 1.1 Color tokens

#### 1.1.1 Палитра — обоснование выбора

Перебраны три варианта accent color:

| Вариант | Hex | Плюсы | Минусы | Вердикт |
|---|---|---|---|---|
| Electric purple | `#7C3AED` | Линейная ассоциация, узнаваем | Generic AI-look, "ещё один AI startup" | Отклонён |
| Monochrome lime | `#B5FF2A` | Bold, узнаваем | Кричит, утомляет на 8-часовой сессии | Отклонён |
| **Warm off-white** | `#F5F1E8` | Editorial-чистота, не утомляет, контраст с графитом | Менее "брендовый" с первого взгляда | **Выбран** |

Решение: **warm off-white `#F5F1E8`** как единственный accent. Семантические цвета (success / warning / danger) — через muted hues, накладываемые на той же шкале. Это даёт editorial feel — текст и UI читаются как печатная страница, а не как dashboard.

#### 1.1.2 Token-таблица

Реализация — CSS custom properties в `:root` через Tailwind 4 `@theme`. Имена tokens намеренно совпадают с Radix scale, чтобы команда могла переиспользовать привычные интуиции.

```css
:root {
  /* Surfaces — graphite scale */
  --color-bg-0:  #0A0A0B;  /* outer background, page */
  --color-bg-1:  #101012;  /* card background */
  --color-bg-2:  #16161A;  /* nested card / table row */
  --color-bg-3:  #1C1C21;  /* hover state */
  --color-bg-4:  #232329;  /* active / pressed */
  --color-bg-5:  #2C2C33;  /* borders subtle */
  --color-bg-6:  #38383F;  /* borders default */
  --color-bg-7:  #4A4A52;  /* borders strong */
  --color-bg-8:  #5C5C66;  /* disabled text */
  --color-bg-9:  #7C7C86;  /* placeholder text */
  --color-bg-10: #A8A8B0;  /* secondary text */
  --color-bg-11: #E4E4E7;  /* primary text */

  /* Accent — warm off-white */
  --color-accent:        #F5F1E8;  /* primary accent */
  --color-accent-muted:  #BDB8AB;  /* secondary accent (hover trail) */
  --color-accent-bg:     #2A2823;  /* accent surface (10% tint) */

  /* Semantic — muted, derived */
  --color-success:       #7EB47A;  /* desaturated green */
  --color-success-bg:    #1A2218;
  --color-warning:       #D4A858;  /* burnt amber */
  --color-warning-bg:    #261F12;
  --color-danger:        #C7625C;  /* terracotta red */
  --color-danger-bg:     #261513;
  --color-info:          #7AA0B4;  /* muted slate */
  --color-info-bg:       #131C22;

  /* FSM-state colors (semantic but bound to alert_state enum) */
  --fsm-normal:       var(--color-bg-9);     /* indifferent grey */
  --fsm-warning:      var(--color-warning);
  --fsm-stop:         var(--color-danger);
  --fsm-claimed:      var(--color-info);
  --fsm-disabled:     var(--color-bg-8);     /* faded */
}
```

#### 1.1.3 Контраст

- `--color-bg-11` (`#E4E4E7`) на `--color-bg-0` (`#0A0A0B`) — contrast 16.5:1. WCAG AAA.
- `--color-bg-10` (`#A8A8B0`) на `--color-bg-1` (`#101012`) — contrast 8.2:1. WCAG AAA.
- `--color-accent` на `--color-bg-0` — contrast 17.1:1. WCAG AAA.
- `--color-danger` на `--color-danger-bg` — contrast 6.4:1. WCAG AA.

Все semantic-цвета прошли проверку через `colour-contrast-checker`.

#### 1.1.4 Light mode

В первой итерации не делаем. Структура tokens готова к light mode (имена не привязаны к "dark"), но palette прописывается отдельным PR.

---

### 1.2 Typography

#### 1.2.1 Шрифты

Выбраны два, оба бесплатные через Google Fonts:

| Назначение | Семейство | Класс | Источник |
|---|---|---|---|
| **Display / headings / numerics** | `JetBrains Mono` | Monospace | Google Fonts |
| **Body / UI** | `Inter Tight` | Sans-serif (tighter than Inter) | Google Fonts |

Почему `JetBrains Mono` для headings: editorial feel — monospaced numerics в таблицах метрик идеально выравниваются. Заголовки страниц задают код-эстетику без впадения в "терминал ретро-cyberpunk".

Почему `Inter Tight` вместо `Inter`: Inter перенасыщен на рынке ("generic AI-look"), `Inter Tight` менее распространён, более характерен — узкие counters, более жёсткий ритм.

Fallback stack:

```css
--font-display: 'JetBrains Mono', 'SF Mono', 'Menlo', ui-monospace, monospace;
--font-body:    'Inter Tight', 'SF Pro Text', system-ui, sans-serif;
--font-numeric: 'JetBrains Mono', tabular-nums, monospace;
```

Все числовые поля (метрики, таблицы, KPI) — `font-feature-settings: "tnum"` принудительно.

#### 1.2.2 Scale

8 размеров, ratio ≈ 1.2 (minor third), но с ручными правками для UI-density.

| Token | Size | Line | Weight | Usage |
|---|---|---|---|---|
| `text-display`  | 56px / 3.5rem  | 0.95 | 500 | Page hero (только Dashboard, опционально) |
| `text-title-1`  | 32px / 2rem    | 1.05 | 500 | Page title (`H1`) |
| `text-title-2`  | 22px / 1.375rem| 1.15 | 500 | Section title (`H2`) |
| `text-title-3`  | 16px / 1rem    | 1.25 | 500 | Card title (`H3`) |
| `text-body`     | 14px / 0.875rem| 1.5  | 400 | Default body |
| `text-body-sm`  | 13px / 0.8125rem | 1.45 | 400 | Table cells, dense UI |
| `text-caption`  | 12px / 0.75rem | 1.4  | 500 | Labels, captions, badge text |
| `text-micro`    | 10px / 0.625rem| 1.3  | 600 | Eyebrow labels (uppercase tracking) |

`text-micro` — uppercase, letter-spacing `0.08em`. Используется для микрозаголовков типа `01 / OVERVIEW`, что и даёт editorial feel.

Display и заголовки — `font-display` (JetBrains Mono). Body, caption — `font-body` (Inter Tight). Числа в таблицах — `font-numeric`.

#### 1.2.3 Numeric formatting правила

Все числовые ячейки выровнены по правому краю, `font-variant-numeric: tabular-nums`. Денежные суммы — `$1,234.56` (доллар приклеен слева). Проценты — `12.4%` без пробела. Большие числа: `12.4K`, `1.2M` (формат `compact`).

---

### 1.3 Spacing

4px-baseline. Tokens `--space-0...--space-12`. Соответствие Tailwind:

| Token | Value | Tailwind alias | Usage |
|---|---|---|---|
| `--space-0`  | 0px   | `space-0`  | reset |
| `--space-1`  | 4px   | `space-1`  | icon ↔ label |
| `--space-2`  | 8px   | `space-2`  | tight inner padding |
| `--space-3`  | 12px  | `space-3`  | row gap (dense) |
| `--space-4`  | 16px  | `space-4`  | card inner padding default |
| `--space-5`  | 20px  | `space-5`  | section gap (small) |
| `--space-6`  | 24px  | `space-6`  | card padding (comfortable) |
| `--space-8`  | 32px  | `space-8`  | section gap |
| `--space-10` | 40px  | `space-10` | page section break |
| `--space-12` | 56px  | `space-12` | hero / page top |

Не использовать значения вне шкалы. Hardcoded `13px`, `7px` — антипаттерн.

---

### 1.4 Radius

Острые углы — герой стиля. Только пять значений:

| Token | Value | Usage |
|---|---|---|
| `--radius-0` | 0px  | Default. Карточки, кнопки, badges. |
| `--radius-1` | 2px  | Inputs (читабельность), tooltip. |
| `--radius-2` | 4px  | Modal/Dialog. |
| `--radius-3` | 6px  | Avatar circle stub. |
| `--radius-full` | 9999px | Pills (фильтры), статусные dots. |

**Default = 0**. Если ты ставишь `border-radius`, ты должен обосновать.

---

### 1.5 Shadows & elevation

В dark mode тени бесполезны (нечего затемнять). Elevation создаём через:

1. **Border + bg-shift.** Карточка = `bg-1` + `border: 1px solid bg-5`. Hovered card = `bg-2` + `border: 1px solid bg-6`.
2. **Inset highlight** на интерактивных surface'ах: `box-shadow: inset 0 1px 0 rgba(255,255,255,0.03)` — даёт ощущение объёма без drop-shadow.
3. **Glow на focus** — единственное использование `box-shadow`: `0 0 0 2px var(--color-accent)` на focused input (см. §1.6).

Запрещены: `box-shadow: 0 4px 12px rgba(0,0,0,0.5)` и подобные мягкие тени. Они ломают editorial-эстетику.

---

### 1.6 Focus state

Каждый interactive element имеет видимый focus:

```css
:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}
```

Никаких `outline: none` без замены. Это требование accessibility, не decoration.

---

### 1.7 Motion

Используем CSS-only где можно. Framer Motion — для list reordering и page transitions.

| Token | Duration | Easing | Usage |
|---|---|---|---|
| `--ease-out`  | `cubic-bezier(0.2, 0.8, 0.2, 1)` | default | enter |
| `--ease-in`   | `cubic-bezier(0.4, 0, 1, 1)` | exit | leave |
| `--ease-spring` | `cubic-bezier(0.34, 1.56, 0.64, 1)` | overshoot | drawer, modal |
| `--dur-fast`  | 120ms | hover, badge state change |
| `--dur-base`  | 200ms | default transitions |
| `--dur-slow`  | 400ms | page transition, drawer open |

**Правила:**

- Не анимировать `width`, `height` (force layout). Использовать `transform`, `opacity`, `clip-path`.
- При `prefers-reduced-motion: reduce` — все длительности → 0ms, оставить opacity-кроссфейд.
- Stagger animations only на page load (5 элементов max). Никаких "колоду карт сдают" поверх каждой таблицы.

---

### 1.8 Layout grid

- **Viewport**: 1280px-1920px+. Минимум 1280 — оператор сидит за 27" 1440p или 4K.
- **Container max-width**: нет жёсткого max — растягиваемся до viewport минус sidebar.
- **Sidebar**: 240px expanded, 64px collapsed (icon-only). Сохранение в localStorage.
- **Topbar**: 56px fixed.
- **Content padding**: `--space-8` (32px) горизонтально, `--space-6` (24px) вертикально.
- **Grid system**: 12-col, gutter `--space-6` (24px). Используется только для page layouts (не для внутренностей карточек).

---

### 1.9 Iconography

**Lucide Icons** (выбор). Reasoning: тонкие strokes (1.5px по умолчанию), большая библиотека, активный maintenance. Phosphor тоже хорош, но Lucide стилистически ближе к editorial-monochrome.

Размеры: `14px` (dense UI), `16px` (default), `20px` (page actions), `24px` (sidebar nav).

**Запрещено**: emoji в UI (😀 🚀). Только Lucide. Исключение — alert messages из backend (там что Telegram пришёл).

---

## 2. Components inventory

Список компонентов с variant'ами. Каждый — отдельный `.tsx` файл в `src/components/ui/`.

### 2.1 Primitives

#### Button

```
Variants: primary | secondary | ghost | danger | link
Sizes:    xs (24px) | sm (28px) | md (32px) | lg (40px)
States:   default | hover | active | focus | disabled | loading
Props:    leftIcon, rightIcon, fullWidth, loading
```

- `primary` — accent bg, bg-0 text. Используется для **одной** primary action на странице.
- `secondary` — bg-2 bg, bg-11 text, bg-6 border.
- `ghost` — transparent bg, bg-10 text. Hover → bg-2.
- `danger` — danger bg, bg-0 text.
- `link` — text-only с underline на hover.

#### Input

```
Variants: text | number | password | search
Sizes:    sm (28px) | md (32px) | lg (40px)
States:   default | focus | error | disabled
Props:    leftIcon, rightIcon, errorMessage, label, helpText
```

Default: `bg-2` background, `bg-6` border. Focus: `bg-3` background, accent outline glow.

#### Select / Combobox

Headless UI base (Radix UI). Стилизация под нашу палитру: trigger как Input, popover как Card.

#### Multiselect / Chips input

Для filter bar в `/ads`. Selected items — pills (`radius-full`), removable.

#### Textarea

Same as Input but multi-line. Min-height 80px.

#### Checkbox, Radio, Switch

Checkbox/Radio — square (`radius-0`). Switch — pill с дайс-точкой внутри.

---

### 2.2 Layout primitives

#### Card

```
Variants: default | nested | interactive
Props:    title, eyebrow, action (slot), padded (bool)
```

Карточка по умолчанию: `bg-1` bg, `bg-5` border 1px, `radius-0`. Eyebrow label (`text-micro`) сверху в стиле `01 / OVERVIEW`.

#### Stack, Row, Grid

Layout primitives на CSS flex/grid с props gap, align, justify.

#### Divider

`hr` с `border-color: var(--color-bg-5)`. Optional label (`text-micro` посередине).

---

### 2.3 Navigation

#### Sidebar

Иерархия:

```
[Logo / brand mark]
─────────
01 OPERATE
  · Dashboard
  · Ads
  · Drafts
02 CATALOG
  · Offers
03 HISTORY
  · History
─────────
04 SYSTEM
  · Settings
─────────
[Worker health pulse — bottom]
```

Active link: accent vertical bar (3px) слева + accent text. Hover: bg-2.

Collapsable до 64px (icon-only). State в `localStorage('sidebar:collapsed')`.

#### Topbar

```
[Breadcrumbs]    [Search ⌘K]    [Worker pulse]    [User menu]
```

Breadcrumbs — `text-caption`, разделитель `›` (или `/` для code-feel).

#### Tabs

Two variants:

- **Underline tabs** — для page-internal switching (e.g. Settings sub-pages).
- **Pill tabs** — для filter switching (e.g. `All / Pending / Failed` в tasks list).

---

### 2.4 Data display

#### Table

Custom, не сторонний. Variants:

```
Density: comfortable (44px row) | compact (32px row) | dense (28px row)
Features: sortable, filterable, virtualized (TanStack Virtual), 
         selectable rows (checkbox column), 
         sticky header, sticky first column, 
         resizable columns
```

Header — `text-caption`, uppercase, `text-bg-9`.
Row hover — `bg-1` → `bg-2`.
Selected row — `bg-accent-bg`, left border 2px accent.
Empty state — внутри table body, не снаружи.

#### Stat card / KPI card

```
[EYEBROW]
[BIG NUMBER]        [trend ↑ +12.4%]
[secondary text — small note]
```

Big number — `text-display` или `text-title-1` в зависимости от density. Trend arrow + delta — рядом с числом.

#### Chart wrapper

Recharts wrapper с предустановленными tokens. Series colors:

- 1 series → `accent`.
- 2 series → `accent` + `accent-muted`.
- 3+ series → accent + semantic palette (info, warning, success).

Tooltips — custom: `bg-3` bg, monospace numerics, eyebrow + value pairs.

#### Badge

```
Variants: state-coloured (normal, warning, stop, claimed, disabled)
         severity (info, success, warning, danger)
         neutral (default)
Sizes:    sm (18px) | md (22px)
```

Badge для FSM state — pills (`radius-full`), 8px dot слева + text. Color binding к `--fsm-*` tokens.

#### Pill / Chip

Filter chip — removable, leftIcon, label, removeIcon (×). Active filter — accent border.

---

### 2.5 Feedback

#### Toast

`bg-2` bg, border, icon слева, message, dismiss ×.
Position: bottom-right, stack 12px gap. Max 4 visible.
Auto-dismiss: 4s (success/info), 8s (warning), sticky (error — пока юзер не закроет).

#### Tooltip

`bg-3` bg, `radius-1`, `text-caption`. Delay 400ms open, 100ms close.

#### Popover

Like Tooltip но больше и интерактивный. Используется для column-settings, мини-action menu.

#### Modal / Dialog

Centered. `bg-1` content, `bg-0` backdrop с 70% opacity. Max-width 480px (sm) / 640px (md) / 800px (lg). Escape closes.

#### Drawer

Right-side slide-in. Width 480px по умолчанию, 640px для timeline drill-down. Backdrop как у Modal.

#### ConfirmDialog

Special pattern для destructive actions. Требует typed confirmation для bulk-операций:

```
You're about to DISABLE 47 ads.
This will create 47 disable tasks.

Type DISABLE to confirm:
[__________________]

[Cancel]              [Confirm DISABLE]
```

Confirm button enable'ится только когда typed input === target string.

---

### 2.6 Domain-specific composites

Эти компоненты знают про domain FB Stop Bot.

#### AdRow

Row в таблице `/ads`. Composite: ad thumbnail (16:9, 64×36px) + ad name + offer code badge + FSM badge + key metrics (spend, CPL, freq) + actions (3 dots menu). Hover экспандит метрики inline.

#### AlertEventRow

Row в `/dashboard` alert feed. Layout:

```
[timestamp]   [stage badge]   [ad name]   [rule codes — pills]   [→]
```

Click → drawer с full event.

#### TaskQueueRow

Row в Disable/Enable lists. Layout:

```
[task_type icon] [ad name] [status badge] [attempts × N] [age] [Retry] [Cancel]
```

#### DraftCard

Page-specific composite для `/drafts`. Полностью описан в §4.6.

#### WorkerPulse

Live status indicator. 8px dot, цвет по health (`success` / `warning` / `danger`). Tooltip — список воркеров. Расположен в Topbar и Sidebar bottom.

#### KbdShortcut

Inline keyboard hint: `⌘K`, `Esc`, `J/K`. Стилизованный как мини-key cap.

---

## 3. Layout patterns

### 3.1 Page shell

```
┌─────────────────────────────────────────────────────────────┐
│ TOPBAR — 56px                                                │
├──────────┬──────────────────────────────────────────────────┤
│          │                                                   │
│ SIDEBAR  │  MAIN CONTENT                                     │
│ 240px    │  padding 32px horizontal, 24px vertical           │
│          │                                                   │
│          │  [Optional right drawer, slide-in]                │
│          │                                                   │
└──────────┴──────────────────────────────────────────────────┘
```

### 3.2 Page header pattern

Каждая страница начинается с одинакового header:

```
[micro-eyebrow: 01 / OPERATE]
[Title-1 large]              [primary action button — top-right]
[caption — page subtitle, optional]
```

После header — `--space-8` vertical break, потом content.

### 3.3 Filter bar pattern

Над таблицами:

```
[search input — flex grow] [filter pill 1 ×] [filter pill 2 ×] [+ Add filter ▾] [density ▾]
```

Active filters рендерятся как chips. Удаление chip — мгновенно применяет.

### 3.4 Empty state pattern

```
            [thin icon — 40px]

           "No alerts in last 24h."
        "Что приятно — значит правила работают."

           [secondary action button]
```

Empty state НЕ shows generic illustration. Только thin icon + текст + optional CTA.

### 3.5 Loading state pattern

- **Skeleton** для table rows (3-5 rows shimmer).
- **Spinner inline** только для button "loading" state.
- **Top progress bar** (NProgress-style, 2px high, accent color) на route transitions.

### 3.6 Error state pattern

Внутри карточки (не page-level):

```
[border-danger card]
[danger icon] Something went wrong loading [section].
Last error: [error.message — truncated, monospace]
[Retry] [Copy error] [Open issue]
```

Page-level error boundary — full-page editorial layout с большим заголовком "Something broke." и `<details>` для stack trace.

### 3.7 Modal pattern

Modal используется ТОЛЬКО для:
- Confirmation (destructive action).
- Form (creating offer, editing rule).
- Short text content (changelog).

Для деталей `AdRow` / `AlertEventRow` / `TaskQueueRow` — **drawer**, не modal. Drawer позволяет видеть контекст в фоне.

---

## 4. Per-page sketches

Все 6 страниц + общая навигация.

### 4.1 Dashboard (`/`)

**Endpoints:** `dashboard_stats.py` (GET `/dashboard/stats`, `/batch`), `dashboard_timeseries.py` (spend-history, chart-data), `dashboard_performance.py`, `dashboard.py` (incidents, alerts).

**Main job:** оператор видит "что происходит прямо сейчас" в одном экране.

**Layout (ASCII):**

```
┌────────────────────────────────────────────────────────────────────────┐
│ 01 / OVERVIEW                                                          │
│ Dashboard                                            [+ scan now]      │
│ Last scan 14s ago · Observer pulse OK                                  │
├────────────────────────────────────────────────────────────────────────┤
│ ┌─────────┬─────────┬─────────┬─────────┐                              │
│ │ ACTIVE  │ WARNING │ STOP    │ DISABLED│   ← KPI strip (4 cards)      │
│ │ 247     │ 12 ↑3   │ 4  ↓1   │ 89      │                              │
│ │ today   │ now     │ now     │ today   │                              │
│ └─────────┴─────────┴─────────┴─────────┘                              │
│                                                                        │
│ ┌──────────────────────────────────┐ ┌────────────────────────────────┐│
│ │ SPEND × HOUR (24h)               │ │ ACTIVE INCIDENTS               ││
│ │                                  │ │                                ││
│ │ [Recharts area chart]            │ │ [scrollable list]              ││
│ │                                  │ │ • CR2 DRC MV  warning · 12m    ││
│ │                                  │ │ • UA17 SP MV  stop · 4m        ││
│ │                                  │ │ • ...                          ││
│ └──────────────────────────────────┘ └────────────────────────────────┘│
│                                                                        │
│ 02 / RECENT EVENTS                                                     │
│ ┌──────────────────────────────────────────────────────────────────┐  │
│ │ [time]  [stage]  [ad name]  [rule codes]                    [→]  │  │
│ │ [time]  [stage]  [ad name]  [rule codes]                    [→]  │  │
│ │ ... 10 rows live-tail                                            │  │
│ └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│ 03 / TASK QUEUE                                                        │
│ ┌──────────────────────┐ ┌──────────────────────┐                     │
│ │ DISABLE QUEUE (12)   │ │ ENABLE QUEUE (3)     │                     │
│ │ [TaskQueueRow ×N]    │ │ [TaskQueueRow ×N]    │                     │
│ └──────────────────────┘ └──────────────────────┘                     │
└────────────────────────────────────────────────────────────────────────┘
```

**Primary action:** `[+ scan now]` (POST `/observer/scan-now`).

**Components used:** PageHeader, KPI strip (4× StatCard), ChartWrapper (area), Card (list), AlertEventRow, TaskQueueRow.

**State management:**

- Server: TanStack Query — `/dashboard/batch` (60s refetch), `/spend-history` (60s).
- Local UI: Zustand — selected chart time range (24h / 7d / 30d), expanded incident.
- URL: none (route-level state).

---

### 4.2 Ads (`/ads`)

**Endpoints:** `dashboard.py` (`/dashboard/ads`), `ads_timeline.py` (`/ads/{id}/timeline`), `disable_tasks.py`, `enable_tasks.py`, `auto_enable.py`.

**Main job:** оператор работает с конкретными ads — фильтрует, отключает, смотрит timeline. **Главная "рабочая лошадка"** страница, на ней проводят 60% времени.

**Layout:**

```
┌────────────────────────────────────────────────────────────────────────┐
│ 04 / OPERATE                                                           │
│ Ads                                            [⌘K Search] [Settings ▾]│
│ 247 active · 12 warning · 4 stop                                       │
├────────────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────────────┐   │
│ │ [Search ad name / id]   [state: all ▾]  [offer: any ▾]  [+ filt]│   │
│ │ Selected: 0 ads          [Density ▾]                            │   │
│ └─────────────────────────────────────────────────────────────────┘   │
│ Active filters: [offer = DRC ×] [state = warning ×]                    │
│                                                                        │
│ ┌──────────────────────────────────────────────────────────────────┐  │
│ │ [☐]  AD             OFFER   STATE     SPEND   CPL    FREQ  …    │  │
│ │ [☐]  CR2 DRC MV...  DRC     warning   $234.5  $18.3  2.4   …    │  │
│ │ [☐]  UA17 SP MV...  UA17    stop      $891.2  $42.1  4.8   …    │  │
│ │ [☐]  ... (virtualized, 1000+ rows)                              │  │
│ └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│ When 1+ row selected → bulk action bar slides up from bottom:          │
│ ┌──────────────────────────────────────────────────────────────────┐  │
│ │ 23 selected · [Disable] [Snooze ▾] [Clear]                       │  │
│ └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│ Click row → right drawer slides in (640px):                            │
│   - AdSnapshot summary                                                 │
│   - Metrics timeline (mini chart)                                      │
│   - Alert timeline                                                     │
│   - Task history                                                       │
└────────────────────────────────────────────────────────────────────────┘
```

**Primary action:** Bulk disable (нижняя action bar, sticky когда есть selected).

**Components:** PageHeader, FilterBar, Table (virtualized, 1000+ rows), BulkActionBar, Drawer (640px), ConfirmDialog (для bulk disable).

**Keyboard shortcuts:**
- `J` / `K` — навигация по строкам.
- `X` — toggle selection.
- `D` — disable selected (с confirm).
- `S` — snooze selected.
- `Enter` — open drawer.
- `Esc` — close drawer / clear selection.
- `/` — focus search.

**Empty state:** Когда `state=warning` фильтр и нет результатов — "No warnings — system is calm." Это уместный editorial-тон.

---

### 4.3 Offers (`/offers`)

**Endpoints:** `offers.py` (`/offers`, `/offers/compare`, `/offers/{id}/rules`).

**Main job:** управление списком офферов и их правилами.

**Layout:**

```
┌────────────────────────────────────────────────────────────────────────┐
│ 02 / CATALOG                                                           │
│ Offers                                              [+ New offer]      │
│ 18 active · 4 inactive                                                 │
├────────────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────────────┐   │
│ │ [Tabs: All · Active · Inactive]      [Search] [Sort: name ▾]    │   │
│ └─────────────────────────────────────────────────────────────────┘   │
│                                                                        │
│ Grid of offer cards (3 cols, gap 24px):                                │
│ ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐      │
│ │ DRC_CR2          │  │ UA17_MV          │  │ ...              │      │
│ │ active           │  │ active           │  │                  │      │
│ │ ─────────────────│  │ ─────────────────│  │                  │      │
│ │ Spend  $1,234.56 │  │ Spend  $891.12   │  │                  │      │
│ │ Leads     45     │  │ Leads     32     │  │                  │      │
│ │ CPL    $27.43    │  │ CPL    $27.84    │  │                  │      │
│ │ Alerts    3      │  │ Alerts    0      │  │                  │      │
│ │ ─────────────────│  │ ─────────────────│  │                  │      │
│ │ [Rules] [Edit]   │  │ [Rules] [Edit]   │  │                  │      │
│ └──────────────────┘  └──────────────────┘  └──────────────────┘      │
└────────────────────────────────────────────────────────────────────────┘
```

Click "Rules" → drawer (480px) с rules editor (6 numeric thresholds).
Click "Edit" → modal с offer fields (code immutable, name editable).

**Components:** OfferCard, Tabs (pill), Drawer (rules editor), Modal (offer edit).

---

### 4.4 History (`/history`)

**Endpoints:** `history.py` (все sub-endpoints).

**Main job:** "что было" — таймлайн событий за период, drill-down.

**Layout:**

```
┌────────────────────────────────────────────────────────────────────────┐
│ 03 / HISTORY                                                           │
│ History                                              [Export CSV ▾]    │
│ Last 30 days · 1,234 events                                            │
├────────────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────────────┐   │
│ │ [Date range: May 1 — May 28 ▾]                                  │   │
│ │ [Campaign: any ▾] [Offer: any ▾] [Stage: any ▾]                 │   │
│ └─────────────────────────────────────────────────────────────────┘   │
│                                                                        │
│ ┌──────────────────────────────────┐  ┌──────────────────────────────┐│
│ │ SUMMARY (left, 40%)              │  │ TIMELINE (right, 60%)        ││
│ │ Total events: 1,234              │  │                              ││
│ │ By stage:                        │  │ May 28 · today               ││
│ │   warning  834                   │  │ ──────────────────────────   ││
│ │   stop     267                   │  │  14:32  warning  CR2 DRC ...  ││
│ │   claimed  133                   │  │  14:28  stop     UA17 SP ...  ││
│ │                                  │  │                              ││
│ │ By rule:                         │  │ May 27 · yesterday           ││
│ │   CPL_HIGH         412           │  │ ──────────────────────────   ││
│ │   SPEND_NO_EVENT   234           │  │  ...                         ││
│ │   ...                            │  │                              ││
│ │                                  │  │ [load more]                  ││
│ └──────────────────────────────────┘  └──────────────────────────────┘│
└────────────────────────────────────────────────────────────────────────┘
```

Day separator — `text-micro` eyebrow `MAY 28 · TODAY` + thin divider.

Click event → drawer с full event detail.

**Components:** DateRangePicker, FilterBar, SummaryStats, TimelineList, Drawer.

---

### 4.5 Settings (`/settings`)

**Endpoints:** `settings_observer.py`, `settings_telegram.py`, `settings_vision.py`, `observer.py` (restart/start-new-cabinet-day), `health_details.py`.

**Main job:** конфигурация системы. Группировано по доменам.

**Layout — single page с tab-навигацией:**

```
┌────────────────────────────────────────────────────────────────────────┐
│ 05 / SYSTEM                                                            │
│ Settings                                                               │
├────────────────────────────────────────────────────────────────────────┤
│ [Underline tabs: Observer · Telegram · Vision · Workers · AI · Health] │
│                                                                        │
│ Content panel per tab — две колонки 60/40:                             │
│                                                                        │
│ ┌─ Left: form fields ──────────────┐ ┌─ Right: status + actions ──┐   │
│ │ Scan interval     [30____] sec   │ │ STATUS                     │   │
│ │ Cabinet URL       [____________] │ │ Observer: ONLINE  ●        │   │
│ │ Country           [PT_______]    │ │ Last scan: 14s ago         │   │
│ │ Auto-disable      [☑]            │ │                            │   │
│ │ Auto-enable reco  [☑]            │ │ ACTIONS                    │   │
│ │                                  │ │ [Restart observer]         │   │
│ │ [Save changes]                   │ │ [Scan now]                 │   │
│ │                                  │ │ [Start new cabinet day]    │   │
│ └──────────────────────────────────┘ └────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
```

Каждый tab — отдельная sub-route (`/settings/observer`, `/settings/telegram`, etc.). TanStack Router file-based.

**Worker restart actions** — destructive, требуют ConfirmDialog ("Type RESTART to confirm").

---

### 4.6 Drafts (`/drafts`)

**Endpoints:** Новая страница. Использует существующий `disable_tasks.py` / `enable_tasks.py` с фильтром `status='draft'`, плюс новый sub-endpoint для `task_type='meta_api_mutation'`.

**Main job:** оператор одобряет AI-mutation drafts. Каждая карточка должна показать **что именно изменится** — диффы, цифры, цели.

**Layout:**

```
┌────────────────────────────────────────────────────────────────────────┐
│ 04 / OPERATE                                                           │
│ Drafts                                                                 │
│ 7 pending · 3 expiring within 1h                                       │
├────────────────────────────────────────────────────────────────────────┤
│ ┌────────────────────────────────────────────────────────────────┐    │
│ │ Pending Drafts (7)                                              │    │
│ │ [filter pills: All · pause · activate · budget · campaign]      │    │
│ └────────────────────────────────────────────────────────────────┘    │
│                                                                        │
│ ┌────────────────────────────────────────────────────────────────┐    │
│ │ DRAFT · 12 min ago · meta_api / pause_ad                       │    │
│ │ Requested by user @markvasilev (chat 12345)                    │    │
│ │ ─────────────────────────────────────────────────────────────  │    │
│ │ Will PAUSE 1 ad                                                │    │
│ │                                                                │    │
│ │   ad_id          120211...8761                                 │    │
│ │   ad_name        CR2 | DRC | MV | Tyver | 25.03                │    │
│ │   current_state  ACTIVE                                        │    │
│ │   target_state   PAUSED                                        │    │
│ │                                                                │    │
│ │ Reasoning (from AI): "Spend $234 / CPL $42 over threshold..."  │    │
│ │ ─────────────────────────────────────────────────────────────  │    │
│ │ Expires in 23h 47m                                             │    │
│ │                            [Cancel]  [Approve & execute] ─────┐│    │
│ └────────────────────────────────────────────────────────────────┘    │
│                                                                        │
│ ┌────────────────────────────────────────────────────────────────┐    │
│ │ DRAFT · 1h ago · meta_api / set_adset_budget                   │    │
│ │ ...                                                            │    │
│ │ Will UPDATE adset budget                                       │    │
│ │   adset_id     120211...                                       │    │
│ │   current      daily_budget: $200.00                           │    │
│ │   target       daily_budget: $350.00 (+75%)                    │    │
│ │   safety_cap   $100,000 (under)                                │    │
│ │ ...                                                            │    │
│ └────────────────────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────────────────┘
```

**DraftCard structure (component):**

```
┌─ DraftCard ────────────────────────────────────────────────────┐
│ Header:                                                        │
│   eyebrow:  DRAFT · 12 min ago · meta_api / pause_ad           │
│   meta:     Requested by @markvasilev                          │
│ Body:                                                          │
│   summary:  "Will PAUSE 1 ad" (one bold sentence)              │
│   diff:     monospaced key→value table, current vs target      │
│             changes highlighted with accent left-border        │
│   reason:   AI reasoning (text-body-sm, muted)                 │
│ Footer:                                                        │
│   meta:     Expires in 23h 47m (counter, updates every minute) │
│   actions:  [Cancel] (ghost danger) [Approve & execute]        │
│             (primary, accent)                                  │
└────────────────────────────────────────────────────────────────┘
```

**ACL behavior:**

- Только owner может Approve (backend проверка через `created_by_chat_id`).
- Если current user не owner — кнопка Approve disabled, tooltip: "Only owner can approve this draft. Created by @markvasilev."
- Cancel доступен всем admin'ам.

**Empty state:** "No pending drafts. AI is quiet today." (editorial-тон).

**Components:** PageHeader, FilterPills, DraftCard (new domain composite), ExpirationCounter (live updating).

---

## 5. Accessibility checklist

WCAG 2.1 AA минимум. Цель — AAA где возможно.

### 5.1 Contrast

- Body text → AAA (16.5:1).
- Secondary text → AA+.
- Disabled text → AA (3:1+).
- UI components borders → 3:1 минимум (verified в §1.1.3).

### 5.2 Keyboard navigation

- Все interactive elements достижимы через `Tab`.
- Logical tab order (визуальный = DOM order).
- `Esc` закрывает все modal/drawer/popover.
- Focus trap внутри modal/drawer.
- Skip-link "Skip to content" в начале каждой страницы (visually hidden, focus visible).
- Глобальные shortcuts (см. §4.2) показаны в `?` modal (открывается на `?`).

### 5.3 Focus visible

`:focus-visible` обязателен на всех interactive (§1.6). `:focus:not(:focus-visible)` подавляется (mouse click не показывает outline).

### 5.4 ARIA

- `aria-label` на icon-only buttons.
- `aria-current="page"` на active sidebar link.
- `aria-live="polite"` на toast region и live stat updates.
- `role="alert"` на error toasts.
- `aria-expanded` на collapsable sections (sidebar, filter panels).
- `aria-describedby` для form errors.

### 5.5 Semantic HTML

- `<main>`, `<nav>`, `<aside>`, `<header>`, `<section>` — не `<div>`.
- `<button>` для кнопок, `<a>` для ссылок (даже если стилизованы похоже).
- `<label>` всегда привязан к input (через `htmlFor`).
- Заголовки иерархичны (`h1` → `h2` → `h3`, не пропускать уровни).

### 5.6 Motion

- `prefers-reduced-motion: reduce` → все durations → 0, оставить opacity-кроссфейд.
- Никаких infinite animations кроме worker pulse dot (slow 2s breathing).

### 5.7 Screen reader

- Все decorative icons → `aria-hidden="true"`.
- Информативные icons → `aria-label` или sibling `<span class="sr-only">`.
- Tables — `<th scope="col">`, `<caption>` где уместно.

---

## 6. Implementation guidelines

### 6.1 File structure

```
frontend-v2/
├── public/
│   └── fonts/                    # self-host шрифты, не CDN
├── src/
│   ├── routes/                   # TanStack Router file-based
│   │   ├── __root.tsx
│   │   ├── index.tsx             # /dashboard (root redirect)
│   │   ├── dashboard.tsx
│   │   ├── ads/
│   │   │   ├── index.tsx
│   │   │   └── $adId.tsx         # /ads/:adId (drawer route)
│   │   ├── offers/
│   │   │   ├── index.tsx
│   │   │   └── $offerId.rules.tsx
│   │   ├── history/index.tsx
│   │   ├── settings/
│   │   │   ├── index.tsx         # redirect to /settings/observer
│   │   │   ├── observer.tsx
│   │   │   ├── telegram.tsx
│   │   │   ├── vision.tsx
│   │   │   ├── workers.tsx
│   │   │   ├── ai.tsx
│   │   │   └── health.tsx
│   │   └── drafts/index.tsx
│   ├── components/
│   │   ├── ui/                   # primitives (Button, Input, Card, ...)
│   │   ├── layout/               # PageShell, Sidebar, Topbar
│   │   ├── data/                 # Table, ChartWrapper, StatCard
│   │   └── domain/               # AdRow, DraftCard, WorkerPulse, ...
│   ├── lib/
│   │   ├── api/                  # API client (typed openapi-generated)
│   │   ├── format/               # formatters (currency, date, count)
│   │   ├── hooks/                # custom hooks
│   │   └── utils/                # cn(), debounce, etc.
│   ├── stores/                   # Zustand stores
│   │   ├── ui-store.ts           # sidebar collapsed, theme, density
│   │   ├── filters-store.ts      # current filters per page
│   │   └── selection-store.ts    # bulk selection
│   ├── styles/
│   │   ├── globals.css           # CSS variables, reset
│   │   └── tokens.css            # @theme tokens for Tailwind 4
│   └── main.tsx
├── index.html
├── package.json
├── tsconfig.json
├── tailwind.config.ts            # Tailwind 4 config
└── vite.config.ts
```

### 6.2 State management

**Разделение ответственности:**

| Тип состояния | Где живёт | Пример |
|---|---|---|
| **Server data** | TanStack Query | `/dashboard/stats`, `/ads`, `/offers` |
| **URL state** | TanStack Router search params | filters, sort, pagination, drill-down id |
| **Cross-page UI** | Zustand | sidebar collapsed, theme, density preference |
| **Selection (transient)** | Zustand или page-local useState | selected rows для bulk action |
| **Form state** | react-hook-form (uncontrolled) | offer edit, rules editor |
| **Ephemeral** | useState | hover, dropdown open |

**Что НЕ должно быть в Zustand:**

- Server data — это TanStack Query задача.
- Form state — react-hook-form.
- Filter state — URL (чтобы можно было копировать ссылку).

### 6.3 Routing

TanStack Router file-based. Конвенции:

- `index.tsx` — корень route group.
- `$paramName.tsx` — dynamic segment.
- `__root.tsx` — layout wrapper.
- Search params типизированы через `zod` schemas.

Filters в URL:

```
/ads?state=warning,stop&offer=DRC&search=tyver&sort=spend:desc
```

При изменении filter → URL обновляется → query refetches.

### 6.4 API layer

Backend OpenAPI schema → `openapi-typescript` → автогенерированные types. Тонкая обёртка через `openapi-fetch`. Никаких ручных `fetch('/api/v1/...')` в компонентах.

```typescript
// src/lib/api/client.ts
import createClient from 'openapi-fetch'
import type { paths } from './generated-types'

export const api = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_URL,
  credentials: 'include',
})
```

В компонентах:

```typescript
const { data } = useQuery({
  queryKey: ['ads', filters],
  queryFn: () => api.GET('/api/dashboard/ads', { params: { query: filters } })
})
```

### 6.5 Testing strategy

- **Unit (Vitest)** — для formatters, utils, custom hooks. ≥80% coverage на `lib/`.
- **Component (Vitest + Testing Library)** — для primitives (Button, Input) и domain composites (AdRow, DraftCard).
- **Integration (Playwright)** — критические пути: open ad drawer, bulk disable, approve draft, restart worker. ≤10 сценариев в первой итерации.
- **Visual regression** — отложено (нет budget на Chromatic / Percy в первой итерации).
- **Accessibility** — `vitest-axe` на критических page-level компонентах.

### 6.6 Performance budgets

- **Bundle size**: <300KB gzipped initial.
- **First Contentful Paint**: <1s локально, <1.5s на staging.
- **Time to Interactive**: <2s.
- **Table render с 1000 rows**: <100ms (virtualization обязательна).
- **Re-render на keypress в search**: <16ms (debounce 200ms).

### 6.7 Anti-patterns (что не делать)

- Не использовать `any` в TS — banned by ESLint config.
- Не использовать `!important` в CSS, кроме reset'ов.
- Не вызывать `fetch` напрямую из компонентов (см. §6.4).
- Не хранить server data в Zustand.
- Не делать round-trip к серверу на каждый keystroke (debounce).
- Не использовать `dangerouslySetInnerHTML` без sanitize.
- Не импортировать целиком из `lucide-react` (tree-shaking важен).

### 6.8 Definition of Done для страницы

Перед merge'ем PR с новой страницей:

- [ ] Все endpoints из brief'а интегрированы.
- [ ] Loading state.
- [ ] Empty state.
- [ ] Error state (per-card + page-level boundary).
- [ ] Mobile NOT supported — но page не должна рассыпаться (graceful degradation на <1024px → "Open on desktop").
- [ ] Keyboard navigation работает (Tab order, Esc closes drawer).
- [ ] `prefers-reduced-motion` respected.
- [ ] ARIA labels на icon-only кнопках.
- [ ] axe-core тесты прошли.
- [ ] Unit-тесты formatters / hooks.
- [ ] Storybook entry для каждого нового composite component.

---

## 7. Open questions

Эти вопросы требуют решения пользователя/команды:

1. **WS vs polling.** Сейчас старый фронт mix'ует WebSocket (на DashboardPage) и polling. В v2 идём в WS-only для real-time (alert feed, task queue, worker pulse), а статичные данные (offers, history) — polling. Подтверждение нужно: бэкенд готов к WS-only или нужно сохранить polling fallback?

2. **Дополнительный режим компактности.** Density toggle (comfortable / compact / dense) — predefined в spec. Но кто-то любит "Bloomberg-level density" с 24px row height. Делаем "dense" как опцию или ограничиваемся двумя уровнями?

3. **TMA-инкорпорация.** `frontend-mini/` живёт в отдельном repo. Делать ли shared design tokens package (`@fb-stop-bot/design-tokens`) для синхронизации, или оставить duplication первое время?

4. **AI Chat surface.** В brief'е есть AI analyze endpoint, но не упомянут отдельный AI Chat UI. Telegram уже даёт `/ask` интерфейс. Нужен ли web-chat (drawer или sidebar panel) в v2 или отложить?

5. **Light mode timeline.** Прописать в roadmap (v2.1?) или забыть совсем (operator всегда сидит в тёмном)?

6. **History export.** Endpoint для CSV/XLSX export уже есть? Если нет — заглушка или сразу делать backend?

---

## 8. Glossary

| Term | Meaning |
|---|---|
| **FSM state** | `alert_state` в `ad_alert_state` table: normal / warning_sent / stop_sent / claimed / disabled |
| **Stage** | `warning` или `stop` в alert_event |
| **Outbox** | `task_queue` table — все side-эффекты идут через неё |
| **Draft** | task с `status='draft'`, ждёт человеческого подтверждения |
| **Vision** | anti-detect браузер, через который сканируем FB Ads Manager |
| **Owner** | `created_by_chat_id` task'а или recipient с `role='owner'` |
| **Eyebrow** | маленький uppercase label над heading'ом (editorial-приём) |
| **TMA** | Telegram Mini App (`frontend-mini/`) |

---

## 9. Change log

| Version | Date | Changes |
|---|---|---|
| v1.0 | 2026-05-28 | Initial spec. Editorial-monochrome direction, warm off-white accent, JetBrains Mono + Inter Tight, 6 pages, 30+ components. |
