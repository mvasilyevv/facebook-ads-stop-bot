/**
 * Shell — responsive-каркас operator web.
 *
 * Раскладка: Sidebar (full-height, со своим brand-хедером 56px) слева,
 * справа — колонка из TopBar (56px) + scrolling main.
 * Grid: cols [196px sidebar | 1fr], rows [56px | 1fr]; Sidebar занимает обе строки.
 * Collapsed sidebar: 64px.
 *
 * main задаёт общий tokenized padding — единый для всех страниц.
 */

import { lazy, Suspense, type ReactNode, useEffect, useRef, useState } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { MobileBottomNav } from "./MobileBottomNav";
import { useToastStore } from "@/components/ui/toastStore";
import { useCommandPalette } from "@/stores/commandPalette";
import { useUiStore } from "@/stores/ui";
import { cn } from "@/lib/utils/cn";
import { OperatorRealtimeStatusProvider, useOperatorRealtime } from "@fb/operator-api";
import {
  fetchOperatorActionProjectionsForRealtime,
  fetchOperatorSnapshotForRealtime,
} from "@/lib/api/operator";

const LazyCommandPalette = lazy(() =>
  import("./CommandPalette").then((module) => ({ default: module.CommandPalette })),
);
const LazyAssistantWidget = lazy(() =>
  import("@/components/domain/assistant/AssistantWidget").then((module) => ({
    default: module.AssistantWidget,
  })),
);
const LazyMobileNavDialog = lazy(() =>
  import("./MobileNavDialog").then((module) => ({ default: module.MobileNavDialog })),
);
const LazyToastViewport = lazy(() =>
  import("@/components/ui/Toast").then((module) => ({ default: module.ToastViewport })),
);

interface ShellProps {
  children: ReactNode;
}

export function Shell({ children }: ShellProps) {
  const realtimeStatus = useOperatorRealtime({
    fetchActionProjections: fetchOperatorActionProjectionsForRealtime,
    fetchSnapshot: fetchOperatorSnapshotForRealtime,
  });
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const commandPaletteOpen = useCommandPalette((s) => s.open);
  const toggleCommandPalette = useCommandPalette((s) => s.toggle);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const mobileNavTriggerRef = useRef<HTMLButtonElement>(null);
  const [assistantReady, setAssistantReady] = useState(false);
  const hasToasts = useToastStore((state) => state.toasts.length > 0);

  useEffect(() => {
    function onCommandPaletteShortcut(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        toggleCommandPalette();
      }
    }
    window.addEventListener("keydown", onCommandPaletteShortcut);
    return () => window.removeEventListener("keydown", onCommandPaletteShortcut);
  }, [toggleCommandPalette]);

  useEffect(() => {
    // The assistant is useful but not part of the safety-critical first paint.
    // Deferring it keeps the operator snapshot inside the initial JS budget.
    const timer = window.setTimeout(() => setAssistantReady(true), 1_500);
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <OperatorRealtimeStatusProvider status={realtimeStatus}>
      <div
        className={cn(
          "min-h-screen grid grid-cols-1 grid-rows-[56px_1fr]",
          collapsed
            ? "md:grid-cols-[var(--sidebar-width-collapsed)_minmax(0,1fr)]"
            : "md:grid-cols-[var(--sidebar-width)_minmax(0,1fr)]",
        )}
      >
        <a
          href="#main-content"
          className="fixed left-3 top-3 z-[100] -translate-y-20 rounded-[var(--radius-2)] bg-accent px-4 py-2 text-[13px] font-semibold text-bg-0 transition-transform focus:translate-y-0"
        >
          Перейти к содержимому
        </a>
        {/* Sidebar: col-1, обе строки (full height, brand-хедер внутри) */}
        <Sidebar />
        {mobileNavOpen ? (
          <Suspense fallback={null}>
            <LazyMobileNavDialog
              open
              onOpenChange={setMobileNavOpen}
              returnFocusRef={mobileNavTriggerRef}
            />
          </Suspense>
        ) : null}
        {/* TopBar: col-2, row-1 (только над контентом) */}
        <TopBar
          onOpenNavigation={() => setMobileNavOpen(true)}
          navigationButtonRef={mobileNavTriggerRef}
        />
        {/* Main: col-2, row-2 */}
        <main
          id="main-content"
          tabIndex={-1}
          className="col-start-1 row-start-2 min-w-0 overflow-x-hidden px-4 pb-[calc(80px+env(safe-area-inset-bottom,0px))] pt-5 sm:px-6 md:col-start-2 md:col-end-3 md:px-[var(--content-padding-x)] md:py-[var(--content-padding-y)]"
        >
          {realtimeStatus !== "connected" ? (
            <div
              role="status"
              aria-live="polite"
              data-state="stale"
              className="mb-4 border-y border-warning/40 bg-warning-bg px-4 py-3 text-[14px] leading-5 text-bg-11"
            >
              Live-связь восстанавливается. Данные считаются устаревшими, денежные действия
              заблокированы до сверки снимка.
            </div>
          ) : null}
          {children}
        </main>
        <MobileBottomNav />
        {/* Тяжёлые data-хуки палитры загружаются только после осознанного открытия. */}
        {commandPaletteOpen ? (
          <Suspense fallback={null}>
            <LazyCommandPalette manageShortcut={false} />
          </Suspense>
        ) : null}
        {hasToasts ? (
          <Suspense fallback={null}>
            <LazyToastViewport />
          </Suspense>
        ) : null}
        {assistantReady ? (
          <Suspense fallback={null}>
            <LazyAssistantWidget />
          </Suspense>
        ) : null}
      </div>
    </OperatorRealtimeStatusProvider>
  );
}
