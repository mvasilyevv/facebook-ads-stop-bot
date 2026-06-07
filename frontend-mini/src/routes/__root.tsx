/**
 * Корневой layout TMA Mini App.
 * AuthGuard → TelegramBackButton → контент (Outlet) → TabBar (нижний).
 * safe-area-inset-bottom: TabBar учитывает сам, контент — через paddingBottom.
 */
import { createRootRoute, Outlet } from "@tanstack/react-router";
import { AuthGuard } from "@/components/layout/AuthGuard";
import { TabBar } from "@/components/layout/TabBar";
import { TelegramBackButton } from "@/components/layout/TelegramBackButton";

export const Route = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
  return (
    <AuthGuard>
      {/* Управляет нативной TG-кнопкой "Назад", DOM не рендерит */}
      <TelegramBackButton />

      {/* Основной контент — отступ снизу под TabBar (56px + safe-area) */}
      <main
        className="flex flex-col min-h-screen max-w-[480px] mx-auto"
        style={{ paddingBottom: "calc(56px + env(safe-area-inset-bottom, 0px))" }}
      >
        <Outlet />
      </main>

      {/* Нижний tab-bar — fixed, скрывается на detail-страницах */}
      <TabBar />
    </AuthGuard>
  );
}
