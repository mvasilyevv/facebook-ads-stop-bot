# Handoff: FB Stop Bot — Operator Tool (Web Dashboard + Telegram Mini App)

## Overview

**FB Stop Bot** is an internal operator tool for traffic arbitrage. It monitors Facebook
Ads, raises alerts, disables/enables ads, and creates campaigns. There are **two clients
for one and the same operator**:

1. **Web dashboard** — desktop, `1280px+`. The operator lives in it 6–8 hours a day.
2. **Telegram Mini App** — mobile, touch. The same person "on the go".

The goal of this design work was **consistency**: previously the web and mini app looked
like two different products. This package brings both to **one unified design language**
(canon = the web editorial-monochrome direction) on a **single shared token set**.

Audience: 1–2 ad-ops professionals. Design priority: **minimum cognitive load, instant
read of alert state, zero visual noise.** This is an operator tool, not a marketing site —
density like a Bloomberg Terminal, render quality like Linear/Vercel.

> UI language is **Russian**. Ad-ops jargon stays in Latin: `spend, CTR, CPM, CPL, CPA,
> ROAS, offer, draft, creative`. FSM states use Russian labels: `Норма / Предупреждение /
> Стоп / В работе / Отключено`.

---

## About the Design Files

The files in `design_files/` are **design references created in HTML/React-via-Babel** —
runnable prototypes that demonstrate the intended **look, layout, and behavior**. They are
**not production code to copy verbatim.**

The task is to **recreate these designs inside the target codebase's own environment**,
using its established framework, component library, state patterns, and conventions:

- If a frontend stack already exists (React, Vue, Svelte, SwiftUI, native Android, etc.),
  rebuild the screens with **that stack's** idioms and existing primitives.
- If no frontend exists yet, **React + TypeScript + CSS variables (or Tailwind mapped to
  the tokens below)** is the recommended choice and matches the prototype structure most
  directly.
- The Telegram Mini App should use the **Telegram Mini Apps SDK** (`@twa-dev/sdk` or
  `window.Telegram.WebApp`) for theme params, viewport, `MainButton`/`BackButton`, and
  safe-area insets — but the **brand accent stays warm off-white**, never Telegram blue.

The prototypes use inline styles and a few global CSS classes (from `tokens.css`) for
speed. In production, **lift every value into the token system** (CSS variables or a theme
object) — do not hardcode hex/px.

---

## Fidelity

**High-fidelity (hifi).** Final colors, typography, spacing, radii, motion, and
interactions are all locked. Recreate the UI **pixel-accurately** using the codebase's
libraries. The exact values are enumerated in **Design Tokens** below and defined
canonically in `design_files/tokens.css`.

Status of each screen:

| Screen | Web | Mini App | Fidelity |
|---|---|---|---|
| Dashboard | ✅ clickable prototype | ✅ clickable prototype | hifi + interactive |
| Ads | ✅ clickable prototype | ✅ clickable prototype | hifi + interactive |
| Drafts | ◻︎ static template | — | hifi static |
| Offers | ◻︎ static template | — | hifi static |
| History | ◻︎ static template | — | hifi static |
| Settings | ◻︎ static template | — | hifi static |

Mini App screens beyond Dashboard/Ads (Ad Detail, Drafts, Health, History, Offers,
Scripts, Settings) are **not yet mocked** — build them by applying this same system to the
web template content, adapted to the mobile patterns described under "Mini App patterns".

---

## Design Language (canon)

- **Editorial monochrome, dark theme.** Graphite surface scale, near-black page.
- **Single accent: warm off-white `#F5F1E8`.** Used for the active nav marker, primary
  buttons, focus rings, selection, key emphasis. No second brand color. Semantic colors
  (success/warning/danger/info) are **muted and reserved for status only**, never decoration.
- **Sharp corners.** `border-radius: 0` by default. The only exceptions: pills/badges
  (full radius), inputs (`2px`), modals (`4px`).
- **No drop-shadows.** Depth comes from **1px borders + a background-shade shift**
  (`--bg-1` → `--bg-2` → `--bg-3`...), not shadows.
- **Type:** `JetBrains Mono` for all headings and **all numbers** (tabular-nums); `Inter
  Tight` for body text.
- **Eyebrow markers:** small uppercase mono labels, often numbered, e.g. `01 / ОБЗОР`.
  The number is rendered in `--accent-muted`.
