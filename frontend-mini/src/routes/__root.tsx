import { createRootRoute, Outlet } from "@tanstack/react-router";

// Корневой layout mini. Phase 4B заменит на AuthGuard + TabBar + safe-area shell.
export const Route = createRootRoute({
  component: () => <Outlet />,
});
