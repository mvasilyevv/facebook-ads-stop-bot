/**
 * TopBar — 56px.
 * Слева: sidebar-toggle + brand-mark (24px accent box "FB") + "FB Stop Bot".
 * Центр: breadcrumb (mono dim → текущий раздел).
 * Справа: SearchTrigger (disabled stub) + WorkerPulse + avatar 28px.
 *
 * Дизайн-канон (dashboard.html):
 * - topbar__brand width 216px, gap 10px
 * - brand-mark 24x24 bg-accent text-bg-0 font-display 600 13px
 * - breadcrumb 12px font-display tracking .02em, current text-bg-11
 * - search-trigger: h-32px px-12px bg-bg-1 border-bg-5, gap 10px, 13px
 * - kbd: font-display 11px bg-bg-3 border-bg-6 py-0.5 px-1.5
 * - user-avatar: 28px bg-bg-3 border-bg-6 font-display 11px 600
 */

import { useRouterState } from "@tanstack/react-router";
import { Search, PanelLeft } from "lucide-react";
import { WorkerPulse } from "./WorkerPulse";
import { useUiStore } from "@/stores/ui";

const ROUTE_CRUMBS: Record<string, { section: string; current: string }> = {
  "/": { section: "Управление", current: "Панель" },
  "/ads": { section: "Управление", current: "Объявления" },
  "/drafts": { section: "Управление", current: "Черновики" },
  "/offers": { section: "Каталог", current: "Офферы" },
  "/history": { section: "История", current: "История" },
  "/settings": { section: "Система", current: "Настройки" },
};

function getCrumbs(pathname: string): { section: string; current: string } {
  if (pathname === "/" || pathname === "") return ROUTE_CRUMBS["/"]!;
  for (const [prefix, crumb] of Object.entries(ROUTE_CRUMBS)) {
    if (prefix !== "/" && pathname.startsWith(prefix)) return crumb!;
  }
  return { section: "App", current: "—" };
}

export function TopBar() {
  const { location } = useRouterState();
  const crumbs = getCrumbs(location.pathname);
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);

  return (
    <header className="col-start-1 col-end-3 row-start-1 row-end-2 border-b border-bg-5 bg-bg-0 flex items-center gap-6 pl-6 pr-8 z-[10]">
      {/* Brand + toggle */}
      <div className="flex items-center gap-2.5 w-[216px] shrink-0">
        <button
          type="button"
          aria-label="Свернуть/развернуть боковую панель"
          onClick={toggleSidebar}
          className="size-7 inline-flex items-center justify-center text-bg-9 hover:text-bg-11 transition-colors"
        >
          <PanelLeft size={15} aria-hidden="true" />
        </button>
        <div
          aria-hidden="true"
          className="size-6 bg-accent text-bg-0 font-display font-semibold text-[13px] flex items-center justify-center tracking-tight shrink-0"
        >
          FB
        </div>
        <div className="font-display text-[13px] font-medium tracking-tight">
          FB Stop Bot
        </div>
      </div>

      {/* Breadcrumb */}
      <nav
        aria-label="Текущий раздел"
        className="text-[12px] text-bg-9 font-display tracking-[.02em]"
      >
        <span aria-hidden="true" className="text-bg-7 mr-2">{crumbs.section}</span>
        <span aria-hidden="true" className="text-bg-7 mr-2">/</span>
        <span className="text-bg-11">{crumbs.current}</span>
      </nav>

      <div className="flex-1" />

      {/* Search trigger (disabled stub — Phase 4) */}
      <button
        type="button"
        className="flex items-center gap-2.5 h-8 px-3 bg-bg-1 border border-bg-5 text-bg-9 text-[13px] cursor-not-allowed font-body"
        aria-label="Поиск — в разработке"
        title="Глобальный поиск — в разработке"
        disabled
      >
        <Search size={14} aria-hidden="true" />
        <span>Поиск объявлений, офферов, событий</span>
        <kbd className="font-display text-[11px] bg-bg-3 border border-bg-6 px-[5px] py-px text-bg-10 ml-1">
          ⌘K
        </kbd>
      </button>

      {/* Worker health pulse */}
      <WorkerPulse />

      {/* User avatar 28px */}
      <div
        aria-hidden="true"
        className="size-7 bg-bg-3 border border-bg-6 flex items-center justify-center font-display text-[11px] font-semibold text-bg-11"
        title="Профиль"
      >
        MV
      </div>
    </header>
  );
}
