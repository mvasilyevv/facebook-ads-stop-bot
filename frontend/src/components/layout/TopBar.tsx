/**
 * TopBar — breadcrumbs + search trigger + density toggle + worker pulse + user.
 */

import { useRouterState } from "@tanstack/react-router";
import { Search, Rows3, Rows4, PanelLeft } from "lucide-react";
import { WorkerPulse } from "./WorkerPulse";
import { useUiStore } from "@/stores/ui";
import { Kbd } from "../ui/Kbd";
import { Tooltip } from "../ui/Tooltip";

const ROUTE_LABELS: Record<string, string> = {
  "/": "Панель",
  "/ads": "Объявления",
  "/offers": "Офферы",
  "/history": "История",
  "/settings": "Настройки",
  "/drafts": "Черновики",
};

function getCrumbs(pathname: string): { section: string; current: string } {
  if (pathname === "/" || pathname === "") {
    return { section: "Управление", current: "Панель" };
  }
  if (pathname.startsWith("/ads")) return { section: "Управление", current: "Объявления" };
  if (pathname.startsWith("/drafts")) return { section: "Управление", current: "Черновики" };
  if (pathname.startsWith("/offers")) return { section: "Каталог", current: "Офферы" };
  if (pathname.startsWith("/history")) return { section: "История", current: "История" };
  if (pathname.startsWith("/settings")) return { section: "Система", current: "Настройки" };
  return { section: "App", current: ROUTE_LABELS[pathname] ?? "—" };
}

export function TopBar() {
  const { location } = useRouterState();
  const crumbs = getCrumbs(location.pathname);
  const density = useUiStore((s) => s.density);
  const toggleDensity = useUiStore((s) => s.toggleDensity);
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);

  return (
    <header className="col-start-1 col-end-3 row-start-1 row-end-2 border-b border-bg-5 bg-bg-0 flex items-center gap-6 pl-6 pr-8 z-[10]">
      <div className="flex items-center gap-2.5 w-[216px]">
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
          className="size-6 bg-accent text-bg-0 font-display font-semibold text-[13px] flex items-center justify-center tracking-tight"
        >
          FB
        </div>
        <div className="font-display text-[13px] font-medium tracking-tight">
          FB Stop Bot
          <span className="text-bg-9"> · v2</span>
        </div>
      </div>

      <nav aria-label="Хлебные крошки" className="text-[12px] text-bg-9 font-display tracking-wider">
        <span>{crumbs.section}</span>
        <span aria-hidden="true" className="text-bg-7 mx-2">
          /
        </span>
        <span className="text-bg-11">{crumbs.current}</span>
      </nav>

      <div className="flex-1" />

      <button
        type="button"
        className="flex items-center gap-2.5 h-8 px-3 bg-bg-1 border border-bg-5 text-bg-9 hover:border-bg-7 hover:text-bg-10 transition-colors text-[13px]"
        aria-label="Открыть поиск"
        disabled
      >
        <Search size={14} aria-hidden="true" />
        <span>Поиск объявлений, офферов, событий</span>
        <Kbd className="ml-1">⌘K</Kbd>
      </button>

      <Tooltip content={`Плотность: ${density}`}>
        <button
          type="button"
          aria-label="Переключить плотность таблиц"
          onClick={toggleDensity}
          className="size-8 inline-flex items-center justify-center text-bg-9 hover:text-bg-11 transition-colors border border-bg-5 hover:border-bg-7"
        >
          {density === "comfortable" ? (
            <Rows3 size={14} aria-hidden="true" />
          ) : (
            <Rows4 size={14} aria-hidden="true" />
          )}
        </button>
      </Tooltip>

      <WorkerPulse />

      <div
        aria-label="Профиль пользователя"
        className="size-7 bg-bg-3 border border-bg-6 flex items-center justify-center font-display text-[11px] font-semibold text-bg-11"
      >
        MV
      </div>
    </header>
  );
}
