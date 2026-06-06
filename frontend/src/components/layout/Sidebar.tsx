/**
 * Sidebar — 240px expanded / 64px collapsed.
 * Группы с numbered eyebrow: "01 Operate" / "02 Catalog" / "03 History" / "04 System".
 * Активный nav-link: accent-цвет + 3px accent-бар слева.
 * State (collapsed) в Zustand.
 */

import { Link, useRouterState } from "@tanstack/react-router";
import { Activity, Layers, FileEdit, Tag, Clock, Settings, type LucideIcon } from "lucide-react";
import { useUiStore } from "@/stores/ui";
import { cn } from "@/lib/utils/cn";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
}

interface NavGroup {
  eyebrowNum: string;
  eyebrow: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    eyebrowNum: "01",
    eyebrow: "Operate",
    items: [
      { to: "/", label: "Панель", icon: Activity },
      { to: "/ads", label: "Объявления", icon: Layers },
      { to: "/drafts", label: "Черновики", icon: FileEdit },
    ],
  },
  {
    eyebrowNum: "02",
    eyebrow: "Catalog",
    items: [{ to: "/offers", label: "Офферы", icon: Tag }],
  },
  {
    eyebrowNum: "03",
    eyebrow: "History",
    items: [{ to: "/history", label: "История", icon: Clock }],
  },
  {
    eyebrowNum: "04",
    eyebrow: "System",
    items: [{ to: "/settings", label: "Настройки", icon: Settings }],
  },
];

export function Sidebar() {
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const { location } = useRouterState();

  return (
    <aside
      data-collapsed={collapsed || undefined}
      className={cn(
        "row-span-1 col-start-1 col-end-2 row-start-2 row-end-3",
        "border-r border-bg-5 bg-bg-0",
        "py-6 flex flex-col gap-6 overflow-hidden",
      )}
    >
      {NAV_GROUPS.map((group) => (
        <div key={group.eyebrowNum} className="flex flex-col">
          {/* Eyebrow группы — скрыт в collapsed-режиме */}
          {!collapsed && (
            <div className="font-display text-[10px] tracking-[.12em] uppercase text-bg-8 px-6 pb-2">
              <span className="text-bg-7 mr-2">{group.eyebrowNum}</span>
              {group.eyebrow}
            </div>
          )}
          {group.items.map((item) => {
            const isActive =
              location.pathname === item.to ||
              (item.to !== "/" && location.pathname.startsWith(item.to));

            return (
              <Link
                key={item.to}
                to={item.to}
                aria-label={item.label}
                aria-current={isActive ? "page" : undefined}
                title={collapsed ? item.label : undefined}
                className={cn(
                  "relative flex items-center gap-2.5",
                  "h-[34px] transition-colors no-underline",
                  "text-[13.5px]",
                  collapsed ? "px-0 justify-center" : "px-6",
                  isActive
                    ? "text-accent bg-bg-1"
                    : "text-bg-10 hover:bg-bg-2 hover:text-bg-11",
                )}
              >
                {isActive && (
                  <span
                    aria-hidden="true"
                    className="absolute left-0 top-1.5 bottom-1.5 w-[3px] bg-accent"
                  />
                )}
                <item.icon
                  size={15}
                  aria-hidden="true"
                  className={cn(isActive ? "opacity-100" : "opacity-70")}
                />
                {!collapsed && <span className="truncate">{item.label}</span>}
              </Link>
            );
          })}
        </div>
      ))}

      {/* Footer — версия и uptime (collapsed скрывает) */}
      {!collapsed && (
        <div className="mt-auto px-6 pt-4 border-t border-bg-5 font-display text-[11px] text-bg-9 tracking-[.02em]">
          <div className="flex justify-between mb-1">
            <span>build</span>
            <span className="text-bg-11">main</span>
          </div>
          <div className="flex justify-between">
            <span>version</span>
            <span className="text-bg-11">0.1.0</span>
          </div>
        </div>
      )}
    </aside>
  );
}
