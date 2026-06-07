/**
 * TopBar — 56px, только над контентом (канон design_handoff/web-dashboard.jsx).
 *
 * Слева: mono-breadcrumb «FB Stop Bot / <раздел>».
 * Справа: search-кнопка с ⌘K → командная палитра, worker-chip (N/M, жёлтый при
 * down), разделитель, bell, MV avatar.
 *
 * Brand-блок и collapse-toggle переехали в Sidebar (не дублируем здесь).
 */

import { useRouterState } from "@tanstack/react-router";
import { Search, Bell } from "lucide-react";
import { WorkerPulse } from "./WorkerPulse";
import { useCommandPalette } from "@/stores/commandPalette";

// pathname → лейбл текущего раздела для breadcrumb (канон: FB Stop Bot / <раздел>).
const ROUTE_CRUMB: Record<string, string> = {
  "/": "Панель",
  "/ads": "Объявления",
  "/drafts": "Черновики",
  "/offers": "Офферы",
  "/history": "История",
  "/settings": "Настройки",
};

function getCrumb(pathname: string): string {
  if (pathname === "/" || pathname === "") return ROUTE_CRUMB["/"]!;
  for (const [prefix, label] of Object.entries(ROUTE_CRUMB)) {
    if (prefix !== "/" && pathname.startsWith(prefix)) return label;
  }
  return "—";
}

export function TopBar() {
  const { location } = useRouterState();
  const crumb = getCrumb(location.pathname);
  const openPalette = useCommandPalette((s) => s.toggle);

  return (
    <header className="col-start-2 col-end-3 row-start-1 row-end-2 z-[20] flex h-14 items-center justify-between border-b border-bg-5 bg-bg-0 px-8">
      {/* Breadcrumb */}
      <nav
        aria-label="Текущий раздел"
        className="flex items-center gap-2 whitespace-nowrap font-display text-[13px] text-bg-9"
      >
        <span aria-hidden="true">FB Stop Bot</span>
        <span aria-hidden="true" className="text-bg-7">
          /
        </span>
        <span className="text-bg-11">{crumb}</span>
      </nav>

      {/* Right cluster */}
      <div className="flex items-center gap-4">
        {/* Search → командная палитра (⌘K) */}
        <button
          type="button"
          onClick={openPalette}
          className="flex items-center gap-2 border border-bg-6 bg-bg-2 px-3 py-1.5 font-body text-[13px] text-bg-9 transition-colors hover:border-bg-7 hover:text-bg-10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          aria-label="Открыть поиск (⌘K)"
          title="Поиск объявлений, офферов, разделов (⌘K)"
        >
          <Search size={14} aria-hidden="true" />
          <span>Поиск</span>
          <kbd className="ml-1.5 rounded-[2px] border border-bg-6 px-1 font-display text-[11px] text-bg-8">
            ⌘K
          </kbd>
        </button>

        {/* Worker health chip */}
        <WorkerPulse />

        <div className="h-[22px] w-px bg-bg-5" aria-hidden="true" />

        {/* Bell */}
        <button
          type="button"
          aria-label="Уведомления"
          className="inline-flex size-8 items-center justify-center text-bg-10 transition-colors hover:bg-bg-2 hover:text-bg-11 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <Bell size={17} aria-hidden="true" />
        </button>

        {/* MV avatar */}
        <div
          aria-hidden="true"
          className="flex size-[30px] items-center justify-center rounded-full border border-bg-6 bg-bg-2 font-display text-[12px] text-bg-11"
          title="Профиль"
        >
          MV
        </div>
      </div>
    </header>
  );
}
