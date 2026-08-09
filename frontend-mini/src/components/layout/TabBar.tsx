/**
 * TabBar — нижний tab-bar канона (5 вкладок).
 * Сейчас / Действия / Реклама / Ещё.
 * Иконка 21px + лейбл 10px, активная — accent + weight 600. safe-area снизу.
 * «Ещё» (Settings) активна также на вторичных системных экранах.
 */
import { useRouter, useLocation } from "@tanstack/react-router";
import {
  LayoutGrid,
  Megaphone,
  Activity,
  MoreHorizontal,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { haptic } from "@/lib/tg";

interface TabConfig {
  to: string;
  label: string;
  Icon: LucideIcon;
  /** Доп. префиксы, при которых вкладка активна (для «Ещё»). */
  extra?: string[];
}

const MAIN_TABS: TabConfig[] = [
  { to: "/", label: "Сейчас", Icon: LayoutGrid },
  {
    to: "/actions",
    label: "Действия",
    Icon: Activity,
    extra: ["/incidents"],
  },
  { to: "/ads", label: "Реклама", Icon: Megaphone },
  {
    to: "/settings",
    label: "Ещё",
    Icon: MoreHorizontal,
    extra: ["/offers", "/system", "/desktop", "/campaigns", "/analytics"],
  },
];

/** Пути, на которых tab-bar СКРЫВАЕМ (detail/вложенные экраны). */
const HIDDEN_ON: RegExp[] = [
  /^\/cabinets\/.+$/,
  /^\/ads\/.+$/,
  /^\/actions\/.+$/,
  /^\/incidents\/.+$/,
];

function shouldHide(pathname: string): boolean {
  return HIDDEN_ON.some((re) => re.test(pathname));
}

function isTabActive(tab: TabConfig, pathname: string): boolean {
  if (tab.to === "/") return pathname === "/" || pathname === "";
  if (pathname === tab.to || pathname.startsWith(`${tab.to}/`)) return true;
  if (tab.extra) {
    return tab.extra.some(
      (p) => pathname === p || pathname.startsWith(`${p}/`),
    );
  }
  return false;
}

export function TabBar() {
  const router = useRouter();
  const location = useLocation();
  const pathname = location.pathname;

  if (shouldHide(pathname)) return null;

  return (
    <nav
      aria-label="Навигация"
      className={cn(
        "fixed bottom-0 left-0 right-0 z-30",
        "max-w-[560px] mx-auto",
        "bg-bg-1",
        "border-t border-[var(--color-hairline-strong)]",
        "pb-[var(--tg-content-safe-bottom,env(safe-area-inset-bottom,0px))]",
        "pl-[var(--tg-content-safe-left,env(safe-area-inset-left,0px))]",
        "pr-[var(--tg-content-safe-right,env(safe-area-inset-right,0px))]",
      )}
    >
      <div className="grid grid-cols-4">
        {MAIN_TABS.map((tab) => {
          const active = isTabActive(tab, pathname);
          const Icon = tab.Icon;
          return (
            <button
              key={tab.to}
              type="button"
              onClick={() => {
                haptic.selection();
                void router.navigate({ to: tab.to });
              }}
              aria-label={tab.label}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex flex-col items-center justify-center gap-1",
                "min-h-14 py-2 px-1",
                "transition-colors duration-[var(--dur-fast)]",
                "focus-visible:outline-none focus-visible:ring-inset focus-visible:ring-2 focus-visible:ring-accent",
                active ? "text-accent" : "text-bg-9 hover:text-bg-10",
              )}
            >
              <Icon size={21} strokeWidth={active ? 2 : 1.6} aria-hidden />
              <span
                className={cn(
                  "text-[12px] leading-none",
                  active ? "font-semibold" : "font-medium",
                )}
              >
                {tab.label}
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
