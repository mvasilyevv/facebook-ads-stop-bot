import { createRootRoute, Outlet } from "@tanstack/react-router";
import { Shell } from "@/components/layout/Shell";

// Корневой layout: Shell (TopBar + Sidebar + Outlet) + grain-overlay (body::before via app.css).
export const Route = createRootRoute({
  component: () => (
    <Shell>
      <Outlet />
    </Shell>
  ),
});