- **4px spacing grid**, strictly.
- **Blueprint texture:** a faint dot + line grid behind dashboard content (Vercel/Geist
  influence). Built in pure CSS, ~40–50% opacity, masked toward the top. Decorative only.

---

## Screens / Views

> Coordinates and sizes below are for the **web desktop** canvas (content area inside a
> 1320px window, minus the sidebar). The Mini App uses a 390×844 viewport (iPhone).

### 1. Web — Dashboard (`FB Stop Bot - Dashboard.html` → `web-dashboard.jsx`)

**Purpose:** answer "what is happening right now?" in one screen, at a glance.

**Layout (top → bottom), inside the app shell:**

- **App shell** = fixed **Sidebar** (left) + **Topbar** (top) + scrolling **main**.
  - Sidebar: `240px` expanded / `64px` collapsed, `1px` right border, `--bg-0`.
    Brand block (26×26 `--accent` square with `FB`, "STOP BOT / operator"), then grouped
    nav with numbered eyebrow group headers (`01 OPERATE`, `02 CATALOG`, `03 HISTORY`,
    `04 SYSTEM`). Nav items: 36px tall, icon + label + optional count badge. Active item
    has `--bg-2` fill, `--accent` text, and a `3px` `--accent` left bar. Footer: a single
    collapse toggle (worker status lives ONLY in the topbar — do not duplicate it here).
  - Topbar: `56px`, `1px` bottom border. Left = mono breadcrumb `FB Stop Bot / Панель`.
    Right = search button with `⌘K` kbd, **worker status chip**, bell, `MV` avatar.
- **Page header:** eyebrow `01 / ОБЗОР · ПО ОБЪЯВЛЕНИЯМ · LIVE`, then `<h1>` "Панель"
  (mono, 30px, weight 500, letter-spacing -0.02em — **no trailing period**). On the right,
  the **Scan cluster** (see Interactions): countdown ring + "СЛЕД. СКАН" + "ПОСЛЕДНИЙ СКАН
  Ns назад" + primary "Сканировать" button.
- **Hero + chart row** (`grid 1fr 1.1fr`, 32px gap, bottom-bordered):
  - **Hero (left):** a pulsing status dot + eyebrow (`ТРЕБУЕТ ВНИМАНИЯ` in `--warning`,
    or `СИСТЕМА В НОРМЕ` in `--success`), then a **giant count-up number** (88px mono,
    weight 500, ls -0.04em) = ads under control, with the caption "объявлений под
    контролем" beside it. Below: the **Health bar** — a segmented 8px bar showing the
    share of the portfolio that is Норма / Предупреждение / Стоп, animating width on mount,
    with a legend.
  - **Chart (right):** a `card` with eyebrow `SPEND × ЧАС · 24Ч` + total, and an area
    **spend-by-hour chart** (24 points) that draws itself on (line stroke animation) and
    shows a pulsing "now" dot at the last point. Hover shows a tooltip.
- **Sparkline KPI row:** a 4-column bordered strip. Each cell = eyebrow (`ACTIVE / WARNING
  / STOP / DISABLED`), trend chip, a **count-up number** (34px) toned by state, and a
  filled **sparkline**, then `label · note`.
- **Live-tail feed:** eyebrow `02 / СОБЫТИЯ ПО ОБЪЯВЛЕНИЯМ · LIVE-TAIL` + a "поток активен"
  pulse marker. A `card` whose rows are **ad-level events**; new rows **slide in from the
  top** every ~3.2s with an accent flash. Each row: time (mono), a state dot (stop dots
  pulse), the ad name (mono, e.g. `CR2 | DRC | MV | Tyver | 25.03`), rule pills, a chevron.
  Clicking a row opens the **Event drawer**.
- **Task queues:** eyebrow `03 / ОЧЕРЕДЬ ЗАДАЧ`, then `grid 1fr 1fr` of two cards —
  `DISABLE QUEUE` and `ENABLE QUEUE`. Rows show a status dot, ad name, status label
  (в очереди / в работе / ошибка / готово), and `×attempts · age`.

