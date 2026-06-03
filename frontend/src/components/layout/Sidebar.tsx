/**
 * Sidebar — 240px expanded / 64px collapsed.
 * Плоский список пунктов со сквозной нумерацией; Панель (главная) — без номера.
 * State в Zustand + localStorage.
 */

import { Link, useRouterState } from "@tanstack/react-router";
import { Activity, Layers, FileEdit, Tag, Clock, Settings, type LucideIcon } from "lucide-react";
import { useUiStore } from "@/stores/ui";
import { Tooltip } from "@/components/ui/Tooltip";
import { cn } from "@/lib/utils/cn";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Номер пункта; Панель — без номера (главная). */
  num?: string;
}

const NAV: NavItem[] = [
  { to: "/", label: "Панель", icon: Activity },
  { to: "/ads", label: "Объявления", icon: Layers, num: "01" },
  { to: "/drafts", label: "Черновики", icon: FileEdit, num: "02" },
  { to: "/offers", label: "Офферы", icon: Tag, num: "03" },
  { to: "/history", label: "История", icon: Clock, num: "04" },
  { to: "/settings", label: "Настройки", icon: Settings, num: "05" },
];

export function Sidebar() {
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const { location } = useRouterState();

  return (
    <aside
      data-collapsed={collapsed || undefined}
      className={cn(
        "row-span-1 col-start-1 col-end-2",
        "border-r border-bg-5 bg-bg-0",
        "py-6 flex flex-col gap-0.5",
        "transition-[width]",
        collapsed ? "w-[64px]" : "w-[240px]",
      )}
    >
      {NAV.map((item) => {
        const isActive =
          location.pathname === item.to ||
          (item.to !== "/" && location.pathname.startsWith(item.to));
        const linkEl = (
          <Link
            to={item.to}
            aria-label={item.label}
            aria-current={isActive ? "page" : undefined}
            className={cn(
              "relative flex items-center gap-2.5",
              "h-[36px] px-6 transition-colors",
              "text-[13.5px] no-underline",
              isActive ? "text-accent bg-bg-1" : "text-bg-10 hover:bg-bg-2 hover:text-bg-11",
              collapsed && "px-0 justify-center",
            )}
          >
            {isActive ? (
              <span
                aria-hidden="true"
                className="absolute left-0 top-1.5 bottom-1.5 w-[3px] bg-accent"
              />
            ) : null}
            <item.icon size={15} className={cn(isActive ? "opacity-100" : "opacity-70")} />
            {!collapsed ? (
              <span className="flex items-baseline gap-2.5 min-w-0">
                <span className="font-display text-[10px] tracking-wider text-bg-7 w-4 shrink-0">
                  {item.num ?? ""}
                </span>
                <span className="truncate">{item.label}</span>
              </span>
            ) : null}
          </Link>
        );
        return collapsed ? (
          <Tooltip key={item.to} content={item.label} side="right">
            {linkEl}
          </Tooltip>
        ) : (
          <span key={item.to} className="contents">
            {linkEl}
          </span>
        );
      })}
    </aside>
  );
}
