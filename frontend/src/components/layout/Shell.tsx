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

import { type ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
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

  return (
    <div
      className={cn(
        "min-h-screen grid grid-rows-[56px_1fr]",
        collapsed ? "grid-cols-[64px_1fr]" : "grid-cols-[240px_1fr]",
      )}
    >
      {/* Sidebar: col-1, обе строки (full height, brand-хедер внутри) */}
      <Sidebar />
      {/* TopBar: col-2, row-1 (только над контентом) */}
      <TopBar />
      {/* Main: col-2, row-2 */}
      <main className="col-start-2 col-end-3 row-start-2 row-end-3 min-w-0 overflow-x-hidden px-10 py-8">
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