**Empty / calm state:** when the scenario is "calm", the hero shows `СИСТЕМА В НОРМЕ`,
incidents/feed render an **editorial empty state** ("Алертов за 24ч нет" + "Что приятно —
значит правила работают, а трафик льётся ровно") and queues show "Очередь пуста".

**Paused state (Observer off):** the scan cluster swaps to a **dashed pause-ring** +
"СКАН ВЫКЛЮЧЕН" + a "▶ Включить" button; a full-width **warning banner** appears under the
header ("Observer выключен — объявления не мониторятся с HH:MM. Алерты, авто-disable и
live-tail на паузе."); the live-tail freezes and its marker reads "на паузе"; the page
eyebrow ends in `ПАУЗА` instead of `LIVE`.

### 2. Web — Ads (`FB Stop Bot - Ads.html` → `ads-web.jsx`)

**Purpose:** the workhorse (≈60% of operator time) — triage and act on 1000+ ads.

**Layout:** same shell (sidebar active = "Объявления", breadcrumb `… / Объявления`).
Main is a **flex column that fills height** so the table scrolls internally.

- **Page header:** eyebrow `04 / УПРАВЛЕНИЕ · ОБЪЯВЛЕНИЯ`, `<h1>` "Объявления", and on the
  right three count badges (Норма / Предупреждение / Стоп totals).
- **Filter bar:**
  - Search input (max 360px) with a leading search icon and a trailing `/` kbd hint
    (focuses on `/`). Placeholder "Поиск по названию / ad_id / offer".
  - **State pills** (Норма / Предупреждение / Стоп / В работе / Отключено): each a
    full-radius button with a state dot; selected = `--accent` border + `--accent-bg` fill
    + `--accent` text.
  - **Offer dropdown** (checkbox list).
  - Right-aligned result count "N объявлений".
  - When filters are active, a row of removable **filter chips** appears below.
- **Virtualized table** (`1px` border, fills remaining height):
  - Header row (`--bg-2`, 32px) with eyebrow column labels. Columns:
    `[checkbox 40px] [AD 1fr] [OFFER 64] [STATE 130] [SPEND 96] [CPL 74] [FREQ 62]
    [CPM 62] [CTR 62] [ROAS 66] [⋯ 40]`.
  - **Only the visible window of rows is in the DOM** (windowed/virtual scrolling with
    overscan). Total scroll height = `rows × rowHeight`; a translated inner container holds
    the slice. Row height is **density-driven** (44/34/28px).
  - Row: checkbox, geo-thumb (40×24 placeholder showing the 2-letter geo), ad name (mono,
    truncated) + first rule pill, offer chip, FSM badge, then **right-aligned numbers**
    (tabular-nums). Flag colors: CPL > 30 → danger, FREQ > 4 → danger, ROAS < 1 → danger;
    CPM/CTR are muted. Selected row = `--accent-bg` + `2px` `--accent` left border; keyboard
    cursor row = `--bg-2`.
  - Below the table: a mono **keyboard legend** (`J/K` nav, `X` select, `D` disable,
    `Enter` details, `/` search).
- **Bulk action bar:** when ≥1 row is selected, a floating bar centers at the bottom
  (`--bg-3`, `1px --bg-7` border, rises in): "N выбрано" + Disable (danger) + Snooze +
  "Очистить".
- **Confirm-disable modal:** Disable opens a centered modal (`4px` radius) requiring the
  operator to **type `DISABLE`** to enable the destructive confirm button. Esc cancels.
- **Ad drawer:** clicking a row opens a `560px` right drawer (slide-in) with: header
  (geo·city eyebrow, full ad name, FSM badge, offer chip, ad_id), triggered-rule banner,
  a **metrics snapshot grid** (spend/CPL/CPM/CTR/freq/ROAS/leads/age — flagged cells turn
  danger), a **CPL sparkline** (8 points), a task-history section, and a footer with
  Snooze / Disable. Esc closes.

### 3. Web — Drafts (template → `templates.jsx` `DraftsTemplate`)

**Purpose:** approve/reject AI-proposed mutations before they execute.

Cards (max-width 760px) stacked. Each card: header eyebrow `DRAFT · <ago> · meta_api /
<op>` + a one-line summary + "Запросил @handle"; a **diff table** (rows of `key`,
`current → target`, with **changed rows** marked by a `2px --accent` left border + `--accent-bg`
fill and the target value in accent); an **AI rationale** block; a footer with an expiry
timer (turns `--warning` when <1h) and **Отклонить / Одобрить и выполнить** buttons. A
filter-pill row sits above the list (Все / pause / activate / budget / campaign).

### 4. Web — Offers (template → `OffersTemplate`)

**Purpose:** catalog of offers + entry to the rule editor.

A 3-column grid of offer cards. Each card: code (mono) + active/inactive badge; a stat
list (Spend / Leads / CPL / Alerts — Alerts>0 in `--warning`); footer buttons "Правила" /
"Изменить". Above: tabs (Все / Активные / Неактивные) + a sort control, and a primary
"Новый оффер" button in the header. (The rule editor itself — 6 numeric thresholds — is
described in the spec; build it as a right drawer or dedicated view reusing the input and
field patterns from Settings.)

### 5. Web — History (template → `HistoryTemplate`)

**Purpose:** browse the event archive.

`grid 40% / 60%`. **Left = summary** cards (total events; breakdown by stage with colored
dots; breakdown by rule with rule pills + counts). **Right = timeline** card with **day
divider** rows (eyebrow `СЕГОДНЯ · 28 МАЯ`) and event rows (time, stage dot, ad name, rule
pill, chevron → opens a drawer for drill-down). Header has a date-range control, filter
dropdowns, and an "Export CSV" button. A "Загрузить ещё" button paginates.

### 6. Web — Settings (template → `SettingsTemplate`)

**Purpose:** configure the system.

Underline **tab navigation**: `Observer · Telegram · Vision · Workers · AI · Health`.
Below, `grid 60% / 40%`: **left = form** (label/control rows separated by `1px` borders;
text fields are `--bg-2` boxes with `--bg-6` border, `2px` radius; toggles are 38×22 pill
switches that go `--accent` when on) + a "Сохранить изменения" primary button; **right =**
a **status card** (Observer ONLINE, last scan) and an **actions card** (restart observer,
scan now, start new cabinet day).

### Mini App patterns (`mini-dashboard.jsx`, `ads-mini.jsx`)

The Mini App reuses the **exact same tokens, type, eyebrows, sharp corners, and warm-white
accent**, adapted to mobile/touch:

- **Container:** 390×844 dark viewport, `padding-top: 50px` for the Telegram status/header
  area (use real safe-area insets in production).
- **Bottom tab-bar** (replaces the sidebar): `Панель · Объявления · Черновики · История ·
  Ещё`. 5 items, ≥52px tall, icon + 10px label, active = `--accent`. The "Ещё" tab holds
  the overflow screens (Offers, Health, Scripts, Settings).
- **Touch targets ≥ 44px** everywhere (scan button is a 44×44 square, etc.).
- **Detail = bottom sheet** (slides up, drag-handle, max-height ~84%) instead of a right
  drawer. Same content (metrics grid, sparkline), reflowed to 2–3 columns.
- Dashboard mini: compact scan header (countdown ring + 44px scan button + worker chip),
  hero (64px number) + health bar, spend chart (120px), **KPI 2×2 grid**, live-tail, and
  the **task queues stacked** below.
- Ads mini: sticky header (search + horizontally-scrolling state chips), a simple
  (non-virtualized) list capped at ~120 rows with a "+N ещё · уточни фильтр" footer; tap a
  row → ad bottom-sheet.

---

## Interactions & Behavior

- **Scan cluster / countdown:** a ring counts down to the next auto-scan (30s interval in
  the mock). At 0 it auto-triggers a scan: the refresh icon spins, an NProgress-style 2px
  bar sweeps, button reads "Сканирую", then "ПОСЛЕДНИЙ СКАН" resets to `0с`. Manual click
  does the same. When Observer is off, the whole cluster shows the **paused** treatment.
- **Live-tail:** appends a new ad-event to the top every ~3.2s, capped at N rows; entrance
  = `slide-down 0.4s` + `flash 1.8s` (accent → transparent). Frozen when paused.
- **Count-up:** numbers animate 0 → target with a cubic ease-out (~750ms) on mount.
- **Health bar:** segment widths animate from 0 on mount (800ms `--ease-out`).
- **Drawers / sheets:** web drawer slides in from the right (`--dur-slow`, `--ease-spring`)
  over a `rgba(10,10,11,0.66)` scrim; mini sheet slides up. **Esc closes** both; clicking
  the scrim closes.
- **Bulk select → action bar:** selecting rows reveals the floating action bar (`fbRise`).
  Disable → type-to-confirm modal. Snooze and clear act immediately and show a toast.
- **Ads keyboard nav:** `/` focus search · `J/K` or `↑/↓` move cursor row · `X` toggle
  select cursor row · `Enter` open drawer · `D` disable selection · `Esc` close
  drawer / clear selection / blur search.
- **Filtering/sorting:** search matches name/ad_id/offer; state pills and offer checkboxes
  AND-combine; table sorts by spend desc by default. All client-side in the mock.
- **Toasts:** transient bottom-center confirmations (e.g. "Создано N disable-задач").
- **Reduced motion:** all of the above respect `prefers-reduced-motion: reduce` — entrance
  animations, the marquee/ticker, count-up, and the worker pulse are disabled/curtailed.

## State Management

Per screen, the prototype holds (lift these into the codebase's store/query layer):

- **Global / theme (Tweaks):** `scenario` (live | calm), `density` (comfortable | compact |
  dense → drives `--row-h/--row-fs/--row-px`), `accent` (one of 4 warm-white hexes),
  `fsm` (muted | vivid | mono palette), `sidebarCollapsed`, `scanOn` (Observer on/off).
  Persisted; applied via `data-density` / `data-fsm` attributes + CSS-variable overrides on
  a root scope element.
- **Dashboard:** open drawer event; the scan hook (`scanning`, `age`, `next`, `interval`);
  live-feed row list (interval-driven).
- **Ads:** `search`, `states[]`, `offers[]`, `selected` (Set of ids), `drawer` (ad |
  null), `confirm` (bool), keyboard `cursor` index; derived `rows = filter(ADS, …)`
  memoized; virtual-scroll `scrollTop` / viewport height.
- **Data:** in production, replace the mock generators with real queries. Dashboard needs
  KPI counts, 24h spend series, active incidents, an event stream (websocket/poll for the
  live-tail), and task-queue contents. Ads needs a paginated/filterable ad list with the
  per-ad metric snapshot and per-ad task history. Drafts needs pending mutations with
  diff + rationale + expiry. The live-tail and scan countdown imply a **realtime channel**
  (websocket) or short-poll.

## Design Tokens

All canonical in `design_files/tokens.css`. Summary:

**Surfaces (graphite scale)**
`--bg-0 #0A0A0B` page · `--bg-1 #101012` card · `--bg-2 #16161A` nested/row ·
`--bg-3 #1C1C21` hover · `--bg-4 #232329` active · `--bg-5 #2C2C33` border-subtle ·
`--bg-6 #38383F` border · `--bg-7 #4A4A52` border-strong · `--bg-8 #5C5C66` disabled-text ·
`--bg-9 #7C7C86` placeholder · `--bg-10 #A8A8B0` secondary-text · `--bg-11 #E4E4E7` primary-text.

**Accent (warm off-white, Tweakable)**
`--accent #F5F1E8` · `--accent-muted #BDB8AB` · `--accent-bg #2A2823`.
Alternate accents: Paper `#EDE7D6`, Bone `#F2EFE9`, Sand `#E8DFC8` (each with its own
muted/bg — see the canvas files).

**Semantic (muted; status only)**
success `#7EB47A` / bg `#1A2218` · warning `#D4A858` / bg `#261F12` ·
danger `#C7625C` / bg `#261513` · info `#7AA0B4` / bg `#131C22`.
*Vivid palette (Tweak):* warning `#E8B43C`, danger `#E5645C`, info `#6FB0CC`, success `#6FC46A`.

**FSM state → color → label**
`normal` → `--bg-9` → **Норма** · `warning` → warning → **Предупреждение** ·
`stop` → danger → **Стоп** · `claimed` → info → **В работе** · `disabled` → `--bg-8` → **Отключено**.

**Typography**
Display/numbers: `JetBrains Mono` (400/500/600/700), tabular-nums on all numerics.
Body: `Inter Tight` (400/500/600).
Observed sizes: hero number 88 (web) / 64 (mini); h1 30; KPI number 34; section/card
numbers 18–22; body 13; secondary 12; eyebrow **10px, 600, uppercase, letter-spacing
0.08em**; smallest legend 11. Min on-screen text 12px. h1 letter-spacing −0.02em, big
numbers −0.03…−0.04em.

**Spacing (4px grid)** `--s-1 4 · --s-2 8 · --s-3 12 · --s-4 16 · --s-5 20 · --s-6 24 ·
--s-8 32 · --s-10 40 · --s-12 56`.

**Radius** `--r-0 0` (default) · `--r-1 2` (inputs) · `--r-2 4` (modals) · `--r-full`
(pills/badges/dots).

**Motion** `--ease-out cubic-bezier(0.2,0.8,0.2,1)` · `--ease-spring
cubic-bezier(0.34,1.56,0.64,1)` · durations `--dur-fast 120ms · --dur-base 200ms ·
--dur-slow 400ms`. Keyframes (in tokens.css): `fbFade, fbSlideIn, fbSlideDown, fbFlash,
fbRise, fbMarquee, fbPulse, fbSpin, fbSheetUp, fbBarSweep`.

**Density** `[data-density]` sets `--row-h` (44/34/28), `--row-fs` (13/13/12),
`--row-px` (16/12/10).

**Depth** = `1px` border (`--bg-5`/`--bg-6`) + background-shade shift. **No box-shadows.**

**Accessibility:** WCAG 2.1 AA minimum (AAA on contrast where possible). Visible focus =
`2px solid var(--accent)` with `2px` offset (`.fb-scope :focus-visible`). Full keyboard
nav; Esc closes drawers/modals. Mini touch targets ≥ 44px. Honor `prefers-reduced-motion`.

## Assets

- **Fonts:** JetBrains Mono + Inter Tight, loaded from Google Fonts in `tokens.css`
  (`@import`). In production, self-host or use the codebase's font pipeline.
- **Icons:** a small inline-SVG Lucide-style set in `icons.jsx` (thin 1.5px strokes). In
  production, prefer the codebase's icon library (e.g. `lucide-react`) — names map closely
  (dashboard, ads, drafts, offers, history, settings, search, bell, refresh, scan, stop,
  snooze, filter, check, chevron, etc.).
- **Geo thumbnails** are placeholders (a 2-letter geo on a bordered box) — replace with
  real ad creative thumbnails when available.
- **No raster image assets** are required by the design.
- **Device/window chrome** (`ios-frame.jsx`, browser window) and the **Tweaks panel**
  (`tweaks-panel.jsx`) are **presentation scaffolding only** — they frame the prototypes on
  a canvas and are **not part of the product UI**. Do not port them.

## Files

In `design_files/`:

**Runnable prototypes (open in a browser):**
- `FB Stop Bot - Dashboard.html` — Web + Mini Dashboard side by side, with Tweaks.
- `FB Stop Bot - Ads.html` — Web + Mini Ads side by side, with Tweaks.
- `FB Stop Bot - Templates.html` — static Drafts / Offers / History / Settings (web).

**Source modules:**
- `tokens.css` — **the canonical token set + base classes** (start here).
- `icons.jsx` — icon set. `components.jsx` — shared primitives (FSM badge, eyebrow, KPI
  card, spend chart, sparkline, rows, trend). `dashboard-shared.jsx` — blueprint bg,
  count-up, health bar, live-feed, worker status, scan hook, countdown/paused rings.
- `web-dashboard.jsx`, `mini-dashboard.jsx` — Dashboard (also exports the reusable Sidebar/
  Topbar/TabBar shell). `ads-web.jsx`, `ads-mini.jsx` — Ads. `templates.jsx` — the 4 static
  web screens.
- `data.js`, `ads-data.js` — mock data + filter/sort helpers (the ~1000-ad generator and
  the realistic dashboard fixtures). Replace with real data sources.
- `ios-frame.jsx`, `tweaks-panel.jsx` — **scaffolding only**, do not port.

**Reference:**
- `frontend_design_spec.md` — the original full design specification (v1.0). This README
  supersedes it where they differ, but the spec has additional per-page detail (e.g. the
  6-threshold offer rule editor, the full FSM, worker/health specifics).

---

### Suggested build order
1. Port `tokens.css` into the codebase's theming system; verify fonts + the graphite scale.
2. Build the shared primitives (FSM badge, eyebrow, KPI/sparkline, buttons, badges, inputs).
3. Build the app shell (Sidebar + Topbar) and the Mini App shell (Tab-bar).
4. Web Dashboard → Web Ads (virtual table is the riskiest piece — validate perf at 1000+).
5. Mini Dashboard → Mini Ads.
6. Drafts → Offers (+ rule editor) → History → Settings.
7. Wire realtime (scan countdown + live-tail) and the destructive-action confirm flows.
