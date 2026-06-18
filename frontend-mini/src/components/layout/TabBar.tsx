/**
 * TabBar — нижний tab-bar канона (5 вкладок).
 * Панель / Объявления / Черновики / История / Ещё.
 * Иконка 21px + лейбл 10px, активная — accent + weight 600. safe-area снизу.
 * «Ещё» (Settings) активна также на /offers, /health, /scripts.
 */
import { useRouter, useLocation } from "@tanstack/react-router";
import {
  LayoutGrid,
  Megaphone,
  FileStack,
  History as HistoryIcon,
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
  { to: "/", label: "Панель", Icon: LayoutGrid },
  { to: "/ads", label: "Объявления", Icon: Megaphone },
  { to: "/drafts", label: "Черновики", Icon: FileStack },
  { to: "/history", label: "История", Icon: HistoryIcon },
  { to: "/settings", label: "Ещё", Icon: MoreHorizontal, extra: ["/offers", "/health", "/scripts"] },
];

/** Пути, на которых tab-bar СКРЫВАЕМ (detail/вложенные экраны). */
const HIDDEN_ON: RegExp[] = [/^\/ads\/.+$/];

function shouldHide(pathname: string): boolean {
  return HIDDEN_ON.some((re) => re.test(pathname));
}

function isTabActive(tab: TabConfig, pathname: string): boolean {
  if (tab.to === "/") return pathname === "/" || pathname === "";
  if (pathname === tab.to || pathname.startsWith(`${tab.to}/`)) return true;
  if (tab.extra) {
    return tab.extra.some((p) => pathname === p || pathname.startsWith(`${p}/`));
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
        "max-w-[480px] mx-auto",
        "bg-bg-1/95 backdrop-blur-md",
        "border-t border-[var(--hairline-strong)]",
        "pb-[env(safe-area-inset-bottom)]",
      )}
    >
      <div className="grid grid-cols-5">
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
                "min-h-[52px] py-2 px-1",
                "transition-colors duration-[var(--dur-fast)]",
                "focus-visible:outline-none focus-visible:ring-inset focus-visible:ring-2 focus-visible:ring-accent",
                active ? "text-accent" : "text-bg-9 hover:text-bg-10",
              )}
            >
              <Icon size={21} strokeWidth={active ? 2 : 1.6} aria-hidden />
              <span
                className={cn("text-[10px] leading-none", active ? "font-semibold" : "font-medium")}
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
