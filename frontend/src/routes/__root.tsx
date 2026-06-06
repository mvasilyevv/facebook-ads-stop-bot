import { createRootRoute, Outlet } from "@tanstack/react-router";

// Корневой layout. Phase 2 заменит на Shell (Sidebar + TopBar + Outlet).
export const Route = createRootRoute({
  component: () => <Outlet />,
});
