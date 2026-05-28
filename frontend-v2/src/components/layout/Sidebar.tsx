/**
 * Sidebar — 240px expanded / 64px collapsed.
 * Состав по группам:
 *   01 OPERATE: Dashboard / Ads / Drafts
 *   02 CATALOG: Offers
 *   03 HISTORY: History
 *   04 SYSTEM: Settings
 * State в Zustand + localStorage.
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
  num: string;
  title: string;
  items: NavItem[];
}

const NAV: NavGroup[] = [
  {
    num: "01",
    title: "Operate",
    items: [
      { to: "/", label: "Dashboard", icon: Activity },
      { to: "/ads", label: "Ads", icon: Layers },
      { to: "/drafts", label: "Drafts", icon: FileEdit },
    ],
  },
  {
    num: "02",
    title: "Catalog",
    items: [{ to: "/offers", label: "Offers", icon: Tag }],
  },
  {
    num: "03",
    title: "History",
    items: [{ to: "/history", label: "History", icon: Clock }],
  },
  {
    num: "04",
    title: "System",
    items: [{ to: "/settings", label: "Settings", icon: Settings }],
  },
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
        "py-6 flex flex-col gap-6",
        "transition-[width]",
        collapsed ? "w-[64px]" : "w-[240px]",
      )}
    >
      {NAV.map((group) => (
        <div key={group.num} className="flex flex-col">
          {!collapsed ? (
            <div className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8 px-6 pb-2">
              <span className="text-bg-7 mr-2">{group.num}</span>
              {group.title}
            </div>
          ) : null}
          {group.items.map((item) => {
            const isActive = location.pathname === item.to ||
              (item.to !== "/" && location.pathname.startsWith(item.to));
            return (
              <Link
                key={item.to}
                to={item.to}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "relative flex items-center gap-2.5",
                  "h-[34px] px-6 transition-colors",
                  "text-[13.5px] no-underline",
                  isActive
                    ? "text-accent bg-bg-1"
                    : "text-bg-10 hover:bg-bg-2 hover:text-bg-11",
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
                {!collapsed ? <span>{item.label}</span> : null}
              </Link>
            );
          })}
        </div>
      ))}

      {!collapsed ? (
        <div className="mt-auto px-6 pt-4 border-t border-bg-5 font-display text-[11px] text-bg-9 tracking-tight">
          <div className="flex justify-between mb-1">
            <span>build</span>
            <span className="text-bg-11">v2.0.0-dev</span>
          </div>
          <div className="flex justify-between">
            <span>spec</span>
            <span className="text-bg-11">v1.0</span>
          </div>
        </div>
      ) : null}
    </aside>
  );
}
