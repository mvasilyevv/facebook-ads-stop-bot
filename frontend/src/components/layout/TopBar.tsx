/**
 * TopBar — 56px, только над контентом.
 *
 * Слева: breadcrumb «FB Agent / <раздел>».
 * Справа: search-кнопка с ⌘K → командная палитра, worker-chip (N/M, жёлтый при
 * down) и MV avatar.
 *
 * Brand-блок и collapse-toggle переехали в Sidebar (не дублируем здесь).
 */

import { useRouterState } from "@tanstack/react-router";
import { Search, Menu } from "lucide-react";
import type { RefObject } from "react";
import { WorkerPulse } from "./WorkerPulse";
import { useCommandPalette } from "@/stores/commandPalette";
import { useOperatorCabinetSnapshot } from "@/lib/api/operator";

// pathname → лейбл текущего раздела для breadcrumb.
const ROUTE_CRUMB: Record<string, string> = {
  "/": "Сейчас",
  "/actions": "Действия",
  "/ads": "Объявления",
  "/campaigns": "Кампании",
  "/incidents": "Инциденты",
  "/offers": "Офферы",
  "/analytics": "Аналитика",
  "/system/sources": "Источники и воркеры",
  "/remote-desktop": "Рабочий стол",
  "/settings": "Настройки",
  "/cabinets": "Кабинет",
};

function getCrumb(pathname: string): string {
  if (pathname === "/" || pathname === "") return ROUTE_CRUMB["/"]!;
  for (const [prefix, label] of Object.entries(ROUTE_CRUMB)) {
    if (prefix !== "/" && pathname.startsWith(prefix)) return label;
  }
  return "—";
}

const CABINET_ID_RE = /^\/cabinets\/([^/]+)/;

interface TopBarProps {
  onOpenNavigation?: () => void;
  navigationButtonRef?: RefObject<HTMLButtonElement | null>;
}

export function TopBar({ onOpenNavigation, navigationButtonRef }: TopBarProps) {
  const { location } = useRouterState();
  const cabinetId = location.pathname.match(CABINET_ID_RE)?.[1] ?? "";
  // Тот же снапшот, что уже держит в кэше страница /cabinets/$cabinetId —
  // здесь только чтение уже загруженных данных, без своего похода в сеть.
  const cabinetSnapshot = useOperatorCabinetSnapshot(cabinetId, { window: "today" });
  const cabinetName = cabinetId ? cabinetSnapshot.data?.meta.account.name : null;
  const crumb = cabinetName || getCrumb(location.pathname);
  const openPalette = useCommandPalette((s) => s.toggle);

  return (
    <header className="col-start-1 row-start-1 z-[20] flex h-14 min-w-0 items-center justify-between gap-3 border-b border-[var(--color-hairline)] bg-bg-0 px-3 sm:px-5 md:col-start-2 md:col-end-3 md:px-8">
      {/* Breadcrumb */}
      <div className="flex min-w-0 items-center gap-2">
        <button
          ref={navigationButtonRef}
          type="button"
          onClick={onOpenNavigation}
          className="inline-flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] text-bg-10 hover:bg-bg-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent md:hidden"
          aria-label="Открыть навигацию"
        >
          <Menu size={17} aria-hidden="true" />
        </button>
        <nav
          aria-label="Текущий раздел"
          className="flex min-w-0 items-center gap-2 whitespace-nowrap font-display text-[13px] text-bg-9"
        >
          <span aria-hidden="true" className="hidden sm:inline">
            FB Agent
          </span>
          <span aria-hidden="true" className="hidden text-bg-8 sm:inline">
            /
          </span>
          <span className="truncate text-bg-11">{crumb}</span>
        </nav>
      </div>

      {/* Right cluster */}
      <div className="flex shrink-0 items-center gap-2 sm:gap-4">
        {/* Search → командная палитра (⌘K) */}
        <button
          type="button"
          onClick={openPalette}
          className="flex size-11 items-center justify-center gap-2 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-2 font-body text-[13px] text-bg-9 transition-colors hover:border-bg-7 hover:text-bg-10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent lg:h-11 lg:w-auto lg:px-3 lg:py-1.5"
          aria-label="Открыть поиск (⌘K)"
          title="Поиск объявлений, офферов, разделов (⌘K)"
        >
          <Search size={14} aria-hidden="true" />
          <span className="hidden lg:inline">Поиск</span>
          <kbd className="ml-1.5 hidden rounded-[var(--radius-1)] border border-[var(--color-hairline-strong)] px-1 font-display text-[12px] text-bg-8 lg:inline">
            ⌘K
          </kbd>
        </button>

        {/* Worker health chip */}
        <WorkerPulse />

        {/* MV avatar */}
        <div
          aria-hidden="true"
          className="hidden size-[30px] items-center justify-center rounded-full border border-[var(--color-hairline-strong)] bg-bg-2 font-display text-[12px] text-bg-11 sm:flex"
          title="Профиль"
        >
          MV
        </div>
      </div>
    </header>
  );
}
