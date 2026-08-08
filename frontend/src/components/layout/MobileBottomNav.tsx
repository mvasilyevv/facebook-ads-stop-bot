import { Link, useRouterState } from "@tanstack/react-router";
import { Activity, LayoutDashboard, Megaphone, MoreHorizontal } from "lucide-react";

interface MobileTab {
  to: "/" | "/actions" | "/ads" | "/settings";
  label: string;
  Icon: typeof LayoutDashboard;
  activePaths?: readonly string[];
}

const tabs: readonly MobileTab[] = [
  { to: "/", label: "Сейчас", Icon: LayoutDashboard },
  {
    to: "/actions",
    label: "Действия",
    Icon: Activity,
    activePaths: ["/incidents"],
  },
  { to: "/ads", label: "Реклама", Icon: Megaphone },
  {
    to: "/settings",
    label: "Ещё",
    Icon: MoreHorizontal,
    activePaths: ["/analytics", "/campaigns", "/offers", "/remote-desktop", "/system"],
  },
];

export function isMobileTabActive(tab: MobileTab, pathname: string): boolean {
  if (tab.to === "/") return pathname === "/";
  if (pathname === tab.to || pathname.startsWith(`${tab.to}/`)) return true;
  return (
    tab.activePaths?.some((path) => pathname === path || pathname.startsWith(`${path}/`)) ?? false
  );
}

export function MobileBottomNav() {
  const { location } = useRouterState();
  return (
    <nav
      aria-label="Основная мобильная навигация"
      className="fixed inset-x-0 bottom-0 z-50 border-t border-[var(--color-hairline-strong)] bg-bg-1/95 pb-[env(safe-area-inset-bottom,0px)] backdrop-blur-xl md:hidden"
    >
      <div className="grid grid-cols-4">
        {tabs.map((tab) => {
          const { to, label, Icon } = tab;
          const active = isMobileTabActive(tab, location.pathname);
          return (
            <Link
              key={to}
              to={to}
              aria-current={active ? "page" : undefined}
              className={`flex min-h-14 flex-col items-center justify-center gap-1 px-2 text-[12px] font-semibold no-underline focus-visible:outline-2 focus-visible:outline-offset-[-3px] focus-visible:outline-accent ${
                active ? "text-accent" : "text-bg-9"
              }`}
            >
              <Icon size={20} strokeWidth={active ? 2 : 1.6} aria-hidden="true" />
              {label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
