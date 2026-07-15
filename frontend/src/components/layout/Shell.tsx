/**
 * Shell — каркас приложения (канон design_handoff/web-dashboard.jsx).
 *
 * Раскладка: Sidebar (full-height, со своим brand-хедером 56px) слева,
 * справа — колонка из TopBar (56px) + scrolling main.
 * Grid: cols [sidebar | 1fr], rows [56px | 1fr]; Sidebar занимает обе строки.
 * Collapsed sidebar: 64px.
 *
 * main задаёт общий паддинг (px-10 py-8) — единый для всех страниц.
 * Dashboard рисует blueprint-фон внутри этого паддинга (decorative, masked to top).
 */

import * as Dialog from "@radix-ui/react-dialog";
import { type ReactNode, useState } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { OperationalStatusBar } from "./OperationalStatusBar";
import { CommandPalette } from "./CommandPalette";
import { ToastViewport } from "@/components/ui/Toast";
import { AssistantWidget } from "@/components/domain/assistant/AssistantWidget";
import { useUiStore } from "@/stores/ui";
import { cn } from "@/lib/utils/cn";

interface ShellProps {
  children: ReactNode;
}

export function Shell({ children }: ShellProps) {
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  return (
    <div
      className={cn(
        "min-h-screen grid grid-cols-1 grid-rows-[56px_1fr]",
        collapsed ? "md:grid-cols-[64px_1fr]" : "md:grid-cols-[240px_1fr]",
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
      <Dialog.Root open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[60] bg-[rgba(10,10,11,0.72)] md:hidden" />
          <Dialog.Content
            className="fixed inset-y-0 left-0 z-[61] outline-none md:hidden data-[state=open]:animate-in data-[state=open]:slide-in-from-left data-[state=closed]:animate-out data-[state=closed]:slide-out-to-left"
            aria-describedby="mobile-nav-description"
          >
            <Dialog.Title className="sr-only">Навигация</Dialog.Title>
            <Dialog.Description id="mobile-nav-description" className="sr-only">
              Основные разделы панели управления
            </Dialog.Description>
            <Sidebar mobile onNavigate={() => setMobileNavOpen(false)} />
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      {/* TopBar: col-2, row-1 (только над контентом) */}
      <TopBar onOpenNavigation={() => setMobileNavOpen(true)} />
      {/* Main: col-2, row-2 */}
      <main
        id="main-content"
        tabIndex={-1}
        className="col-start-1 row-start-2 min-w-0 overflow-x-hidden px-4 py-5 sm:px-6 md:col-start-2 md:col-end-3 md:px-10 md:py-8"
      >
        <OperationalStatusBar />
        {children}
      </main>
      {/* Командная палитра (⌘K) — всегда смонтирована для глобального хоткея */}
      <CommandPalette />
      {/* Toast-фидбек: единственный viewport на всё приложение (toast.success/error) */}
      <ToastViewport />
      {/* Плавающий AI-ассистент — всегда смонтирован (unread-бейдж живёт даже закрытым) */}
      <AssistantWidget />
    </div>
  );
}
