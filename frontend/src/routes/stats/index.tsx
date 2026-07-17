import { createFileRoute, redirect } from "@tanstack/react-router";
import { analyticsRouteSearch } from "@/lib/analyticsSearch";

export const Route = createFileRoute("/stats/")({
  beforeLoad: () => {
    throw redirect({
      to: "/analytics",
      search: analyticsRouteSearch(),
      replace: true,
    });
  },
});
