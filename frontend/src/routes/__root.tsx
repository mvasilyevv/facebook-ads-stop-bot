import { createRootRoute, Outlet } from "@tanstack/react-router";
import { Shell } from "@/components/layout/Shell";

// Корневой layout: единый flat-ledger Shell (TopBar + Sidebar + Outlet).
export const Route = createRootRoute({
  component: () => (
    <Shell>
      <Outlet />
    </Shell>
  ),
});
