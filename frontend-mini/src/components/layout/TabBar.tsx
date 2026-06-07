/**
 * TabBar — нижний tab-bar с safe-area-inset-bottom.
 * 5 основных вкладок + "Ещё" (overflow) для дополнительных.
 * Тач-цели: иконка + лейбл, min-height 56px.
 * Активная вкладка: accent off-white. Неактивная: bg-9.
 */
import { useRouter, useLocation } from "@tanstack/react-router";
import { cn } from "@/lib/cn";
import { haptic } from "@/lib/tg";

// ─── Иконки SVG (inline, без зависимости от lucide для минимального бандла) ──

function IconDashboard({ active }: { active: boolean }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={active ? 2 : 1.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="3" y="3" width="7" height="7" />
      <rect x="14" y="3" width="7" height="7" />
      <rect x="3" y="14" width="7" height="7" />
      <rect x="14" y="14" width="7" height="7" />
    </svg>
  );
}

function IconAds({ active }: { active: boolean }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={active ? 2 : 1.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
      <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
    </svg>
  );
}

function IconOffers({ active }: { active: boolean }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={active ? 2 : 1.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="12" cy="12" r="10" />
      <path d="M8 14s1.5 2 4 2 4-2 4-2" />
      <line x1="9" y1="9" x2="9.01" y2="9" />
      <line x1="15" y1="9" x2="15.01" y2="9" />
    </svg>
  );
}

function IconHistory({ active }: { active: boolean }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={active ? 2 : 1.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polyline points="12 8 12 12 14 14" />
      <path d="M3.05 11a9 9 0 1 0 .5-4.5" />
      <polyline points="3 3 3 9 9 9" />
    </svg>
  );
}

function IconSettings({ active }: { active: boolean }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={active ? 2 : 1.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

// ─── Конфигурация вкладок ──────────────────────────────────────────────────

interface TabConfig {
  to: string;
  label: string;
  Icon: React.ComponentType<{ active: boolean }>;
}

/**
 * Основные 5 вкладок.
 * Остальные страницы (/drafts, /health, /scripts, /ads/:id) открываются
 * как вложенные экраны — Tab Bar скрывается или показывает Back.
 */
const MAIN_TABS: TabConfig[] = [
  { to: "/",         label: "Дашборд",  Icon: IconDashboard },
  { to: "/ads",      label: "Объявл.", Icon: IconAds },
  { to: "/offers",   label: "Офферы",  Icon: IconOffers },
  { to: "/history",  label: "История", Icon: IconHistory },
  { to: "/settings", label: "Ещё",     Icon: IconSettings },
];

/** Пути, на которых Tab Bar СКРЫВАЕМ (detail/вложенные экраны). */
const HIDDEN_ON: RegExp[] = [
  /^\/ads\/.+$/,
  /^\/drafts\//,
];

function shouldHide(pathname: string): boolean {
  return HIDDEN_ON.some((re) => re.test(pathname));
}

/** Проверяем активность вкладки (точное совпадение для "/", prefix для остальных). */
function isTabActive(tabTo: string, pathname: string): boolean {
  if (tabTo === "/") return pathname === "/" || pathname === "";
  return pathname === tabTo || pathname.startsWith(`${tabTo}/`);
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
        "bg-[var(--color-bg-0)]/95 backdrop-blur-md",
        "border-t border-[var(--color-bg-5)]",
        // safe-area для iPhone (нотч/кнопка home)
        "pb-[env(safe-area-inset-bottom)]",
      )}
    >
      <div className="flex items-stretch">
        {MAIN_TABS.map((tab) => {
          const active = isTabActive(tab.to, pathname);
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
                "flex-1 flex flex-col items-center justify-center gap-[3px]",
                "min-h-[56px] pt-2 pb-1 px-1",
                "transition-colors duration-[var(--dur-fast)]",
                "focus-visible:outline-none focus-visible:ring-inset focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]",
                active
                  ? "text-[var(--color-accent)]"
                  : "text-[var(--color-bg-9)] hover:text-[var(--color-bg-10)]",
              )}
            >
              <tab.Icon active={active} />
              <span className="text-[10px] font-body leading-none">{tab.label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
