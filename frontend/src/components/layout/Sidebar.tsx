/**
 * Sidebar — full-height боковая навигация (канон design_handoff/web-dashboard.jsx).
 *
 * Структура:
 *   - Brand-хедер (56px): 26×26 accent-квадрат «FB» + «STOP BOT / operator».
 *   - Nav-группы с numbered eyebrow (01 OPERATE / 02 CATALOG / 03 HISTORY / 04 SYSTEM).
 *     Item: 36px, icon + label + опциональный count-badge. Active = bg-2 fill +
 *     accent text + 3px accent left-bar.
 *   - Footer: ТОЛЬКО collapse-toggle (worker-статус живёт в TopBar, не дублируем).
 *
 * Count-badges (реальные данные):
 *   - Объявления: ads_in_warning + ads_in_stop (активные инциденты, из stats).
 * 240px expanded / 64px collapsed (state в Zustand).
 */

import { Link, useRouterState } from "@tanstack/react-router";
import {
  LayoutDashboard,
  Layers,
  Radar,
  Tag,
  Clock,
  Settings,
  PanelLeft,
  Rocket,
  type LucideIcon,
} from "lucide-react";
import { useUiStore } from "@/stores/ui";
import { useDashboardStats } from "@/lib/api/dashboard";
import { cn } from "@/lib/utils/cn";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Ключ для подстановки count-badge. */
  badgeKey?: "ads";
}

interface NavGroup {
  eyebrowNum: string;
  eyebrow: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    eyebrowNum: "01",
    eyebrow: "OPERATE",
    items: [
      { to: "/", label: "Панель", icon: LayoutDashboard },
      { to: "/ads", label: "Объявления", icon: Layers, badgeKey: "ads" },
      { to: "/campaigns", label: "Кампании", icon: Radar },
      { to: "/campaigns/create", label: "Создание", icon: Rocket },
    ],
  },
  {
    eyebrowNum: "02",
    eyebrow: "CATALOG",
    items: [{ to: "/offers", label: "Офферы", icon: Tag }],
  },
  {
    eyebrowNum: "03",
    eyebrow: "HISTORY",
    items: [{ to: "/history", label: "История", icon: Clock }],
  },
  {
    eyebrowNum: "04",
    eyebrow: "SYSTEM",
    items: [{ to: "/settings", label: "Настройки", icon: Settings }],
  },
];

export function Sidebar() {
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  const { location } = useRouterState();

  // Реальные count-badges (кэшируются, разделяются с Dashboard).
  const { data: stats } = useDashboardStats();
  const adsBadge = stats ? (stats.ads_in_warning ?? 0) + (stats.ads_in_stop ?? 0) : 0;
  const badgeFor = (key?: "ads"): number => (key === "ads" ? adsBadge : 0);

  return (
    <aside
      data-collapsed={collapsed || undefined}
      className={cn(
        "col-start-1 col-end-2 row-start-1 row-end-3",
        "flex flex-col overflow-hidden border-r border-[var(--hairline)] bg-bg-0",
      )}
    >
      {/* Brand-хедер (56px, совпадает с высотой TopBar) */}
      <div
        className={cn(
          "flex h-14 shrink-0 items-center gap-2.5 border-b border-[var(--hairline)]",
          collapsed ? "justify-center px-0" : "px-5",
        )}
      >
        <div
          aria-hidden="true"
          className="flex size-[26px] shrink-0 items-center justify-center rounded-[var(--radius-1)] bg-accent"
        >
          <span className="font-display text-[14px] font-bold text-bg-0">FB</span>
        </div>
        {!collapsed && (
          <div className="min-w-0">
            <div className="font-display text-[13px] font-semibold leading-[1.1] text-bg-11">
              STOP BOT
            </div>
            <div className="text-[10px] tracking-[0.04em] text-bg-9">operator</div>
          </div>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-3">
        {NAV_GROUPS.map((group) => (
          <div key={group.eyebrowNum} className="mb-3.5">
            {!collapsed ? (
              <div className="mb-2 px-5 font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-bg-9">
                <span className="mr-1.5 text-accent-muted">{group.eyebrowNum}</span>
                {group.eyebrow}
              </div>
            ) : (
              <div className="mx-4 mb-2 h-px bg-[var(--hairline)]" aria-hidden="true" />
            )}
            {group.items.map((item) => {
              const isActive =
                location.pathname === item.to ||
                (item.to !== "/" && location.pathname.startsWith(item.to));
              const badge = badgeFor(item.badgeKey);

              return (
                <Link
                  key={item.to}
                  to={item.to}
                  aria-label={item.label}
                  aria-current={isActive ? "page" : undefined}
                  title={collapsed ? item.label : undefined}
                  className={cn(
                    "relative flex h-9 w-full items-center gap-[11px] no-underline transition-colors",
                    "rounded-[var(--radius-2)] text-[13px]",
                    collapsed ? "justify-center px-0" : "px-5",
                    isActive
                      ? "bg-bg-2 text-accent"
                      : "text-bg-10 hover:bg-bg-1 hover:text-bg-11",
                  )}
                >
                  {isActive && (
                    <span
                      aria-hidden="true"
                      className="absolute left-0 top-2 bottom-2 w-[3px] rounded-full bg-accent"
                    />
                  )}
                  <item.icon size={18} strokeWidth={1.6} aria-hidden="true" />
                  {!collapsed && <span className="flex-1 text-left">{item.label}</span>}
                  {!collapsed && item.badgeKey && badge > 0 && (
                    <span
                      className={cn(
                        "font-display text-[11px] tabular-nums",
                        isActive ? "text-accent" : "text-bg-9",
                      )}
                    >
                      {badge}
                    </span>
                  )}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      {/* Footer — только collapse-toggle */}
      <div
        className={cn(
          "flex items-center border-t border-[var(--hairline)] py-3",
          collapsed ? "justify-center px-0" : "justify-end px-4",
        )}
      >
        <button
          type="button"
          onClick={toggleSidebar}
          aria-label={collapsed ? "Развернуть меню" : "Свернуть меню"}
          className="inline-flex size-7 items-center justify-center rounded-[var(--radius-2)] text-bg-9 transition-colors hover:bg-bg-2 hover:text-bg-11 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <PanelLeft size={16} aria-hidden="true" />
        </button>
      </div>
    </aside>
  );
}
