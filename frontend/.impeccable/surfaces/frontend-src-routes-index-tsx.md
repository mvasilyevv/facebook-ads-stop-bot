---
version: 1
slug: "frontend-src-routes-index-tsx"
primary_target: "frontend/src/routes/index.tsx"
related_targets: ["frontend-mini/src/routes/index.tsx"]
---

# Сейчас — portfolio overview

- **Scope and mode:** `/` in web and TMA, Operate mode, designed first at 1440×900 and 390×844.
- **Audience:** the single owner operating a specialized iGaming portfolio, often under time pressure and low-light conditions.
- **Job:** understand within seconds whether money is safe, which cabinet needs attention, and what confirmed or pending action exists; open the exact cabinet or action without hunting.
- **Primary tasks:** inspect freshness/health, compare actual/base/stop, read ranked attention, acknowledge an incident, preview a pause, and follow task lifecycle through confirmed/failed/unknown.
- **Proof and content:** source freshness, portfolio totals separated by currency, one row per cabinet, a shared spend audit scale, short Clicks → Registrations → FTD → Confirmed deposits funnel, ranked attention, and active/recent actions.
- **Constraints:** `null` is unknown and `0` is confirmed zero; partial/stale/unavailable never look healthy; state is icon + label + color; no horizontal page scroll at 360px; keyboard and touch parity; 44px targets; WCAG 2.2 AA; no invented KPI or copy.
- **Chosen direction:** **Точный журнал** — print registration proof plus financial reconciliation. Each value reads as an auditable row with source, `as_of`, and a state mark.
- **Approved composition:** `.impeccable/mocks/operator-now-comp-a-ledger-leads.png` with sidecar `.impeccable/mocks/operator-now-comp-a-ledger-leads.prompt.json` marked `approved: true`.
- **Desktop hierarchy:** the shared portfolio spend scale leads; ranked attention occupies the first right rail; the short funnel follows the scale and action lifecycle follows attention.
- **Mobile/TMA hierarchy:** ranked attention appears first so an active breach is not hidden below the fold, then the same shared scale becomes cabinet evidence strips, followed by action lifecycle and funnel.
- **Memorable moment:** a single continuous spend scale aligns actual, base, stop, and current time across the portfolio; breached or unresolved rows carry a visible registration error mark that opens the exact reason.
- **Navigation and density:** a compact persistent desktop rail and four-item bottom navigation on mobile; ruled rows are dense but all interactive targets remain at least 44 px.
- **Type direction:** Commissioner for Cyrillic interface copy and JetBrains Mono only for money, time, identifiers, and measured values; final assets must be self-hosted and fit the font budget.
- **Depth and motion:** flat matte surfaces with one-pixel rules and crop marks; motion is limited to current-time, task progress, reconciliation, and state transitions.
- **Rejected composition behavior:** attention does not lead the desktop canvas, and an already-running command never precedes a newly proven breach on mobile.
