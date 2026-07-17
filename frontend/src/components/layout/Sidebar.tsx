/**
 * Sidebar — full-height боковая навигация (канон design_handoff/web-dashboard.jsx).
 *
 * Структура:
 *   - Brand-хедер (56px): 26×26 accent-квадрат «FB» + «STOP BOT / operator».
 *   - Nav-группы с numbered eyebrow (01 OPERATE / 02 SYSTEM).
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
  BarChart3,
  Settings,
  MonitorUp,
  PanelLeft,
  Rocket,
  X,
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
  /** Вложенные пункты (отрисовываются с отступом под родителем). */
  children?: NavItem[];
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
      { to: "/", label: "Обзор", icon: LayoutDashboard },
      { to: "/ads", label: "Объявления", icon: Layers, badgeKey: "ads" },
      {
        to: "/campaigns",
        label: "Кампании",
        icon: Radar,
        children: [{ to: "/campaigns/create", label: "Создание", icon: Rocket }],
      },
      { to: "/offers", label: "Офферы", icon: Tag },
      { to: "/analytics", label: "Аналитика", icon: BarChart3 },
    ],
  },
  {
    eyebrowNum: "02",
    eyebrow: "SYSTEM",
    items: [
      { to: "/remote-desktop", label: "Рабочий стол", icon: MonitorUp },
      { to: "/settings", label: "Настройки", icon: Settings },
    ],
  },
];

interface SidebarProps {
  mobile?: boolean;
  onNavigate?: () => void;
}

export function Sidebar({ mobile = false, onNavigate }: SidebarProps) {
  const storedCollapsed = useUiStore((s) => s.sidebarCollapsed);
  const collapsed = mobile ? false : storedCollapsed;
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  const { location } = useRouterState();

  // Реальные count-badges (кэшируются, разделяются с Dashboard).
  const { data: stats } = useDashboardStats();
  const adsBadge = stats ? (stats.ads_in_warning ?? 0) + (stats.ads_in_stop ?? 0) : 0;
  const badgeFor = (key?: "ads"): number => (key === "ads" ? adsBadge : 0);

  // Активность по самому длинному совпавшему пути: на /campaigns/create
  // горит только «Создание», а «Кампании» — приглушённо как родитель.
  const pathActive = (to: string): boolean =>
    to === "/"
      ? location.pathname === "/"
      : location.pathname === to || location.pathname.startsWith(`${to}/`);

  // Единый рендер пункта (родитель или подпункт).
  //   active — полная подсветка (bg + accent + левый бар),
  //   muted  — приглушённый родитель (активен дочерний маршрут),
  //   child  — отступ и уменьшенная иконка для вложенного пункта.
  const renderLink = (
    item: NavItem,
    opts: { active: boolean; muted?: boolean; child?: boolean },
  ) => {
    const badge = badgeFor(item.badgeKey);
    return (
      <Link
        key={item.to}
        to={item.to}
        // exact: иначе TanStack помечает родителя активным на дочернем маршруте
        // (на /campaigns/create горели бы и «Кампании», и «Создание»).
        activeOptions={{ exact: true }}
        onClick={onNavigate}
        aria-label={item.label}
        aria-current={opts.active ? "page" : undefined}
        title={collapsed ? item.label : undefined}
        className={cn(
          "relative flex w-full items-center gap-[11px] no-underline transition-colors",
          "rounded-[var(--radius-2)] text-[13px]",
          opts.child ? "h-8" : "h-9",
          collapsed ? "justify-center px-0" : opts.child ? "pl-[42px] pr-5" : "px-5",
          opts.active
            ? "bg-bg-2 text-accent"
            : opts.muted
              ? "text-accent-muted hover:bg-bg-1"
              : "text-bg-10 hover:bg-bg-1 hover:text-bg-11",
        )}
      >
        {opts.active && (
          <span
            aria-hidden="true"
            className="absolute left-0 top-2 bottom-2 w-[3px] rounded-full bg-accent"
          />
        )}
        <item.icon size={opts.child ? 15 : 18} strokeWidth={1.6} aria-hidden="true" />
        {!collapsed && <span className="flex-1 text-left">{item.label}</span>}
        {!collapsed && item.badgeKey && badge > 0 && (
          <span
            className={cn(
              "font-display text-[11px] tabular-nums",
              opts.active ? "text-accent" : "text-bg-9",
            )}
          >
            {badge}
          </span>
        )}
      </Link>
    );
  };

  return (
    <aside
      data-collapsed={collapsed || undefined}
      className={cn(
        "flex flex-col overflow-hidden border-r border-[var(--hairline)] bg-bg-0",
        mobile
          ? "h-full w-[min(82vw,280px)]"
          : "col-start-1 col-end-2 row-start-1 row-end-3 hidden md:flex",
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
              const childActive = item.children?.some((c) => pathActive(c.to)) ?? false;
              const selfActive = pathActive(item.to) && !childActive;

              if (!item.children) {
                return renderLink(item, { active: selfActive });
              }

              return (
                <div key={item.to}>
                  {renderLink(item, { active: selfActive, muted: childActive })}
                  {collapsed ? (
                    item.children.map((c) => renderLink(c, { active: pathActive(c.to) }))
                  ) : (
                    <div className="relative mt-0.5">
                      {/* Направляющая линия вложенности */}
                      <span
                        aria-hidden="true"
                        className="absolute left-[27px] top-0 bottom-1.5 w-px bg-[var(--hairline)]"
                      />
                      {item.children.map((c) =>
                        renderLink(c, { active: pathActive(c.to), child: true }),
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </nav>

      {/* Footer — mobile close / desktop collapse-toggle */}
      <div
        className={cn(
          "flex items-center border-t border-[var(--hairline)] py-3",
          mobile ? "justify-stretch px-4" : collapsed ? "justify-center px-0" : "justify-end px-4",
        )}
      >
        {mobile ? (
          <button
            type="button"
            onClick={onNavigate}
            className="inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-[var(--radius-2)] border border-[var(--hairline-strong)] text-[13px] text-bg-10 transition-colors hover:bg-bg-2 hover:text-bg-11 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <X size={15} aria-hidden="true" />
            Закрыть меню
          </button>
        ) : (
          <button
            type="button"
            onClick={toggleSidebar}
            aria-label={collapsed ? "Развернуть меню" : "Свернуть меню"}
            className="inline-flex size-7 items-center justify-center rounded-[var(--radius-2)] text-bg-9 transition-colors hover:bg-bg-2 hover:text-bg-11 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <PanelLeft size={16} aria-hidden="true" />
          </button>
        )}
      </div>
    </aside>
  );
}
