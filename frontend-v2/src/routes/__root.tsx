/**
 * Root route — оборачивает всё приложение в Shell + общие провайдеры.
 * TanStack Router file-based convention.
 */

import { Outlet, createRootRoute } from "@tanstack/react-router";
import { Shell } from "@/components/layout/Shell";
import { ToastViewport } from "@/components/ui/Toast";

export const Route = createRootRoute({
  component: RootComponent,
  notFoundComponent: NotFound,
});

function RootComponent() {
  return (
    <Shell>
      <Outlet />
      <ToastViewport />
    </Shell>
  );
}

function NotFound() {
  return (
    <div className="py-20 text-center">
      <h1 className="page-title">404.</h1>
      <p className="text-bg-10 mt-3">Страница не найдена.</p>
    </div>
  );
}
