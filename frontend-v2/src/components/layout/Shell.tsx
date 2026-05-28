/**
 * Shell — каркас приложения: TopBar + Sidebar + Main.
 * Главный layout, оборачивается __root.tsx роутом.
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
        "min-h-screen grid",
        collapsed
          ? "grid-cols-[64px_1fr] grid-rows-[56px_1fr]"
          : "grid-cols-[240px_1fr] grid-rows-[56px_1fr]",
      )}
    >
      <TopBar />
      <Sidebar />
      <main className="col-start-2 col-end-3 row-start-2 row-end-3 px-10 py-8 overflow-x-hidden">
        {children}
      </main>
    </div>
  );
}
