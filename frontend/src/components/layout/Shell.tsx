/**
 * Shell — каркас приложения.
 * Grid: 240px (sidebar) × 1fr, 56px (topbar) × 1fr.
 * Grain-overlay через body::before задаётся в app.css.
 * Collapsed sidebar: 64px.
 */

import { type ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
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
        collapsed
          ? "grid-cols-[64px_1fr]"
          : "grid-cols-[240px_1fr]",
      )}
    >
      {/* TopBar занимает всю ширину (col 1-3) */}
      <TopBar />
      {/* Sidebar: col-1, row-2 */}
      <Sidebar />
      {/* Main content: col-2, row-2 */}
      <main className="col-start-2 col-end-3 row-start-2 row-end-3 px-10 py-8 overflow-x-hidden min-w-0">
        {children}
      </main>
    </div>
  );
}
